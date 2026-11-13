from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Line(Base):
    """노선. line_id는 "1"~"8"이다."""

    __tablename__ = "lines"

    line_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    line_name: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
