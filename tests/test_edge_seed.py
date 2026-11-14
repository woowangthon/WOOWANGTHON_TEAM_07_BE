"""검증된 역 간선 생성기·로더·연결성·대체역 통합 검증.

- 생성기는 합성 목업과 합성 시드로 검증한다(프론트 원본 파일은 저장소에 없고 읽지 않는다).
- 로더와 통합 테스트는 저장소의 실제 시드 파일을 임시 SQLite에만 적재한다. data/dev.db와 로그는 열지 않는다.
"""

import csv
import importlib.util
import json
import shutil
from itertools import combinations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import load_edges as cli
from app.database import get_session
from app.main import create_app
from app.models import Elevator, ElevatorStatus, Station, StationEdge, StationLine
from app.services.edge_connectivity import connectivity_report
from app.services.edge_seed import EdgeRow, load_edges, read_provisional_edges, read_verified_edges
from app.services.station_seed import (
    DEFAULT_SEED_DIR,
    ConflictError,
    SeedError,
    load_seed,
    read_seed_dir,
)
from app.timeutil import to_utc_iso, utc_now

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_edge_seed", ROOT / "scripts" / "build_edge_seed.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

SENTINEL = "SENTINEL_MOCK_ELEVATOR_DATA"


# ---------- 합성 목업·시드 ----------


def mock_text(line_segments: dict[str, list[list[str]]], stations: dict, loops=()) -> str:
    """scripts/build_edge_seed.py가 읽는 형식의 최소 목업. 지정하지 않은 노선은 segments가 비어 있다."""
    out = ["(function () {", "  var lines = {"]
    for n in "12345678":
        segs = line_segments.get(n, [])
        body = ",\n".join("        [" + ", ".join(f'"{i}"' for i in seg) + "]" for seg in segs)
        out.append(
            f'    "{n}": {{\n      id: "{n}", name: "{n}호선", short: "{n}", color: "#000",\n'
            f'      loop: {"true" if n in loops else "false"},\n      segments: [\n{body}\n      ],\n'
            f"      paths: []\n    }},"
        )
    out.append("  };")
    out.append("  var stations = {")
    for fe_id, (name, line_list) in stations.items():
        ls = ", ".join(f'"{x}"' for x in line_list)
        out.append(f'    "{fe_id}": {{ id: "{fe_id}", name: "{name}", x: 1.0, y: 2.0, lines: [{ls}] }},')
    out.append("  };")
    out.append(f'  var elevators = {{ "x": "{SENTINEL}" }};')
    out.append("})();")
    return "\n".join(out)


def write_seed(tmp_path: Path, rows, groups=(), evidence=()) -> Path:
    d = tmp_path / "seed"
    d.mkdir()
    with (d / "stations_oa12035.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["external_station_code", "line_id", "station_name", "source_serial"])
        for i, (code, line, name) in enumerate(rows, 1):
            w.writerow([code, line, name, i])
    with (d / "station_review.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["review_type", "codes", "proposed_station_name", "status", "note"])
        for codes, name in groups:
            w.writerow(["TRANSFER_CANDIDATE", "|".join(codes), name, "CONFIRMED", "test"])
    with (d / "transfer_evidence_oa13290.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["group_codes", "from_code", "from_line", "official_station_name", "name_match",
                    "to_code", "to_line", "distance_m", "transfer_time"])
        for a, b in evidence:
            w.writerow(["x", a, "1", "n", "exact", b, "2", "1", "00:01"])
    return d


def build3(text, seed):
    """VERIFIED(TRANSFER)와 PROVISIONAL(ADJACENT)을 합쳐 본다. 등급 분리는 별도 테스트가 확인한다."""
    verified_rows, provisional_rows, review, stats = gen.build(text, seed)
    return verified_rows + provisional_rows, review, stats


def verified(rows):
    return {(r[0], r[1], r[3]) for r in rows}  # (type, from, to)


SEED = [("0101", "1", "가"), ("0102", "1", "나"), ("0103", "1", "다"), ("0105", "1", "마"),
        ("0201", "2", "가2"), ("0202", "2", "나2")]


def test_consecutive_codes_and_front_order_are_provisional_not_verified(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "b", "c"]]}, {"a": ("가", ["1"]), "b": ("나", ["1"]), "c": ("다", ["1"])})

    rows, review, stats = build3(text, seed)

    assert verified(rows) == {("ADJACENT", "0101", "0102"), ("ADJACENT", "0102", "0103")}
    assert all(r[5] == "OA-12035+FE-MOCK" for r in rows)
    assert stats["mapping_failed"] == 0
    # 등급: 공식 인접 증명이 없으므로 ADJACENT는 VERIFIED가 아니라 PROVISIONAL이다.
    v, p, _, st = gen.build(text, seed)
    assert v == [] and len(p) == 2
    assert st["adjacent_verified"] == 0 and st["adjacent_provisional"] == 2


