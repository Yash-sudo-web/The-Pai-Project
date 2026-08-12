"""device tokens and nudge deliveries

Revision ID: c7f2ab914e30
Revises: 8a499dd3840a
Create Date: 2026-08-10 11:04:52.310884
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c7f2ab914e30'
down_revision = '8a499dd3840a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('device_tokens',
    sa.Column('token', sa.Text(), nullable=False),
    sa.Column('user_id', sa.Text(), nullable=False),
    sa.Column('platform', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("platform IN ('ios', 'android')", name='ck_device_platform'),
    sa.PrimaryKeyConstraint('token')
    )
    with op.batch_alter_table('device_tokens', schema=None) as batch_op:
        batch_op.create_index('ix_device_tokens_user', ['user_id'], unique=False)

    op.create_table('nudge_deliveries',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('user_id', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('nudge_deliveries', schema=None) as batch_op:
        batch_op.create_index(
            'ix_nudge_deliveries_user_kind_sent',
            ['user_id', 'kind', sa.literal_column('sent_at DESC')],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('nudge_deliveries', schema=None) as batch_op:
        batch_op.drop_index('ix_nudge_deliveries_user_kind_sent')

    op.drop_table('nudge_deliveries')

    with op.batch_alter_table('device_tokens', schema=None) as batch_op:
        batch_op.drop_index('ix_device_tokens_user')

    op.drop_table('device_tokens')
