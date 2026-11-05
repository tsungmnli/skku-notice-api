"""Sources: departments, sources, articles.source_id (expand step, no behaviour change)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Written out here, not imported: a migration must keep meaning what it meant when it was made.
CAMPUS_BOARD_URL = "https://www.skku.edu/skku/campus/skk_comm/notice01.do"


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_departments")),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=48), nullable=False),
        sa.Column("department_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("list_url", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level IN ('all', 'undergrad', 'grad')", name=op.f("ck_sources_level")
        ),
        sa.ForeignKeyConstraint(
            ["department_code"],
            ["departments.code"],
            name=op.f("fk_sources_department_code_departments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("slug", name=op.f("uq_sources_slug")),
    )

    # The campus-wide board is source 1: every existing article belongs to it, and the column
    # default below points at it. Other boards are added by the crawler's source sync.
    op.execute(
        sa.text(
            "INSERT INTO departments (code, name, sort_order) VALUES ('skku', '성균관대학교', 0)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO sources (id, slug, department_code, name, list_url, level, sort_order) "
            "VALUES (1, 'skku', 'skku', '공지사항', :url, 'all', 0)"
        ).bindparams(url=CAMPUS_BOARD_URL)
    )
    # The row above has an explicit id, so move the sequence past it or the next insert collides.
    op.execute(sa.text("SELECT setval(pg_get_serial_sequence('sources', 'id'), 1)"))

    # NOT NULL with a default: existing rows get 1 and the previous release (which inserts
    # articles without this column) keeps working while this migration is already applied.
    op.add_column(
        "articles",
        sa.Column("source_id", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_articles_source_id_sources"),
        "articles",
        "sources",
        ["source_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Added next to the old UNIQUE(article_no), not instead of it: the previous release still
    # upserts with ON CONFLICT (article_no), which needs that constraint. It is dropped by a
    # later migration, after the crawler has moved to (source_id, article_no).
    op.create_unique_constraint(
        op.f("uq_articles_source_id_article_no"), "articles", ["source_id", "article_no"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_articles_source_id_article_no"), "articles", type_="unique")
    op.drop_constraint(op.f("fk_articles_source_id_sources"), "articles", type_="foreignkey")
    op.drop_column("articles", "source_id")
    op.drop_table("sources")
    op.drop_table("departments")
