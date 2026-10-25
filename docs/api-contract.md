# API 계약 (v1)

프론트(`skku-notice-web`)의 `lib/api/types.ts`와 `lib/mock/handlers.ts`가 정의한 동작을 그대로 서버로 옮긴 문서입니다.
구현이 이 문서와 다르면 **둘 중 하나를 고쳐야** 합니다 (조용히 어긋나지 않게).

## 경로 규칙

ingress가 `/api` 접두어를 **떼지 않고** 그대로 넘기므로, 앱이 `/api` 아래에 직접 마운트됩니다.

| 경로 | 설명 |
| --- | --- |
| `GET /api/healthz` | liveness. 항상 `{"ok": true}` |
| `GET /api/readyz` | readiness. DB 등 의존성 확인. 실패 시 503 |
| `/api/v1/...` | 아래 비즈니스 엔드포인트 |
| `/api/docs`, `/api/openapi.json` | `DOCS_ENABLED=true`일 때만 |

프론트 `NEXT_PUBLIC_API_BASE`는 `https://<host>/api/v1` 입니다 (개발 기본값 `http://localhost:8000/api/v1`).

## 공통

- 필드는 snake_case, 날짜는 `YYYY-MM-DD`, 시각은 ISO 8601 + `+09:00` (Asia/Seoul).
- 에러 본문은 항상 `{"detail": "<한국어 메시지>"}` 문자열 한 개. (프론트가 본문을 그대로 사용자에게 보여 줍니다.)
- 검증 실패는 HTTP 422, 없는 리소스는 404.

## 타입

```ts
Article = {
  id: number            // DB 기본키 (프론트가 즐겨찾기에 저장하는 값)
  article_no: number    // 학교 게시판의 글 번호
  title: string
  writer: string
  posted_on: string     // "YYYY-MM-DD"
  display_no: number | null
  is_pinned: boolean
  view_count: number | null
  url: string
  categories: string[]  // 카테고리 code 목록
  deadline_at: string | null
}
ArticleListResponse = { items: Article[]; total: number; page: number; size: number; has_next: boolean }
Category = { code: string; name: string; count: number }
Writer = { writer: string; count: number }
LookupResponse = { items: Article[]; missing_ids: number[] }
```

## `GET /api/v1/articles`

