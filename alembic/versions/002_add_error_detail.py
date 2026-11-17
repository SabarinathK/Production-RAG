"""Add error_detail column to document_metadata

Revision ID: 002_add_error_detail
Revises: 001_initial_schema
Create Date: 2026-09-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_add_error_detail'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = [c['name'] for c in inspector.get_columns('document_metadata')] if 'document_metadata' in inspector.get_table_names() else []
    if 'error_detail' not in existing_cols:
        op.add_column(
            'document_metadata',
            sa.Column('error_detail', sa.Text(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column('document_metadata', 'error_detail')
