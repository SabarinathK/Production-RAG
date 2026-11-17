"""Initial Database Migration Schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-09-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=True),
        sa.Column('trace_id', sa.String(length=64), nullable=True),
        sa.Column('endpoint', sa.String(length=200), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('execution_time_ms', sa.Float(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        if_not_exists=True
    )
    op.create_table(
        'document_metadata',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('s3_key', sa.String(length=500), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='PROCESSING'),
        sa.Column('meta_info', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        if_not_exists=True
    )
    # Create index only if it doesn't exist
    op.create_index(
        op.f("ix_document_metadata_content_hash"),
        "document_metadata",
        ["content_hash"],
        unique=False,
    )

def downgrade() -> None:
    op.drop_index(op.f('ix_document_metadata_content_hash'), table_name='document_metadata')
    op.drop_table('document_metadata')
    op.drop_table('audit_logs')
