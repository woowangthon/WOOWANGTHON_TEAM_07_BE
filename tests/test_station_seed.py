"""OA-12035 기반 1~8호선 시드와 멱등 적재 검증. 개발 DB(data/dev.db)는 건드리지 않는다."""

import csv
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cli.load_seed import EXIT_NOT_READY, EXIT_OK, EXIT_SEED_ERROR, main
from app.services.station_seed import (
    ConflictError,
    SeedError,
    load_seed,
    read_seed_dir,
)

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"
SAMPLE_15994 = ROOT / "docs" / "samples" / "OA-15994_SeoulMetroFaciInfo_2026-11-13.json"
EXPECTED_PER_LINE = {"1": 10, "2": 50, "3": 34, "4": 26, "5": 56, "6": 39, "7": 42, "8": 19}
TABLES = ("lines", "stations", "station_lines", "station_external_ids", "station_edges", "elevators")


def run_load(engine, seed_dir=SEED, dry_run=False):
    rows, links, review = read_seed_dir(seed_dir)
    with Session(engine) as session:
        return load_seed(session, rows, links, review, dry_run=dry_run)


def snapshot(engine) -> dict:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2")).all() for t in TABLES
        }


def count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def db_url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def copy_seed(tmp_path: Path, name: str = "seed") -> Path:
    dest = tmp_path / name
    shutil.copytree(SEED, dest)
    return dest


def all_pending_seed(tmp_path: Path, name: str = "seed_pending") -> Path:
    """환승 후보를 전부 PENDING으로 되돌린 시드. 병합이 일어나지 않는 기준 상태를 만든다."""
    dest = copy_seed(tmp_path, name)
    path = dest / "station_review.csv"
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)
    for r in rows:
        if r["review_type"] == "TRANSFER_CANDIDATE":
            r["status"] = "PENDING"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return dest


def confirmed_review_rows() -> list[dict[str, str]]:
    with (SEED / "station_review.csv").open(encoding="utf-8", newline="") as f:
        return [
            r for r in csv.DictReader(f)
            if r["review_type"] == "TRANSFER_CANDIDATE" and r["status"] == "CONFIRMED"
        ]


def evidence_rows() -> list[dict[str, str]]:
    with (SEED / "transfer_evidence_oa13290.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def set_review(seed_dir: Path, codes: str, status: str, name: str | None = None) -> None:
    path = seed_dir / "station_review.csv"
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)
    hit = [r for r in rows if r["review_type"] == "TRANSFER_CANDIDATE" and r["codes"] == codes]
    assert len(hit) == 1, f"review 행을 찾지 못했습니다: {codes}"
    hit[0]["status"] = status
    if name is not None:
        hit[0]["proposed_station_name"] = name
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sample_codes() -> set[str]:
    rows = json.loads(SAMPLE_15994.read_text(encoding="utf-8"))["row"]
    return {r["STN_CD"] for r in rows}


# ---------- 시드 파일 ----------


