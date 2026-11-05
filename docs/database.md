# 데이터베이스

이 repo(API)와 홈서버의 DB 사이의 **약속**을 적은 문서입니다.
DB 서버 자체(Postgres 설치, 볼륨, 백업, 방화벽)는 이 repo가 아니라 `skku-notice-infra` repo의 `db/skku-notice/` 에서 관리합니다.

## 소유권

| 무엇                                          | 어디서                                       |
| --------------------------------------------- | -------------------------------------------- |
| 스키마(테이블, 인덱스)                        | **이 repo** — `app/models.py`, `migrations/` |
| DB 서버, 데이터베이스/역할 생성, 백업, 방화벽 | `skku-notice-infra` repo                     |

스키마는 코드 버전과 함께 움직이므로 마이그레이션은 이 repo에 둡니다. DB 쪽에서는 "앱 역할이 데이터베이스를 소유한다"만 보장하면 됩니다.

## DB가 보장해야 하는 것

- 데이터베이스 `skku`, 로그인 역할 `skku_app` (데이터베이스 소유자). 소유자여야 Alembic이 테이블을 만들 수 있습니다.
- Postgres 15 이상. 확장(extension)은 쓰지 않으므로 슈퍼유저 권한이 필요 없습니다.
- 클러스터에서 5432 포트로 접근 가능 (방화벽은 클러스터 IP만 허용).

`DATABASE_URL` 형식 (Vault `kv/skku-notice`에 저장):

```
postgresql://skku_app:<비밀번호>@<홈서버IP>:5432/skkunoti
```

`postgres://` 로 시작해도 앱이 `postgresql+psycopg://` 로 변환합니다.

## 마이그레이션

```bash
uv run python -m app.migrate        # = alembic upgrade head
uv run alembic revision --autogenerate -m "설명"   # 모델 변경 후 새 마이그레이션 생성
uv run alembic downgrade -1
```

- 앱은 기동할 때 마이그레이션하지 않습니다. 배포 시에는 ArgoCD 마이그레이션 **Job**(Sync 훅, wave 0 — Secret 뒤, Deployment 앞)이 `python -m app.migrate` 를 실행합니다.
- 마이그레이션 파일은 생성 후 직접 읽어 보세요. 자동 생성은 컬럼 이름 변경을 "삭제 + 추가"로 만들어 데이터를 잃게 할 수 있습니다.
- 테스트(`tests/test_migrations.py`)가 "모델과 마이그레이션이 어긋나지 않는지"를 검사합니다. 모델을 고치고 마이그레이션을 빠뜨리면 실패합니다.

## 스키마

```
categories(code PK, name, sort_order)
departments(code PK, name, sort_order)
sources(id PK, slug UNIQUE, department_code → departments ON DELETE RESTRICT, name, list_url,
        level CHECK in ('all','undergrad','grad'), enabled, sort_order, created_at)
articles(id PK, source_id → sources ON DELETE RESTRICT DEFAULT 1, article_no,
         UNIQUE(source_id, article_no), title, writer, posted_on, display_no, is_pinned,
         view_count, url, deadline_at timestamptz, event_at timestamptz,
         deadline_source, deadline_evidence, detail_checked_at timestamptz,
         content_changed_at timestamptz, view_count_synced_at timestamptz NOT NULL DEFAULT now(),
         created_at, updated_at)
article_categories(article_id → articles ON DELETE CASCADE,
                   category_code → categories ON DELETE RESTRICT)   -- PK(article_id, category_code)
users(id uuid PK, email UNIQUE, password_hash, google_sub UNIQUE, name,
      email_verified_at timestamptz, created_at, preferences jsonb NOT NULL DEFAULT '{}')
sessions(token_hash PK, user_id → users ON DELETE CASCADE, created_at, expires_at,
         last_used_at, client, user_agent)
email_tokens(token_hash PK, user_id → users ON DELETE CASCADE, purpose, created_at,
             expires_at, used_at)
user_article_reads(user_id → users ON DELETE CASCADE, article_id → articles ON DELETE CASCADE,
                   read_at timestamptz)   -- PK(user_id, article_id)
```

- `sources` 는 크롤러가 읽는 게시판 하나입니다. 학생이 UI에서 고르는 단위는 `departments` 이고, 한 학과가 게시판을 여러 개 가질 수 있습니다
  (글로벌융합학부는 공통 공지 + 전공별 4개). `sources.id = 1` 은 학교 전체 공지(`notice01.do`)이고 마이그레이션 0006이 직접 넣습니다.
  다른 게시판은 크롤러의 소스 동기화가 채웁니다.
- `articles.source_id` 의 기본값 1 은 일부러 둔 것입니다. 0006을 먼저 적용한 상태에서도 이전 릴리스(이 컬럼을 모르는)가 글을 계속 넣을 수 있고,
  기존 글은 모두 학교 전체 공지 소속이 됩니다. 게시판마다 글 번호를 따로 매기므로 진짜 키는 `(source_id, article_no)` 입니다.
  `article_no` 단독 UNIQUE 는 0007이 지웠습니다. 이전 릴리스의 `ON CONFLICT (article_no)` 가 그 제약을 필요로 했기 때문에 0006까지는
  남겨 두었습니다. **0007은 `(source_id, article_no)` 로 upsert 하는 크롤러를 먼저 배포한 뒤에 적용하세요.** 옛 크롤러가 도는 중에 적용하면
  그 upsert가 실패합니다. 0007을 되돌리면(`downgrade`) 두 게시판이 같은 번호를 가진 글이 있을 때 단독 UNIQUE를 다시 만들 수 없어 실패합니다.
