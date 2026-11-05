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

프론트 `NEXT_PUBLIC_API_BASE`는 `https://<host>/api/v1` 입니다 (개발 기본값 `http://localhost:8000/api/v1` — 아래 "개발 환경"의 쿠키 규칙 때문에 `127.0.0.1`이 아니라 `localhost`).

## 공통

- 필드는 snake_case, 날짜는 `YYYY-MM-DD`, 시각은 ISO 8601 + `+09:00` (Asia/Seoul).
- 에러 본문의 `detail`은 항상 한국어 메시지 문자열 한 개입니다. (프론트가 그대로 사용자에게 보여 줍니다.) 계정 기능(`/auth`, `/me`)의 에러에는 프론트가 분기에 쓰는 기계용 `code`가 함께 옵니다: `{"detail": "<메시지>", "code": "<코드>"}`. 읽기 API의 에러에는 `code`가 없습니다. (자세한 코드 목록은 아래 "계정" 절)
- 검증 실패는 HTTP 422, 없는 리소스는 404.
- 공개 읽기(`GET /articles`, `/articles/{id}`, `/categories`, `/departments`, `/writers`)의 **성공** 응답에는 `Cache-Control: public, max-age=60`이 붙습니다. 누구에게나 같은 응답이라(NEW/UPDATED는 웹이 계산) 브라우저와 Cloudflare가 1분간 보관해도 됩니다. 오류(404, 422)에는 붙지 않습니다. `POST /articles/lookup`, `/auth/*`, `/me/*`는 캐시하지 마세요(뒤의 둘은 `no-store`). `/calendar`는 따로 5분입니다.

## 타입

```ts
Article = {
  id: number            // DB 기본키 (프론트가 즐겨찾기에 저장하는 값)
  article_no: number    // 학교 게시판의 글 번호. 게시판마다 따로 매기므로 (source, article_no)가 유일하고 article_no만으로는 유일하지 않음
  source: string        // 글이 올라온 게시판의 slug (예: "skku", "sco-data"). GET /departments의 sources[].slug와 같은 값
  department: string    // 그 게시판이 속한 학과의 code (예: "skku", "sco"). 학교 전체 공지는 source, department 모두 "skku"
  title: string
  writer: string
  posted_on: string     // "YYYY-MM-DD"
  display_no: number | null
  is_pinned: boolean
  view_count: number | null
  url: string
  categories: string[]  // 카테고리 code 목록
  deadline_at: string | null
  event_at: string | null           // 행사/근무일 (마감 아님). 시각을 알면 ISO 8601, 모르면 날짜만("2026-11-20"). 정렬·필터에 쓰이지 않음
  deadline_source: "title" | "body" | null  // deadline_at을 읽은 곳. 마감이 없으면 null
  content_changed_at: string | null // 크롤러가 글이 수정된 것을 본 시각 (ISO 8601 +09:00). 바뀐 적 없으면 null. UPDATED 표시용 (아래 "NEW / UPDATED 표시")
}
ArticleListResponse = { items: Article[]; total: number; page: number; size: number; has_next: boolean }
Category = { code: string; name: string; count: number }
Source = { slug: string; name: string; level: "all" | "undergrad" | "grad"; count: number }   // 게시판 하나
Department = { code: string; name: string; count: number; sources: Source[] }   // count = 소속 게시판 글 수의 합
Writer = { writer: string; count: number }
LookupResponse = { items: Article[]; missing_ids: number[] }
```

### 본문에서 읽은 날짜

