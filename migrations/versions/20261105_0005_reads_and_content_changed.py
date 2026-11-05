"""NEW/UPDATED badges: articles.content_changed_at, view_count_synced_at, user_article_reads

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows stay NULL on purpose: "changed now" would light up UPDATED on every article
    # the moment this ships. Readers treat NULL as "never changed" and use `posted_on`.
    op.add_column(
        "articles", sa.Column("content_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    # NOT NULL with a default, so the previous API release (which does not know the column)
    # keeps inserting articles while this migration is already applied. Existing rows get the
    # time of the migration: their views are refreshed within a day, not on the next crawl.
    op.add_column(
        "articles",
        sa.Column(
            "view_count_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "user_article_reads",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name=op.f("fk_user_article_reads_article_id_articles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_article_reads_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "article_id", name=op.f("pk_user_article_reads")),
    )


def downgrade() -> None:
    op.drop_table("user_article_reads")
    op.drop_column("articles", "view_count_synced_at")
    op.drop_column("articles", "content_changed_at")
