"""add station_line_id to station_external_ids

OA-12035 역번호와 OA-15994 STN_CD는 노선별 코드다. 외부 코드가 어느 노선 행에 속하는지
station_line_id로 연결하고, 복합 FK (station_line_id, station_id)로 모순을 막는다.

데이터 보존
- 기존 행은 삭제하거나 바꾸지 않는다. 새 컬럼은 NULL 허용이며 기존 행은 NULL로 남는다.
- station_lines는 테이블을 재구성하지 않고 유니크 인덱스만 추가한다
  (station_edges가 station_lines를 참조하므로 재구성하면 FK 검사에 걸릴 수 있다).
- station_external_ids는 다른 테이블이 참조하지 않아 배치 재구성이 안전하다.

Revision ID: b7e2c91a4d10
Revises: 8d70068f68f4
Create Date: 2026-11-13 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2c91a4d10'
down_revision: Union[str, Sequence[str], None] = '8d70068f68f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 복합 FK의 부모 키. SQLite는 부모 키에 UNIQUE 인덱스가 있으면 된다.
    op.create_index(
        'uq_station_lines_id_station',
        'station_lines',
        ['station_line_id', 'station_id'],
        unique=True,
    )

    with op.batch_alter_table('station_external_ids', schema=None) as batch_op:
        batch_op.add_column(sa.Column('station_line_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_station_external_ids_station_line_id'),
            ['station_line_id'],
            unique=False,
        )
        batch_op.create_foreign_key(
            'fk_station_external_ids_station_line',
            'station_lines',
            ['station_line_id', 'station_id'],
            ['station_line_id', 'station_id'],
        )


def downgrade() -> None:
    """Downgrade schema. 추가한 제약·인덱스·컬럼만 제거하고 기존 행은 유지한다."""
    with op.batch_alter_table('station_external_ids', schema=None) as batch_op:
        batch_op.drop_constraint('fk_station_external_ids_station_line', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_station_external_ids_station_line_id'))
        batch_op.drop_column('station_line_id')

    op.drop_index('uq_station_lines_id_station', table_name='station_lines')
