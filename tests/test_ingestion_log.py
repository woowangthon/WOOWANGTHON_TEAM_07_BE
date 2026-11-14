"""수집 이슈 로그(JSONL) 검증. 임시 디렉터리만 쓰며 실제 data/logs는 건드리지 않는다."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from app.clients.oa15994 import ElevatorRecord
from app.services import ingestion_log
from app.services.ingestion_log import (
    DEFAULT_LOG_PATH,
    REASON_NO_STATION_MATCH,
    REASON_UNKNOWN_STATUS,
    IngestionLogError,
    append_entries,
    check_writable,
    make_entries,
    new_run_id,
)

ROOT = Path(__file__).resolve().parent.parent
KEY = "FAKEKEY-abc123XYZ"
ENTRY_KEYS = {
    "run_id", "logged_at", "source", "reason", "external_station_code",
    "station_name_raw", "elevator_name", "source_status_text",
}


def rec(code="0150", name="A", status="가동중", text="사용가능", station="서울역(1)"):
    return ElevatorRecord(code, station, name, "B1-1F", "2번 출입구", text, status)


def entries(records, unmapped=(), secrets=()):
    return make_entries(
        run_id="run-1", logged_at="2030-01-01T03:00:00Z", source="OA-15994",
        records=records, unmapped_keys=set(unmapped), secrets=secrets,
    )


# ---------- 항목 만들기 ----------


def test_unmapped_elevator_is_logged_as_no_station_match():
    result = entries([rec(code="9999", name="X")], unmapped={("9999", "X")})

    assert len(result) == 1
    assert result[0] == {
        "run_id": "run-1", "logged_at": "2030-01-01T03:00:00Z", "source": "OA-15994",
        "reason": REASON_NO_STATION_MATCH, "external_station_code": "9999",
        "station_name_raw": "서울역(1)", "elevator_name": "X", "source_status_text": "사용가능",
    }


def test_unknown_status_is_logged_with_the_original_text():
    result = entries([rec(name="U", status=None, text="점검 중")])

    assert [e["reason"] for e in result] == [REASON_UNKNOWN_STATUS]
    assert result[0]["source_status_text"] == "점검 중"  # 원문 보존


@pytest.mark.parametrize("raw", ["고장", " 사용가능", "사용가능 ", "OK", "점검\t중"])
def test_unknown_status_original_text_is_kept_exactly(raw):
    assert entries([rec(status=None, text=raw)])[0]["source_status_text"] == raw


def test_known_mapped_elevators_are_not_logged():
    assert entries([rec(name="A"), rec(name="B", status="가동 안됨", text="보수중")]) == []


def test_unmapped_and_unknown_are_each_logged_once():
    records = [
        rec(code="9999", name="미매핑", status=None, text="점검"),  # 미매핑이면서 미확인
        rec(name="미확인", status=None, text="고장"),
        rec(name="정상"),
    ]

    result = entries(records, unmapped={("9999", "미매핑")})

    assert [(e["elevator_name"], e["reason"]) for e in result] == [
        ("미매핑", REASON_NO_STATION_MATCH),
        ("미확인", REASON_UNKNOWN_STATUS),
    ]


def test_every_entry_has_exactly_the_documented_fields():
    result = entries([rec(code="9999", name="X"), rec(name="U", status=None, text="?")], unmapped={("9999", "X")})

    assert result and all(set(e) == ENTRY_KEYS for e in result)


def test_entries_never_contain_the_key_or_a_request_url():
    leaky = rec(
        code="9999", name=f"설비 {KEY}", station=f"http://openapi.seoul.go.kr:8088/{KEY}/json/Svc/1/5/", text=f"x {KEY}"
    )

    result = entries([leaky], unmapped={leaky.key}, secrets=(KEY,))

    blob = json.dumps(result, ensure_ascii=False)
    assert KEY not in blob
    assert "openapi.seoul.go.kr" not in blob and "http://" not in blob  # 요청 URL은 호스트까지 남기지 않는다
    assert "[URL 생략]" in blob


def test_long_values_are_truncated():
    result = entries([rec(status=None, text="가" * 5000)])

    assert len(result[0]["source_status_text"]) == ingestion_log.MAX_FIELD_LENGTH


def test_run_ids_are_unique_hex_strings():
    ids = {new_run_id() for _ in range(50)}

    assert len(ids) == 50 and all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)


# ---------- 쓰기 가능 확인 ----------


def test_check_writable_creates_directory_and_empty_file(tmp_path):
    path = tmp_path / "a" / "b" / "issues.jsonl"

    check_writable(path)

    assert path.is_file() and path.stat().st_size == 0  # 내용은 쓰지 않는다


def test_check_writable_keeps_existing_content(tmp_path):
    path = tmp_path / "issues.jsonl"
    path.write_text('{"keep": 1}\n', encoding="utf-8")

    check_writable(path)

    assert path.read_text(encoding="utf-8") == '{"keep": 1}\n'


def test_check_writable_rejects_a_directory_path(tmp_path):
    with pytest.raises(IngestionLogError, match="파일이 아닙니다"):
        check_writable(tmp_path)


def test_check_writable_rejects_when_a_parent_is_a_file(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(IngestionLogError, match="준비할 수 없습니다"):
        check_writable(blocker / "logs" / "issues.jsonl")


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="Windows는 chmod 권한 검사를 하지 않고, root는 우회한다"
)
def test_check_writable_rejects_a_read_only_directory(tmp_path):
    directory = tmp_path / "ro"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        with pytest.raises(IngestionLogError):
            check_writable(directory / "issues.jsonl")
    finally:
        directory.chmod(0o700)


# ---------- append ----------


def test_append_writes_one_json_per_line(tmp_path):
    path = tmp_path / "issues.jsonl"
    items = entries([rec(status=None, text="점검")])

    assert append_entries(path, items) == 1

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == items[0]


def test_append_never_overwrites_and_keeps_runs_apart(tmp_path):
    path = tmp_path / "issues.jsonl"
    first = make_entries(run_id="run-A", logged_at="2030-01-01T03:00:00Z", source="OA-15994",
                         records=[rec(status=None, text="점검")], unmapped_keys=set())
    second = make_entries(run_id="run-B", logged_at="2030-01-01T04:00:00Z", source="OA-15994",
                          records=[rec(code="9999", name="X")], unmapped_keys={("9999", "X")})

    append_entries(path, first)
    before = path.read_text(encoding="utf-8")
    append_entries(path, second)

    text = path.read_text(encoding="utf-8")
    assert text.startswith(before)  # 앞의 내용이 그대로다
    rows = [json.loads(line) for line in text.splitlines()]
    assert [r["run_id"] for r in rows] == ["run-A", "run-B"]


def test_append_keeps_korean_text_readable(tmp_path):
    path = tmp_path / "issues.jsonl"

    append_entries(path, entries([rec(status=None, text="점검 중")]))

    assert "점검 중" in path.read_text(encoding="utf-8")  # \uXXXX로 바뀌지 않는다


def test_append_with_no_entries_does_not_touch_the_file(tmp_path):
    path = tmp_path / "issues.jsonl"

    assert append_entries(path, []) == 0
    assert not path.exists()


def test_append_failure_raises_a_safe_error(tmp_path):
    with pytest.raises(IngestionLogError, match="기록하지 못했습니다"):
        append_entries(tmp_path, entries([rec(status=None, text="x")]))  # 디렉터리에는 쓸 수 없다


# ---------- 기본 경로 ----------


def test_default_log_path_is_under_data_logs_and_git_ignored():
    assert DEFAULT_LOG_PATH == ROOT / "data" / "logs" / "ingestion_issues.jsonl"
    assert "data/logs/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "check-ignore", "-q", str(DEFAULT_LOG_PATH)], cwd=ROOT)
        assert result.returncode == 0  # Git에서 제외된다


# ---------- URL 제거 ----------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("GET http://openapi.seoul.go.kr:8088/KEY/json/Svc/1/5/ failed", "GET [URL 생략] failed"),
        ("a https://h.test/x?y=1 b http://c.test c", "a [URL 생략] b [URL 생략] c"),
        ("URL 없음", "URL 없음"),
        ("ftp는 건드리지 않는다 ftp://h.test", "ftp는 건드리지 않는다 ftp://h.test"),
    ],
)
def test_strip_urls_removes_whole_http_urls(text, expected):
    from app.clients.seoul_open_api import strip_urls

    assert strip_urls(text) == expected