def test_seed_files_shape_and_coverage_statement():
    rows, links, review = read_seed_dir(SEED)
    assert len(rows) == 276
    assert Counter(r.line_id for r in rows) == EXPECTED_PER_LINE
    assert all(re.fullmatch(r"\d{4}", r.code) for r in rows)
    assert len({r.code for r in rows}) == 276
    assert all(r.station_name == r.station_name.strip() and r.station_name for r in rows)

    manifest = json.loads((SEED / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rows"]["seed_lines_1_to_8"] == 276
    assert manifest["rows"]["per_line"] == EXPECTED_PER_LINE
    # 1호선은 일부 구간만 확보했고, 전체 확보라고 표시하지 않는다.
    assert manifest["coverage"]["line_1"] == "PARTIAL"
    assert "전체를 확보했다는 뜻이 아니다" in manifest["coverage"]["statement"]


def test_confirmed_groups_are_backed_by_official_evidence():
    """CONFIRMED는 공식 환승 기록(OA-13290)이 양방향으로 있는 그룹뿐이어야 한다."""
    _, _, review = read_seed_dir(SEED)
    confirmed = confirmed_review_rows()
    assert len(confirmed) == len(review.confirmed_groups) == 34

    evidence = evidence_rows()
    by_group: dict[str, list[dict[str, str]]] = {}
    for e in evidence:
        by_group.setdefault(e["group_codes"], []).append(e)
    assert set(by_group) == {r["codes"] for r in confirmed}  # 증거 없는 CONFIRMED도, 확정 안 된 증거도 없다

    line_of = {r.code: r.line_id for r in read_seed_dir(SEED)[0]}
    for r in confirmed:
        codes = r["codes"].split("|")
        assert len({line_of[c] for c in codes}) == len(codes)  # 같은 노선 코드가 없다
        directed = {(e["from_code"], e["to_code"]) for e in by_group[r["codes"]]}
        for a in codes:
            for b in codes:
                if a != b:
                    assert (a, b) in directed  # 모든 쌍이 양방향으로 공식 기록에 있다
        matches = {e["name_match"] for e in by_group[r["codes"]]}
        if r["codes"] == "0150|0426":
            assert matches == {"manual"}  # 수동 확정은 서울역 하나뿐이다
            assert "수동 확정" in r["note"] and "일반 규칙이 아님" in r["note"]
        else:
            assert matches <= {"exact", "base"}
        assert r["proposed_station_name"].strip()
        assert "OA-13290" in r["note"]

    assert {r["codes"] for r in confirmed if "수동 확정" in r["note"]} == {"0150|0426"}
    sizes = Counter(len(r["codes"].split("|")) for r in confirmed)
    assert sizes == {2: 32, 3: 2}
    names = {r["codes"]: r["proposed_station_name"] for r in confirmed}
    assert names["0150|0426"] == "서울역"
    assert names["0153|0319|2535"] == "종로3가"
    assert names["0428|2629"] == "삼각지"
    assert names["0432|2738"] == "총신대입구(이수)"


def test_no_pending_transfer_candidates_remain():
    _, _, review = read_seed_dir(SEED)
    assert review.transfer_pending == 0
    assert review.name_variant_pending == 5  # 역명 표기 차이 5건은 계속 PENDING


def test_line_specific_original_names_are_preserved_in_seed():
    rows = {r.code: r.station_name for r in read_seed_dir(SEED)[0]}
    # 합쳐진 역의 표시명과 별개로, 노선별 원본 역명은 시드 파일에 그대로 있다.
    assert (rows["0150"], rows["0426"]) == ("서울", "서울")
    assert (rows["0432"], rows["2738"]) == ("총신대입구(이수)", "이수")
    assert (rows["0153"], rows["0319"], rows["2535"]) == ("종로3가", "종로3가", "종로3가(탑골공원)")
    assert (rows["0428"], rows["2629"]) == ("삼각지(전쟁기념관)", "삼각지")


def test_manifest_records_transfer_source_and_gaps():
    manifest = json.loads((SEED / "manifest.json").read_text(encoding="utf-8"))
    src = manifest["transfer_source"]
    assert src["dataset_id"] == "OA-13290" and src["rows"] == 140
    assert manifest["review"]["transfer_confirmed_groups"] == 34
    assert manifest["review"]["transfer_confirmed_manual"] == 1
    gaps = manifest["coverage"]["official_transfer_rows_not_resolvable_to_seed"]
    # 1호선 쪽 역이 시드에 없어서 해석되지 않는 공식 기록이 있다 = 1호선이 일부 구간뿐이라는 단서
    gap_keys = {(g["line"], g["official_station_name"]) for g in gaps}
    assert gap_keys >= {("2", "신도림"), ("7", "온수")}
    assert ("1", "서울역") not in gap_keys  # 수동 확정으로 해석된 기록은 공백 목록에서 빠진다


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: t.replace("0150,1,서울", "150,1,서울", 1), "4자리"),
        (lambda t: t.replace("0150,1,서울", "0150,9,서울", 1), "1~8호선이 아닌"),
        (lambda t: t.replace("0150,1,서울", "0151,1,서울", 1), "중복"),
    ],
)
def test_seed_validation_rejects_bad_rows(tmp_path, mutate, message):
    seed_dir = copy_seed(tmp_path)
    path = seed_dir / "stations_oa12035.csv"
    text_ = path.read_text(encoding="utf-8")
    mutated = mutate(text_)
    assert mutated != text_
    path.write_text(mutated, encoding="utf-8")
    with pytest.raises(SeedError, match=message):
        read_seed_dir(seed_dir)


