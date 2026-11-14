from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TrainSchedule(Base):
    """역별 열차 시간표(OA-101). 시각은 서비스일 0시 기준 정수 초다(24:30은 88200).

    - arrival_time, departure_time이 NULL이면 원본이 00:00:00(없음)이다. 시발역은 도착이, 종착역은 출발이 없다.
    - service_day_type: WEEKDAY, SATURDAY, HOLIDAY. 원본 WEEK_TAG 1, 2, 3의 대응은 추정이다(docs/api-notes.md).
    - direction: 원본 INOUT_TAG(1, 2). 노선별 의미(상행·하행, 내선·외선)는 확인되지 않았다.
    - 계획 시각이다. 지연은 반영되지 않는다.
    """

    __tablename__ = "train_schedules"
    __table_args__ = (
        UniqueConstraint(
            "station_line_id", "service_day_type", "direction", "train_no",
            name="uq_train_schedules_stop",
        ),
        CheckConstraint(
            "service_day_type IN ('WEEKDAY', 'SATURDAY', 'HOLIDAY')",
            name="ck_train_schedules_day_type",
        ),
        CheckConstraint("direction IN (1, 2)", name="ck_train_schedules_direction"),
        Index(
            "ix_train_schedules_lookup",
            "station_line_id", "service_day_type", "direction", "departure_time",
        ),
    )

    schedule_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_line_id: Mapped[int] = mapped_column(
        ForeignKey("station_lines.station_line_id"), nullable=False
    )
    service_day_type: Mapped[str] = mapped_column(String(16))
    direction: Mapped[int] = mapped_column(Integer)
    train_no: Mapped[str] = mapped_column(String(16))
    arrival_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    departure_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_station_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    destination_station_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_express: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fetched_at: Mapped[str] = mapped_column(String(32), nullable=False)
