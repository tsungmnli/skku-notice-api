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
- 마감은 지금은 제목에서만 뽑습니다. 이미 저장된 마감은 제목에서 못 뽑았다고 지워지지 않습니다.
- 종료 코드: 성공 0, 실패 1 (첫 페이지가 비었거나, 페이지 구조가 바뀌었거나, 재시도 후에도 접속 실패).
- 설정은 `DATABASE_URL`만 필수입니다 (API와 달리 `PUBLIC_URL`, `CORS_ORIGINS` 불필요).