def test_confirmed_group_validation(tmp_path):
    # 같은 노선의 두 코드를 한 그룹으로 확정할 수 없다.
    seed_dir = copy_seed(tmp_path)
    path = seed_dir / "station_review.csv"
    with path.open("a", encoding="utf-8", newline="") as f:
        f.write("TRANSFER_CANDIDATE,0150|0151,시험,CONFIRMED,같은 노선\n")
    with pytest.raises(SeedError, match="같은 노선"):
        read_seed_dir(seed_dir)

    # 시드에 없는 코드는 거부한다.
    seed_dir2 = tmp_path / "seed2"
    shutil.copytree(SEED, seed_dir2)
    with (seed_dir2 / "station_review.csv").open("a", encoding="utf-8", newline="") as f:
        f.write("TRANSFER_CANDIDATE,0150|9999,시험,CONFIRMED,없는 코드\n")
    with pytest.raises(SeedError, match="시드에 없습니다"):
        read_seed_dir(seed_dir2)


# ---------- 적재: 기본 동작 ----------


def test_load_creates_276_line_specific_codes(engine):
    report = run_load(engine)

    assert count(engine, "lines") == 8
    assert count(engine, "stations") == 240  # 276 − 36: 확정 그룹 34개에서 줄어든 역 수
    assert count(engine, "station_lines") == 276
    with engine.connect() as conn:
        oa12035 = conn.execute(
            text(
                "SELECT external_station_code, station_id, station_line_id FROM station_external_ids "
                "WHERE source = 'OA-12035'"
            )
        ).all()
        operators = conn.execute(text("SELECT DISTINCT operator FROM station_lines")).all()
        line_operators = conn.execute(text("SELECT DISTINCT operator FROM lines")).all()
    assert len(oa12035) == 276
    assert all(r.station_line_id is not None for r in oa12035)
    assert len({r.station_line_id for r in oa12035}) == 276  # 코드마다 서로 다른 노선 행
    assert operators == [("서울교통공사",)]
    assert line_operators == [(None,)]  # 노선 단위 operator는 단정하지 않는다
    assert report.counts["stations_created"] == 240
    assert not report.dry_run


def test_second_load_changes_nothing_and_keeps_ids(engine):
    run_load(engine)
    before = snapshot(engine)

    report = run_load(engine)

    assert snapshot(engine) == before  # 행 수와 ID 모두 불변
    assert not [k for k in report.counts if k.endswith("_created")]
    assert report.counts["stations_unchanged"] == 240
    assert report.counts["station_lines_unchanged"] == 276


def test_dry_run_rolls_back_everything(engine):
    before = snapshot(engine)

    report = run_load(engine, dry_run=True)

    assert report.dry_run
    assert report.counts["stations_created"] == 240  # 적용해 본 결과는 보고한다
    assert snapshot(engine) == before  # 그러나 DB는 그대로


# ---------- 환승역 ----------


def test_pending_candidates_are_not_merged_even_with_same_name(engine, tmp_path):
    run_load(engine, all_pending_seed(tmp_path))

    assert count(engine, "stations") == 276
    with engine.connect() as conn:
        # 어떤 역도 노선 행을 둘 이상 가지지 않는다 = 자동 병합 없음
        multi = conn.execute(
            text("SELECT station_id FROM station_lines GROUP BY station_id HAVING COUNT(*) > 1")
        ).all()
        # 같은 역명을 가진 별도 역이 역명 동일 그룹 수(32)만큼 남아 있다
        same_name = conn.execute(
            text("SELECT station_name FROM stations GROUP BY station_name HAVING COUNT(*) > 1")
        ).all()
        city_hall = conn.execute(
            text("SELECT station_id FROM stations WHERE station_name = '시청' ORDER BY station_id")
        ).all()
    assert multi == []
    assert len(same_name) == 32
    assert len(city_hall) == 2


