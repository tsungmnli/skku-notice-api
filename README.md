# skku-notice-api

SKKU 공지 모아보기(`skku-notice-web`)의 백엔드. FastAPI.

- API 계약: [docs/api-contract.md](docs/api-contract.md)
- 라우트는 모두 `/api` 아래입니다 (ingress가 접두어를 떼지 않음). 프로브: `/api/healthz`, `/api/readyz`

## 로컬 실행

```bash
uv sync
docker compose -f compose.dev.yaml up -d      # 개발용 Postgres
cp .env.example .env
uv run python -m app.migrate                  # 테이블 생성
uv run python -m scripts.seed_dev             # 프론트 mock과 같은 36건 (여러 번 실행해도 안전)
uv run uvicorn app.main:create_app --factory --reload   # http://localhost:8000/api/docs
```

프론트는 `NEXT_PUBLIC_API_BASE=http://localhost:8000/api/v1` 로 연결합니다.
DB 구성, 마이그레이션 규칙, 홈서버 DB와의 약속은 [docs/database.md](docs/database.md).

## 검증

```bash
uv run pytest        # DB 테스트는 compose.dev.yaml 의 Postgres가 떠 있어야 실행됩니다 (없으면 건너뜀)
REQUIRE_DB=1 uv run pytest   # CI용: DB가 없으면 건너뛰지 않고 실패
uv run ruff check . && uv run ruff format --check .
```

## 구조

```
app/
  config.py        설정 + prod 환경 가드 (필수 값이 없으면 기동 실패)
  main.py          create_app() — CORS, 에러 형식, DB 연결, 라우터 마운트
  models.py        SQLAlchemy 모델 (스키마의 단일 출처)
  schemas.py       응답/요청 본문 (프론트 types.ts와 1:1)
  queries.py       읽기 쿼리 (필터·정렬·페이징). HTTP를 모름
  timeutil.py      서울 시간 도우미, 테스트에서 바꿔 끼우는 시계(get_now)
  ics.py           마감 → iCalendar(ICS) 변환. HTTP·DB를 모르는 순수 함수
  db.py            엔진/세션, /readyz 의 DB 체크
  migrate.py       python -m app.migrate  (배포 시 Sync 훅 Job이 실행)
  routers/
    health.py      /api/healthz (liveness), /api/readyz (readiness)
    v1/            articles, categories, writers, calendar
migrations/        Alembic (versions/ 는 자동 생성 후 직접 검토)
scripts/           개발 전용 도구 (seed_dev) — 이미지에는 포함되지 않음
compose.dev.yaml   개발용 Postgres. 운영 DB는 skku-notice-infra repo에서 관리
tests/             tests/db/ 는 실제 Postgres가 필요
  db/reference.py  mock 핸들러를 옮긴 기준 구현. test_articles_parity.py가 API와 대조
```

## 이미지

```bash
docker build -t tsungmn/skku-notice-api .
docker run --rm -p 8000:8000 -e APP_ENV=dev tsungmn/skku-notice-api
```

운영 환경(`APP_ENV=prod`)에서는 `DATABASE_URL`, `PUBLIC_URL`, `CORS_ORIGINS`가 없으면 서버가 일부러 뜨지 않습니다.

## 크롤러

```bash
uv run python -m app.crawl --dry-run --pages 2   # 학교 사이트에서 읽어 파싱 결과만 출력 (DB 불필요)
uv run python -m app.crawl --pages 50            # 처음 한 번: 과거 글까지 채우기
uv run python -m app.crawl                       # 평소 실행 (CRAWL_MAX_PAGES, 기본 3페이지)
```

