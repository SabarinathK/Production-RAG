"""Add user_id column to document_metadata

Revision ID: 004_add_user_id_to_docs
Revises: 003_add_users_table
Create Date: 2026-09-27
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_add_user_id_to_docs'
down_revision: Union[str, None] = '003_add_users_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = [c['name'] for c in inspector.get_columns('document_metadata')] if 'document_metadata' in inspector.get_table_names() else []
    if 'user_id' not in existing_cols:
        op.add_column(
            'document_metadata',
            sa.Column('user_id', sa.String(length=100), nullable=True)
        )
    existing_indexes = [idx['name'] for idx in inspector.get_indexes('document_metadata')] if 'document_metadata' in inspector.get_table_names() else []
    if 'ix_document_metadata_user_id' not in existing_indexes:
        op.create_index(op.f('ix_document_metadata_user_id'), 'document_metadata', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_document_metadata_user_id'), table_name='document_metadata')
    op.drop_column('document_metadata', 'user_id')
