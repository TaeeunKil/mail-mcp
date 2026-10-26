# 검증 결과

검증일: 2026-10-24 (UTC)

- Python 3.12, 별도 venv, 공식 MCP SDK 2.3.0.
- `python -m pytest -q`: **30 passed**.
- `pip install .`: 성공. 배포용 entry point와 패키지 설치 확인.
- `python -m compileall -q imap_connector`: 성공.
- `pip check`: 의존성 충돌 없음.
- 실제 SDK의 stdio subprocess handshake, 도구 4개 목록, 읽기 전용 힌트, 자격증명 없는 계정 목록, 잘못된 ID 응답을 검사.
- 모의 IMAP: 인증서/호스트 검증, EXAMINE 및 BODY.PEEK, 범위 제한, 주입 방지, UIDVALIDITY, 한글 헤더/본문/메일함/검색 CHARSET, MIME 첨부 제외, 큰 본문 다운로드 거부, 오류 비밀값 제거, 잘못된 설정/응답 검사.

## 수행하지 않은 검증

- 실제 네이버/회사 메일 로그인, 실메일 읽기, 공급자별 호환성.
- dot 연결, 원격 서버 인증·인가, OAuth, 배포, 운영 보안 감사.
- 모든 MIME 변형 및 악성 서버의 완전한 리소스 격리.

자격증명은 사용하지 않았으며 테스트에는 명시적인 가짜 값만 사용했습니다. 메일 서버 연결 및 외부 서버 공개는 없었습니다. stdio 프로세스 실행만 검사했습니다.

## 저장소 준비 추가 검증

- 보안 안내와 네이버 우선 README 추가 후 로컬 테스트 30개 재통과.
- GitHub Actions 워크플로: Python 3.11/3.12/3.13 테스트 계획. 원격 CI는 아직 실행하지 않았으며 이 매트릭스의 통과를 주장하지 않습니다.
- 로컬에서 실행한 Python 버전은 3.12입니다.

## Vercel 원격 MCP 추가 검증 (2026-10-26)

- Windows Python 3.12.10, MCP SDK 2.3.0에서 실행.
- Vercel Import 설치 명령인 `pip install -r requirements.txt`: 통과. `requirements.txt`는 `mcp==2.3.0`을 고정.
- `python -m pip check`: 의존성 충돌 없음. `python -m compileall -q app.py imap_connector tests`: 통과.
- 전체 `pytest`: **40 passed**. 모의 IMAP 로그아웃/오류/시간 제한, 한글 헤더와 HTML 본문, stdio handshake, HTTP lifespan, 14개 반복/동시 진단 요청, 메일 도구 미노출, 안내 화면의 비수집 상태를 검증.
- 실제 네이버 계정/비밀번호, 메일 서버 접속, 외부 URL 배포, ChatGPT OAuth 로그인은 사용하지 않음. Vercel에 추가 배포하지 않음.
- Windows sandbox의 일반 실행에서 비동기 runtime이 localhost socketpair에서 대기하여, 로컬 TestClient suite만 지역 ASGI 테스트 권한으로 실행함. 외부 네트워크/메일 호출은 없었음.

### 미검증/보류

- Vercel 실제 build/deploy, 배포 URL의 소유자 전용 보호, ChatGPT의 소유자 OAuth issuer/토큰 플로우.
- 네이버 실계정 등록, 실제 메일 읽기, 비밀번호 저장/폐기.
