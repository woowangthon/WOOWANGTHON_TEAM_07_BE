from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StationExternalId(Base):
    """외부 데이터의 역 코드와 내부 역의 대응.

    현재는 station_id 기준이다. 노선별 역 코드가 확인되면 station_line_id(NULL 허용) 컬럼을 추가한다.
    """

    __tablename__ = "station_external_ids"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_station_code", name="uq_station_external_ids_source_code"
        ),
    )

    station_external_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    external_station_code: Mapped[str] = mapped_column(String(16))
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.station_id"), nullable=False, index=True
    )
