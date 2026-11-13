from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StationLine(Base):
    """역-노선 연결. 환승역은 노선마다 행이 하나씩 생긴다.

    (station_line_id, station_id) 유니크 인덱스는 station_external_ids의 복합 FK가
    참조하는 부모 키다. station_line_id가 이미 PK라 값 중복 검사 목적이 아니라
    "이 노선 행이 어느 역에 속하는가"를 외래키로 강제하기 위한 것이다.
    """

    __tablename__ = "station_lines"
    __table_args__ = (
        UniqueConstraint("station_id", "line_id", name="uq_station_lines_station_line"),
        Index("uq_station_lines_id_station", "station_line_id", "station_id", unique=True),
    )

    station_line_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.station_id"), nullable=False, index=True
    )
    line_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("lines.line_id"), nullable=False, index=True
    )
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
