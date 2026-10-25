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
articles(id PK, article_no UNIQUE, title, writer, posted_on, display_no, is_pinned,
         view_count, url, deadline_at timestamptz, created_at, updated_at)
article_categories(article_id → articles ON DELETE CASCADE,
                   category_code → categories ON DELETE RESTRICT)   -- PK(article_id, category_code)
```

- `writer` 는 별도 테이블 없이 문자열 컬럼입니다. `/writers` 는 `GROUP BY writer` 로 계산합니다.
- `deadline_at` 이 23:59(KST)면 "시간 없는 날짜 마감"입니다.
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
접속 정보는 `TEST_DATABASE_URL` (기본값 `postgresql://skku:dev@localhost:5432/postgres`, `CREATE DATABASE` 권한 필요 — compose.dev.yaml 의 `skku` 역할은 가능).
DB에 접속할 수 없으면 해당 테스트는 건너뜁니다. CI에서는 `REQUIRE_DB=1` 로 건너뛰는 대신 실패하게 합니다.
