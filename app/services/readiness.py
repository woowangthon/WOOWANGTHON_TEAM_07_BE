"""DB를 변경하지 않고 스키마와 데이터 신선도를 확인한다."""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app import models  # noqa: F401
from app.config import settings
from app.database import Base
from app.errors import AppError
from app.models.elevator_status import ElevatorStatus
from app.timeutil import to_utc_iso, utc_now, utc_str_to_kst_iso


@lru_cache
def schema_heads() -> set[str]:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def check_readiness(db: Session) -> dict:
    connection = db.connection()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "alembic_version" not in tables or not set(Base.metadata.tables) <= tables:
        raise AppError(503, "DATABASE_NOT_READY", "DB 준비가 필요합니다. 백업 후 alembic upgrade head를 실행해 주세요.")
    revisions = set(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if revisions != schema_heads():
        raise AppError(503, "DATABASE_NOT_READY", "DB 버전이 맞지 않습니다. 백업 후 alembic upgrade head를 실행해 주세요.")
    cutoff = to_utc_iso(utc_now() - timedelta(minutes=settings.elevator_status_ttl_minutes))
    total, latest = db.execute(select(func.count(), func.max(ElevatorStatus.fetched_at))).one()
    fresh = db.scalar(select(func.count()).select_from(ElevatorStatus).where(ElevatorStatus.fetched_at >= cutoff))
    return {
        "status": "ready",
        "database": "ok",
        "elevator_status": {
            "total": total,
            "fresh": fresh,
            "stale": total - fresh,
            "latest_fetched_at": utc_str_to_kst_iso(latest) if latest else None,
        },
    }
