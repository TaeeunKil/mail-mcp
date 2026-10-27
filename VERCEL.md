# Vercel 및 Codex 연결 안내

## 현재 배포 상태

- 현재 주소는 <https://mail-mcp-beta.vercel.app/>입니다. 최신 main push의 Vercel 배포 체크는 성공했습니다. Production은 현재 진단 모드를 유지하며 실제 HTTP 응답은 이 작업에서 확인하지 못했습니다.
- 이번 배포에는 데스크톱 Bearer 모드와 Supabase OAuth consent/JWT 검증 코드가 포함됐습니다. 배포됐다는 사실은 메일 접근이 활성화됐다는 뜻이 아닙니다. 진단 모드가 기본이며 필요한 인증·메일 설정이 모두 있어야 전용 모드가 작동합니다.
- Chrome에서 `/` 안내 화면은 정상 표시됐다는 사용자 확인이 있습니다. `/mcp` 브라우저 탐색은 `ERR_BLOCKED_BY_CLIENT`, 별도 HTTP 요청은 DNS 제한/도구의 URL 접근 거부로 응답을 얻지 못했습니다. `initialize`, `tools/list`, `service_status`의 실제 HTTP 응답은 검증되지 않았고 서버 오류로 확정할 수도 없습니다.
- 현재까지 네이버 메일, OAuth 공급자, 토큰, 앱 비밀번호를 사용하지 않았습니다.

## 원격 모드

Vercel 함수는 `app.py`의 ASGI 앱을 사용하고 MCP SDK 2.3.0의 stateless Streamable HTTP를 `/mcp`에서 제공합니다.

| `MAIL_MCP_MODE` | 공개 동작 |
| --- | --- |
| 값 없음 또는 `diagnostic` | `service_status` 하나만 공개. 네이버 자격증명과 Bearer 토큰은 조회하지 않음 |
| `token` | 개인용 Bearer 인증 성공 후 `service_status`, `list_accounts`, `list_folders`, `search_messages`, `read_message` 제공 |
| 다른 값 | 비공개 서비스로 fail closed; `/mcp`는 503 |

`token` 모드에서 토큰이나 네이버 설정이 없거나 유효하지 않으면 도구를 등록하지 않고 `/mcp`는 503을 반환합니다. 설정이 정상이어도 인증이 없거나 틀리면 초기화와 도구 목록부터 401로 막습니다. 토큰은 URL query string, 쿠키, 별도 임의 헤더에서 받지 않고 `Authorization: Bearer …`만 받습니다.

## 데스크톱 Codex용 개인 토큰 설정

이 방식은 부모가 확인한 **데스크톱 Codex 맞춤형 MCP 설정**에서 토큰 환경변수를 제공하는 연결을 위한 것입니다. 실제 토큰을 생성하거나 등록하지 않았으며, 아래 과정은 사용자가 자신의 컴퓨터와 Vercel 대시보드에서 직접 수행할 때 사용합니다.

1. 본인 컴퓨터에서 암호학적 난수 32바이트를 표준 Base64로 인코딩해 만듭니다. 출력된 문자열을 대화, 저장소, URL, 티켓에 붙이지 마세요. 표준 Base64의 `+`, `/`, 끝의 `=`를 포함한 값과 URL-safe Base64 표현을 모두 지원합니다.

   ```powershell
   $bytes = New-Object byte[] 32
   $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
   try {
     $rng.GetBytes($bytes)
     [Convert]::ToBase64String($bytes)
   } finally {
     $rng.Dispose()
   }
   ```

2. Vercel 프로젝트의 Settings → Environment Variables에서 다음 값을 **Production**에 추가합니다.

   | 변수 | 값 |
   | --- | --- |
   | `MAIL_MCP_MODE` | `token` |
   | `MAIL_MCP_TOKEN` | 위에서 로컬로 만든 임의 문자열 |
   | `NAVER_IMAP_USER` | 네이버 메일 주소 |
   | `NAVER_IMAP_APP_PASSWORD` | 네이버에서 발급한 앱 비밀번호 |

