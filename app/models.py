from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Stable constraint names keep Alembic migrations deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Category(Base):
    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # Display order in the filter UI (and the order of Article.categories).
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")


article_categories = Table(
    "article_categories",
    Base.metadata,
    Column("article_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("category_code", ForeignKey("categories.code", ondelete="RESTRICT"), primary_key=True),
    # Serves "articles in category X" (the primary key already covers the reverse direction).
    Index("ix_article_categories_category_code_article_id", "category_code", "article_id"),
)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The school board's own post number; the crawler upserts on it.
    article_no: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(Text)
    writer: Mapped[str] = mapped_column(String(100))
    posted_on: Mapped[date] = mapped_column(Date)
    display_no: Mapped[int | None] = mapped_column(Integer)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    view_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    # Stored as timestamptz; 23:59 KST means "date only" (see docs/api-contract.md).
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    categories: Mapped[list[Category]] = relationship(
        secondary=article_categories, order_by=Category.sort_order, lazy="selectin"
    )

    __table_args__ = (
        Index("ix_articles_posted_on_id", "posted_on", "id"),
        Index("ix_articles_writer", "writer"),
        # Calendar and deadline sorting only look at articles that have a deadline.
        Index(
            "ix_articles_deadline_at",
            "deadline_at",
            postgresql_where=text("deadline_at IS NOT NULL"),
        ),
    )
