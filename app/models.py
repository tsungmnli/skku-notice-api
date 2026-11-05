import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class Department(Base):
    """What a student picks in the UI (a department or college). One department can own several
    boards: the global convergence school (sco) has a common one plus one per major."""

    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")


class Source(Base):
    """One notice board the crawler reads. Id 1 is the campus-wide board, which every article
    that existed before sources were introduced belongs to (see migration 0006)."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable handle used in config and URLs, e.g. 'skku', 'sw', 'sco-major-data'.
    slug: Mapped[str] = mapped_column(String(48), unique=True)
    department_code: Mapped[str] = mapped_column(
        ForeignKey("departments.code", ondelete="RESTRICT")
    )
    # The board's own title, e.g. '공지사항', '데이터사이언스융합전공'.
    name: Mapped[str] = mapped_column(String(64))
    # The board's list page without a query string; view links and paging are built on it.
    list_url: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(16))
    # A disabled source keeps its articles but is skipped by the crawler.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("level IN ('all', 'undergrad', 'grad')", name="level"),)


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
    # Which board the post is on. The server default (the campus-wide board) lets the previous
    # release, which does not know this column, keep inserting while 0006 is already applied.
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), server_default="1"
    )
    # The school board's own post number. Boards number their posts independently, so the key is
    # (source_id, article_no) (see the unique constraint below), never the number alone.
    article_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    writer: Mapped[str] = mapped_column(String(100))
    posted_on: Mapped[date] = mapped_column(Date)
    display_no: Mapped[int | None] = mapped_column(Integer)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    view_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    # Stored as timestamptz; 23:59 KST means "date only" (see docs/api-contract.md).
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the event itself takes place (a talk, a work day); never a deadline. Same 23:59 rule.
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Where `deadline_at` came from: 'title' or 'body'. NULL while there is no deadline.
    deadline_source: Mapped[str | None] = mapped_column(String(8))
    # The body line a 'body' deadline was read from (max 100 chars); the body itself is not stored.
    deadline_evidence: Mapped[str | None] = mapped_column(String(100))
    # Set once the detail page has been fetched and read, so it is not requested again.
    detail_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the crawler last saw the post's content change (today: its title). This is what the
    # UPDATED badge hangs on, so view counts, pinning and re-reading rules never touch it.
    # NULL = never seen changed; readers then fall back to `posted_on`.
    content_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When `view_count` was last rewritten. Views only grow, so writing every crawl would update
    # the row each time; the crawler refreshes them at most once a day (see crawler/store.py).
    view_count_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    categories: Mapped[list[Category]] = relationship(
        secondary=article_categories, order_by=Category.sort_order, lazy="selectin"
    )
    # The board row, read in the same query as the article (an inner join: `source_id` is NOT
    # NULL), so serving a page of articles costs no extra query per post. `ArticleOut` reads
    # the board's slug and its department from it.
    source: Mapped[Source] = relationship(lazy="joined", innerjoin=True)

    __table_args__ = (
        UniqueConstraint("source_id", "article_no", name="uq_articles_source_id_article_no"),
        Index("ix_articles_posted_on_id", "posted_on", "id"),
        Index("ix_articles_writer", "writer"),
        # Calendar and deadline sorting only look at articles that have a deadline.
        Index(
            "ix_articles_deadline_at",
            "deadline_at",
            postgresql_where=text("deadline_at IS NOT NULL"),
        ),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    # Always stored lowercase (see the CHECK below), so UNIQUE is also case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    # NULL for accounts that only ever signed in with Google.
    password_hash: Mapped[str | None] = mapped_column(Text)
    # Google's stable account id (`sub`); the e-mail address can change, this cannot.
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    name: Mapped[str | None] = mapped_column(String(100))
    # NULL until the address is confirmed; an unverified account cannot log in.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Settings that follow the user between devices (see app/auth/preferences.py).
    preferences: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)


class UserArticleRead(Base):
    """A signed-in user opened this notice. Drives the NEW / UPDATED badges across devices.

    Only notices still inside their display window are kept (see app/freshness.py): a row is
    refused when the notice is already past it and the crawler's housekeeping deletes it when it
    gets there, so the table stays small however long an account lives.
    """

    __tablename__ = "user_article_reads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    # The latest time the user opened it. UPDATED compares this with `content_changed_at`.
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """A login. Only the SHA-256 (hex) of the random token is stored, never the token."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Sliding expiry: bumped (at most once a day) when the session is used.
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Which kind of client logged in; lets us list and revoke devices.
    client: Mapped[str] = mapped_column(String(16), server_default="web")
    # A short summary of the User-Agent for the device list, not the raw header.
    user_agent: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        CheckConstraint("client IN ('web', 'android', 'ios')", name="client_known"),
        Index("ix_sessions_user_id", "user_id"),
        # The cleanup job deletes by expiry.
        Index("ix_sessions_expires_at", "expires_at"),
    )


class EmailToken(Base):
    """A one-time link sent by mail (e-mail verification or password reset), stored hashed."""

    __tablename__ = "email_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set when the link is used; a used token never works again.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("purpose IN ('verify', 'reset')", name="purpose_known"),
        # One live token per user and purpose. Issuing a new one must first retire the old one
        # (set used_at), which is also what makes "resend" invalidate the earlier link.
        Index(
            "uq_email_tokens_user_id_purpose_unused",
            "user_id",
            "purpose",
            unique=True,
            postgresql_where=text("used_at IS NULL"),
        ),
        Index("ix_email_tokens_expires_at", "expires_at"),
    )
