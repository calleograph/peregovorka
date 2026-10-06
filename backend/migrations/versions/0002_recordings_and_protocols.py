"""recordings and protocols

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06 12:29:17.780771
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Записи аудио и протоколы встреч (создана autogenerate, вычитана вручную).
    op.create_table('protocols',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('created_by', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('protocols', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_protocols_meeting_id'), ['meeting_id'], unique=False)

    op.create_table('recordings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('participant_identity', sa.String(length=80), nullable=False),
    sa.Column('path', sa.String(length=1000), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recordings_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_recordings_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_recordings_room_id'), ['room_id'], unique=False)



def downgrade() -> None:
    # Записи аудио и протоколы встреч (создана autogenerate, вычитана вручную).
    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recordings_room_id'))
        batch_op.drop_index(batch_op.f('ix_recordings_meeting_id'))
        batch_op.drop_index(batch_op.f('ix_recordings_created_at'))

    op.drop_table('recordings')
    with op.batch_alter_table('protocols', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_protocols_meeting_id'))

    op.drop_table('protocols')
