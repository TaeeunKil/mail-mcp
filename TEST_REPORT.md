# 검증 결과

## 2026-10-26 로컬 OAuth 준비

- 작업 위치: `C:\Users\user\Documents\Codex\2026-10-26\task-2\mail-mcp`.
- Supabase OAuth 단위 테스트: **50 passed**.
- IMAP core 단위 테스트: **32 passed**.
- Bearer/Base64 및 설정 단위 테스트: **14 passed, 10 deselected**. 선택된 테스트는 HTTP `TestClient`를 사용하지 않는 설정/토큰 형식 검증입니다.
- `python -m compileall -q app.py imap_connector tests`: 통과.
- `python -m pip check`: 통과.
- `git diff --check`: 통과 (LF/CRLF 안내만 표시).
- 모든 토큰/키/사용자 ID는 테스트 전용 합성 값이었습니다. 실제 메일이나 Supabase endpoint에 연결하지 않았습니다.

## 전체 테스트 실행 제한

- 전체 115개 테스트를 실행했으나 `tests/test_http.py::test_http_app_uses_sdk_lifespan`에서 MCP SDK initialize가 Windows 실행 환경에서 멈춰 완료되지 않았습니다. 프로세스를 중단했고 전체 통과로 보고하지 않습니다.
- 별도 `tests/test_mcp.py::test_stdio_protocol`도 stdio protocol 초기화에서 멈췄습니다.
- `tests/test_token_auth.py` 전체는 `TestClient` 앱 시작에서 멈췄습니다. HTTP/stdio 통합 테스트는 이번 환경에서 검증되지 않았습니다.
- 위 중단은 테스트 실패 결과가 아니라 실행 정체입니다. Vercel의 실제 HTTP 요청 또는 배포 상태도 이 테스트로 검증하지 않았습니다.

## 로컬 구현 상태

- 진단 모드 기본값은 유지하며 공개 상태 도구만 제공합니다.
- OAuth opt-in 모드에는 Supabase issuer/JWKS 파생, ES256/RS256 서명 검증, exact audience, owner UUID, client ID, `mail_mcp_scopes` 검증을 추가했습니다.
- consent UI는 Supabase JS SDK authorization_id, login, approve/deny 흐름을 사용합니다. 비밀번호 입력값은 제출 직후 비우며 Naver 비밀번호 필드나 저장 기능은 없습니다.
- OAuth 및 메일 환경변수가 완전하지 않으면 메일 도구를 등록하지 않고 503으로 차단합니다. 테스트용 합성 Naver 설정에서 기존 read-only 도구만 등록되는 것을 확인했습니다.
- Hook SQL은 자리표시자 예시이며 실제 프로젝트에 실행하지 않았습니다.

## 브라우저에서 확인한 설정과 미검증 상태

- 별도 `mail-mcp-auth` Supabase Free/Seoul 프로젝트, OAuth Server 활성화, Site URL `https://mail-mcp-beta.vercel.app`, Authorization Path `/oauth/consent`, DCR OFF가 부모 브라우저에서 확인됐습니다.
- ChatGPT callback은 `https://chatgpt.com/connector/oauth/76ZjhxWCIq3k`로 확인됐습니다. 공개 PKCE client 생성은 승인 대기 중입니다.
- 현재 Production 코드는 `b0494b4`이며 `/oauth/consent`는 아직 404입니다. 로컬 코드는 배포되지 않았습니다.
- 실제 `initialize`, OAuth callback/consent/token exchange, owner UUID, JWKS HTTP fetch, Naver login/mail read는 확인하지 않았습니다.
- Push와 redeploy는 이번 로컬 검증 보고 이후 승인된 절차로 진행합니다. 실제 OAuth client 생성은 별도 승인 대기이며, 실제 Naver 비밀값은 사용자가 직접 Vercel에 설정해야 합니다.

## Windows 테스트 런타임 진단

- `tests/test_http.py::test_http_app_uses_sdk_lifespan`을 12초 제한으로 실행하면 앱 lifespan이나 MCP `initialize` 요청에 도달하기 전에 `TestClient.__enter__()`에서 멈춥니다.
- `tests/test_mcp.py::test_stdio_protocol`도 12초 제한 실행에서 클라이언트 코루틴이 시작되기 전 `asyncio.run()`의 이벤트 루프 생성 단계에 멈춥니다.
- `faulthandler` 스택은 두 경로 모두 Python 3.12.10 Windows `ProactorEventLoop._make_self_pipe()`에서 `socket._fallback_socketpair()`를 거쳐 `socket.accept()`를 기다림을 보여줍니다. `asyncio.new_event_loop()`만 호출하는 최소 스크립트에서도 같은 대기가 재현됐고 7초 후 종료했습니다.
- 이 실행환경은 루프백 socket-pair 초기화를 완료하지 못합니다. 관찰된 대기는 MCP 앱 코드와 무관합니다. 샌드박스 제한인지 Windows 네트워크 스택 문제인지는 실행환경 설정을 바꾸지 않고 구분할 수 없어 변경하지 않았습니다. 이 bounded 실행들은 assertion 실패가 아닌 환경 제한 상태이며, HTTP lifespan과 stdio protocol은 이 환경에서 미검증입니다.