def test_default_seed_merges_only_confirmed_groups(engine):
    run_load(engine)

    with engine.connect() as conn:
        sizes = Counter(
            n for (n,) in conn.execute(
                text("SELECT COUNT(*) FROM station_lines GROUP BY station_id")
            )
        )
        still_separate = conn.execute(
            text("SELECT station_name, COUNT(*) FROM stations GROUP BY station_name HAVING COUNT(*) > 1")
        ).all()
        city_hall = conn.execute(
            text("SELECT COUNT(*) FROM stations WHERE station_name = '시청'")
        ).scalar()
        seoul = conn.execute(
            text(
                "SELECT DISTINCT s.station_id, s.station_name FROM station_external_ids e "
                "JOIN stations s ON s.station_id = e.station_id "
                "WHERE e.source = 'OA-12035' AND e.external_station_code IN ('0150', '0426')"
            )
        ).all()
        isu = conn.execute(
            text(
                "SELECT DISTINCT s.station_name FROM station_external_ids e "
                "JOIN stations s ON s.station_id = e.station_id "
                "WHERE e.source = 'OA-12035' AND e.external_station_code IN ('0432', '2738')"
            )
        ).all()
    assert sizes == {1: 206, 2: 32, 3: 2}  # 확정 그룹 34개만 합쳐졌다
    assert still_separate == []  # 같은 이름의 별도 역이 남지 않았다(후보 33개가 모두 공식 근거로 확정)
    assert city_hall == 1
    assert len(seoul) == 1 and seoul[0].station_name == "서울역"  # 수동 확정한 서울역
    assert isu == [("총신대입구(이수)",)]


def test_only_confirmed_group_is_merged(engine, tmp_path):
    seed_dir = all_pending_seed(tmp_path)
    set_review(seed_dir, "0151|0201", "CONFIRMED", "시청")

    report = run_load(engine, seed_dir)

    assert count(engine, "stations") == 275
    assert count(engine, "station_lines") == 276
    assert len(report.review.confirmed_groups) == 1
    with engine.connect() as conn:
        merged = conn.execute(
            text(
                "SELECT e.external_station_code, e.station_id, e.station_line_id, sl.line_id "
                "FROM station_external_ids e JOIN station_lines sl "
                "ON sl.station_line_id = e.station_line_id "
                "WHERE e.source = 'OA-12035' AND e.external_station_code IN ('0151', '0201') "
                "ORDER BY e.external_station_code"
            )
        ).all()
        multi_line_stations = conn.execute(
            text("SELECT station_id FROM station_lines GROUP BY station_id HAVING COUNT(*) > 1")
        ).all()
    assert len({r.station_id for r in merged}) == 1  # 한 역
    assert [r.line_id for r in merged] == ["1", "2"]  # 노선별 코드 관계 유지
    assert len({r.station_line_id for r in merged}) == 2
    assert len(multi_line_stations) == 1  # 확정하지 않은 다른 후보는 합쳐지지 않았다


def test_rejected_candidate_is_not_merged(engine, tmp_path):
    seed_dir = all_pending_seed(tmp_path)
    set_review(seed_dir, "0151|0201", "REJECTED")

    run_load(engine, seed_dir)

    assert count(engine, "stations") == 276


# ---------- 충돌과 롤백 ----------


def _insert_line(conn, line_id: str) -> None:
    conn.execute(
        text("INSERT INTO lines (line_id, line_name) VALUES (:i, :n)"),
        {"i": line_id, "n": f"{line_id}호선"},
    )


def test_conflict_with_existing_station_rolls_back_without_overwriting(engine):
    # 코드 0150이 이미 이름이 다른 역에 연결되어 있다.
    with engine.begin() as conn:
        _insert_line(conn, "1")
        conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (1, '다른이름')"))
        conn.execute(
            text("INSERT INTO station_lines (station_line_id, station_id, line_id) VALUES (1, 1, '1')")
        )
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                "VALUES ('OA-12035', '0150', 1, 1)"
            )
        )
    before = snapshot(engine)

    with pytest.raises(ConflictError, match="이름이 기존 값과 다릅니다"):
        run_load(engine)

    assert snapshot(engine) == before  # 시드가 일부 들어가지도, 이름이 바뀌지도 않았다


