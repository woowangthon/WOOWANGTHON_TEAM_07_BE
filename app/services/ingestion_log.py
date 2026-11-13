"""수집 이슈 로그 (JSON Lines). data/logs/ingestion_issues.jsonl

한 줄에 JSON 하나를 **추가(append)**한다. 기존 내용을 덮어쓰지 않으며, 실행은 run_id로 구분한다.
data/logs/는 .gitignore로 Git에서 제외된다.

기록하는 이유(reason)
- NO_STATION_MATCH : 역 코드가 station_external_ids(source=OA-15994)에 없어 저장하지 않은 설비
- UNKNOWN_STATUS   : USE_YN이 사용가능/보수중이 아니어서 상태를 해석하지 않은 설비(원문 보존)

항목: run_id, logged_at(UTC), source, reason, external_station_code, station_name_raw,
      elevator_name, source_status_text(USE_YN 원문)

API 키와 요청 URL은 기록하지 않는다. 외부 응답에서 온 문자열도 한 번 더 정리(키 가리기, URL 제거)하고
길이를 제한한다.
"""

import json
import os
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from app.clients.oa15994 import ElevatorRecord
from app.clients.seoul_open_api import redact, strip_urls

DEFAULT_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "logs" / "ingestion_issues.jsonl"

REASON_NO_STATION_MATCH = "NO_STATION_MATCH"
REASON_UNKNOWN_STATUS = "UNKNOWN_STATUS"

MAX_FIELD_LENGTH = 500


class IngestionLogError(Exception):
    """로그 파일을 준비하거나 기록하지 못했을 때. 메시지에는 키나 URL을 넣지 않는다."""


def new_run_id() -> str:
    return uuid.uuid4().hex


def _clean(value: str, secrets: Sequence[str]) -> str:
    text = value
    for secret in secrets:
        text = redact(text, secret)
    text = redact(text)  # 키 구간이 있는 URL 형태도 가린다
    text = strip_urls(text)  # 요청 URL은 호스트와 경로까지 남기지 않는다
    return text[:MAX_FIELD_LENGTH]


def make_entries(
    *,
    run_id: str,
    logged_at: str,
    source: str,
    records: Iterable[ElevatorRecord],
    unmapped_keys: set[tuple[str, str]],
    secrets: Sequence[str] = (),
) -> list[dict[str, str]]:
    """미매핑 설비와 미확인 상태 설비의 로그 항목을 만든다.

    unmapped_keys는 (역 코드, 설비명) 집합이다. 미매핑 설비는 NO_STATION_MATCH로,
    그 밖에 상태가 None인(역 매핑은 된) 설비는 UNKNOWN_STATUS로 기록한다.
    """
    entries: list[dict[str, str]] = []
    unknown: list[ElevatorRecord] = []
    for record in records:
        if record.key in unmapped_keys:
            reason = REASON_NO_STATION_MATCH
        elif record.status is None:
            unknown.append(record)
            continue
        else:
            continue
        entries.append(_entry(run_id, logged_at, source, reason, record, secrets))
    for record in unknown:
        entries.append(_entry(run_id, logged_at, source, REASON_UNKNOWN_STATUS, record, secrets))
    return entries


def _entry(
    run_id: str, logged_at: str, source: str, reason: str, record: ElevatorRecord, secrets: Sequence[str]
) -> dict[str, str]:
    return {
        "run_id": run_id,
        "logged_at": logged_at,
        "source": source,
        "reason": reason,
        "external_station_code": _clean(record.station_code, secrets),
        "station_name_raw": _clean(record.station_name, secrets),
        "elevator_name": _clean(record.elevator_name, secrets),
        "source_status_text": _clean(record.source_status_text, secrets),
    }


def check_writable(path: Path) -> None:
    """DB를 커밋하기 전에 로그를 쓸 수 있는지 확인한다. 디렉터리를 만들고 추가 모드로 열어 본다.

    내용은 쓰지 않는다. 파일이 없으면 빈 파일이 생긴다.
    """
    failure: IngestionLogError | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not path.is_file():
            failure = IngestionLogError("로그 경로가 파일이 아닙니다.")
        else:
            with path.open("a", encoding="utf-8"):
                pass
    except OSError as exc:
        failure = IngestionLogError(f"로그 파일을 준비할 수 없습니다 ({type(exc).__name__}).")
    if failure is not None:
        raise failure


def append_entries(path: Path, entries: Sequence[dict[str, str]]) -> int:
    """항목을 한 줄씩 추가한다. 기록한 줄 수를 돌려준다. 실패하면 IngestionLogError다."""
    if not entries:
        return 0
    text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    failure: IngestionLogError | None = None
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)  # 한 번에 쓴다
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        failure = IngestionLogError(f"로그를 기록하지 못했습니다 ({type(exc).__name__}).")
    if failure is not None:
        raise failure
    return len(entries)