- `deadline_at`은 제목에서 먼저 읽고, 제목에 없으면 크롤러가 상세 페이지 본문에서 읽습니다. 어디서 읽었는지가 `deadline_source`이며, `"body"`는 정규식이 본문의 단서어(모집기간, 접수기간 …) 뒤 날짜를 읽은 값이라 제목 기준보다 틀릴 가능성이 높습니다. 프론트는 "본문 기준"으로 표시합니다.
- `event_at`은 "일시/일정/행사일"처럼 **행사 자체**가 열리는 시각입니다. 마감이 아니므로 `has_deadline`, `deadline_from/to`, `deadline_asc`는 `event_at`을 보지 않습니다. 시각을 모르면 `"2026-11-20"`처럼 **날짜만** 내려갑니다(23:59를 붙이지 않음). 반대로 `deadline_at`은 시각을 몰라도 항상 `...T23:59:00+09:00`입니다(그날 끝까지가 마감이라는 뜻). 캘린더 피드는 둘 다 종일 일정으로 만듭니다.
- 본문 값을 읽은 **근거 줄**(`deadline_evidence`)은 디버깅용이라 DB에만 있고 응답에는 나가지 않습니다.

## `GET /api/v1/articles`

| 쿼리 | 설명 |
| --- | --- |
| `q` | 공백으로 나눈 모든 단어가 (제목 + 작성자 + 카테고리 이름)에 포함 (AND, 대소문자 무시). 최대 100자. `%`, `_`, `\`는 문자 그대로 검색 |
| `category` | 반복 가능 (`category=a&category=b`). 하나라도 일치 (OR) |
| `writer` | 반복 가능. 정확히 일치, 하나라도 일치 (OR) |
| `department` | 반복 가능. 학과 `code`(`GET /departments`). 그 학과의 **모든 게시판** 글 |
| `source` | 반복 가능. 게시판 `slug` |
| `date_from`, `date_to` | `posted_on` 범위 (양끝 포함). `from > to`면 422 `"date_from은 date_to보다 이후일 수 없습니다."` |
| `pinned` | `include`(기본) / `exclude` / `only` |
| `has_deadline` | `true` → 마감 있는 글만, `false` → 마감 없는 글만, 생략 → 전체 |
| `deadline_from`, `deadline_to` | 마감 **날짜(서울 기준)** 범위. 지정하면 마감 없는 글은 제외. `from > to`면 422 `"deadline_from은 deadline_to보다 이후일 수 없습니다."` |
| `sort` | `posted_desc`(기본) / `posted_asc` / `views_desc` / `deadline_asc` |
| `page` | 1부터, 기본 1 (1~100000, 밖이면 422) |
| `size` | 기본 20, 범위 밖이면 1~50으로 보정 |

**어느 게시판의 글을 읽는가** (`/articles`, `/categories`, `/writers` 공통)
- `department`와 `source`를 **둘 다 안 주면 학교 전체 공지(`level = 'all'`)만** 나옵니다. 학과 공지는 안 섞입니다. 학과를 고르지 않은 사람에게는 보여 줄 학과 공지가 없기 때문입니다.
- 하나라도 주면 **주어진 학과의 게시판 + 주어진 게시판의 합집합**입니다 (OR). 이때 학교 전체 공지는 따라오지 않으니, 같이 보려면 `source=skku`를 함께 보냅니다. 예: `department=cse&source=sco-data`는 소프트웨어학과 공지와 데이터사이언스융합전공 공지를 함께 줍니다.
- 모르는 학과 `code`나 `slug`는 오류가 아니라 결과 0건입니다.
- 어떤 사람의 학과 선택은 서버가 아니라 클라이언트가 들고 있다가 이 파라미터로 보냅니다. 응답이 사용자마다 달라지지 않아서 공개 캐시를 그대로 쓸 수 있습니다.
- 응답의 각 글은 자기 `source`(게시판 slug)와 `department`(학과 code)를 가지고 있습니다. 합집합으로 여러 게시판을 섞어 받아도 글마다 어느 게시판 것인지 구분할 수 있고, 학과 탭이 이 값으로 글머리에 게시판 이름을 붙입니다. `/articles/{id}`와 `POST /articles/lookup`의 글도 같습니다. 글 id로 즐겨찾기·캘린더를 쓰는 데는 영향이 없습니다.

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

| 쿼리 | 설명 |
| --- | --- |
| `department`, `source` | `GET /articles`와 같은 규칙으로 범위를 정합니다 (안 주면 학교 전체 공지). `count`는 그 범위의 글 수라서, 같은 파라미터로 목록을 부르면 `?category=<code>`의 `total`과 일치합니다 |

## `GET /api/v1/departments`

`Department[]`. 학생이 고르는 단위는 학과이고, 한 학과가 게시판을 여러 개 가질 수 있습니다(글로벌융합학부는 공통 공지 + 전공별 4개). 순서는 학과, 그 안의 게시판 모두 정의 순서(`app/crawler/sources.py`)입니다.

- **학교 전체 공지(`level = 'all'`)는 나오지 않습니다.** 그건 아무것도 고르지 않았을 때 `GET /articles`가 보여 주는 기본 범위이지, 고르는 대상이 아닙니다.
- **켜져 있는(`enabled`) 게시판만** 나옵니다. 게시판이 모두 꺼진 학과는 목록에 없습니다. 글이 0개인 게시판은 `count: 0`으로 나옵니다.
- `sources` 동기화(`--sync-sources`)를 한 번도 안 했다면 빈 배열입니다.
- `level`: `undergrad`는 학부, `grad`는 대학원입니다 (`all`은 위 이유로 나오지 않음).
- 학과의 글은 `GET /articles?department=<code>`, 게시판 하나만 보려면 `?source=<slug>`.

## `GET /api/v1/writers`

| 쿼리 | 설명 |
| --- | --- |
| `q` | 작성자 이름에 포함(부분 일치). 공백뿐이면 필터 없음 |
| `limit` | 최대 개수. 생략 시 200, 1~200 밖의 값은 보정 |
| `department`, `source` | `GET /articles`와 같은 규칙으로 범위를 정합니다 (안 주면 학교 전체 공지) |

`Writer[]`. `count` 내림차순, 같으면 이름 오름차순.

## NEW / UPDATED 표시

서버는 표시를 계산해 내려주지 않습니다. 목록 응답이 사용자와 무관해야 캐시할 수 있기 때문입니다. 웹이 `posted_on`, `content_changed_at`, "내가 연 글" 기록으로 **직접 계산**합니다.

- **"읽음"** 은 사용자가 글의 링크를 누른 것입니다 (앱이 본문을 보여주지 않으므로). 목록만 훑은 글은 읽은 것이 아닙니다.
- **표시 기간**은 서울 날짜 기준 **7일**입니다 (오늘 포함). `windowStart = 오늘(서울) − 6일`. 서버(`app/freshness.py`)에 상수로 있고 설정으로 바꿀 수 없습니다. 웹과 서버가 같은 값을 써야 해서, 바꿀 때는 둘을 함께 배포합니다.
- 날짜 비교는 서울 날짜입니다. `content_changed_at`은 이미 `+09:00`이라 앞 10자(`YYYY-MM-DD`)가 서울 날짜입니다.

| 표시 | 조건 (위에서 아래로 처음 맞는 것) |
| --- | --- |
| `NEW` | `posted_on >= windowStart` 이고 읽은 기록이 없음 |
| `UPDATED` | `content_changed_at`의 날짜 `>= windowStart` 이고, (읽은 기록이 없거나 `read_at < content_changed_at`) |
| 없음 | 그 외 |

```ts
const isNew = (a, read) => a.posted_on >= windowStart && !read
const isUpdated = (a, read) =>
  a.content_changed_at != null &&
  a.content_changed_at.slice(0, 10) >= windowStart &&
  (!read || new Date(read.read_at) < new Date(a.content_changed_at))
