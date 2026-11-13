"""station_external_ids.station_line_id 마이그레이션(b7e2c91a4d10) 검증."""

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import make_engine

PREVIOUS_REVISION = "8d70068f68f4"


def _insert_base(conn) -> None:
    conn.execute(text("INSERT INTO lines (line_id, line_name) VALUES ('1', '1호선'), ('2', '2호선')"))
    conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (1, '동묘앞'), (2, '시청')"))
    conn.execute(
        text(
            "INSERT INTO station_lines (station_line_id, station_id, line_id) "
            "VALUES (1, 1, '1'), (2, 2, '1'), (3, 2, '2')"
        )
    )


def test_upgrade_and_downgrade_preserve_existing_rows(alembic_cfg_for, migrated_url):
    cfg = alembic_cfg_for(migrated_url)
    command.upgrade(cfg, PREVIOUS_REVISION)

    old_engine = make_engine(migrated_url)
    with old_engine.begin() as conn:
        _insert_base(conn)
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id) "
                "VALUES ('OA-15994', '0159', 1), ('OA-15994', '0151', 2)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO station_edges (from_station_line_id, to_station_line_id, edge_type) "
                "VALUES (2, 3, 'TRANSFER')"
            )
        )
    old_engine.dispose()

    command.upgrade(cfg, "head")
    engine = make_engine(migrated_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("station_external_ids")}
        assert "station_line_id" in columns
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source, external_station_code, station_id, station_line_id "
                    "FROM station_external_ids ORDER BY external_station_code"
                )
            ).all()
            # 기존 행은 그대로이고 새 컬럼은 NULL이다.
            assert rows == [("OA-15994", "0151", 2, None), ("OA-15994", "0159", 1, None)]
            assert conn.execute(text("SELECT COUNT(*) FROM station_lines")).scalar() == 3
            assert conn.execute(text("SELECT COUNT(*) FROM station_edges")).scalar() == 1
    finally:
        engine.dispose()

    command.downgrade(cfg, PREVIOUS_REVISION)
    engine = make_engine(migrated_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("station_external_ids")}
        assert "station_line_id" not in columns
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source, external_station_code, station_id "
                    "FROM station_external_ids ORDER BY external_station_code"
                )
            ).all()
            assert rows == [("OA-15994", "0151", 2), ("OA-15994", "0159", 1)]
            assert conn.execute(text("SELECT COUNT(*) FROM station_lines")).scalar() == 3
            assert conn.execute(text("SELECT COUNT(*) FROM station_edges")).scalar() == 1
    finally:
        engine.dispose()


def test_composite_fk_accepts_matching_pair(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                "VALUES ('OA-12035', '0201', 2, 3)"
            )
        )
        assert conn.execute(text("SELECT COUNT(*) FROM station_external_ids")).scalar() == 1


def test_composite_fk_rejects_station_line_of_another_station(engine):
    # station_line 1은 station 1의 노선 행이다. station 2와 짝지으면 모순이다.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_base(conn)
            conn.execute(
                text(
                    "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                    "VALUES ('OA-12035', '0151', 2, 1)"
                )
            )


def test_composite_fk_rejects_unknown_station_line(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_base(conn)
            conn.execute(
                text(
                    "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                    "VALUES ('OA-12035', '0151', 2, 999)"
                )
            )


def test_station_line_id_may_be_null(engine):
    with engine.begin() as conn:
        _insert_base(conn)
        conn.execute(
            text(
                "INSERT INTO station_external_ids (source, external_station_code, station_id) "
                "VALUES ('OA-101', '0151', 2)"
            )
        )
        assert conn.execute(
            text("SELECT station_line_id FROM station_external_ids")
        ).scalar() is None


def test_external_code_uniqueness_is_kept(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_base(conn)
            conn.execute(
                text(
                    "INSERT INTO station_external_ids (source, external_station_code, station_id, station_line_id) "
                    "VALUES ('OA-12035', '0151', 2, 2), ('OA-12035', '0151', 2, 3)"
                )
            )