def test_code_gap_stays_pending(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["c", "d"]]}, {"c": ("다", ["1"]), "d": ("마", ["1"])})  # 0103 -> 0105

    rows, review, _ = build3(text, seed)

    assert rows == []
    assert any(r[0] == "ADJACENT_PENDING" and "연속하지 않음" in r[4] for r in review)


def test_loop_closure_is_pending_not_verified(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "b", "c"]]}, {"a": ("가", ["1"]), "b": ("나", ["1"]), "c": ("다", ["1"])}, loops="1")

    rows, review, _ = build3(text, seed)

    assert ("ADJACENT", "0101", "0103") not in verified(rows)
    assert any("순환 연결" in r[4] and r[3] == "PENDING" for r in review)


def test_branch_segments_are_distinguished_from_main(tmp_path):
    seed = write_seed(tmp_path, SEED + [("0106", "1", "바")])
    text = mock_text(
        {"1": [["a", "b"], ["a", "x"]]},
        {"a": ("가", ["1"]), "b": ("나", ["1"]), "x": ("마", ["1"])},
    )

    rows, review, _ = build3(text, seed)

    evidence = {(r[1], r[3]): r[6] for r in rows}
    assert evidence[("0101", "0102")].startswith("FE_SEGMENT_MAIN")
    assert any("BRANCH1" in r[4] for r in review)  # 지선 쌍은 코드가 연속하지 않아 PENDING


def test_unmapped_front_station_is_reported_and_never_created(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "z"]]}, {"a": ("가", ["1"]), "z": ("없는역", ["1"])})

    rows, review, stats = build3(text, seed)

    assert rows == [] and stats["mapping_failed"] == 1
    assert ["MAPPING_FAILED", "1", "z(없는역)", "PENDING", "NOT_IN_BACKEND_SEED"] in review


def test_same_name_on_another_line_is_not_merged(tmp_path):
    seed = write_seed(tmp_path, [("0101", "1", "가"), ("0201", "2", "다")])
    text = mock_text({"1": [["a", "d"]]}, {"a": ("가", ["1"]), "d": ("다", ["1"])})  # 다는 2호선에만 있다

    rows, review, stats = build3(text, seed)

    assert rows == [] and stats["mapping_failed"] == 1


def test_parenthesis_and_station_suffix_variants_map_within_line_and_are_listed(tmp_path):
    seed = write_seed(tmp_path, [("0101", "1", "가나(부연)"), ("0102", "1", "다라")])
    text = mock_text({"1": [["a", "b"]]}, {"a": ("가나역", ["1"]), "b": ("다라", ["1"])})

    rows, review, stats = build3(text, seed)

    assert verified(rows) == {("ADJACENT", "0101", "0102")}
    assert any(r[0] == "NAME_VARIANT" and "normalized" in r[4] for r in review)


def test_two_front_stations_on_one_backend_station_both_fail(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "a2", "b"]]}, {"a": ("가", ["1"]), "a2": ("가", ["1"]), "b": ("나", ["1"])})

    rows, review, stats = build3(text, seed)

    assert rows == []
    assert sum(1 for r in review if r[0] == "MAPPING_FAILED" and r[4] == "DUPLICATE_MAPPING") == 2


def test_front_line_inconsistency_fails_mapping(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "b"]]}, {"a": ("가", ["2"]), "b": ("나", ["1"])})  # a의 노선 목록에 1호선이 없다

    rows, review, stats = build3(text, seed)

    assert rows == []
    assert any(r[4] == "FE_LINE_INCONSISTENT" for r in review)


