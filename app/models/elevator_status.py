from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ElevatorStatus(Base):
    """설비별 최신 상태(동적). 설비당 1행이며 Upsert로 갱신하고 이력은 쌓지 않는다.

    observed_at(원본 측정 시각)과 fetched_at(우리가 가져온 시각)은 서로 다른 값이므로
    임의로 동일하게 채우지 않는다. observed_at은 원본에 없으면 NULL이다.
    시각은 ISO 8601 UTC 문자열이다(예: 2026-11-13T05:00:00Z).
    """

    __tablename__ = "elevator_status"
    __table_args__ = (
        CheckConstraint(
            "status IN ('가동중', '가동 안됨')", name="ck_elevator_status_status"
        ),
    )

    elevator_id: Mapped[int] = mapped_column(
        ForeignKey("elevators.elevator_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16))
    source_status_text: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fetched_at: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32))