const badge = (a, read) => (isNew(a, read) ? "NEW" : isUpdated(a, read) ? "UPDATED" : null)
```

| 상황 | 표시 |
| --- | --- |
| 오늘 올라온 글, 아직 안 열어 봄 | `NEW` |
| 그 글을 열어 봄 | 없음 |
| 열어 본 뒤 제목이 바뀜 | `UPDATED` (다시 열면 없음) |
| 이번 주에 올라온 글인데 열기 전에 제목이 바뀜 | `NEW` (아직 안 봤으니 NEW가 우선) |
| 3주 전 글, 어제 제목이 바뀜, 열어 본 적 없음 | `UPDATED` (수정일로부터 7일) |
| 열어 본 뒤 7일이 지남 | 없음 |

**`content_changed_at`이 감지하는 것은 지금은 제목이 바뀐 경우뿐입니다.** 제목은 그대로이고 본문·첨부만 바뀐 글(학점 교류 안내처럼)은 아직 UPDATED가 되지 않습니다. 본문 변경 감지가 들어가면 같은 필드가 그 경우도 담으므로 웹은 바꿀 것이 없습니다. 조회수, 고정/해제, 마감을 다시 읽은 결과는 수정이 아닙니다. 수정 시각은 "학교가 고친 시각"이 아니라 "크롤러가 알아챈 시각"이라 크롤링 주기만큼 늦을 수 있습니다.

### 읽음 기록을 어디에 두는가

- **로그인 안 함**: 브라우저 `localStorage`에 `{ [article_id]: read_at(ISO) }`.
- **로그인함**: 서버(`/me/reads`). 링크를 누를 때마다 보내지 말고 몇 초 모았다가 한 번에 `POST`하고, 탭을 닫을 때는 `navigator.sendBeacon`으로 남은 것을 보냅니다 (그래서 `PUT`이 아니라 `POST`입니다). `GET`은 로그인 직후와 탭이 다시 보일 때만 부르고 주기적으로 부르지 않습니다.
- **로그인할 때 병합**: 로컬 기록을 `read_at`을 붙여 `POST /me/reads`로 올리고(200개씩), 이어서 `GET /me/reads`의 결과를 기준으로 삼습니다. 두 기기에서 읽어도 합집합이 되고 충돌할 일이 없습니다(서버는 글마다 **가장 늦은** `read_at`을 유지). 올린 뒤 로컬 사본은 지웁니다. 공용 PC를 생각해 로그아웃할 때도 로컬 읽음 기록을 지우는 것을 권합니다.
- **로컬 정리**: `read_at`의 서울 날짜가 `windowStart`보다 이전인 항목은 지웁니다. 글 정보가 필요 없습니다. 그런 기록은 있든 없든 표시가 같기 때문입니다. (게시일은 읽은 날보다 늦지 않으므로 `NEW` 판정에는 창 안의 `read_at`만 쓰이고, 수정 뒤에 읽은 기록은 수정이 창 안이면 읽은 시각도 창 안입니다. 수정 *전에* 읽은 기록은 없어도 `UPDATED`입니다.)
- 서버는 표시 기간이 끝난 글의 기록을 지우고, 이미 기간이 끝난 글이나 없는 글의 id는 **오류 없이 무시**합니다. 그래서 웹이 오래된 id를 보내도 됩니다.
- 읽음 기록은 계정에 저장되는 개인정보입니다(글 id와 연 시각, 표시 기간이 끝나면 삭제). 웹의 `/policy` 수집 항목에 추가해 주세요.

## `GET /api/v1/calendar`

`id` 반복 쿼리 (1~100개). 마감 또는 행사일이 있는 글을 `text/calendar` (ICS)로 내보냅니다. 프론트가 구독 URL로 쓰며
구글 캘린더 서버가 직접 가져가므로 **인터넷에서 접근 가능한 HTTPS**여야 합니다. `HEAD`도 지원합니다.

- `id`가 0개이거나 100개를 넘으면 422 `"조회할 ID는 1~100개까지 가능합니다."`
- 마감도 행사일도 없는 글, 없는 id, 범위를 벗어난 id, 중복 id는 오류 없이 건너뜁니다. 내보낼 글이 하나도 없어도 빈 (유효한) 캘린더를 200으로 돌려줍니다.
- 글 하나에 이벤트가 최대 둘: 마감(`[마감] <제목>`)과 행사일(`[행사] <제목>`, `event_at`이 있을 때). 글은 **가장 이른 날짜** 오름차순(같으면 `id`)이고, 한 글 안에서는 마감, 행사 순입니다. 행사일의 종일/시간 일정 규칙은 마감과 같습니다.
- **지난 일정도 포함**합니다 (즐겨찾기를 그대로 반영, 지난 일정을 숨기는 것은 캘린더 앱의 몫).
- 마감 시각이 23:59(서울)면 **종일 일정** (`DTSTART;VALUE=DATE`, `DTEND`는 다음 날 — 종료일은 배타적), 아니면 **시간 일정**: 마감 시각에 시작해 30분 길이, UTC(`Z`)로 표기.
- 제목은 `[마감] <글 제목>`, 설명은 `작성: <작성자>` + 게시글 URL. 일정은 "바쁨"으로 표시되지 않습니다(`TRANSP:TRANSPARENT`).
- `UID`는 마감이 `article-<id>@skku-notice`, 행사일이 `article-<id>-event@skku-notice`로 고정. 마감이 바뀌어도 UID가 같아서 구독자 캘린더에서 이벤트가 갱신됩니다 (중복 생성 안 됨). 글이 사라지거나 마감이 없어지면 피드에서 빠지고 캘린더 앱이 삭제합니다.
- `DTSTAMP`/`LAST-MODIFIED`는 글 행의 `updated_at`. 같은 데이터면 응답이 항상 같은 바이트입니다.
- `Cache-Control: public, max-age=300`. 구글 캘린더의 실제 갱신 주기는 구글이 정하며(대체로 하루 단위), 피드의 `REFRESH-INTERVAL`은 힌트일 뿐입니다.

# 계정 (이메일·Google)

서버가 정의한 계약입니다 (프론트의 mock에는 없던 부분이라, 웹 화면은 이 문서를 기준으로 만듭니다). 웹이 먼저이고 앱(Android/iOS)은 나중이지만, 앱용 분기(`X-Client`, Bearer)는 이미 구현돼 있습니다.

## 인증 방식

| | 웹 | 앱 (나중) |
| --- | --- | --- |
| 로그인 후 받는 것 | `sid` 쿠키 (본문에 토큰 없음) | 응답 본문의 `token` |
| 이후 요청 | 쿠키가 자동으로 붙음. `fetch`는 `credentials: "include"` 필요 | `Authorization: Bearer <token>` |
| 구분 방법 | `X-Client` 헤더 없음 (또는 `web`) | `X-Client: android` 또는 `ios`. 다른 값은 400 `invalid_client` |

- 쿠키 `sid`: `HttpOnly`, `SameSite=Lax`, `Path=/api`, 운영에서는 `Secure`. 웹과 API가 같은 호스트(`skkunoti.tsungmn.com`, API는 `/api`)라서 CORS 없이 동작합니다.
- 세션은 슬라이딩 30일입니다 (`SESSION_TTL_DAYS`). 쓰면 만료가 밀리고, 갱신은 하루에 한 번만 기록합니다. 만료되면 `401 not_authenticated`입니다.
- 세션은 이메일이 인증된 계정만 가질 수 있습니다.
- **CSRF**: POST/PUT/PATCH/DELETE에 `Origin`이 있으면 허용된 웹 출처(`CORS_ORIGINS`, `PUBLIC_URL`)이거나 요청 대상 호스트와 같아야 합니다. 브라우저는 알아서 붙입니다. `sid` 쿠키만 있고 `Origin`도 `Authorization`도 없으면 403 `origin_not_allowed`입니다. Bearer 요청은 대상이 아닙니다.
- **개발 환경**: 웹(`:3000`)과 API(`:8000`)가 포트만 다르면 쿠키가 붙으려면 둘 다 같은 호스트 이름이어야 합니다. `localhost`로 통일하고 `CORS_ORIGINS=http://localhost:3000`으로 맞추세요. (`127.0.0.1`과 `localhost`가 섞이면 쿠키가 안 붙습니다. Google 로그인의 개발용 콜백도 `localhost`를 씁니다.)
- `/auth/*`와 `/me`의 응답에는 `Cache-Control: no-store`가 붙습니다.
- 요청 본문은 64KB까지입니다. 넘으면 413 `payload_too_large`.