def test_confirmed_group_makes_every_pair_transfer_and_nothing_else(tmp_path):
    seed_rows = [("0101", "1", "가"), ("0201", "2", "가"), ("0301", "3", "가"), ("0102", "1", "나"), ("0202", "2", "나")]
    evidence = [(a, b) for a, b in combinations(["0101", "0201", "0301"], 2)]
    evidence += [(b, a) for a, b in evidence]
    seed = write_seed(tmp_path, seed_rows, groups=[(("0101", "0201", "0301"), "가")], evidence=evidence)
    # 프론트는 나(0102/0202)도 환승이라 적었지만 확정 그룹이 아니다.
    text = mock_text({}, {"n": ("나", ["1", "2"])})

    rows, review, stats = build3(text, seed)

    transfers = {(r[1], r[3]) for r in rows if r[0] == "TRANSFER"}
    assert transfers == {("0101", "0201"), ("0101", "0301"), ("0201", "0301")}
    assert all(r[5] == "OA-13290" for r in rows if r[0] == "TRANSFER")
    assert not any(r[0] == "ADJACENT" for r in rows)


def test_missing_reverse_official_record_aborts(tmp_path):
    seed_rows = [("0101", "1", "가"), ("0201", "2", "가")]
    seed = write_seed(tmp_path, seed_rows, groups=[(("0101", "0201"), "가")], evidence=[("0101", "0201")])

    with pytest.raises(gen.BuildError):
        gen.build(mock_text({}, {}), seed)


def test_front_transfer_claim_without_confirmed_group_is_listed_not_created(tmp_path):
    seed = write_seed(tmp_path, [("0101", "1", "가"), ("0201", "2", "가")])
    text = mock_text({"1": [["a", "a"]], "2": [["a", "a"]]}, {"a": ("가", ["1", "2"])})

    rows, review, _ = build3(text, seed)

    assert not any(r[0] == "TRANSFER" for r in rows)
    assert any(r[0] == "TRANSFER_UNCONFIRMED" for r in review)


def test_mock_elevator_data_never_reaches_outputs(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "b"]]}, {"a": ("가", ["1"]), "b": ("나", ["1"])})

    rows, review, stats = build3(text, seed)

    assert SENTINEL not in json.dumps([rows, review, stats], ensure_ascii=False)


def test_generation_is_deterministic(tmp_path):
    seed = write_seed(tmp_path, SEED)
    text = mock_text({"1": [["a", "b", "c"]]}, {"a": ("가", ["1"]), "b": ("나", ["1"]), "c": ("다", ["1"])})

    assert gen.build(text, seed) == gen.build(text, seed)


# ---------- 실제 시드 파일 (등급) ----------


def _confirmed_group_pair_count() -> int:
    _, _, review = read_seed_dir(DEFAULT_SEED_DIR)
    return sum(len(list(combinations(g.codes, 2))) for g in review.confirmed_groups)


def test_verified_file_contains_only_official_transfer_edges():
    edges = read_verified_edges(DEFAULT_SEED_DIR)

    assert len(edges) == _confirmed_group_pair_count() == 38
    assert all(e.edge_type == "TRANSFER" and e.source == "OA-13290" for e in edges)
    assert len({frozenset((e.from_code, e.to_code)) for e in edges}) == len(edges)


def test_every_confirmed_group_pair_has_a_transfer_edge_and_both_official_records():
    _, _, review = read_seed_dir(DEFAULT_SEED_DIR)
    edges = read_verified_edges(DEFAULT_SEED_DIR)
    have = {frozenset((e.from_code, e.to_code)) for e in edges}
    evidence = {(r["from_code"], r["to_code"]) for r in csv.DictReader((DEFAULT_SEED_DIR / "transfer_evidence_oa13290.csv").open(encoding="utf-8"))}

    for group in review.confirmed_groups:
        for a, b in combinations(sorted(group.codes), 2):
            assert frozenset((a, b)) in have
            assert (a, b) in evidence and (b, a) in evidence


def test_provisional_file_is_adjacent_only_and_separate():
    provisional = read_provisional_edges(DEFAULT_SEED_DIR)

    assert len(provisional) == 239
    assert all(e.edge_type == "ADJACENT" and e.source == "OA-12035+FE-MOCK" for e in provisional)
    assert all(int(e.to_code) - int(e.from_code) == 1 and e.from_line == e.to_line for e in provisional)
    verified_pairs = {frozenset((e.from_code, e.to_code)) for e in read_verified_edges(DEFAULT_SEED_DIR)}
    assert not verified_pairs & {frozenset((e.from_code, e.to_code)) for e in provisional}


