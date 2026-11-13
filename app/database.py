from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:
    # SQLite는 기본적으로 외래키를 검사하지 않으므로 커넥션마다 켠다.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_fk)
    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """요청마다 세션을 하나 열고 끝나면 닫는다. 테스트는 이 의존성을 임시 DB로 바꿔 쓴다."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