| 쿼리 | 설명 |
| --- | --- |
| `q` | 공백으로 나눈 모든 단어가 (제목 + 작성자 + 카테고리 이름)에 포함 (AND, 대소문자 무시). 최대 100자. `%`, `_`, `\`는 문자 그대로 검색 |
| `category` | 반복 가능 (`category=a&category=b`). 하나라도 일치 (OR) |
| `writer` | 반복 가능. 정확히 일치, 하나라도 일치 (OR) |
| `date_from`, `date_to` | `posted_on` 범위 (양끝 포함). `from > to`면 422 `"date_from은 date_to보다 이후일 수 없습니다."` |
| `pinned` | `include`(기본) / `exclude` / `only` |
| `has_deadline` | `true` → 마감 있는 글만, `false` → 마감 없는 글만, 생략 → 전체 |
| `deadline_from`, `deadline_to` | 마감 **날짜(서울 기준)** 범위. 지정하면 마감 없는 글은 제외. `from > to`면 422 `"deadline_from은 deadline_to보다 이후일 수 없습니다."` |
| `sort` | `posted_desc`(기본) / `posted_asc` / `views_desc` / `deadline_asc` |
| `page` | 1부터, 기본 1 (1~100000, 밖이면 422) |
| `size` | 기본 20, 범위 밖이면 1~50으로 보정 |

정렬 규칙
- `posted_*`: `posted_on` 기준, 같으면 `id` 같은 방향으로 (페이지 경계에서 순서가 흔들리지 않게).
- `views_desc`: `view_count` 내림차순, `null`은 0 취급. 동점이면 최신 글(`posted_on`, `id` 내림차순).
- `deadline_asc`: 마감 있는 글만, 마감 시각 오름차순(같으면 `id` 오름차순). **지난 마감은 제외**(서울 기준 오늘 0시 이전 — 오늘 마감이면 시각이 지났어도 남음). 단, `deadline_from`/`deadline_to`가 있으면 범위가 우선하여 지난 마감도 포함.
- `deadline_from`/`deadline_to`의 날짜 경계는 서울 자정이다: 마감이 `10/26 00:00`이면 10/26의 글이지 10/25의 글이 아니다.

응답: `ArticleListResponse`. `has_next = (page-1)*size + size < total`.

### mock과 달라지는 점

`tests/db/reference.py`(mock을 옮긴 기준 구현)와 `tests/db/test_articles_parity.py`가 아래를 제외한 모든 동작이 mock과 같음을 검사합니다.

| | mock | 서버 | 영향 |
| --- | --- | --- | --- |
| `has_deadline=false` | 필터 없이 전부 통과 | 마감 **없는** 글만 | 프론트는 `false`를 보내지 않음 |
| `q`가 보는 카테고리 | 영문 `code` (`academic`) | 한글 `name` (`학사`) | 사용자는 한글로 검색함. 영문 code로는 더 이상 걸리지 않음 |
| 정렬 동점 | 입력 순서 | `id`로 결정 (위 정렬 규칙) | 페이지 경계에서 순서가 흔들리지 않음 |
| 알 수 없는 `sort`/`pinned`, `page < 1` | 기본값처럼 처리 | 422 | 프론트는 정해진 값만 보냄 |
| 중복 id (`lookup`) | 그대로 중복 반환 | 한 번만 | 아래 참고 |

## `GET /api/v1/articles/{id}`

`Article`. 없으면(범위를 벗어난 id 포함) 404 `"게시글을 찾을 수 없습니다."`

## `POST /api/v1/articles/lookup`

본문 `{"ids": number[]}`. 개수가 1~200이 아니면 422 `"조회할 ID는 1~200개까지 가능합니다."`
응답 `LookupResponse`. `items`는 요청한 `ids` 순서를 유지하고(중복 id는 한 번만), 없는 id는 `missing_ids`에 요청 순서대로(역시 한 번만). 범위를 벗어난 값(0, 음수, 32비트 정수 초과)은 오류가 아니라 없는 id로 취급.

## `GET /api/v1/categories`

`Category[]`. 글이 0개인 카테고리도 포함. 순서는 카테고리 정의 순서.

## `GET /api/v1/writers`

| 쿼리 | 설명 |
| --- | --- |
| `q` | 작성자 이름에 포함(부분 일치). 공백뿐이면 필터 없음 |
| `limit` | 최대 개수. 생략 시 200, 1~200 밖의 값은 보정 |

`Writer[]`. `count` 내림차순, 같으면 이름 오름차순.

## `GET /api/v1/calendar`

`id` 반복 쿼리 (1~100개). 마감이 있는 글을 `text/calendar` (ICS)로 내보냅니다. 프론트가 구독 URL로 쓰며
구글 캘린더 서버가 직접 가져가므로 **인터넷에서 접근 가능한 HTTPS**여야 합니다. `HEAD`도 지원합니다.

- `id`가 0개이거나 100개를 넘으면 422 `"조회할 ID는 1~100개까지 가능합니다."`
- 마감이 없는 글, 없는 id, 범위를 벗어난 id, 중복 id는 오류 없이 건너뜁니다. 내보낼 글이 하나도 없어도 빈 (유효한) 캘린더를 200으로 돌려줍니다.
- 이벤트는 마감 시각 오름차순(같으면 `id`). **지난 마감도 포함**합니다 (즐겨찾기를 그대로 반영, 지난 일정을 숨기는 것은 캘린더 앱의 몫).
- 마감 시각이 23:59(서울)면 **종일 일정** (`DTSTART;VALUE=DATE`, `DTEND`는 다음 날 — 종료일은 배타적), 아니면 **시간 일정**: 마감 시각에 시작해 30분 길이, UTC(`Z`)로 표기.
- 제목은 `[마감] <글 제목>`, 설명은 `작성: <작성자>` + 게시글 URL. 일정은 "바쁨"으로 표시되지 않습니다(`TRANSP:TRANSPARENT`).
- `UID`는 `article-<id>@skku-notice`로 고정. 마감이 바뀌어도 UID가 같아서 구독자 캘린더에서 이벤트가 갱신됩니다 (중복 생성 안 됨). 글이 사라지거나 마감이 없어지면 피드에서 빠지고 캘린더 앱이 삭제합니다.
- `DTSTAMP`/`LAST-MODIFIED`는 글 행의 `updated_at`. 같은 데이터면 응답이 항상 같은 바이트입니다.
- `Cache-Control: public, max-age=300`. 구글 캘린더의 실제 갱신 주기는 구글이 정하며(대체로 하루 단위), 피드의 `REFRESH-INTERVAL`은 힌트일 뿐입니다.
