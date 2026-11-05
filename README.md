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
uv run uvicorn app.main:create_app --factory --reload   # http://127.0.0.1:8000/api/docs
```

프론트는 `NEXT_PUBLIC_API_BASE=http://localhost:8000/api/v1` 로 연결합니다. (`SMTP_HOST` 가 없으면 인증·재설정 메일의 링크가 이 서버 콘솔에 출력됩니다.) 쿠키 때문에 웹과 API는 같은 호스트 이름(`localhost`)으로 열어야 합니다 (`127.0.0.1` 금지).
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

운영 환경(`APP_ENV=prod`)에서는 `DATABASE_URL`, `PUBLIC_URL`, `CORS_ORIGINS`, 계정 기능용 값
(`SESSION_SECRET`, `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD`, `TURNSTILE_SECRET_KEY`, `TRUSTED_PROXY_HOPS`)이
없으면 서버가 일부러 뜨지 않습니다. 전체 목록은 `.env.example`.

## 크롤러

```bash
uv run python -m app.crawl --dry-run --pages 2   # 학교 사이트에서 읽어 파싱 결과만 출력 (DB 불필요, 등록된 모든 게시판)
uv run python -m app.crawl --dry-run --source cse # 게시판 하나만 (slug는 app/crawler/sources.py)
uv run python -m app.crawl --pages 50 --no-incremental   # 처음 한 번: 과거 글까지 채우기
uv run python -m app.crawl                       # 평소 실행: 활성 게시판을 차례로 (게시판마다 CRAWL_MAX_PAGES, 기본 3페이지)
uv run python -m app.crawl --source sco-data --pages 50 --no-incremental   # 새 게시판 하나만 과거 글까지 채우기
uv run python -m app.crawl --incremental         # 새 글이 없는 쪽에서 멈추는 가벼운 실행 (아래)
uv run python -m app.crawl --sync-sources        # 게시판 목록(app/crawler/sources.py)을 DB에 반영하고 끝냄 (크롤링 안 함)
```

- 대상: DB의 `sources` 표에서 `enabled`인 게시판 전부(`--sync-sources`로 채웁니다. 한 번도 안 돌렸다면 학교 전체 공지 하나뿐입니다). 학교 전체 공지는 `https://www.skku.edu/skku/campus/skk_comm/notice01.do`. 카테고리는 글머리 `[라벨]`로 정합니다.
- **게시판이 여럿일 때**: 게시판을 `sort_order` 순서로 하나씩 읽습니다. 한 게시판이 실패해도(접속 실패, 목록이 비어 있음, DB 오류) 나머지는 계속 읽고, 상세 읽기와 계정 정리까지 마친 뒤 **종료 코드 1**로 끝납니다. 어느 게시판이 실패했는지는 로그의 `board <slug> failed`와 `N of M boards failed: ...`에 나옵니다. 종료 코드는 0 = 모두 성공, 1 = 하나라도 실패하거나 건너뜀, 2 = `--source`가 없는 게시판이거나 꺼진 게시판.
- `--source <slug>`는 그 게시판의 목록과 그 게시판 글의 상세 페이지만 읽습니다.
- `article_no` 기준 upsert라서 다시 실행해도 안전합니다. 쓰기는 이렇게 줄입니다.
  - 제목·작성자·게시일·고정 여부·마감 등 **눈에 보이는 값**이 바뀐 글만 쓰고 `updated_at`(캘린더 피드의 `LAST-MODIFIED`)을 갱신합니다.
  - **제목**이 바뀐 글은 `content_changed_at`도 갱신합니다. 웹의 UPDATED 표시가 이 값을 씁니다. 마감을 다시 읽어 바뀌거나 고정이 풀린 것은 수정으로 치지 않습니다. 본문·첨부만 바뀐 글은 아직 감지하지 못합니다.
  - **조회수**만 바뀐 글은 마지막 갱신(`view_count_synced_at`)으로부터 하루가 지났을 때만 쓰고, 그때도 `updated_at`은 올리지 않습니다. 그래서 조회수순 정렬은 최대 하루 늦습니다.
  - 파서를 고쳐 제목 문자열이 달라지게 되면(공백 처리 등) 최근 글 전부가 "제목이 바뀐 글"로 보여 UPDATED가 한꺼번에 뜹니다. 그런 변경을 배포하기 전에 `UPDATE articles SET content_changed_at = NULL` 로 되돌릴 준비를 하세요.