## 타입

```ts
User = {
  id: string                // uuid
  email: string             // 항상 소문자
  name: string | null
  email_verified: boolean
  has_password: boolean     // false면 Google로만 가입한 계정
  google_linked: boolean
  created_at: string        // ISO 8601 (+09:00)
  preferences: Preferences  // 계정에 저장된 설정. 저장한 적이 없으면 각 섹션이 null
}
Preferences = { calendar: CalendarPrefs | null }
CalendarPrefs = {
  hidden_categories: string[]   // 사용자가 **끈** 카테고리 코드. 새 카테고리는 기본으로 켜지도록 켠 것이 아니라 끈 것을 저장
  favorites_only: boolean
}
LoginResponse = { user: User; token: string | null; expires_at: string }  // token은 앱(X-Client: android|ios)일 때만
Detail = { detail: string }
```

입력 규칙: 이메일은 앞뒤 공백을 지우고 소문자로 바꿔 비교합니다 (최대 320자). 비밀번호는 가입·재설정에서 8~128자입니다. 이름은 최대 100자입니다.

## 엔드포인트

모두 `/api/v1` 아래입니다.

| 엔드포인트 | 본문 | 성공 | 설명 |
| --- | --- | --- | --- |
| `POST /auth/register` | `{email, password, name?, turnstile_token?}` | 202 `Detail` | 새 이메일이면 인증 메일, 이미 가입된 이메일이면 "이미 가입됨" 안내 메일을 보냅니다. **응답은 두 경우가 같습니다.** Turnstile 실패는 400 `captcha_failed` |
| `POST /auth/verify-email` | `{token}` | 200 `Detail` | 메일 링크의 토큰. 한 번만 쓸 수 있고 24시간 유효 |
| `POST /auth/resend-verification` | `{email}` | 202 `Detail` | 계정이 있든 없든 같은 응답 |
| `POST /auth/login` | `{email, password}` | 200 `LoginResponse` | 웹은 `sid` 쿠키가 설정됩니다. 미인증이면 403 `email_not_verified`. 이메일이 없는 경우와 비밀번호가 틀린 경우는 같은 401 |
| `POST /auth/logout` | 없음 | 204 | 항상 성공합니다 (세션이 이미 죽었어도). 쿠키를 지웁니다 |
| `POST /auth/forgot-password` | `{email}` | 202 `Detail` | 계정이 있든 없든 같은 응답. 인증된 계정에는 재설정 메일, 미인증 계정에는 인증 메일이 갑니다 |
| `POST /auth/reset-password` | `{token, password}` | 200 `Detail` | 토큰은 1회용, 1시간 유효. **모든 기기의 세션이 로그아웃됩니다.** 로그인은 새로 해야 합니다 |
| `GET /auth/google?next=/경로` | 없음 | 302 → Google | 브라우저를 이동시키는 용도 (`fetch`가 아니라 `window.location`). `next`는 이 사이트 안의 경로만 허용하고, 아니면 `/`로 대체합니다 |
| `GET /auth/google/callback` | (Google이 호출) | 303 → 웹 | 성공하면 `sid` 쿠키를 설정하고 `PUBLIC_URL` + `next`로 이동 |
| `GET /me` | 없음 | 200 `User` | 로그인 확인용. 로그인이 안 됐으면 401 |
| `PATCH /me/preferences` | `{calendar?: CalendarPrefs \| null}` | 200 `Preferences` | 계정 설정 저장. 본문에 **있는** 키는 그 섹션을 통째로 바꾸고, `null`이면 초기화하며, 없는 키는 그대로 둡니다. 코드는 영문 소문자·숫자·`_`·`-` 최대 32자, 최대 20개(중복은 지우고 정렬해서 저장). 잘못된 값은 422. 계정당 시간당 120회 |
| `GET /me/reads` | 없음 | 200 `{items: {article_id, read_at}[]}` | 표시 기간 안에 있는 글 중 이 계정이 연 것. `read_at`은 `+09:00`. 최신순. 페이지 없음(기간이 끝난 글은 빠지므로 크기가 제한됨) |
| `POST /me/reads` | `{items: {article_id, read_at?}[]}` (0~200개) | 200 `{saved: number}` | 글을 열었다고 기록. 몇 번을 보내도 같고, 글마다 **가장 늦은** `read_at`을 유지합니다(더 이른 값으로 되돌리지 않음). `read_at`은 생략하면 지금, 오프셋이 없으면 422, 지금보다 미래면 지금으로 잘라 냅니다. 로그인 전에 쌓은 기록을 올릴 때만 붙이세요. `saved`는 받아들인 id 수이며, 없는 글과 표시 기간이 끝난 글은 오류 없이 건너뜁니다. `article_id`가 1~2³¹−1 밖이거나 201개 이상이면 422. 계정당 분당 60회 |
| `DELETE /me` | `{password?}` | 204 | 아래 참고 |

