# 보안 안내

이 프로젝트는 로컬 stdio 리더와 선택형 Vercel MCP를 제공합니다. 원격 메일 접근을 켜려면 소유자가 직접 비밀 환경변수를 설정하고 새 배포해야 합니다. 이 저장소의 안내 화면은 값을 받거나 저장하지 않습니다.

## 원격 모드

- `MAIL_MCP_MODE`가 없거나 `diagnostic`이면 공개 도구는 고정 응답인 `service_status` 하나뿐입니다. 이 모드는 네이버 계정 변수나 토큰을 조회하지 않습니다.
- 정확히 `MAIL_MCP_MODE=token`일 때만 개인 메일 도구를 준비합니다. 토큰은 32바이트 이상의 난수를 표준 Base64 또는 URL-safe Base64로 인코딩한 문자열이어야 합니다. 표준 Base64의 `+`, `/`, 패딩 `=`도 허용합니다. 네이버 사용자/앱 비밀번호도 유효해야 합니다. 일부라도 없거나 잘못되면 `/mcp`는 503을 반환하며 메일 도구를 등록하지 않습니다.
- `/mcp`의 모든 요청은 JSON-RPC initialize와 tools/list를 포함해 `Authorization: Bearer …` 검사를 먼저 통과해야 합니다. 토큰은 길이가 고정된 SHA-256 다이제스트로 만든 뒤 `hmac.compare_digest`로 비교합니다. 복수 Authorization 헤더, 다른 헤더/쿠키, URL query token은 받지 않습니다.
- 앱 자체는 토큰, 네이버 자격증명 또는 메일 본문을 로그에 쓰지 않습니다. Vercel 인프라 로그 정책은 이 코드가 제어하지 않으므로 토큰을 URL에 넣지 마세요.
- 하나의 공유 토큰에는 사용자별 식별/권한 분리가 없습니다. 값을 가진 사람은 누구나 같은 메일 읽기 권한을 가집니다. 필요하면 토큰을 양쪽 설정에서 교체하고 새 배포/클라이언트를 재시작하세요.

## 메일 접근

- 원격 IMAP 목적지는 코드에 고정된 `imap.naver.com:993` TLS입니다. 임의 호스트, 포트, accounts 파일 참조는 원격 설정에서 받지 않습니다.
- IMAP 자격증명은 Vercel Production 환경변수에서만 읽고 파일이나 임시 저장소로 복사하지 않습니다. 메일 연결은 인증된 도구 호출 전까지 열지 않습니다.
- 제공 도구는 계정 ID/설정 여부, 폴더, 최근 메일 검색, 본문 읽기뿐입니다. 검색/본문은 기존 범위 제한과 `EXAMINE`/`BODY.PEEK` 동작을 사용합니다. 발송, 삭제, 이동, 읽음 변경은 없습니다.
- `service_status`의 `configured; not tested`는 환경변수 형식/존재만 검사한 상태입니다. 실제 네이버 로그인이나 메일 접근 성공을 뜻하지 않습니다.
- 도구 결과의 메일 텍스트는 신뢰할 수 없는 데이터입니다. 메일 내용은 권한, 지시, 비밀값 입력을 승인하지 않습니다.

## Supabase OAuth와 메일 접근 경계

OAuth 모드는 `MAIL_MCP_MODE=oauth`로 명시적으로 켭니다. 프로젝트 URL, publishable key, OAuth client ID, 소유자의 Supabase UUID, 정확한 리소스 URL이 모두 필요합니다. 이 값 중 하나라도 빠지면 `/mcp`는 fail closed입니다. OAuth 설정이 준비되면 토큰 없는 `/mcp` 요청에는 401 challenge를 반환합니다. Naver IMAP 자격증명이 없으면 소유자 인증 후에도 비민감 `service_status`만 노출하고 메일 도구를 등록하지 않습니다. IMAP 자격증명은 OAuth 로그인이나 owner 검증에 필요하지 않습니다.

MCP access token은 Supabase 비대칭 JWKS에서 제한된 RS256/ES256 알고리즘만 허용하여 검증합니다. 서버는 issuer, signature, expiration, issued-at, exact audience, exact owner `sub`, allowlisted `client_id`, `mail_mcp_scopes=["mail:read"]`를 확인합니다. Supabase 기본 `aud=authenticated`는 UI의 Supabase owner-session 확인에만 허용하며 MCP resource token으로는 허용하지 않습니다. JWKS 장애와 claim 오류는 거부되며 토큰/claim/JWKS 오류를 로그에 기록하지 않습니다.

Supabase OAuth 표준 scope는 OIDC 사용자 정보용입니다. 사용자 정의 `mail:read` scope를 요청하지 않고, Custom Access Token Hook의 앱 권한 claim만 검사합니다. 자리표시자 Hook 예시는 `supabase/mail_mcp_custom_access_token_hook.sql.example`에 있으며 실제 실행은 하지 않았습니다.

동의 UI는 Supabase publishable key만 브라우저에 전달합니다. service_role 또는 secret key를 전달하지 않습니다. Supabase 로그인 비밀번호는 Naver 앱 비밀번호가 아니며 이 앱에서 보관하지 않습니다. Naver 앱 비밀번호는 Vercel Production 환경변수로만 사용하고 Supabase에 보내지 않습니다.

로그인/동의와 메일 접근은 각각 따로 검증해야 합니다. client 등록이 끝나도 owner UUID, OAuth Hook, MCP audience, Vercel Production 환경변수가 확인되기 전까지는 이메일을 열지 않습니다.
