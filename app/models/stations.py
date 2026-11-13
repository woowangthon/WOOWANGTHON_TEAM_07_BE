from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Station(Base):
    """역. 역 좌표 출처는 아직 정해지지 않아 nullable이다."""

    __tablename__ = "stations"

    station_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_name: Mapped[str] = mapped_column(String(64))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
