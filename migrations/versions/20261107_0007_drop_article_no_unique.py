"""Drop the old UNIQUE(article_no): the key is (source_id, article_no) (contract step)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05 18:00:00

Boards number their posts independently, so the same number can legitimately appear on two
boards. Migration 0006 added UNIQUE(source_id, article_no) and kept this constraint only because
the previous release upserts with ON CONFLICT (article_no). Apply this migration after the
crawler that upserts on (source_id, article_no) is deployed: with the old crawler still running,
its upsert has no matching constraint and fails.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("uq_articles_article_no"), "articles", type_="unique")


def downgrade() -> None:
    # Fails, on purpose, once two boards hold the same number: those rows cannot be told apart
    # by this key, and silently deleting one would lose an article.
    op.create_unique_constraint(op.f("uq_articles_article_no"), "articles", ["article_no"])