3. Production 환경변수는 해당 코드가 포함된 deployment가 성공한 뒤 적용됩니다. 모드가 명시되지 않았거나 인증·메일 설정이 부족하면 진단 전용 또는 fail-closed 상태로 남습니다.
4. 데스크톱 Codex 사용자 설정에서 맞춤형 MCP 연결을 만들고 Streamable HTTP를 선택합니다. URL은 `https://mail-mcp-beta.vercel.app/mcp`입니다. 인증 토큰 환경변수 이름에 `MAIL_MCP_TOKEN`을 넣고, Codex 사용자 환경변수에도 Vercel과 같은 값을 직접 설정합니다.
5. Codex를 재시작한 다음 연결을 확인합니다. `service_status`는 구성값이 준비됐다고만 보고하며 네이버 IMAP 로그인을 시험하지 않습니다. 실제 메일 연결 테스트는 소유자가 승인한 뒤에만 진행해야 합니다.

Bearer 토큰과 네이버 앱 비밀번호는 서로 다른 값입니다. Bearer 토큰은 Vercel과 Codex 양쪽에 두고, 네이버 앱 비밀번호는 Vercel에만 둡니다. 이 저장소는 값을 받거나 저장하는 웹 폼, 브라우저 저장소, 파일 저장 기능을 제공하지 않습니다.

## 네이버 사전 조건 및 고정된 권한

- 네이버 메일에서 IMAP 사용을 켜고 2단계 인증/앱 비밀번호 발급 가능 여부를 확인합니다.
- 일반 네이버 로그인 비밀번호 대신 네이버가 발급한 앱 비밀번호를 사용합니다.
- 원격 연결 목적지는 코드에 고정한 `imap.naver.com:993` TLS입니다. 임의 호스트/포트는 설정할 수 없습니다.
- 기존 읽기 전용 검색과 본문 읽기만 노출합니다. 발송, 삭제, 이동, 읽음 상태 변경은 없습니다.
- `list_accounts`는 인증된 호출자에게 `naver` 계정 ID와 설정 여부만 반환합니다. 이메일 주소와 비밀번호는 도구 응답에 넣지 않습니다.
- 검색 및 읽기 도구는 호출할 때 IMAP 세션을 열고 끝날 때 로그아웃합니다. 설정 값만 있다고 연결 성공으로 간주하지 않습니다.

## 토큰 교체 및 접근 중단

- 교체: 본인 컴퓨터에서 새 토큰을 만들고 Vercel의 `MAIL_MCP_TOKEN`과 Codex 사용자 환경변수를 모두 갱신한 뒤 Vercel 재배포/Codex 재시작을 합니다. 이전 토큰은 새 배포 이후 더 이상 인증되지 않습니다.
- 서버 접근 중단: Vercel에서 `MAIL_MCP_MODE`를 `diagnostic`으로 바꾸거나 `MAIL_MCP_TOKEN`을 제거하고 다시 배포합니다. 진단 모드에는 메일 도구가 없습니다.
- 네이버 앱 비밀번호 폐기/교체는 네이버 보안 설정에서 직접 해야 합니다. 이 MCP는 비밀번호를 취소하거나 변경할 수 없습니다.
- 공유 토큰을 받은 사람은 누구나 같은 읽기 권한을 가지므로, 더 이상 신뢰하지 않는 사람에게 노출됐다면 토큰과 네이버 앱 비밀번호를 각각 교체하세요.

## ChatGPT 웹/cloud dot OAuth (Supabase)

### 확인된 프로젝트 설정

- 기존 wordloop 프로젝트는 수정하지 않았습니다. 별도 Supabase 프로젝트 `mail-mcp-auth`는 Free/Seoul이고 OAuth Server를 활성화했습니다.
- Supabase Site URL은 `https://mail-mcp-beta.vercel.app`, Authorization Path는 `/oauth/consent`로 확인했습니다. 이 조합의 동의 화면 경로는 `https://mail-mcp-beta.vercel.app/oauth/consent`입니다.
- ChatGPT가 보여준 등록 callback URI는 `https://chatgpt.com/connector/oauth/76ZjhxWCIq3k`입니다. Supabase OAuth Apps에 정확히 일치하는 URI로 등록해야 합니다. 와일드카드는 사용하지 않습니다.
- Dynamic Client Registration은 OFF입니다. 공개 PKCE client는 생성됐고 ChatGPT callback URI는 `https://chatgpt.com/connector/oauth/76ZjhxWCIq3k`입니다. client 식별자는 Vercel의 `MAIL_MCP_OAUTH_CLIENT_ID`에 설정해야 하며 소스에 저장하지 않습니다. 전체 OAuth 흐름은 아직 검증하지 않았습니다.
- 이전 배포 확인에서는 OAuth 설정이 없었습니다. 이후 사용자가 Vercel OAuth 환경변수와 owner/client 제한 Hook을 구성하고 재배포했다고 확인했습니다. 이 변경도 Production에 배포됐으며, 무인증 `/mcp` initialize 요청에서 401과 보호 리소스 metadata challenge가 실제로 확인됐습니다. Naver IMAP 자격증명은 의도적으로 설정하지 않았습니다. 인증 후에는 코드와 CI가 비민감 `service_status`만 노출하도록 검증했으며, 실제 OAuth 로그인은 브라우저에서 계속 확인 중입니다.

