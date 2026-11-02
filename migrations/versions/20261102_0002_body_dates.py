"""dates read from the notice body

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("event_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("articles", sa.Column("deadline_source", sa.String(length=8), nullable=True))
    op.add_column("articles", sa.Column("deadline_evidence", sa.String(length=100), nullable=True))
    op.add_column(
        "articles", sa.Column("detail_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Every deadline stored so far was read from a title.
    op.execute("UPDATE articles SET deadline_source = 'title' WHERE deadline_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("articles", "detail_checked_at")
    op.drop_column("articles", "deadline_evidence")
    op.drop_column("articles", "deadline_source")
    op.drop_column("articles", "event_at")
