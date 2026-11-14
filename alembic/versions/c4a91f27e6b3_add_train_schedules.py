"""add train_schedules

OA-101 열차 시간표 저장 테이블. 시각은 서비스일 0시 기준 정수 초로 저장한다.
기존 테이블은 건드리지 않는다.

Revision ID: c4a91f27e6b3
Revises: b7e2c91a4d10
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a91f27e6b3'
down_revision: Union[str, Sequence[str], None] = 'b7e2c91a4d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'train_schedules',
        sa.Column('schedule_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('station_line_id', sa.Integer(), nullable=False),
        sa.Column('service_day_type', sa.String(length=16), nullable=False),
        sa.Column('direction', sa.Integer(), nullable=False),
        sa.Column('train_no', sa.String(length=16), nullable=False),
        sa.Column('arrival_time', sa.Integer(), nullable=True),
        sa.Column('departure_time', sa.Integer(), nullable=True),
        sa.Column('origin_station_code', sa.String(length=16), nullable=True),
        sa.Column('destination_station_code', sa.String(length=16), nullable=True),
        sa.Column('is_express', sa.Boolean(), nullable=False),
        sa.Column('fetched_at', sa.String(length=32), nullable=False),
        sa.CheckConstraint("direction IN (1, 2)", name='ck_train_schedules_direction'),
        sa.CheckConstraint(
            "service_day_type IN ('WEEKDAY', 'SATURDAY', 'HOLIDAY')",
            name='ck_train_schedules_day_type',
        ),
        sa.ForeignKeyConstraint(['station_line_id'], ['station_lines.station_line_id']),
        sa.PrimaryKeyConstraint('schedule_id'),
        sa.UniqueConstraint(
            'station_line_id', 'service_day_type', 'direction', 'train_no',
            name='uq_train_schedules_stop',
        ),
    )
    op.create_index(
        'ix_train_schedules_lookup',
        'train_schedules',
        ['station_line_id', 'service_day_type', 'direction', 'departure_time'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_train_schedules_lookup', table_name='train_schedules')
    op.drop_table('train_schedules')
