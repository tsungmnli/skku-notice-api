# 배포 체크리스트 (계정 기능)

코드 변경은 없고, 운영에 올리기 전에 코드 밖에서 해야 하는 일을 모았습니다.
운영 주소는 `https://skkunoti.tsungmn.com`이고, API는 같은 호스트의 `/api`입니다 (ingress가 `/api` 접두사를 떼지 않고 그대로 넘깁니다).

## 1. Resend (메일)

- [ ] Resend 대시보드 Domains에서 발송 도메인을 등록합니다. 기본값은 `skkunoti.tsungmn.com`이고, travel이 이미 쓰는 공용 발송 도메인이 있으면 그걸 재사용해도 됩니다. 어느 쪽이든 `SMTP_FROM`만 바꾸면 됩니다.
- [ ] Resend가 알려주는 SPF(MX, TXT)와 DKIM(TXT) 레코드를 Cloudflare DNS에 그대로 넣습니다. 프록시(주황 구름)는 끕니다.
- [ ] DMARC를 `_dmarc.<발송 도메인>`에 TXT로 추가합니다. 처음에는 `v=DMARC1; p=none; rua=mailto:<받을 주소>`로 시작해서 리포트를 보고 강화합니다.
- [ ] API 키를 만듭니다 (Sending access 권한이면 충분). 이 값이 `SMTP_PASSWORD`입니다.
- [ ] Resend에서 도메인이 Verified가 된 뒤 테스트 메일을 한 통 보내 봅니다.