def test_manifest_grades_and_counts_match_files():
    manifest = json.loads((DEFAULT_SEED_DIR / "station_edges_manifest.json").read_text(encoding="utf-8"))
    review = list(csv.DictReader((DEFAULT_SEED_DIR / "station_edges_review.csv").open(encoding="utf-8")))

    stats = manifest["stats"]
    assert stats["adjacent_verified"] == 0  # 공식 인접 증명 출처가 없다
    assert stats["adjacent_provisional"] == len(read_provisional_edges(DEFAULT_SEED_DIR))
    assert stats["transfer_verified"] == len(read_verified_edges(DEFAULT_SEED_DIR))
    assert stats["mapping_failed"] == sum(1 for r in review if r["review_type"] == "MAPPING_FAILED")
    assert stats["adjacent_pending"] == sum(1 for r in review if r["review_type"] == "ADJACENT_PENDING")
    assert set(manifest["grades"]) == {"VERIFIED", "PROVISIONAL", "PENDING"}
    assert "elevators" in manifest["frontend_mock"]["not_used"]
    assert manifest["adjacent_source_provisional"] == "OA-12035+FE-MOCK"


def test_review_file_has_no_verified_rows_and_is_not_loader_input():
    review = list(csv.DictReader((DEFAULT_SEED_DIR / "station_edges_review.csv").open(encoding="utf-8")))

    assert review and all(r["status"] in {"PENDING", "ACCEPTED_BY_LINE_AND_CODE"} for r in review)


# ---------- 로더 (임시 DB) ----------


@pytest.fixture
def seeded(engine):
    with Session(engine) as session:
        rows, links, review = read_seed_dir(DEFAULT_SEED_DIR)
        load_seed(session, rows, links, review)
        yield session


def _edge_count(session) -> int:
    return session.scalar(select(func.count()).select_from(StationEdge))


def _load_all(session):
    """통합 테스트용: 공식 TRANSFER와 PROVISIONAL ADJACENT를 모두 임시 DB에 넣는다(서비스 계층에서만 가능)."""
    load_edges(session, read_verified_edges(DEFAULT_SEED_DIR))
    load_edges(session, read_provisional_edges(DEFAULT_SEED_DIR), allow_provisional=True)


def test_default_load_creates_only_official_transfer_edges(seeded):
    stations_before = seeded.scalar(select(func.count()).select_from(Station))
    lines_before = seeded.scalar(select(func.count()).select_from(StationLine))

    report = load_edges(seeded, read_verified_edges(DEFAULT_SEED_DIR))

    assert report.counts["transfer_created"] == 38 and report.counts["adjacent_created"] == 0
    assert _edge_count(seeded) == 38
    assert seeded.scalar(select(func.count()).select_from(Station)) == stations_before == 240
    assert seeded.scalar(select(func.count()).select_from(StationLine)) == lines_before == 276


def test_provisional_adjacent_is_rejected_by_default_before_touching_the_db(seeded):
    provisional = read_provisional_edges(DEFAULT_SEED_DIR)

    with pytest.raises(SeedError, match="공식 근거가 없는"):
        load_edges(seeded, provisional)
    with pytest.raises(SeedError, match="공식 근거가 없는"):
        load_edges(seeded, read_verified_edges(DEFAULT_SEED_DIR) + provisional[:1])  # 섞어도 전체 거부

    assert _edge_count(seeded) == 0


def test_hand_made_adjacent_with_any_source_is_rejected_by_default(seeded):
    for source in ("OA-12035+FE-MOCK", "OA-13290", "OA-FAKE-OFFICIAL"):
        with pytest.raises(SeedError):
            load_edges(seeded, [EdgeRow("ADJACENT", "0150", "1", "0151", "1", source)])
    assert _edge_count(seeded) == 0


def test_verified_reader_rejects_adjacent_rows(seed_copy_factory):
    seed = seed_copy_factory()
    _append(seed, "ADJACENT", "0150", "1", "0151", "1", "OA-12035+FE-MOCK", "x")

    with pytest.raises(SeedError, match="공식 근거가 없는 ADJACENT"):
        read_verified_edges(seed)


