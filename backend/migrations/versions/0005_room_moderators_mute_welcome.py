"""руководители комнат, микрофон по умолчанию выключен, приветствие

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Только добавление: существующие комнаты остаются прежними (микрофон при входе включается как раньше).
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mute_on_join', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('welcome_message', sa.Text(), nullable=True))

    op.create_table('room_moderators',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('subject_type', sa.String(length=10), nullable=False),
    sa.Column('subject_ref', sa.String(length=512), nullable=False),
    sa.Column('display_name', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('room_id', 'subject_type', 'subject_ref', name='uq_room_moderator_subject')
    )
    with op.batch_alter_table('room_moderators', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_room_moderators_room_id'), ['room_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('room_moderators', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_room_moderators_room_id'))
    op.drop_table('room_moderators')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('welcome_message')
        batch_op.drop_column('mute_on_join')
