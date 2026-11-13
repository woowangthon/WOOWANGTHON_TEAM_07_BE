from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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


# ---------- 역 API 테스트용 시드 (임시 DB에만 넣는다) ----------

from sqlalchemy.orm import Session as _Session  # noqa: E402

from app.database import get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Line as _Line  # noqa: E402
from app.models import Station as _Station  # noqa: E402
from app.models import StationLine as _StationLine  # noqa: E402

# 역 id, 역명, 노선. 실제 역 마스터가 아니라 검색·상세·정렬 규칙을 보려는 시험 데이터다.
STATION_FIXTURE = [
    (1, "서울역", ["1", "4"]),              # 환승역 (1호선 포함)
    (2, "시청", ["1", "2"]),                # 환승역 (1호선 포함)
    (3, "강남", ["2"]),
    (4, "동묘앞", ["1"]),
    (5, "교대(법원, 검찰청)", ["2", "3"]),      # 환승역, 괄호 포함 이름
    (6, "서울대입구(관악구청)", ["2"]),
    (7, "종로3가", ["1", "3", "5"]),         # 3개 노선 환승역
    (8, "신도림", ["1"]),                   # 같은 역명 두 개(정렬 확인)
    (9, "신도림", ["2"]),
    (10, "정렬시험", ["8", "2"]),            # 입력 순서와 무관하게 호선 오름차순인지 확인
]


@pytest.fixture
def db(engine):
    with _Session(engine) as session:
        for n in range(1, 9):
            session.add(_Line(line_id=str(n), line_name=f"{n}호선", operator=None))
        session.flush()
        for station_id, name, _lines in STATION_FIXTURE:
            session.add(_Station(station_id=station_id, station_name=name))
        session.flush()
        for station_id, _name, lines in STATION_FIXTURE:
            for line_id in lines:
                session.add(_StationLine(station_id=station_id, line_id=line_id))
        session.commit()
        yield session


@pytest.fixture
def client(db):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)
