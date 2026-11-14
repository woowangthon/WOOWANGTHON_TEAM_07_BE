import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import Base
import app.models  # noqa: F401  모델 등록

EXPECTED_TABLES = {
    "lines",
    "stations",
    "station_lines",
    "station_external_ids",
    "station_edges",
    "elevators",
    "elevator_status",
}


def _insert_base(conn):
    """외래키가 필요한 행들의 기준 데이터를 넣는다."""
    conn.execute(text("INSERT INTO lines (line_id, line_name) VALUES ('1', '1호선')"))
    conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (1, '동묘앞')"))
    conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (2, '신설동')"))
    conn.execute(
        text("INSERT INTO station_lines (station_line_id, station_id, line_id) VALUES (1, 1, '1')")
    )
    conn.execute(
        text("INSERT INTO station_lines (station_line_id, station_id, line_id) VALUES (2, 2, '1')")
    )


def _insert_elevator(conn, elevator_id=1, station_id=1, name="승강기)엘리베이터-동묘앞 상행 10-3 내부#2"):
    conn.execute(
        text(
            "INSERT INTO elevators (elevator_id, station_id, source, external_station_code, elevator_name) "
            "VALUES (:eid, :sid, 'OA-15994', '0159', :name)"
        ),
        {"eid": elevator_id, "sid": station_id, "name": name},
    )


def _insert_status(conn, elevator_id=1, status="가동중", observed_at=None, fetched_at="2026-11-13T02:00:00Z"):
    conn.execute(
        text(
            "INSERT INTO elevator_status (elevator_id, status, observed_at, fetched_at, source) "
            "VALUES (:eid, :status, :obs, :fetched, 'OA-15994')"
        ),
        {"eid": elevator_id, "status": status, "obs": observed_at, "fetched": fetched_at},
    )


# ---------- 구조 ----------

def test_expected_tables_exist(engine):
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    # 만들지 않기로 한 테이블이 없는지 확인
    for name in ("train_arrivals", "station_access_times", "departure_calculations", "elevator_source_ids"):
        assert name not in tables
    # train_schedules는 OA-101 확인 후 추가했다(c4a91f27e6b3)
    assert "train_schedules" in tables


def test_models_match_migration(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_migration_roundtrip(alembic_cfg_for, migrated_url):
    cfg = alembic_cfg_for(migrated_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


# ---------- 외래키 ----------

def test_fk_enforced_on_station_lines(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO station_lines (station_id, line_id) VALUES (999, '1')")
            )


def test_fk_enforced_on_elevators_station(engine):
    with engine.begin() as conn:
        _insert_base(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_elevator(conn, station_id=999)


def test_fk_enforced_on_elevator_status(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_status(conn, elevator_id=12345)


def test_fk_enforced_on_station_edges(engine):
    with engine.begin() as conn:
        _insert_base(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO station_edges (from_station_line_id, to_station_line_id, edge_type) "
                    "VALUES (1, 999, 'ADJACENT')"
                )
            )


# ---------- UNIQUE ----------

def test_unique_station_lines(engine):
    with engine.begin() as conn:
        _insert_base(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO station_lines (station_id, line_id) VALUES (1, '1')")
            )


def test_unique_station_external_ids(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id) "
                "VALUES ('OA-15994', '0159', 1)"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO station_external_ids (source, external_station_code, station_id) "
                    "VALUES ('OA-15994', '0159', 2)"
                )
            )


def test_unique_elevators_composite_key(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        _insert_elevator(conn, elevator_id=1)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_elevator(conn, elevator_id=2)  # 같은 (source, 역 코드, 설비명)


def test_unique_station_edges_pair(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        conn.execute(
            text(
                "INSERT INTO station_edges (from_station_line_id, to_station_line_id, edge_type) "
                "VALUES (1, 2, 'ADJACENT')"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO station_edges (from_station_line_id, to_station_line_id, edge_type) "
                    "VALUES (1, 2, 'TRANSFER')"
                )
            )


# ---------- NOT NULL ----------

def test_elevator_station_id_not_null(engine):
    with engine.begin() as conn:
        _insert_base(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO elevators (station_id, source, external_station_code, elevator_name) "
                    "VALUES (NULL, 'OA-15994', '0159', 'x')"
                )
            )


def test_fetched_at_not_null(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        _insert_elevator(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_status(conn, fetched_at=None)


def test_observed_at_nullable_and_stored_separately(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        _insert_elevator(conn)
        _insert_status(conn, observed_at=None, fetched_at="2026-11-13T02:00:00Z")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT observed_at, fetched_at FROM elevator_status WHERE elevator_id = 1")
        ).one()
    assert row.observed_at is None
    assert row.fetched_at == "2026-11-13T02:00:00Z"


def test_station_edges_source_nullable(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        conn.execute(
            text(
                "INSERT INTO station_edges (from_station_line_id, to_station_line_id, edge_type, source) "
                "VALUES (1, 2, 'ADJACENT', NULL)"
            )
        )


# ---------- CHECK ----------

def test_elevator_status_check(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        _insert_elevator(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_status(conn, status="보수중")  # 보수중은 가동 안됨으로 매핑해야 한다


def test_station_edges_type_check(engine):
    with engine.begin() as conn:
        _insert_base(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO station_edges (from_station_line_id, to_station_line_id, edge_type) "
                    "VALUES (1, 2, 'WALK')"
                )
            )


def test_elevator_status_one_row_per_elevator(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        _insert_elevator(conn)
        _insert_status(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_status(conn)  # PK 중복 = 이력 행을 쌓지 않는다