### 로그인과 동의 흐름

1. MCP 클라이언트는 Supabase authorization endpoint로 이동하고 Supabase가 `/oauth/consent?authorization_id=...`로 돌아옵니다.
2. 소유자는 이 페이지에서 Supabase 계정으로 로그인합니다. 로그인 화면은 가입 기능이 없으며 Naver 비밀번호를 받지 않습니다.
3. Supabase JS SDK의 `getAuthorizationDetails`, `approveAuthorization`, `denyAuthorization`으로 요청한 앱, callback URI, OIDC scope를 확인하고 동의/거부합니다. Authorization Code와 PKCE 교환은 Supabase가 처리합니다.
4. 서버는 `/mcp` 요청마다 Supabase JWKS 서명, 정확한 issuer와 리소스 audience, 만료/발급 시각, 고정 owner UUID, 등록된 OAuth client ID, `mail_mcp_scopes=["mail:read"]` claim을 확인합니다. 불일치하거나 JWKS에 접근할 수 없으면 거부합니다.
5. Supabase 기본 audience `authenticated`를 MCP 리소스 audience로 간주하지 않습니다. Custom Access Token Hook 예시는 OAuth client 하나에만 정확한 MCP audience와 앱 권한 claim을 추가합니다. 표준 OAuth scopes는 OIDC 정보용이며 `mail:read` 같은 사용자 정의 scope는 현재 Supabase OAuth에서 지원되지 않습니다.
6. OAuth 환경 설정이 빠지거나 잘못되면 `/mcp`는 503으로 fail closed 합니다. OAuth 설정이 준비되면 토큰 없는 `/mcp` 요청은 401 OAuth challenge를 반환합니다. Naver IMAP 설정이 없을 때 소유자 인증에 성공한 클라이언트에는 비민감 `service_status`만 보이고 메일 도구는 등록되지 않습니다. 유효한 Naver 설정이 추가된 뒤에만 기존 읽기 전용 메일 도구가 등록됩니다.

### Vercel Production 변수 이름

실제 값은 사용자가 Vercel 대시보드에 직접 설정해야 합니다. 이 저장소에는 값이나 비밀번호를 넣지 않습니다.

| 변수 | 용도 |
| --- | --- |
| `MAIL_MCP_MODE` | OAuth 모드 opt-in 값 `oauth` |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_PUBLISHABLE_KEY` | 브라우저 SDK용 공개 publishable key |
| `MAIL_MCP_OAUTH_CLIENT_ID` | 등록된 공개 PKCE client 식별자. Vercel에 직접 설정하며 source에 넣지 않습니다. |
| `MAIL_MCP_OWNER_SUB` | Supabase 로그인 후 확인한 소유자 UUID |
| `MAIL_MCP_RESOURCE_URL` | 정확한 보호 리소스 URL `https://mail-mcp-beta.vercel.app/mcp` |
| `NAVER_IMAP_USER` | Naver 메일 주소 |
| `NAVER_IMAP_APP_PASSWORD` | Naver 앱 비밀번호 |

`SUPABASE_PUBLISHABLE_KEY`는 공개 클라이언트 키이며 secret/service_role key와 다릅니다. 브라우저나 환경변수에 service_role key를 추가하지 않습니다. 환경변수가 모두 저장되고 Production 재배포가 끝나기 전에는 연결 완료로 표시하지 않습니다.

Custom Access Token Hook SQL은 예시일 뿐 실행되지 않았습니다. owner `sub` UUID, client ID, resource URL 세 placeholder를 채운 뒤, exact client ID와 owner UUID가 모두 일치할 때만 `aud`와 `mail_mcp_scopes=["mail:read"]`를 설정하도록 제한되어 있습니다. owner UUID가 아직 확인되지 않았으므로 Hook을 활성화하거나 SQL을 실행하지 마세요. 적용 전에는 resource URL `https://mail-mcp-beta.vercel.app/mcp`를 정확히 대입하고 검토하세요. DB SDK나 service_role은 필요하지 않습니다.