- 대상: `https://www.skku.edu/skku/campus/skk_comm/notice01.do`. 카테고리는 글머리 `[라벨]`로 정합니다.
- `article_no` 기준 upsert라서 다시 실행해도 안전하고, 바뀐 글만 `updated_at`이 갱신됩니다.
- 마감은 먼저 제목에서 뽑습니다. 제목에 없으면 그 글의 상세 페이지를 한 번 읽어 본문의 단서어(`app/crawler/deadline_keywords.py`) 뒤에 나오는 날짜를 씁니다. 본문 자체는 저장하지 않습니다.
- 상세 페이지는 실행 한 번에 `CRAWL_DETAIL_LIMIT`(기본 30)건까지, 최근 `CRAWL_DETAIL_MAX_AGE_DAYS`(기본 60)일 이내 글만 최신순으로 읽고, 한 번 읽은 글은 다시 읽지 않습니다 (`detail_checked_at`). 접속이나 파싱에 실패한 글은 다음 실행에서 다시 시도하고, 연달아 3건이 실패하면 그 실행의 나머지는 건너뜁니다.
- 본문에 서로 다른 마감이 둘 이상이거나, `날짜까지`가 서로 다른 날짜로 둘 이상이면(학교별 마감 목록 등) 마감을 비워 둡니다. 틀린 날짜보다 빈 칸이 낫다는 판단입니다. 게시일보다 앞선 마감도 읽지 않습니다.
- 행사일은 `일시`·`일정` 같은 단서어 뒤의 날짜입니다. `11/16~11/17` 같은 범위, 또는 `일시 (4회 중 택 1)` 아래 줄지어 나오는 회차처럼 서로 다른 날이 둘 이상이면 행사일을 비워 두고, 제목의 날짜로 대신하지도 않습니다. 같은 날 여러 시각이면 날짜만 남깁니다 (본문에 행사 단서어가 아예 없을 때만 제목의 날짜를 씁니다).
- 이미 저장된 마감은 제목에서 못 뽑았다고 지워지지 않습니다. 제목에서 마감이 새로 뽑히면 본문에서 읽은 마감을 대신합니다.
- 마감이 안 뜨는 글이 보이면: `uv run python -m scripts.audit_deadlines`가 마감 없는 최근 글의 상세 페이지를 다시 읽어, 날짜가 들어간 줄과 읽었는지 여부, 날짜 앞 단어(단서어 후보)를 보여줍니다 (읽기 전용, 글당 1초 간격, 최대 100건). 마감을 뜻하는 단어는 `app/crawler/deadline_keywords.py`에 한 줄 추가하면 됩니다. 글 번호로 직접 보려면 `--article-no 141478`, 저장한 HTML은 `--html 파일.html`(네트워크 불필요).
- 일부러 비운 글 세기: 본문에 마감이 여럿이거나 행사일이 범위·여러 회차라서 비운 글은 audit이 ⚠로 표시하고, 요약에 건수와 글 번호를 보여줍니다. 이 표시는 DB에 저장되지 않아서 페이지를 다시 읽어 판단합니다. 기본은 마감이 없는 글을 읽고, `--missing event`는 행사일이 없는 글, `--missing either`는 둘 중 하나라도 없는 글을 읽습니다. `--ambiguous-only`를 붙이면 ⚠ 글(과 읽기 실패)만 자세히 출력하고, 요약은 읽은 글 전체 기준입니다. 행사일이 없는 글 대부분은 원래 행사일이 없는 글이라, `--missing event`는 100건을 읽어도 ⚠가 적게 나올 수 있습니다.
- 종료 코드: 성공 0, 실패 1 (첫 페이지가 비었거나, 페이지 구조가 바뀌었거나, 재시도 후에도 접속 실패).
- 설정은 `DATABASE_URL`만 필수입니다 (API와 달리 `PUBLIC_URL`, `CORS_ORIGINS` 불필요).

### 읽기 규칙을 바꾼 뒤, 이미 저장된 글 다시 읽기

본문 읽기 규칙(`deadline.py`, `deadline_keywords.py`)을 고쳐도 이미 읽은 글은 다시 읽히지 않습니다. 읽은 표시(`detail_checked_at`)만 풀면 다음 크롤링부터 최신순으로 다시 읽습니다. 마감과 행사일은 지우지 않고 읽은 결과로 바뀌므로, 다시 읽는 동안에도 기존 값이 보이고, 접속에 실패한 글은 옛 값이 그대로 남습니다. 수정 시각(`updated_at`, 캘린더 피드의 `LAST-MODIFIED`)은 마감이 실제로 바뀌었거나 사라진 글만 갱신됩니다. 제목에서 읽은 마감(`deadline_source = 'title'`)은 다시 읽지 않습니다.

```sql
-- 몇 건이 다시 읽히는지 먼저 확인합니다. (60은 CRAWL_DETAIL_MAX_AGE_DAYS 기본값)
SELECT count(*) FROM articles
WHERE detail_checked_at IS NOT NULL
  AND deadline_source IS DISTINCT FROM 'title'
  AND posted_on >= CURRENT_DATE - 60;

UPDATE articles SET detail_checked_at = NULL
WHERE detail_checked_at IS NOT NULL
  AND deadline_source IS DISTINCT FROM 'title'
  AND posted_on >= CURRENT_DATE - 60;
```

한 번에 읽는 양은 `CRAWL_DETAIL_LIMIT`(기본 30, 최대 200)이라, 건수가 많으면 크롤링이 몇 번 돌아야 끝납니다. 바로 끝내려면 `CRAWL_DETAIL_LIMIT=200 uv run python -m app.crawl`을 건수가 줄지 않을 때까지 반복하세요. 쪽당 `CRAWL_DELAY_SECONDS`만큼 쉬며 읽습니다.

