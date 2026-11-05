"""Which boards the crawler reads: the registry, and copying it into the database.

The list below is the source of truth for the `departments` and `sources` tables. To add a
board, add a line here and run `python -m app.crawl --sync-sources`; no migration is needed.
Only the registry is read by the code, so a board's rows are never edited by hand.

Sync adds and updates; it never deletes. A board that is dropped from the list keeps its row
(and its articles), so remove it by setting `level="grad"` or by deleting the row yourself.
"""

from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.crawler.parser import BOARD_URL
from app.models import Department, Source

LEVELS = ("all", "undergrad", "grad")

# The campus-wide board: inserted by migration 0006, and the board every article from before
# there were sources belongs to. The crawl functions default to it.
CAMPUS_SOURCE_ID = 1


@dataclass(frozen=True)
class DepartmentDef:
    code: str
    name: str


@dataclass(frozen=True)
class SourceDef:
    slug: str
    department: str  # DepartmentDef.code
    name: str
    list_url: str  # the board's list page without a query string
    level: str = "undergrad"  # 'all' = everyone, 'undergrad', 'grad'

    @property
    def enabled(self) -> bool:
        # Graduate boards are out of scope for now: they are registered but not crawled.
        return self.level != "grad"


@dataclass(frozen=True)
class SyncResult:
    departments: int
    sources: int


# The order here is the order students see (sort_order is the position in the list).
DEPARTMENTS: tuple[DepartmentDef, ...] = (
    DepartmentDef("skku", "성균관대학교"),
    DepartmentDef("sw", "소프트웨어융합대학"),
    DepartmentDef("cse", "소프트웨어학과"),
    DepartmentDef("intelligentsw", "지능형소프트웨어학과"),
    DepartmentDef("ai", "인공지능학과"),
    DepartmentDef("sco", "글로벌융합학부"),
)

SOURCES: tuple[SourceDef, ...] = (
    # Id 1 in the database (migration 0006): every article from before sources belongs to it.
    SourceDef("skku", "skku", "공지사항", BOARD_URL, level="all"),
    SourceDef("sw", "sw", "공지사항(학부)", "https://sw.skku.edu/sw/notice.do"),
    SourceDef("cse", "cse", "공지사항", "https://cse.skku.edu/cse/notice.do"),
    SourceDef(
        "intelligentsw",
        "intelligentsw",
        "공지사항(학부)",
        "https://intelligentsw.skku.edu/intelligentsw/notice.do",
    ),
    SourceDef("ai", "ai", "공지사항", "https://ai.skku.edu/ai/notice.do"),
    # One department, five boards: the common one and one per convergence major.
    SourceDef("sco-notice", "sco", "공지사항", "https://sco.skku.edu/sco/community/notice.do"),
    SourceDef(
        "sco-data",
        "sco",
        "데이터사이언스융합전공",
        "https://sco.skku.edu/sco/community/major_data.do",
    ),
    SourceDef(
        "sco-ai", "sco", "인공지능융합전공", "https://sco.skku.edu/sco/community/major_info.do"
    ),
    SourceDef(
        "sco-culture",
        "sco",
        "컬처앤테크놀로지융합전공",
        "https://sco.skku.edu/sco/community/major_culture.do",
    ),
    SourceDef(
        "sco-magnetic",
        "sco",
        "자기설계융합전공",
        "https://sco.skku.edu/sco/community/major_magnetic.do",
    ),
)


def sync_sources(
    session: Session,
    *,
    departments: tuple[DepartmentDef, ...] = DEPARTMENTS,
    sources: tuple[SourceDef, ...] = SOURCES,
) -> SyncResult:
    """Make the `departments` and `sources` rows match the registry. Idempotent.

    Sources are matched by `slug`, so the campus board keeps id 1. The caller owns the
    transaction.
    """
    dept_stmt = pg_insert(Department).values(
        [{"code": d.code, "name": d.name, "sort_order": i} for i, d in enumerate(departments)]
    )
    session.execute(
        dept_stmt.on_conflict_do_update(
            index_elements=[Department.code],
            set_={"name": dept_stmt.excluded.name, "sort_order": dept_stmt.excluded.sort_order},
        )
    )
    src_stmt = pg_insert(Source).values(
        [
            {
                "slug": s.slug,
                "department_code": s.department,
                "name": s.name,
                "list_url": s.list_url,
                "level": s.level,
                "enabled": s.enabled,
                "sort_order": i,
            }
            for i, s in enumerate(sources)
        ]
    )
    session.execute(
        src_stmt.on_conflict_do_update(
            index_elements=[Source.slug],
            set_={
                col: getattr(src_stmt.excluded, col)
                for col in ("department_code", "name", "list_url", "level", "enabled", "sort_order")
            },
        )
    )
    return SyncResult(departments=len(departments), sources=len(sources))