### 가입 후 흐름

1. `register`는 항상 202입니다. 화면에는 "메일을 보냈습니다. 스팸함도 확인해 주세요" 같은 안내를 보여 주고, 계정이 있는지는 알려 주지 않습니다.
2. 메일의 링크는 `PUBLIC_URL`의 **웹 페이지**를 가리킵니다. 그래서 웹이 이 경로를 가져야 합니다.
   - `/verify-email?token=...`: 토큰을 `POST /auth/verify-email`로 보내고 결과를 보여 줍니다.
   - `/reset-password?token=...`: 새 비밀번호를 받아 `POST /auth/reset-password`로 보냅니다.
   - `/login`, `/forgot-password`: "이미 가입됨" 메일이 가리키는 곳입니다.
3. 인증 전 로그인은 403 `email_not_verified`입니다. 웹은 이때 "인증 메일 다시 보내기"(`resend-verification`)를 보여 줍니다.
4. 같은 주소로 인증 전에 여러 번 가입하면 **마지막에 가입한 비밀번호**가 유효합니다.
5. 인증 링크를 다시 요청하면 이전 링크는 무효가 됩니다.

### Google 로그인

- 웹은 `GET /api/v1/auth/google?next=/원하는/경로`로 **페이지를 이동**시킵니다. 끝나면 사이트로 돌아오고 이미 로그인된 상태(`sid` 쿠키)입니다.
- 실패해도 API 응답이 아니라 **리다이렉트**로 돌아옵니다: `/login?error=<코드>`. 코드는 `google_failed`(취소·만료·교환 실패), `google_unverified`(Google이 이메일을 인증된 것으로 보증하지 않음), `google_conflict`(그 이메일이 이미 다른 Google 계정과 연결됨), `google_disabled`(서버에 Google 로그인이 설정되어 있지 않음)입니다.
- Google이 설정되지 않은 서버에서도 404 JSON이 아니라 `/login?error=google_disabled` 리다이렉트로 돌아옵니다. 프론트는 이 버튼을 숨길 방법이 없으므로(설정 조회 API 없음), 눌렀을 때 사용자가 JSON 화면이 아니라 안내 문구를 보게 하려는 것입니다.
- 같은 이메일로 가입한 **인증된** 계정이 있으면 그 계정에 Google을 연결하고 비밀번호는 그대로 둡니다. **인증 전** 계정이면 비밀번호와 세션을 폐기하고 Google 계정으로 넘깁니다.
- Google로만 가입한 계정(`has_password: false`)은 이메일 로그인이 안 됩니다. `forgot-password`로 비밀번호를 만들 수 있습니다.