### 현재 확인되지 않은 항목

- 브라우저에서 OAuth Server, Site URL/Authorization Path, DCR OFF, 공개 PKCE client, ChatGPT callback URI, owner/client 제한 Hook 설정을 확인했습니다. Production OAuth 연결의 end-to-end 결과는 별도 브라우저 확인 중입니다.
- 프로젝트 JWKS는 ES256으로 확인됐고 로컬 검증기는 ES256 및 RS256을 제한적으로 허용합니다. 실제 Supabase 토큰이나 ChatGPT callback을 로컬 테스트에 사용하지 않았습니다.
- Supabase OAuth가 MCP 클라이언트의 `resource`, `offline_access`, 공개 PKCE 조합을 처리하지 못했다는 [공개 호환성 보고](https://github.com/supabase/auth/issues/2820)가 있습니다. 사용자 프로젝트에서 재현됐다는 뜻은 아닙니다. 실제 client 등록 후 authorize → 로그인 → 동의 → callback → MCP token 요청을 확인해야 합니다.
- 로컬 테스트는 합성 RSA/ES256 키와 모의 claim만 사용합니다. 이 작업에서 메일 연결이나 비밀 등록은 하지 않습니다. Production OAuth 연결 시작 결과는 브라우저 테스트와 별도로 확인해야 합니다.

## OAuth 세션 진단 코드

Supabase `signInWithPassword`가 성공한 뒤에도 MCP가 로그인 세션 JWT를 별도로 검증합니다. 이 검증은 세션 `aud=authenticated`, 검증된 Supabase issuer/JWKS, 만료/시작 시간, `sub`를 확인하며 MCP 리소스 audience, OAuth client, mail scope를 필요로 하지 않습니다. 반대로 `/mcp`는 별도의 MCP resource token에 필요한 owner/client/audience/scope 검증을 그대로 적용합니다.

소유자 승인 UI는 비밀번호 로그인 요청 성공과 이후 `/oauth/session-check` 검증을 다른 단계로 표시합니다. 검증이 401을 반환하면 `reason`은 고정된 코드 하나만 들어있으며, token, claim 값, exception 문자열은 응답하지 않습니다.

| 응답 코드 | 의미 |
| --- | --- |
| `issuer_mismatch` | 토큰 issuer가 `SUPABASE_URL`에서 계산한 Supabase issuer와 다릅니다. |
| `audience_mismatch` | 로그인 세션 audience가 `authenticated`가 아닙니다. MCP `/mcp` audience와는 다릅니다. |
| `expired`, `not_yet_valid`, `issued_at_invalid` | 세션의 시간 클레임을 통과하지 못했습니다. |
| `invalid_signature`, `key_algorithm_mismatch` | 서명 검증 실패 또는 토큰과 JWKS 키 알고리즘 불일치입니다. |
| `jwks_unavailable`, `jwks_invalid`, `signing_key_not_found` | 공개 JWKS 연결, 응답 형식, 또는 키 ID 선택 문제입니다. |
| `missing_claim`, `invalid_subject`, `invalid_claims`, `malformed_token`, `invalid_key_id`, `unsupported_algorithm` | 세션 JWT의 형식 또는 필수 클레임 문제입니다. |
| `verification_unavailable` | 분류하지 못한 일시적 검증 오류입니다. |
| HTTP 403 (`owner: false`) | 세션 JWT 검증은 통과했지만 `sub`가 설정된 소유자와 다릅니다. 값 자체는 표시하지 않습니다. |

서버는 `SUPABASE_URL`에서 `https://<project-ref>.supabase.co/auth/v1` issuer와 `/auth/v1/.well-known/jwks.json` JWKS URL을 고정합니다. PyJWT 2.15.1의 예외를 위 고정 분류로 매핑하며 오류 문자열을 반환하거나 로깅하지 않습니다. 현재 공개 JWKS 확인은 HTTP 200과 ES256 서명 키를 확인했으며, 이 코드는 ES256/RS256만 허용합니다.

다음 브라우저 확인에서는 UI의 `진단 코드`만 확인하면 됩니다. JWT, `Authorization` 헤더, claim, 이메일, 비밀번호를 공유하지 마세요. `/mcp` 응답은 오류 세부사항을 노출하지 않고 일반 OAuth 401 challenge를 유지합니다.
