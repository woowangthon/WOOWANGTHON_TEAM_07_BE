from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Elevator(Base):
    """엘리베이터 설비의 기본 정보(정적). 상태는 elevator_status에 둔다.

    방면은 상·하행으로 나누지 않고 location_description에 원문 그대로 둔다.
    OA-15994에는 고유 ID가 없어 (source, 역 코드, 설비명) 복합 키를 쓴다.
    현재는 station_id 기준이다. 설비 소속 노선이 확인되면 station_line_id(NULL 허용)를 추가한다.
    """

    __tablename__ = "elevators"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_station_code",
            "elevator_name",
            name="uq_elevators_source_station_name",
        ),
    )

    elevator_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.station_id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    external_station_code: Mapped[str] = mapped_column(String(16))
    elevator_name: Mapped[str] = mapped_column(String(255))
    location_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation_section: Mapped[str | None] = mapped_column(String(32), nullable=True)
