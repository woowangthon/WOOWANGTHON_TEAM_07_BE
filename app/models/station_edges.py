from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StationEdge(Base):
    """역 인접·환승 간선. 소요 시간은 DB에 두지 않고 설정 기본값을 쓴다.

    출처가 검증되기 전까지 source는 NULL이며, 데이터 적재는 출처 확정 후에 한다.
    """

    __tablename__ = "station_edges"
    __table_args__ = (
        UniqueConstraint(
            "from_station_line_id", "to_station_line_id", name="uq_station_edges_pair"
        ),
        CheckConstraint(
            "edge_type IN ('ADJACENT', 'TRANSFER')", name="ck_station_edges_edge_type"
        ),
    )

    edge_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    from_station_line_id: Mapped[int] = mapped_column(
        ForeignKey("station_lines.station_line_id"), nullable=False, index=True
    )
    to_station_line_id: Mapped[int] = mapped_column(
        ForeignKey("station_lines.station_line_id"), nullable=False, index=True
    )
    edge_type: Mapped[str] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
