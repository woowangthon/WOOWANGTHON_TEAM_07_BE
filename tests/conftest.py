from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from app.database import make_engine

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """임시 파일 SQLite에 마이그레이션을 적용한 엔진. 개발 DB(data/dev.db)는 건드리지 않는다."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    command.upgrade(_alembic_config(url), "head")
    eng = make_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def migrated_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'roundtrip.db'}"


@pytest.fixture
def alembic_cfg_for():
    return _alembic_config
