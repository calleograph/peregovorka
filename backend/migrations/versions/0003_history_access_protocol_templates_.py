"""history access, protocol templates, grants, recording export

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 17:40:21.312784
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Доступ к истории, шаблоны протоколов, разрешения на встречи, выгрузка записей (autogenerate, вычитана вручную).
    op.create_table('protocol_templates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('instruction', sa.Text(), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('owner_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('protocol_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_protocol_templates_owner_user_id'), ['owner_user_id'], unique=False)

    op.create_table('meeting_grants',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('granted_by', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('meeting_id', 'user_id', name='uq_meeting_grant')
    )
    with op.batch_alter_table('meeting_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meeting_grants_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_grants_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('protocols', schema=None) as batch_op:
        batch_op.add_column(sa.Column('instruction', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('title', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('edited_by', sa.String(length=300), nullable=True))

    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('export_status', sa.String(length=20), server_default='local', nullable=False))
        batch_op.add_column(sa.Column('export_location', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('export_error', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('exported_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('history_access', sa.String(length=20), server_default='admin', nullable=False))



def downgrade() -> None:
    # Доступ к истории, шаблоны протоколов, разрешения на встречи, выгрузка записей (autogenerate, вычитана вручную).
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('history_access')

    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.drop_column('exported_at')
        batch_op.drop_column('export_error')
        batch_op.drop_column('export_location')
        batch_op.drop_column('export_status')

    with op.batch_alter_table('protocols', schema=None) as batch_op:
        batch_op.drop_column('edited_by')
        batch_op.drop_column('edited_at')
        batch_op.drop_column('title')
        batch_op.drop_column('instruction')

    with op.batch_alter_table('meeting_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_meeting_grants_user_id'))
        batch_op.drop_index(batch_op.f('ix_meeting_grants_meeting_id'))

    op.drop_table('meeting_grants')
    with op.batch_alter_table('protocol_templates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_protocol_templates_owner_user_id'))

    op.drop_table('protocol_templates')