def test_edge_endpoints_follow_type_rules(seeded):
    _load_all(seeded)

    rows = seeded.execute(
        select(StationEdge.edge_type, StationEdge.source, StationEdge.from_station_line_id, StationEdge.to_station_line_id)
    ).all()
    sl = {r.station_line_id: (r.station_id, r.line_id) for r in seeded.scalars(select(StationLine))}
    assert {r.edge_type for r in rows} == {"ADJACENT", "TRANSFER"}
    for edge_type, source, a, b in rows:
        if edge_type == "TRANSFER":
            assert sl[a][0] == sl[b][0] and sl[a][1] != sl[b][1] and source == "OA-13290"
        else:
            assert sl[a][0] != sl[b][0] and sl[a][1] == sl[b][1] and source == "OA-12035+FE-MOCK"


def test_rerun_is_idempotent(seeded):
    edges = read_verified_edges(DEFAULT_SEED_DIR)
    load_edges(seeded, edges)

    report = load_edges(seeded, edges)

    assert _edge_count(seeded) == 38
    assert report.counts["transfer_created"] == 0 and report.counts["transfer_unchanged"] == 38


def test_provisional_rerun_is_idempotent_when_explicitly_allowed(seeded):
    _load_all(seeded)
    first = _edge_count(seeded)

    _load_all(seeded)

    assert _edge_count(seeded) == first == 38 + 239


def test_dry_run_changes_nothing(seeded):
    report = load_edges(seeded, read_verified_edges(DEFAULT_SEED_DIR), dry_run=True)

    assert report.dry_run and report.counts["transfer_created"] == 38
    assert _edge_count(seeded) == 0


def test_reverse_direction_row_is_the_same_edge(seeded):
    first = read_verified_edges(DEFAULT_SEED_DIR)[0]
    load_edges(seeded, [first])
    row = seeded.scalars(select(StationEdge)).one()
    row.from_station_line_id, row.to_station_line_id = row.to_station_line_id, row.from_station_line_id
    seeded.commit()  # 반대 방향으로 저장된 기존 행

    report = load_edges(seeded, [first])

    assert _edge_count(seeded) == 1 and report.counts["transfer_unchanged"] == 1


def test_conflicting_existing_edge_rolls_everything_back_without_overwrite(seeded):
    edges = read_verified_edges(DEFAULT_SEED_DIR)
    load_edges(seeded, edges[:1])
    row = seeded.scalars(select(StationEdge)).one()
    row.source = "OTHER"  # 같은 쌍, 다른 출처
    seeded.commit()

    with pytest.raises(ConflictError):
        load_edges(seeded, edges[:5])

    assert _edge_count(seeded) == 1
    assert seeded.scalars(select(StationEdge)).one().source == "OTHER"


def test_unmappable_code_rolls_back_the_whole_batch(seeded):
    good = read_verified_edges(DEFAULT_SEED_DIR)[0]
    bad = EdgeRow("TRANSFER", "9998", "1", "9999", "2", "OA-13290")

    with pytest.raises(SeedError):
        load_edges(seeded, [good, bad])

    assert _edge_count(seeded) == 0


def test_without_station_seed_nothing_is_loaded(engine):
    with Session(engine) as session:
        with pytest.raises(SeedError):
            load_edges(session, read_verified_edges(DEFAULT_SEED_DIR)[:3])
        assert _edge_count(session) == 0


def test_transfer_between_unmerged_stations_is_rejected(engine):
    """환승 그룹이 병합되지 않은 DB(역 시드를 병합 없이 적재)에서는 TRANSFER가 서로 다른 역을 잇는다."""
    with Session(engine) as session:
        rows, links, review = read_seed_dir(DEFAULT_SEED_DIR)
        review.confirmed_groups.clear()  # 병합하지 않고 적재
        load_seed(session, rows, links, review)

        with pytest.raises(SeedError):
            load_edges(session, read_verified_edges(DEFAULT_SEED_DIR)[:1])

        assert _edge_count(session) == 0


def test_confirmed_groups_map_to_one_station_each_in_the_db(seeded):
    """확정 그룹의 모든 코드가 DB에서 같은 station_id의 서로 다른 station_line_id로 이어진다."""
    from app.models import StationExternalId

    _, _, review = read_seed_dir(DEFAULT_SEED_DIR)
    for group in review.confirmed_groups:
        rows = seeded.execute(
            select(StationExternalId.station_id, StationExternalId.station_line_id)
            .where(StationExternalId.source == "OA-12035", StationExternalId.external_station_code.in_(group.codes))
        ).all()
        assert len(rows) == len(group.codes)
        assert len({r.station_id for r in rows}) == 1
        assert len({r.station_line_id for r in rows}) == len(group.codes)