| 환경 변수 | 값 |
|---|---|
| `SMTP_HOST` | `smtp.resend.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `resend` |
| `SMTP_PASSWORD` | Resend API 키 |
| `SMTP_FROM` | `SKKU 공지 <noreply@skkunoti.tsungmn.com>` (실제 발송 도메인에 맞춰 변경) |

## 2. Google 로그인

- [ ] Google Cloud 콘솔에서 OAuth 클라이언트 ID를 **웹 애플리케이션** 유형으로 만듭니다.
- [ ] 승인된 리디렉션 URI: `https://skkunoti.tsungmn.com/api/v1/auth/google/callback`
- [ ] 승인된 JavaScript 원본: `https://skkunoti.tsungmn.com`
- [ ] OAuth 동의 화면에서 범위는 `openid`, `email`, `profile`만 씁니다. 게시 상태를 **프로덕션**으로 바꿔야 테스트 사용자가 아닌 사람도 로그인할 수 있습니다.
- [ ] 클라이언트 ID와 secret을 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`으로 넣습니다. 둘 중 하나만 있거나 `SESSION_SECRET`이 없으면 서버가 뜨지 않습니다.
- 앱(Android/iOS)용 네이티브 클라이언트 ID는 앱 단계에서 따로 만듭니다.

## 3. Cloudflare Turnstile

- [ ] Turnstile에서 사이트를 만들고 허용 호스트에 `skkunoti.tsungmn.com`을 넣습니다. 위젯 모드는 Managed를 권장합니다.
- [ ] **Secret key**는 `TURNSTILE_SECRET_KEY`로 넣습니다 (API가 검증에 사용).
- [ ] **Site key**는 공개값이고 웹 프론트가 씁니다. 웹 단계에서 사용합니다.
- 가입 요청의 본문 필드는 `turnstile_token`입니다. Cloudflare에 닿지 못하면 가입은 실패 쪽으로 닫힙니다.

## 4. 프록시 단계 수 (`TRUSTED_PROXY_HOPS`)

- [ ] Cloudflare → ingress → 파드 구조면 `2`입니다. 이 값이 틀리면 속도 제한이 한 IP로 뭉치거나 `X-Forwarded-For` 위조를 못 막습니다.
- [ ] 배포 후 확인: 계정 요청을 몇 번 보내고, 로그나 제한 키에 찍히는 IP가 **내 공인 IP**인지 봅니다. 클라우드플레어나 클러스터 내부 IP가 보이면 값이 틀린 것입니다.
- 모르면 확인 전까지 보수적으로 `2`로 두고, 위 확인으로 맞춥니다.

## 5. 시크릿과 설정

k8s 시크릿(또는 쓰고 있는 시크릿 저장소)에 아래를 추가합니다. prod에서는 빠진 값이 있으면 서버가 일부러 뜨지 않습니다.

| 변수 | 비고 |
|---|---|
| `APP_ENV` | `prod` |
| `DATABASE_URL` | 기존 값 |
| `PUBLIC_URL` | `https://skkunoti.tsungmn.com` |
| `CORS_ORIGINS` | `https://skkunoti.tsungmn.com` (`*` 불가) |
| `SESSION_SECRET` | 32자 이상 랜덤 값. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SMTP_*` | 1번 표 |
| `TURNSTILE_SECRET_KEY` | 3번 |
| `TRUSTED_PROXY_HOPS` | 4번 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 선택. 2번 |

- [ ] **마이그레이션 Job도 같은 시크릿을 받아야 합니다.** `migrations/env.py`가 API와 같은 설정 검증(`get_settings()`)을 거치므로, Job에 새 값이 빠져 있으면 Job이 실패하고 Sync가 막힙니다.
- [ ] 크롤러 CronJob은 별도 설정(`get_crawler_settings`)이라 `DATABASE_URL`만 있으면 됩니다. 새 시크릿은 필요 없습니다.

## 6. 배포 순서

1. 시크릿을 먼저 반영합니다 (Job과 Deployment보다 앞).
2. 이미지를 올리면 Argo CD가 마이그레이션 Job(`python -m app.migrate`)으로 `0003_accounts`를 적용합니다. 적용 후 `users`, `sessions`, `email_tokens` 테이블이 생겼는지 확인합니다.
3. Deployment가 새 이미지로 롤아웃됩니다. 기존 `/api/v1/articles` 등 읽기 API는 그대로 동작해야 합니다.
4. 다음 크롤러 실행 로그에 `cleanup: sessions=N email_tokens=M removed`가 찍히는지 확인합니다.

## 7. 스모크 테스트

- [ ] `GET /api/readyz`가 200입니다.
- [ ] `POST /api/v1/auth/register` (Turnstile 토큰 포함) → 인증 메일 도착 → 링크의 토큰으로 `POST /api/v1/auth/verify-email` → `POST /api/v1/auth/login` → `GET /api/v1/me`가 200입니다. 로그인 응답에 `sid` 쿠키(HttpOnly, Secure, `Path=/api`)가 붙는지 봅니다.
- [ ] `POST /api/v1/auth/logout` 뒤 `GET /api/v1/me`가 401입니다.
- [ ] 비밀번호 재설정 메일이 오고, 재설정 후 기존 세션이 끊깁니다.
- [ ] Google 로그인: `https://skkunoti.tsungmn.com/api/v1/auth/google`로 들어가 로그인하면 사이트로 돌아오고 `sid` 쿠키가 생깁니다.
- [ ] 다른 `Origin`을 붙인 POST가 403입니다.
- [ ] 계정 삭제(`DELETE /api/v1/me`)가 비밀번호 재인증을 요구하고, 삭제 뒤 같은 이메일로 다시 가입할 수 있습니다.
- [ ] 캘린더 피드(`/api/v1/calendar...`)의 캐시 헤더가 그대로입니다 (`no-store`는 `/api/v1/auth*`와 `/api/v1/me*`에만 붙습니다).

## 8. 알려진 한계

- 속도 제한기는 프로세스 메모리에 있어서 재시작이나 replica 증가 때 약해집니다. 보조 장치이고, 1차 방어는 Cloudflare/ingress 제한입니다. Cloudflare에서 `/api/v1/auth/*`에 Rate limiting 규칙을 걸어 두는 것을 권장합니다.
- `--no-proxy-headers` 때문에 슬래시 자동 리다이렉트의 `Location`이 `http://`로 나갈 수 있습니다. 슬래시를 잘못 붙인 요청에서만 보입니다.
- 앱의 이메일 가입은 Turnstile이 네이티브로 돌지 않아서 앱 단계에서 방식을 정해야 합니다.