- 계정 테이블: `users.email` 은 항상 소문자(CHECK)라서 UNIQUE가 대소문자를 구분하지 않습니다.
  `password_hash` 가 NULL이면 Google로만 가입한 계정입니다. `email_verified_at` 이 NULL이면 로그인할 수 없습니다.
- `users.preferences` 는 기기를 오가는 설정(캘린더 필터)을 담은 JSON 한 덩어리입니다. 스키마는 `app/auth/preferences.py` 가 정하고, 읽을 때는 못 읽는 섹션을 버리고 쓸 때는 엄격하게 검사합니다. 컬럼이 NOT NULL + 기본값이라 이전 버전 API 가 돌아가는 중에 마이그레이션해도 안전합니다.
- `sessions.token_hash`, `email_tokens.token_hash` 는 무작위 토큰의 SHA-256(hex)만 저장합니다. 토큰 원문은 DB에 없습니다.
  `sessions.client` 는 `web`/`android`/`ios` 중 하나입니다.
- `email_tokens` 는 (user, purpose)당 **미사용** 토큰이 하나만 있을 수 있습니다(부분 유니크 인덱스).
  새 토큰을 발급하기 전에 이전 토큰을 `used_at` 으로 닫아야 합니다.
- UUID 기본값 `gen_random_uuid()` 는 PostgreSQL 13+ 내장 함수라 확장이 필요 없습니다.
- `writer` 는 별도 테이블 없이 문자열 컬럼입니다. `/writers` 는 `GROUP BY writer` 로 계산합니다.
- `deadline_at` 이 23:59(KST)면 "시간 없는 날짜 마감"입니다. `event_at`(행사·근무일)도 같은 규칙입니다.
- `deadline_source` 는 마감을 어디서 읽었는지(`title` / `body`)이고, `deadline_evidence` 는 본문에서 읽은
  경우 그 줄(100자 이내)입니다. 본문 전체는 저장하지 않습니다.
- `detail_checked_at` 이 채워진 글은 상세 페이지를 이미 읽은 것이라 다시 요청하지 않습니다.
- `content_changed_at` 은 크롤러가 **글이 수정된 것을 본 시각**입니다(지금은 제목이 바뀐 경우만). NEW/UPDATED 표시의 근거라서 조회수, 고정 해제, 마감을 다시 읽은 결과로는 바뀌지 않습니다. `NULL` 은 "바뀐 적 없음"이고, 읽는 쪽은 `posted_on` 으로 대신합니다. 마이그레이션이 기존 글을 `NULL` 로 두는 것이 중요합니다 (`now()` 로 채우면 모든 글이 UPDATED가 됩니다).
- `view_count_synced_at` 은 조회수를 마지막으로 쓴 시각입니다. 조회수만 달라진 글은 이 값이 하루보다 오래됐을 때만 다시 씁니다 (`app/crawler/store.py`의 `VIEW_REFRESH_AFTER`). 기본값 `now()` 덕분에 새 글은 하루 뒤부터 갱신됩니다.
- `user_article_reads` 는 로그인한 사용자가 연 글입니다 (글 id와 시각만). 표시 기간(`app/freshness.py`, 서울 날짜 7일)이 끝난 글의 행은 크롤러 CronJob이 지우고, API도 기간 밖의 글은 저장을 거절합니다. 그래서 테이블 크기는 계정 수 × 최근 7일의 글 수를 넘지 않습니다. 계정이나 글이 지워지면 같이 지워집니다.
- 제목 검색용 `pg_trgm` 인덱스는 아직 없습니다. 글이 수만 건이어도 순차 스캔으로 충분하며, 느려지면 3단계 이후에 추가합니다.

## 로컬 개발

```bash
docker compose -f compose.dev.yaml up -d
cp .env.example .env     # DATABASE_URL 주석을 풀기
uv run python -m app.migrate
uv run python -m scripts.seed_dev      # 프론트 mock과 같은 36건
```

`scripts/seed_dev.py` 는 `APP_ENV=prod` 에서는 실행을 거부합니다.

## 테스트와 DB

DB가 필요한 테스트는 서버에 임시 데이터베이스(`skku_test_<랜덤>`)를 만들고 끝나면 지웁니다.
접속 정보는 `TEST_DATABASE_URL` (기본값 `postgresql://skku:dev@127.0.0.1:5432/postgres`, `CREATE DATABASE` 권한 필요 — compose.dev.yaml 의 `skku` 역할은 가능).
DB에 접속할 수 없으면 해당 테스트는 건너뜁니다. CI에서는 `REQUIRE_DB=1` 로 건너뛰는 대신 실패하게 합니다.