# ---------- 입력 검증 (변조된 파일) ----------


@pytest.fixture
def seed_copy_factory(tmp_path):
    def make():
        dst = tmp_path / "seed_copy"
        if not dst.exists():
            shutil.copytree(DEFAULT_SEED_DIR, dst)
        return dst
    return make


@pytest.fixture
def seed_copy(seed_copy_factory):
    return seed_copy_factory()


def _append(seed_copy, *fields, filename="station_edges_verified.csv"):
    with (seed_copy / filename).open("a", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerow(fields)


def test_reader_accepts_a_copy_without_the_review_file(seed_copy):
    (seed_copy / "station_edges_review.csv").unlink()

    assert read_verified_edges(seed_copy)


@pytest.mark.parametrize(
    "bad_row",
    [
        ("TRANSFER", "0202", "2", "0201", "2", "OA-13290", "x"),           # 방향 규칙 위반
        ("TRANSFER", "0201", "2", "0202", "2", "OA-13290", "x"),           # 같은 노선
        ("TRANSFER", "0201", "2", "0202", "3", "OA-13290", "x"),           # 노선 표기가 시드와 다름
        ("TRANSFER", "0151", "1", "0301", "3", "OA-13290", "x"),           # 확정 그룹이 아닌 쌍
        ("TRANSFER", "0201", "2", "0202", "2", "FE-MOCK", "x"),            # 출처 불일치
        ("TRANSFER", "9990", "1", "9991", "2", "OA-13290", "x"),           # 시드에 없는 코드
        ("BUS", "0201", "2", "0202", "2", "OA-13290", "x"),                # 허용되지 않은 유형
    ],
)
def test_verified_reader_rejects_bad_rows(seed_copy, bad_row):
    _append(seed_copy, *bad_row)

    with pytest.raises(SeedError):
        read_verified_edges(seed_copy)


def test_verified_reader_rejects_duplicate_pair(seed_copy):
    first = next(csv.DictReader((seed_copy / "station_edges_verified.csv").open(encoding="utf-8")))
    _append(seed_copy, first["edge_type"], first["from_code"], first["from_line"], first["to_code"],
            first["to_line"], first["source"], "x")

    with pytest.raises(SeedError, match="중복"):
        read_verified_edges(seed_copy)


def test_verified_reader_rejects_missing_group_pair(seed_copy):
    path = seed_copy / "station_edges_verified.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # 마지막 환승 쌍 삭제

    with pytest.raises(SeedError, match="빠졌습니다"):
        read_verified_edges(seed_copy)


def test_verified_reader_rejects_transfer_without_official_record_in_both_directions(seed_copy):
    path = seed_copy / "transfer_evidence_oa13290.csv"
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")  # 한 방향 기록 삭제

    with pytest.raises(SeedError, match="양방향"):
        read_verified_edges(seed_copy)


@pytest.mark.parametrize(
    "bad_row",
    [
        ("ADJACENT", "0201", "2", "0203", "2", "OA-12035+FE-MOCK", "x"),  # 역번호가 연속하지 않음
        ("ADJACENT", "0201", "2", "0202", "2", "OA-13290", "x"),           # 출처 불일치(공식 출처 도용)
        ("ADJACENT", "0202", "2", "0201", "2", "OA-12035+FE-MOCK", "x"),   # 방향 규칙 위반
        ("ADJACENT", "0201", "2", "0426", "4", "OA-12035+FE-MOCK", "x"),   # 노선이 다름
        ("ADJACENT", "9990", "1", "9991", "1", "OA-12035+FE-MOCK", "x"),   # 시드에 없는 코드
        ("TRANSFER", "0201", "2", "0202", "3", "OA-13290", "x"),           # PROVISIONAL 파일에 TRANSFER
    ],
)
def test_provisional_reader_rejects_bad_rows(seed_copy, bad_row):
    _append(seed_copy, *bad_row, filename="station_edges_provisional.csv")

    with pytest.raises(SeedError):
        read_provisional_edges(seed_copy)


def test_reader_rejects_wrong_header(seed_copy):
    path = seed_copy / "station_edges_verified.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("edge_type,", "type,", 1), encoding="utf-8")

    with pytest.raises(SeedError):
        read_verified_edges(seed_copy)


# ---------- CLI ----------


def _url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _seed_db(engine):
    with Session(engine) as session:
        rows, links, review = read_seed_dir(DEFAULT_SEED_DIR)
        load_seed(session, rows, links, review)


def test_cli_defaults_to_dry_run_and_apply_loads_only_official_edges(engine, capsys):
    _seed_db(engine)

    assert cli.main(["--database-url", _url(engine)]) == 0
    assert "[dry-run]" in capsys.readouterr().out
    with Session(engine) as session:
        assert _edge_count(session) == 0

    assert cli.main(["--database-url", _url(engine), "--apply"]) == 0
    out = capsys.readouterr().out
    with Session(engine) as session:
        assert _edge_count(session) == 38
        assert session.scalar(select(func.count()).select_from(StationEdge).where(StationEdge.edge_type == "ADJACENT")) == 0
    assert "PROVISIONAL" in out and "transfer_created: 38" in out


def test_cli_apply_cannot_load_provisional_adjacent_even_if_placed_in_the_verified_file(engine, seed_copy, capsys):
    _seed_db(engine)
    _append(seed_copy, "ADJACENT", "0150", "1", "0151", "1", "OA-12035+FE-MOCK", "x")

    assert cli.main(["--database-url", _url(engine), "--seed-dir", str(seed_copy), "--apply"]) == 1
    with Session(engine) as session:
        assert _edge_count(session) == 0


def test_cli_has_no_option_to_include_provisional(engine):
    with pytest.raises(SystemExit):
        cli.main(["--database-url", _url(engine), "--apply", "--include-provisional"])


def test_cli_exits_2_when_database_file_is_missing(tmp_path, capsys):
    assert cli.main(["--database-url", f"sqlite:///{tmp_path / 'none.db'}"]) == 2


def test_cli_rolls_back_on_invalid_seed(engine, seed_copy, capsys):
    _seed_db(engine)
    _append(seed_copy, "TRANSFER", "0201", "2", "0202", "2", "OA-13290", "x")

    assert cli.main(["--database-url", _url(engine), "--seed-dir", str(seed_copy), "--apply"]) == 1
    with Session(engine) as session:
        assert _edge_count(session) == 0


def test_real_database_and_logs_are_not_touched_by_loading():
    watched = [ROOT / "data" / "dev.db", ROOT / "data" / "logs" / "ingestion_issues.jsonl"]
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in watched if p.exists()}

    read_verified_edges(DEFAULT_SEED_DIR)
    read_provisional_edges(DEFAULT_SEED_DIR)

    assert before == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in watched if p.exists()}


