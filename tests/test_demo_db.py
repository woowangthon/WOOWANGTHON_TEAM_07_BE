"""시연용 데모 DB 생성과 통합 동작 검증. 모든 DB는 임시 디렉터리에 만든다(data/dev.db는 열지 않는다)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import build_demo_db as cli
from app.database import get_session, make_engine
from app.main import create_app
from app.models import Elevator, ElevatorStatus, Station
from app.services.demo_db import (
    DEFAULT_SAMPLE,
    ROOT,
    SYNTHETIC_PREFIX,
    SYNTHETIC_SOURCE,
    DemoBuildError,
    build_demo_db,
    default_fetched_at,
    ensure_safe_output_dir,
    inspect_db,
)
from app.services.edge_seed import load_edges, read_provisional_edges
from app.services.station_seed import DEFAULT_SEED_DIR, SeedError, load_seed, read_seed_dir


class Demo:
    def __init__(self, result):
        self.result = result
        self.engine = make_engine(f"sqlite:///{result.db_path}")
        self.session = Session(self.engine)
        app = create_app()
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)

    def station_id(self, name):
        return self.session.scalar(select(Station.station_id).where(Station.station_name == name))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    result = build_demo_db(tmp_path_factory.mktemp("demo_full"), include_provisional_adjacent=True)
    d = Demo(result)
    yield d
    d.session.close()
    d.engine.dispose()


@pytest.fixture(scope="module")
def demo_official_only(tmp_path_factory):
    result = build_demo_db(tmp_path_factory.mktemp("demo_official"), synthetic=False)
    d = Demo(result)
    yield d
    d.session.close()
    d.engine.dispose()


# ---------- 구성과 건수 ----------


def test_counts_of_the_full_demo_db(demo):
    counts = demo.result.manifest["counts"]

    assert counts == {
        "stations": 240, "station_lines": 276, "edges_transfer": 38, "edges_adjacent": 239,
        "elevators_real_sample": 866, "elevator_status_real_sample": 866,
        "elevators_synthetic": 5, "elevator_status_synthetic": 5,
    }
    assert demo.result.manifest["kind"] == "DEMO" and demo.result.manifest["alembic"] == "b7e2c91a4d10"


def test_station_mapping_matches_a_seed_only_source_db(engine, tmp_path_factory):
    with Session(engine) as session:
        rows, links, review = read_seed_dir(DEFAULT_SEED_DIR)
        load_seed(session, rows, links, review)
    source = Path(engine.url.database)

    result = build_demo_db(tmp_path_factory.mktemp("demo_cmp"), source_db=source, synthetic=False)

    assert result.manifest["source_db"]["station_mapping_identical_to_demo"] is True
    assert result.manifest["source_db"]["counts"] == {"stations": 240, "station_lines": 276}


def test_official_only_build_has_no_adjacent_and_no_synthetic(demo_official_only):
    counts = demo_official_only.result.manifest["counts"]

    assert counts["edges_transfer"] == 38 and counts["edges_adjacent"] == 0
    assert counts["elevators_synthetic"] == 0
    assert demo_official_only.result.manifest["edges"]["adjacent_provisional_loaded"] == 0


def test_operational_loader_limits_are_unchanged(demo):
    with pytest.raises(SeedError, match="공식 근거가 없는"):
        load_edges(demo.session, read_provisional_edges(DEFAULT_SEED_DIR))


# ---------- 실제 샘플과 시각 ----------


def test_real_sample_has_no_invented_times(demo):
    rows = demo.session.execute(
        select(ElevatorStatus.observed_at, ElevatorStatus.fetched_at, ElevatorStatus.source)
        .where(ElevatorStatus.source == "OA-15994")
    ).all()

    assert len(rows) == 866
    assert {r.observed_at for r in rows} == {None}
    assert {r.fetched_at for r in rows} == {"2026-11-13T00:00:00Z"}  # 샘플 파일명 날짜의 하한값
    manifest = demo.result.manifest["elevator_sample"]
    assert manifest["observed_at"] is None and "과거" in manifest["disclaimer"]
    assert "현재 상태를 보장하지 않는다" in manifest["disclaimer"]


def test_default_fetched_at_comes_only_from_the_sample_file_name(tmp_path):
    assert default_fetched_at(DEFAULT_SAMPLE) == "2026-11-13T00:00:00Z"
    with pytest.raises(DemoBuildError):
        default_fetched_at(tmp_path / "sample_without_date.json")


def test_real_sample_status_distribution_is_preserved(demo):
    statuses = dict(demo.session.execute(
        select(ElevatorStatus.status, func.count()).where(ElevatorStatus.source == "OA-15994").group_by(ElevatorStatus.status)
    ).all())

    assert statuses == {"가동중": 845, "가동 안됨": 21}


# ---------- 합성 데이터 분리 ----------


def test_synthetic_rows_are_separate_and_clearly_marked(demo):
    synthetic = demo.session.execute(
        select(Elevator.elevator_name, Elevator.location_description, ElevatorStatus.source)
        .join(ElevatorStatus, ElevatorStatus.elevator_id == Elevator.elevator_id)
        .where(Elevator.source == SYNTHETIC_SOURCE)
    ).all()

    assert len(synthetic) == 5
    assert all(r.elevator_name.startswith(SYNTHETIC_PREFIX) and "DEMO/SYNTHETIC" in r.location_description for r in synthetic)
    assert all(r.source == SYNTHETIC_SOURCE for r in synthetic)
    # 실제 샘플 행은 합성 표시를 갖지 않는다.
    real_names = demo.session.scalars(select(Elevator.elevator_name).where(Elevator.source == "OA-15994")).all()
    assert len(real_names) == 866 and not any("DEMO" in n for n in real_names)


# ---------- 통합 동작 (임시 DB, 실제 시계) ----------


def test_ttl_is_not_bypassed_real_sample_is_stale_synthetic_is_fresh(demo):
    gangnam = demo.station_id("강남")

    body = demo.client.get(f"/api/stations/{gangnam}/elevators").json()

    synthetic = [e for e in body["elevators"] if e["elevator_name"].startswith(SYNTHETIC_PREFIX)]
    real = [e for e in body["elevators"] if not e["elevator_name"].startswith(SYNTHETIC_PREFIX)]
    assert len(synthetic) == 1 and synthetic[0]["data_fresh"] is True and synthetic[0]["status"] == "가동 안됨"
    assert real and all(e["unknown_reason"] == "STALE" and e["status"] is None and e["data_fresh"] is False for e in real)
    assert body["station_status"]["status"] == "UNAVAILABLE" and body["station_status"]["data_fresh"] is False
    assert body["station_status"]["unknown_elevator_count"] == len(real)


def test_status_filter_never_returns_stale_real_sample(demo):
    for status in ("가동중", "가동 안됨"):
        body = demo.client.get("/api/elevators/status", params={"status": status}).json()

        assert body["count"] > 0
        assert all(e["elevator_name"].startswith(SYNTHETIC_PREFIX) for e in body["elevators"])


def test_all_sample_elevators_are_unknown_in_the_unfiltered_list(demo):
    body = demo.client.get("/api/elevators/status").json()

    real = [e for e in body["elevators"] if not e["elevator_name"].startswith(SYNTHETIC_PREFIX)]
    assert body["count"] == 866 + 5 and len(real) == 866
    assert all(e["unknown_reason"] == "STALE" and e["status"] is None for e in real)
    assert all(e["observed_at"] is None for e in body["elevators"])


def test_alternatives_integrate_bfs_edges_and_elevator_status(demo):
    gangnam = demo.station_id("강남")

    body = demo.client.get(f"/api/stations/{gangnam}/alternatives").json()

    by_name = {a["station_name"]: a for a in body["alternatives"]}
    assert by_name["역삼"]["distance"] == 1 and by_name["역삼"]["same_line"] is True
    assert by_name["교대(법원, 검찰청)"]["distance"] == 1
    assert by_name["남부터미널(예술의전당)"]["distance"] == 2 and by_name["남부터미널(예술의전당)"]["same_line"] is False
    assert "선릉" not in by_name  # 합성 상태가 가동 안됨이라 후보가 아니다
    # 실제 샘플이 STALE이라 어떤 후보도 '최신 정상'으로 보이지 않는다.
    assert all(a["station_status"]["data_fresh"] is False for a in body["alternatives"])
    assert "검증되지 않음" in body["limitations"][0]


def test_without_provisional_edges_demo_alternatives_stay_empty(demo_official_only):
    gangnam = demo_official_only.station_id("강남")

    body = demo_official_only.client.get(f"/api/stations/{gangnam}/alternatives").json()

    assert body["alternatives"] == [] and body["empty_reason"] is not None


# ---------- 안전 규칙 ----------


@pytest.mark.parametrize("bad", [ROOT / "data" / "demo_out", ROOT / "demo_out", Path.home() / "demo_out_should_not_exist"])
def test_output_outside_temp_or_inside_repo_is_rejected_without_creating_it(bad):
    with pytest.raises(DemoBuildError):
        ensure_safe_output_dir(bad)
    assert not bad.exists()


def test_non_empty_output_dir_is_not_overwritten(tmp_path):
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")

    with pytest.raises(DemoBuildError, match="비어 있지 않습니다"):
        build_demo_db(tmp_path)

    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "x"


def test_cli_builds_and_refuses_repo_paths(tmp_path, capsys):
    out = tmp_path / "cli_demo"

    assert cli.main(["--out-dir", str(out), "--no-synthetic"]) == 0
    assert inspect_db(out / "demo.db")["counts"]["edges_adjacent"] == 0  # 옵션 없이는 인접 간선을 넣지 않는다
    assert "과거 응답" in capsys.readouterr().out
    assert cli.main(["--out-dir", str(ROOT / "data" / "x")]) == 1


def test_manifest_file_is_written_and_marks_demo(demo):
    path = demo.result.out_dir / "demo_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["kind"] == "DEMO" and "운영 데이터가 아니다" in manifest["warning"]
    assert manifest["synthetic"]["source"] == SYNTHETIC_SOURCE
    assert manifest["ttl_policy"].startswith("기존 TTL")
    assert (demo.result.out_dir / "ingestion_issues.jsonl").exists()  # 로그도 임시 디렉터리에 쓴다


def test_real_database_and_logs_are_untouched_by_building(tmp_path):
    watched = [ROOT / "data" / "dev.db", ROOT / "data" / "logs" / "ingestion_issues.jsonl"]
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in watched if p.exists()}

    build_demo_db(tmp_path / "once", synthetic=False)

    assert before == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in watched if p.exists()}
