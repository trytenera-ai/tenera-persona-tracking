"""Add thumbnail_url to sessions

Revision ID: 003
Revises: 002
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("thumbnail_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "thumbnail_url")