# ---------- 연결성 (#31) ----------


def test_connectivity_reports_breaks_and_isolated_stations(db):
    """conftest 픽스처 역: 2호선은 시청·강남·교대·서울대입구·신도림·정렬시험."""
    def sl(station, line):
        return db.scalar(select(StationLine.station_line_id).where(
            StationLine.station_id == station, StationLine.line_id == line))

    db.add(StationEdge(from_station_line_id=sl(3, "2"), to_station_line_id=sl(5, "2"), edge_type="ADJACENT", source="t"))
    db.add(StationEdge(from_station_line_id=sl(5, "2"), to_station_line_id=sl(6, "2"), edge_type="ADJACENT", source="t"))
    db.add(StationEdge(from_station_line_id=sl(5, "2"), to_station_line_id=sl(5, "3"), edge_type="TRANSFER", source="t"))
    db.commit()

    report = connectivity_report(db)

    two = report["2"]
    assert two.station_count == 6 and two.component_sizes == (3, 1, 1, 1)
    assert not two.connected and len(two.isolated_station_line_ids) == 3
    assert sum(report["3"].component_sizes) == report["3"].station_count


def test_connectivity_with_official_edges_only_shows_no_adjacent_connections(seeded):
    load_edges(seeded, read_verified_edges(DEFAULT_SEED_DIR))

    report = connectivity_report(seeded)

    # 공식 근거가 있는 ADJACENT가 없으므로 모든 노선이 역마다 끊긴 상태로 보고된다(숨기지 않는다).
    for line in report.values():
        assert line.component_sizes == (1,) * line.station_count
        assert len(line.isolated_station_line_ids) == line.station_count