### 계정 삭제 (`DELETE /me`)

- 비밀번호가 있는 계정: 본문 `{"password": "..."}`가 필요합니다. 없거나 틀리면 403 `invalid_credentials`이고, 15분에 5번 틀리면 429입니다.
- Google 전용 계정: 본문이 없어도 되지만 **로그인한 지 10분 이내**여야 합니다. 아니면 403 `reauth_required` (다시 로그인하라고 안내).
- 성공하면 204이고 세션·토큰이 함께 삭제되며 쿠키가 지워집니다. 같은 이메일로 다시 가입할 수 있습니다.
- 본문이 있는 DELETE이므로 `fetch(url, { method: "DELETE", body, headers: {"Content-Type": "application/json"} })`로 보냅니다.

## 에러 코드

| 상태 | `code` | 의미 |
| --- | --- | --- |
| 400 | `invalid_token` | 메일 링크가 없거나 이미 썼거나 만료됨 |
| 400 | `captcha_failed` | Turnstile 확인 실패 (가입) |
| 400 | `invalid_client` | `X-Client`가 `web`, `android`, `ios`가 아님 |
| 401 | `not_authenticated` | 로그인이 필요함, 또는 세션이 만료됨 |
| 401 | `invalid_credentials` | 이메일 또는 비밀번호가 틀림 (로그인) |
| 403 | `email_not_verified` | 이메일 인증 전 로그인 |
| 403 | `invalid_credentials` | 계정 삭제 시 비밀번호가 틀림 |
| 403 | `reauth_required` | Google 전용 계정이 삭제하려면 다시 로그인해야 함 |
| 403 | `origin_not_allowed` | 허용되지 않은 `Origin`, 또는 쿠키 요청에 `Origin`이 없음 |
| 409 | `google_conflict` | 이메일이 이미 다른 Google 계정에 연결됨 |
| 413 | `payload_too_large` | 본문이 64KB를 넘음 (청크 전송으로 넘으면 `detail`만 옴) |
| 422 | (없음) | 입력 검증 실패. `detail`에 첫 오류 한 줄 |
| 429 | `rate_limited` | 요청이 너무 많음. `Retry-After` 헤더(초)가 붙음 |