- **가벼운 실행(`--incremental` 또는 `CRAWL_INCREMENTAL=true`)**: 목록을 한 쪽씩 읽다가, DB에 없는 글이 하나도 없는 쪽이 나오면 거기서 멈춥니다. 조용한 날에는 목록 요청이 3건이 아니라 1건입니다. 대신 2쪽 이후 글의 제목 수정과 조회수 갱신은 이 실행에서는 보지 못합니다. 그래서 **자주 도는 CronJob은 `--incremental`, 하루 한 번 도는 CronJob은 기본(전체)**으로 두는 걸 권합니다. 아무것도 안 붙이면 예전처럼 항상 전체를 읽습니다. 과거 글 채우기(`--pages 50`)는 반드시 `--no-incremental`로 하세요(이미 아는 글이 나오면 멈추므로).
- **학교 서버 예절**: 요청 사이에 `CRAWL_DELAY_SECONDS`(기본 1초)에 `CRAWL_JITTER`(기본 25%)만큼 무작위 시간을 더해 쉽니다. 429/5xx에 `Retry-After`가 오면 그만큼 기다리고(재시도 간격보다 짧으면 기존 간격), 60초를 넘게 기다리라고 하면 그 글은 포기하고 다음 실행으로 넘깁니다. 한 번의 실행이 보내는 요청은 재시도를 포함해 `CRAWL_MAX_REQUESTS`(기본 150)건이 상한이고, 그중 **목록 읽기는 모든 게시판을 합쳐 `CRAWL_LIST_REQUESTS`(기본 60)건까지**입니다. 상세 읽기는 나머지 상한 안에서 씁니다. 그래서 게시판이 많아도 목록이 상세 읽기를 굶기지 않습니다(게시판 10개 × 3쪽 = 30건이므로 60이면 재시도 여유가 있습니다). 목록 상한에 닿으면 그 뒤 게시판은 읽지 않고 건너뛰며(로그에 `skipped: request budget used up`, 종료 코드 1) 다음 실행이 이어서 읽습니다. 한 게시판의 목록이 실패하면 그 게시판만 실패로 치고, 상세 읽기는 상한에 닿으면 조용히 멈춰 남은 글을 다음 실행으로 미룹니다(로그에 `stopped: request budget used up`). 게시판 하나를 `--pages 50`으로 채울 때는 `CRAWL_LIST_REQUESTS`와 `CRAWL_MAX_REQUESTS`를 함께 올리세요. User-Agent에 연락처를 넣고 싶으면 `CRAWL_USER_AGENT`를 설정하세요.
- 크롤링이 끝날 때 만료된 세션·메일 토큰과 함께, 표시 기간(7일)이 끝난 글의 읽음 기록을 지웁니다 (`app/auth/cleanup.py`).
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

한 번에 읽는 양은 `CRAWL_DETAIL_LIMIT`(기본 30, 최대 200)이라, 건수가 많으면 크롤링이 몇 번 돌아야 끝납니다. 바로 끝내려면 `CRAWL_DETAIL_LIMIT=200 CRAWL_MAX_REQUESTS=300 uv run python -m app.crawl`을 건수가 줄지 않을 때까지 반복하세요(요청 상한도 같이 올려야 합니다). 쪽당 `CRAWL_DELAY_SECONDS`만큼 쉬며 읽습니다.

### 본문 변경 감지 (아직 꺼져 있음)

지금 UPDATED는 **제목이 바뀐 글**만 잡습니다. 제목은 그대로이고 본문·첨부만 고치는 경우(학점 교류 안내 같은)를 잡으려면 상세 페이지를 다시 읽어 본문 해시를 비교해야 하는데, 페이지 안의 잡음(조회수, 날짜 도장, 배너) 때문에 **고치지 않은 글이 UPDATED로 뜨는 오탐**이 생길 수 있습니다. 그래서 해시를 DB에 저장하기 전에 읽기 전용 스크립트로 오탐률부터 잽니다. DB를 쓰지 않으며 `app/crawler/fingerprint.py`의 지문을 씁니다.

```bash
uv run python -m scripts.audit_body_hash --twice                  # 최근 14일 글 30건을 연달아 두 번 읽어 비교
uv run python -m scripts.audit_body_hash --snapshot body.json     # 첫 실행은 저장, 이후 실행은 저장본과 비교
uv run python -m scripts.audit_body_hash --snapshot body.json --update   # 비교한 뒤 새 상태로 다시 저장
uv run python -m scripts.audit_body_hash --article-no 141478 --twice     # 글 번호 지정 (DB 불필요)
```

- `--twice`: 두 번 읽은 해시가 다르면, 아무도 안 고쳤는데 값이 움직이는 것(잡음)입니다. **본문 0건**이면 본문 해시는 오탐을 만들지 않습니다. `본문 밖` 건수는 본문 바깥(첨부 목록 등) 잡음입니다.
- `--snapshot`: 몇 시간~며칠 뒤 다시 돌려 "본문 변경"으로 뜬 글을 눈으로 보고, 진짜 수정인지 잡음인지 셉니다. 저장 파일에는 공지 본문이 들어 있으니 git에 올리지 마세요.
- 저장된 테스트용 상세 페이지에는 첨부파일 목록 마크업이 없어서, 첨부가 `본문 밖` 해시에 잡히는지는 첨부가 있는 글 하나로 직접 확인해야 합니다.
- 켜는 기준(제안): `--twice`에서 본문 오탐이 0건, `--snapshot` 며칠치에서 "본문 변경"이 대부분 진짜 수정일 때. 그다음에 `body_hash`/`body_checked_at` 컬럼과 재확인 주기(게시·수정 3일 이내는 6시간마다, 14일까지는 하루 한 번, 실행당 상한)를 넣습니다.
