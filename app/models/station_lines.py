from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StationLine(Base):
    """역-노선 연결. 환승역은 노선마다 행이 하나씩 생긴다."""

    __tablename__ = "station_lines"
    __table_args__ = (
        UniqueConstraint("station_id", "line_id", name="uq_station_lines_station_line"),
    )

    station_line_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.station_id"), nullable=False, index=True
    )
    line_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("lines.line_id"), nullable=False, index=True
    )
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
