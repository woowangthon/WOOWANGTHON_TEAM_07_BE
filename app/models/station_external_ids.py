from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StationExternalId(Base):
    """외부 데이터의 역 코드와 내부 역의 대응.

    OA-12035 역번호와 OA-15994 STN_CD는 노선별 코드이므로 station_line_id로 노선 행까지 연결한다.
    station_line_id는 NULL을 허용한다(노선을 특정할 수 없는 소스, 이 컬럼이 생기기 전의 기존 행).
    복합 FK (station_line_id, station_id)는 두 값이 서로 다른 역을 가리키는 모순을 막는다.
    station_line_id가 NULL이면 이 FK는 검사하지 않는다(SQLite MATCH SIMPLE).
    """

    __tablename__ = "station_external_ids"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_station_code", name="uq_station_external_ids_source_code"
        ),
        ForeignKeyConstraint(
            ["station_line_id", "station_id"],
            ["station_lines.station_line_id", "station_lines.station_id"],
            name="fk_station_external_ids_station_line",
        ),
    )

    station_external_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    external_station_code: Mapped[str] = mapped_column(String(16))
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.station_id"), nullable=False, index=True
    )
    station_line_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