def test_connectivity_with_provisional_edges_lists_breaks_instead_of_hiding_them(seeded):
    _load_all(seeded)

    report = connectivity_report(seeded)

    assert set(report) == set("12345678")
    for line in report.values():
        assert sum(line.component_sizes) == line.station_count
    assert report["2"].component_sizes[0] >= 40
    assert not report["1"].connected  # 1호선은 일부 구간만 시드에 있다


# ---------- BFS(#32)와 대체역 API(#33) 통합 ----------


def _station_id(session, name):
    return session.scalar(select(Station.station_id).where(Station.station_name == name))


def _elevator(session, eid, station_id, status):
    session.add(Elevator(elevator_id=eid, station_id=station_id, source="OA-15994",
                         external_station_code=f"T{eid}", elevator_name=f"설비{eid}",
                         location_description=None, operation_section=None))
    session.flush()
    session.add(ElevatorStatus(elevator_id=eid, status=status, source_status_text=None, observed_at=None,
                               fetched_at=to_utc_iso(utc_now()), source="OA-15994"))
    session.flush()


def _client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


@pytest.fixture
def real_client(seeded):
    _load_all(seeded)  # 임시 DB에서만 PROVISIONAL ADJACENT까지 넣어 통합 동작을 본다
    return _client(seeded), seeded


def test_alternatives_use_loaded_edges_across_transfer_station(real_client):
    client, session = real_client
    gangnam, yeoksam, gyodae, nambu = (_station_id(session, n) for n in ("강남", "역삼", "교대(법원, 검찰청)", "남부터미널(예술의전당)"))
    assert None not in (gangnam, yeoksam, gyodae, nambu)
    _elevator(session, 1, gangnam, "가동 안됨")
    _elevator(session, 2, yeoksam, "가동중")
    _elevator(session, 3, nambu, "가동중")
    session.commit()

    body = client.get(f"/api/stations/{gangnam}/alternatives").json()

    by_id = {a["station_id"]: a for a in body["alternatives"]}
    assert by_id[yeoksam]["distance"] == 1 and by_id[yeoksam]["same_line"] is True
    assert by_id[nambu]["distance"] == 2 and by_id[nambu]["same_line"] is False  # 환승 역을 거친 3호선 역
    assert [a["station_id"] for a in body["alternatives"]][0] == yeoksam
    assert "검증되지 않음" in body["limitations"][0]
    assert body["empty_reason"] is None


def test_alternatives_do_not_invent_links_for_unverified_sections(real_client):
    client, session = real_client
    station = _station_id(session, "동묘앞")  # 공식 역번호가 연속하지 않아 간선이 PENDING
    _elevator(session, 10, station, "가동 안됨")
    session.commit()

    body = client.get(f"/api/stations/{station}/alternatives").json()

    assert body["alternatives"] == [] and body["empty_reason"] is not None


def test_alternatives_with_only_official_edges_have_no_adjacency(seeded):
    """기본 --apply 상태(공식 TRANSFER만)에서는 인접 간선이 없어 후보를 만들어 내지 않는다."""
    load_edges(seeded, read_verified_edges(DEFAULT_SEED_DIR))
    gangnam, yeoksam = _station_id(seeded, "강남"), _station_id(seeded, "역삼")
    _elevator(seeded, 1, gangnam, "가동 안됨")
    _elevator(seeded, 2, yeoksam, "가동중")
    seeded.commit()

    body = _client(seeded).get(f"/api/stations/{gangnam}/alternatives").json()

    assert body["alternatives"] == [] and body["empty_reason"] is not None
    assert any("연결 데이터가 없" in m for m in body["limitations"])


def test_available_but_stale_target_is_not_treated_as_healthy(real_client):
    client, session = real_client
    gangnam, yeoksam = _station_id(session, "강남"), _station_id(session, "역삼")
    _elevator(session, 20, gangnam, "가동중")
    session.add(Elevator(elevator_id=21, station_id=gangnam, source="OA-15994", external_station_code="T21",
                         elevator_name="설비21", location_description=None, operation_section=None))
    session.flush()  # 상태 행이 없는 설비 -> 일부 미확인
    _elevator(session, 22, yeoksam, "가동중")
    session.commit()

    body = client.get(f"/api/stations/{gangnam}/alternatives").json()

    assert body["station_status"]["status"] == "AVAILABLE" and body["station_status"]["data_fresh"] is False
    assert body["empty_reason"] is None and body["count"] >= 1