def test_conflict_when_station_has_extra_line_rolls_back(engine):
    with engine.begin() as conn:
        _insert_line(conn, "1")
        _insert_line(conn, "4")
        # 종각(0152)은 환승 그룹이 아니다. 여기에 4호선이 이미 붙어 있으면 충돌이다.
        conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (1, '종각')"))
        conn.execute(
            text(
                "INSERT INTO station_lines (station_line_id, station_id, line_id) "
                "VALUES (1, 1, '1'), (2, 1, '4')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                "VALUES ('OA-12035', '0152', 1, 1)"
            )
        )
    before = snapshot(engine)

    with pytest.raises(ConflictError, match="그룹에 없는 노선"):
        run_load(engine)

    assert snapshot(engine) == before


def test_conflict_when_linked_code_points_to_another_station(engine):
    run_load(engine)
    with engine.begin() as conn:
        # OA-15994 0150을 다른 역·노선 행으로 옮겨 놓는다(기존 연결을 어긋나게 한다).
        other = conn.execute(
            text(
                "SELECT e.station_id, e.station_line_id FROM station_external_ids e "
                "WHERE e.source = 'OA-12035' AND e.external_station_code = '0151'"
            )
        ).one()
        conn.execute(
            text(
                "UPDATE station_external_ids SET station_id = :s, station_line_id = :l "
                "WHERE source = 'OA-15994' AND external_station_code = '0150'"
            ),
            {"s": other.station_id, "l": other.station_line_id},
        )
    before = snapshot(engine)

    with pytest.raises(ConflictError, match="다른 역"):
        run_load(engine)

    assert snapshot(engine) == before


def test_confirming_after_separate_load_requires_manual_merge(engine, tmp_path):
    run_load(engine, all_pending_seed(tmp_path))  # 먼저 PENDING 상태로 별도 역으로 적재
    before = snapshot(engine)

    with pytest.raises(ConflictError, match="합칠 수 없습니다"):
        run_load(engine)  # 기본 시드는 33개 그룹이 CONFIRMED다

    assert snapshot(engine) == before  # 기존 역을 조용히 합치거나 지우지 않는다


def test_legacy_external_id_without_station_line_is_backfilled(engine):
    # 마이그레이션 전에 만들어진 행(station_line_id NULL, operator NULL)은 값을 채우기만 한다.
    with engine.begin() as conn:
        _insert_line(conn, "1")
        conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (1, '서울역')"))
        conn.execute(
            text("INSERT INTO station_lines (station_line_id, station_id, line_id) VALUES (1, 1, '1')")
        )
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id) "
                "VALUES ('OA-12035', '0150', 1)"
            )
        )

    report = run_load(engine)

    assert count(engine, "stations") == 240  # 기존 역 1개를 재사용
    assert report.counts["external_ids_OA-12035_backfilled"] == 1
    assert report.counts["station_lines_backfilled"] == 1
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT station_id, station_line_id FROM station_external_ids "
                "WHERE source = 'OA-12035' AND external_station_code = '0150'"
            )
        ).one()
    assert row == (1, 1)


def test_existing_unrelated_rows_are_preserved(engine):
    with engine.begin() as conn:
        _insert_line(conn, "9")
        conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (500, '시험역')"))
        conn.execute(
            text("INSERT INTO station_lines (station_line_id, station_id, line_id) VALUES (900, 500, '9')")
        )
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                "VALUES ('OA-X', '7777', 500, 900)"
            )
        )

    run_load(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM stations WHERE station_id = 500")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM lines WHERE line_id = '9'")).scalar() == 1
        assert conn.execute(
            text("SELECT COUNT(*) FROM station_external_ids WHERE source = 'OA-X'")
        ).scalar() == 1
    assert count(engine, "stations") == 241


# ---------- OA-15994 STN_CD 대조 ----------


