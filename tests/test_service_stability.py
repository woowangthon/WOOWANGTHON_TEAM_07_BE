import asyncio
import socket
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.cli import serve
from app.config import settings
from app.database import get_session
from app.main import create_app
from app.services import elevator_refresh


def test_ready_on_migrated_database(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready", "database": "ok",
        "elevator_status": {"total": 0, "fresh": 0, "stale": 0, "latest_fetched_at": None},
    }


def test_ready_rejects_old_revision(client, db):
    db.execute(text("UPDATE alembic_version SET version_num='old'"))
    db.commit()
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_READY"
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_handles_unavailable_database():
    from sqlalchemy.exc import OperationalError
    class BrokenSession:
        def connection(self):
            raise OperationalError("secret database URL", {}, Exception("secret"))
    app = create_app()
    app.dependency_overrides[get_session] = lambda: BrokenSession()
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert "secret" not in response.text


def test_ready_counts_stale_data(client, db):
    from app.models import Elevator, ElevatorStatus
    from app.timeutil import to_utc_iso, utc_now
    db.add(Elevator(elevator_id=1, station_id=1, source="OA-15994", external_station_code="0150", elevator_name="test"))
    db.flush()
    db.add(ElevatorStatus(elevator_id=1, status="가동중", source="OA-15994", fetched_at="2000-01-01T00:00:00Z"))
    db.commit()
    state = client.get("/ready").json()["elevator_status"]
    assert (state["fresh"], state["stale"]) == (0, 1)
    assert state["latest_fetched_at"] == "2000-01-01T09:00:00+09:00"
    db.execute(text("UPDATE elevator_status SET fetched_at=:now"), {"now": to_utc_iso(utc_now())})
    db.commit()
    state = client.get("/ready").json()["elevator_status"]
    assert (state["fresh"], state["stale"]) == (1, 0)


def test_launcher_rejects_occupied_port_before_touching_db(monkeypatch, capsys):
    def no_database():
        pytest.fail("포트 충돌 시 DB에 접근하면 안 됩니다.")
    monkeypatch.setattr(serve, "SessionLocal", no_database)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert serve.main(["--port", str(port)]) == 2
    assert "Get-NetTCPConnection" in capsys.readouterr().err


def test_launcher_does_not_create_missing_database(monkeypatch, tmp_path):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert serve.main(["--port", str(port)]) == 2
    assert not path.exists()


def test_refresh_rejects_missing_key_and_interval_past_ttl(monkeypatch):
    monkeypatch.setattr(settings, "elevator_auto_refresh", True)
    monkeypatch.setattr(settings, "seoul_open_api_key", "")
    with pytest.raises(ValueError, match="SEOUL_OPEN_API_KEY"):
        elevator_refresh.validate_refresh_settings()
    monkeypatch.setattr(settings, "seoul_open_api_key", "test")
    monkeypatch.setattr(settings, "elevator_status_ttl_minutes", 10)
    monkeypatch.setattr(settings, "elevator_refresh_interval_minutes", 10)
    with pytest.raises(ValueError, match="TTL"):
        elevator_refresh.validate_refresh_settings()


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_collector_is_reaped_on_timeout_or_shutdown(monkeypatch, failure):
    class Child:
        returncode = None
        killed = False
        async def wait(self):
            if not self.killed:
                if failure == "cancel":
                    raise asyncio.CancelledError
                await asyncio.Event().wait()
            return 1
        def kill(self):
            self.killed = True
            self.returncode = 1
    child = Child()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    monkeypatch.setattr(elevator_refresh, "COLLECT_TIMEOUT_SECONDS", 0.01)
    expected = TimeoutError if failure == "timeout" else asyncio.CancelledError
    with pytest.raises(expected):
        asyncio.run(elevator_refresh.collect_once())
    assert child.killed


def test_refresh_retries_with_backoff_then_resets(monkeypatch):
    collect = AsyncMock(side_effect=[False, OSError("secret"), True, False])
    delays = []
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 4:
            raise asyncio.CancelledError
    monkeypatch.setattr(elevator_refresh, "collect_once", collect)
    monkeypatch.setattr(elevator_refresh.asyncio, "sleep", sleep)
    monkeypatch.setattr(settings, "elevator_refresh_interval_minutes", 30)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(elevator_refresh.refresh_loop())
    assert delays == [60, 120, 1800, 60]


def test_refresh_disabled_does_not_spawn_child(monkeypatch):
    collect = AsyncMock()
    monkeypatch.setattr(settings, "elevator_auto_refresh", False)
    monkeypatch.setattr(elevator_refresh, "collect_once", collect)
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
    collect.assert_not_called()


def test_sqlite_busy_timeout_and_foreign_keys(engine):
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