## 속도 제한

프로세스 메모리 기반의 보조 장치입니다 (재시작하거나 replica가 늘면 약해짐). 1차 방어는 ingress/Cloudflare입니다. 프론트는 429를 받으면 `Retry-After`만큼 기다리게 하면 됩니다.

| 대상 | 한도 |
| --- | --- |
| 로그인 실패 | `(이메일, IP)`당 15분에 10회. 이메일 단독은 15분에 30회, IP 단독은 15분에 50회 |
| 가입 | IP당 시간당 5회 |
| 메일을 보내는 요청 (가입·재발송·비밀번호 찾기) | 이메일당 시간당 3회, IP당 시간당 10회. 계정이 있는지와 관계없이 센다 |
| 인증·재설정 링크 사용 | IP당 시간당 30회 |
| Google 로그인 시작 | IP당 시간당 60회 |
| 계정 삭제 비밀번호 실패 | 계정당 15분에 5회 |
| 읽음 기록 저장 (`POST /me/reads`) | 계정당 분당 60회 |

실패한 로그인만 센다는 점이 중요합니다. `(이메일, IP)` 기준이라서 남이 내 이메일로 틀려도 다른 IP의 나는 로그인할 수 있습니다.

## 아직 없는 것 (다음 단계)

- 즐겨찾기 서버 저장 (`/me/favorites`, localStorage 가져오기), 사용자별 캘린더 토큰 URL.
- 앱용 `POST /auth/google/token` (네이티브 Google ID 토큰). 웹 콜백과 같은 `upsert_google_user`를 쓸 예정입니다.
- 푸시 토큰, 키워드 알림.