def test_oa15994_links_match_verified_codes_only(engine):
    rows, links, _ = read_seed_dir(SEED)
    seed_codes = {r.code for r in rows}
    stn_cd = sample_codes()

    assert len(stn_cd) == 274
    assert stn_cd <= seed_codes  # 샘플의 모든 STN_CD가 OA-12035 역번호(4자리)에 있다
    assert {l.code for l in links} == stn_cd  # 연결 목록은 대조로 확인된 코드와 정확히 같다
    assert seed_codes - stn_cd == {"0321", "2538"}  # 충무로(3), 청구(5)는 연결하지 않는다

    run_load(engine)
    with engine.connect() as conn:
        linked = conn.execute(
            text(
                "SELECT a.external_station_code, a.station_id, a.station_line_id, "
                "b.station_id AS base_station, b.station_line_id AS base_line "
                "FROM station_external_ids a JOIN station_external_ids b "
                "ON b.source = 'OA-12035' AND b.external_station_code = a.external_station_code "
                "WHERE a.source = 'OA-15994'"
            )
        ).all()
        unlinked = conn.execute(
            text(
                "SELECT COUNT(*) FROM station_external_ids WHERE source = 'OA-15994' "
                "AND external_station_code IN ('0321', '2538')"
            )
        ).scalar()
    assert len(linked) == 274
    # OA-15994 코드가 같은 코드의 OA-12035 행과 같은 노선 행에 연결되어 있다
    assert all(r.station_id == r.base_station and r.station_line_id == r.base_line for r in linked)
    assert unlinked == 0


def test_name_differences_are_all_listed_in_review():
    rows, _, _ = read_seed_dir(SEED)
    by_code = {r.code: r.station_name for r in rows}
    sample = json.loads(SAMPLE_15994.read_text(encoding="utf-8"))["row"]
    names: dict[str, str] = {}
    for r in sample:
        names.setdefault(r["STN_CD"], r["STN_NM"])
    mismatched = {
        code
        for code, nm in names.items()
        if re.sub(r"\(\d+\)$", "", nm).strip() != by_code[code]
    }
    with (SEED / "station_review.csv").open(encoding="utf-8", newline="") as f:
        variants = {
            r["codes"]: r["status"]
            for r in csv.DictReader(f)
            if r["review_type"] == "NAME_VARIANT"
        }
    assert mismatched <= set(variants)  # 역명이 다른 코드는 전부 review에 있다
    pending = {c for c, s in variants.items() if s == "PENDING"}
    assert pending <= mismatched  # PENDING은 실제로 표기가 다른 항목뿐이다


# ---------- CLI ----------


def test_cli_dry_run_then_load_then_reload(engine, capsys):
    url = db_url(engine)

    assert main(["--database-url", url, "--dry-run"]) == EXIT_OK
    assert count(engine, "stations") == 0
    assert "[dry-run]" in capsys.readouterr().out

    assert main(["--database-url", url]) == EXIT_OK
    out = capsys.readouterr().out
    assert "적재 완료" in out and "stations_created: 240" in out
    assert "1호선은 일부 구간" in out
    before = snapshot(engine)

    assert main(["--database-url", url]) == EXIT_OK
    out = capsys.readouterr().out
    assert "_created" not in out
    assert snapshot(engine) == before


def test_cli_refuses_unmigrated_database(alembic_cfg_for, migrated_url):
    command.upgrade(alembic_cfg_for(migrated_url), "8d70068f68f4")  # station_line_id 이전 상태

    assert main(["--database-url", migrated_url]) == EXIT_NOT_READY


def test_cli_refuses_missing_sqlite_file(tmp_path):
    url = f"sqlite:///{tmp_path / 'missing.db'}"

    assert main(["--database-url", url]) == EXIT_NOT_READY
    assert not (tmp_path / "missing.db").exists()  # 빈 DB 파일을 만들지 않는다


def test_cli_reports_conflict_and_rolls_back(engine, capsys):
    with engine.begin() as conn:
        _insert_line(conn, "1")
        conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (1, '다른이름')"))
        conn.execute(
            text("INSERT INTO station_lines (station_line_id, station_id, line_id) VALUES (1, 1, '1')")
        )
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                "VALUES ('OA-12035', '0150', 1, 1)"
            )
        )
    before = snapshot(engine)

    assert main(["--database-url", db_url(engine)]) == EXIT_SEED_ERROR

    assert "롤백" in capsys.readouterr().err
    assert snapshot(engine) == before


def test_cli_rejects_invalid_seed(engine, tmp_path, capsys):
    seed_dir = copy_seed(tmp_path)
    path = seed_dir / "stations_oa12035.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("0150,1,서울", "150,1,서울", 1), encoding="utf-8")

    assert main(["--database-url", db_url(engine), "--seed-dir", str(seed_dir)]) == EXIT_SEED_ERROR
    assert count(engine, "stations") == 0
