# voipbin/mcp 전체 검증 및 갭 분석 (2026-09-28)

## 1. 배경

`github.com/voipbin/mcp` (PyPI `voipbin-mcp` 0.1.1) 은 2026-04-07 마지막 커밋
이후 약 6개월간 변경이 없다. 그 사이 VoIPBin REST API (bin-openapi-manager)
는 상당히 확장되었다. 본 문서는 (a) 이슈가 유효한지, (b) 무엇이 빠졌는지를
실측 기반으로 기록한다.

검증 방법과 그 한계:

- **스펙 대조 (전수, 프로그램)**: `bin-openapi-manager/openapi/openapi.yaml`
  (278 path, 430 operation) 의 (method, path) 집합을 MCP 툴이 호출하는
  (method, 정규화 path) 집합과 비교. 정규화는 `{...}` → `{id}`.
- **툴 인벤토리 (전수)**: FastMCP `list_tools()` → 52개. 호출 메서드 분포는
  GET 35, POST 10, PUT 3, DELETE 4. GET 35 의 내부 구성은 list 17 + by-id 17 +
  싱글턴 1 (`get_customer`, `tools/customer.py:7` — id 를 받지 않는다).
  함수명 기준으로는 `get_*` 가 18개지만 그 중 1개가 이 싱글턴이다.
- **라이브 검증 (부분, §3-2 에 대상 명시)**: `api.voipbin.net/v1.0` 에
  api-validator 계정 accesskey 로 호출. 비용 발생 경로는 CLAUDE.md 규칙에 따라
  **의도적으로 제외**했다.
- **테스트**: `uv run pytest tests/ -v` → 42 passed. 단 이 숫자는 건강 지표가
  아니다 (§6-3).
- **배포 아티팩트 검증**: 빈 venv 에 `pip install voipbin-mcp` 후 import 시도.

## 2. 결론 요약

이슈는 유효하다. 그리고 진단은 "뒤처져 있다"보다 심각하다. **현재 PyPI 배포판은
설치하면 실행조차 되지 않는다.**

| 축 | 판정 | 근거 |
|---|---|---|
| 배포 아티팩트 동작 | **완전 불능** | §3-1 |
| 소스 트리의 경로/메서드 유효성 | 유효 (0 mismatch) | 스펙 대조 mismatch 0건 |
| API 커버리지 | 52 / 430 operation = **12.1%** | 378 operation 미노출 |
| 리소스 커버리지 | 18 / 66 path 디렉터리 | §7 |
| 요청 바디 정확성 | **6건 결함** (4-1, 4-3, 4-5, 4-6 두 건, 4-7) | §4 |
| LLM 대면 docstring 정확성 | **11개 무효 enum 값 (3개 모듈) + 1건 스펙 역행 서술** | §4-4 |
| 클라이언트 계층 | **5건 결함** | §5 |
| 셀프호스팅 지원 | **불가** | §5-4 |
| 테스트 커버리지 | 18 모듈 중 16개 무테스트 + 기존 4개 중 1개는 **틀린 계약을 고정** | §6-3 |
| CI 의 결함 탐지 능력 | **구조적으로 §3-1 을 볼 수 없음** | §6-4 |
| 릴리스 게이트 | **테스트/임포트 검사 없이 publish** | §6-5 |

## 3. 치명 결함: 배포판이 import 되지 않는다

### 3-1. `mcp` 의존성 상한 부재 (BLOCKER)

`pyproject.toml:24` 는 `mcp>=1.0.0` 으로 **상한이 없다**. `mcp` 2.x 는
`mcp.server.fastmcp` 를 제거하고 FastMCP 를 `MCPServer` 로 개명했다.
`uv.lock` 은 1.27.0 을 고정하고 있으나, lock 은 개발 환경에만 적용되고
`pip install voipbin-mcp` 하는 사용자에게는 적용되지 않는다.

빈 venv 실측:

```
$ python3 -m venv probe && ./probe/bin/pip install voipbin-mcp
$ ./probe/bin/pip show mcp | head -2
Name: mcp
Version: 2.2.0
$ ./probe/bin/python -c 'import voipbin_mcp.server'
  File ".../voipbin_mcp/server.py", line 5, in <module>
    from mcp.server.fastmcp import FastMCP
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
where FastMCP was renamed to MCPServer ... or pin 'mcp<2' to keep running v1 code.
```

README 가 안내하는 `pip install voipbin-mcp` / `uvx voipbin-mcp` 경로가 지금
그대로 실패한다. 이것이 최우선 결함이며, 소스 트리의 테스트가 42개 통과하는
것과 무관하게 **제품은 현재 배포 상태로 사용 불가**다.

조치: `mcp>=1.0.0,<2` 로 핀. (2.x 마이그레이션은 별도 스코프. 지금은 배포를
살리는 것이 먼저다.)

### 3-2. 라이브 검증 대상 명시

"모든 경로"를 때린 것이 아니다. 실제로 호출한 것과 하지 않은 것을 구분한다.

호출하여 확인:
- list GET 17개 중 16개 → 200. (`/routes` 는 403, §3-3)
- by-id GET 17개 중 13개 → 200. (실계정에 인스턴스가 없는 conferences,
  emails, messages 및 routes 는 by-id 미확인)
- 싱글턴 `GET /customer` → 200.
- `POST /contacts`, `POST /conferences`, `POST /campaigns`, `POST /ais`,
  `POST /flows` → 200/201, 생성물은 DELETE 200 으로 전부 정리.
- 오류 형상: `GET /calls/not-a-uuid` → 400
  `{"error":{"message":"The provided id is not a valid UUID.","reason":"INVALID_ID","request_id":"req_...","status":"INVALID_ARGUMENT"}}`
- 무인증 `GET /calls` → 401 (동일 중첩 envelope)
- 쿠키 인증 `Cookie: accesskey=...` → 200

**의도적으로 호출하지 않음 (비용 발생 또는 파괴적)**:
- `POST /messages` (send_message) — SMS 과금
- `POST /emails` (send_email) — 이메일 과금
- `POST /calls` (create_call) — PSTN 과금
- `POST /calls/{id}/hangup`, `POST /activeflows/{id}/stop` — 타 세션 파괴
- PUT 3개 (`/campaigns/{id}`, `/contacts/{id}`, `/flows/{id}`) — 생성/삭제
  왕복으로 대체 검증했으나 PUT 자체는 미호출

### 3-3. superadmin 전용 툴이 노출되어 있다

`GET /routes`, `GET /routes/{id}` → **403 PERMISSION_DENIED**.
`bin-api-manager/pkg/servicehandler/route.go:51,86,129,177,217,267` 6곳이
`amagent.PermissionProjectSuperAdmin` 을 요구한다. 일반 고객 accesskey 로는
절대 성공할 수 없는 툴 2개(`list_routes`, `get_route`)가 등록되어 있다.
LLM 은 이를 시도하고 실패하며, 실패 이유도 §5-1 때문에 불명확하게 전달된다.

## 4. 요청 바디 및 docstring 결함

### 4-1. `create_contact` 의 주소가 서버에서 조용히 유실된다 (BLOCKER)

`tools/contacts.py:44-45,77-79` 는 `phone_numbers` / `emails` 키를 보낸다.
`openapi/paths/contacts/main.yaml` 의 POST 필드는 `addresses`
(`CommonAddress` + `is_primary`) 와 `tag_ids` 다. 존재하지 않는 키다.

라이브 확인:

```
POST /contacts {"first_name":"MCPProbe","phone_numbers":[...],"emails":[...]}
  -> 201, 응답의 addresses = null                      (조용히 유실)
POST /contacts {"first_name":"MCPProbe2","addresses":[{"type":"tel","target":"+1...","is_primary":true}]}
  -> 201, 응답의 addresses = [{type:tel,target:...,is_primary:true,...}]
```

201 이므로 AI 도 사용자도 실패를 인지할 수 없다. "전화번호와 함께 연락처를
추가해줘"가 전화번호 없는 연락처를 만든다. README 의 대표 예시가 정확히 이
케이스라 문서가 버그를 홍보하고 있다.

### 4-2. `update_contact` 는 주소/태그를 애초에 받을 수 없다

`update_contact` docstring(`contacts.py:88-89`)도 `phone_numbers`/`emails` 를
유효한 키로 안내한다. 그러나 `paths/contacts/id.yaml` 의 PUT 바디는
`first_name`, `last_name`, `display_name`, `company`, `job_title`,
`external_id`, `notes` **뿐이다**. `addresses` 도 `tag_ids` 도 없다.

주소/태그 변경은 별도 하위 리소스다:
- `paths/contacts/id_addresses.yaml` (POST)
- `paths/contacts/id_addresses_id.yaml` (PUT, DELETE)
- `paths/contacts/id_tags.yaml` (POST)
- `paths/contacts/id_tags_id.yaml` (DELETE)

따라서 `update_contact` 를 "addresses 형태로 교정"하는 것은 §4-1 과 똑같은
무시되는-필드 버그를 새로 만드는 오답이다. 올바른 조치는 (a) docstring 에서
허위 키 제거, (b) 위 4개 하위 리소스를 별도 툴로 노출.

### 4-3. `create_campaign` 이 required 4개를 누락한다

`campaigns.py:39-46`. 스펙 required 는 name, detail, type, service_level,
end_handle, actions, outplan_id, outdial_id, queue_id, next_campaign_id 전체.
뒤 4개는 파라미터로조차 존재하지 않는다. 라이브 200 이지만 outplan/outdial
없는 캠페인은 실사용상 미완성 리소스다.

### 4-4. LLM 대면 docstring 이 존재하지 않는 enum 값 11개를 지시한다 (전수 조사)

docstring 은 LLM 이 읽는 유일한 스펙이다. 모든 툴 모듈의 docstring 에 등장하는
값 목록을 스펙 enum 과 전수 대조했다. 결과는 campaigns 2건이 아니라 **3개 모듈
(campaigns, ais, flows) 11개 값**이다.

| 위치 | docstring 이 말하는 값 | 실제 스펙 enum | 무효 값 (개수) |
|---|---|---|---|
| `campaigns.py:52` | call, sms, email | `CampaignManagerCampaignType` (openapi.yaml:1695-1704) = `[call, flow]` | **sms, email** (2). flow 는 미문서화 |
| `campaigns.py:55` | stop, loop, next | `CampaignManagerCampaignEndHandle` (openapi.yaml:1663-1672) = `[stop, continue]` | **loop, next** (2). continue 는 미문서화 |
| `ais.py:51` | openai.gpt-4o-mini, anthropic.claude-3-5-sonnet, gemini.gemini-pro-latest | `AIManagerAIEngineModel` (openapi.yaml:2017-2028, 11개) = gemini.gemini-2.5-flash / -2.5-pro / -2.0-flash / -pro-latest, openai.gpt-5.2 / 5.1 / 5 / 5-mini / 5-nano, grok.grok-3 / -3-mini | **openai.gpt-4o-mini, anthropic.claude-3-5-sonnet** (2). anthropic 은 제공자로 존재하지 않음. 3개 예시 중 2개 무효 |
| `ais.py:57` | deepgram, google, azure, openai | `AIManagerAISTTType` (openapi.yaml:2959-2963) = `["", cartesia, deepgram, elevenlabs]` | **google, azure, openai** (3). 4개 중 3개 무효 |
| `flows.py:43` | answer, hangup, play, record, talk, echo, ivr | `FlowManagerActionType` (openapi.yaml:4939, enum 4943-4983, **40개**). `recording_start`/`recording_stop` 은 있으나 `record` 없음, `ivr` 없음 | **record, ivr** (2) |

합계 11개.

`ais.py:55` 의 tts_type 5개(google, azure, elevenlabs, openai, playht)는 전수
확인 결과 `AIManagerAITTSType` (openapi.yaml:2908-2930, 22개) 에 모두 존재한다.
유효하다.

과소 서술(무효는 아니나 선택지를 숨기는) 항목:
- `calls.py:51,53`: "One of: tel, sip, agent" → `CommonAddress`
  (openapi.yaml:3635-3644) 는 conference, email, extension, line, web_session
  도 허용한다.
- `campaigns.py:12` 모듈 요약문이 "outbound calling, SMS, or email" 이라고 적혀
  있어 52행을 고쳐도 같은 파일 안에서 모순이 남는다. 함께 고쳐야 한다.
- `flows.py:47-48` 예시가 action id 로 `"a1"` 을 쓰는데 스펙은 `format: uuid` 다.

### 4-4b. `ais.py:52-53` 은 스펙과 정반대를 서술한다 (보안 오도)

docstring 이 `engine_key` 를 "may appear in the response" 라고 안내한다.
스펙은 openapi.yaml:2179 에서 "Write-only; not returned in responses" 라고
명시한다. 즉 모델에게 비밀이 응답에 섞여 나올 수 있다는 **틀린 위험 모델**을
심는다. §4-8 의 정책 논의와 별개로 이 한 줄은 즉시 교정 대상이다.

### 4-5. `create_ai` 가 required 를 누락하고 최신 기능을 전혀 노출하지 않는다

`ais.py:35-71`. required 인 `parameter` 를 보내지 않는다(라이브 200 이지만
스펙 위반). 더 중요하게 그 사이 추가된 `type`(normal/insight), `rag_id`,
`tool_names`, `mcp_server_ids`, `vad_config`, `auto_aicall_audit_enabled` 가
전부 없다. MCP 로는 Insight AI 를 만들 수 없고, 툴 활성화도, 지식베이스 연결도
못 한다. VoIPBin 의 최신 AI 차별화 기능이 MCP 표면에서 보이지 않는다.

### 4-6. 그 외 required 누락 2건

- `emails.py:48-52` `send_email` 이 `attachments` 를 보내지 않는다.
  `paths/emails/main.yaml` 의 required 는 destinations, subject, content,
  **attachments** 4개다.
- `conferences.py:59-62` 는 `pre_flow_id`/`post_flow_id` 를 비어있지 않을 때만
  보낸다. `paths/conferences/main.yaml` 은 type, name, detail, timeout, data,
  pre_flow_id, post_flow_id 전부를 required 로 선언한다. 추가로
  `conferences.py:53` 은 `type` 을 `"conference"` 로 하드코딩하는데 스펙
  (openapi.yaml:3701-3705) 은 `connect`, `queue` 도 허용한다.

### 4-7. `create_flow` / `create_call` 의 선택 필드 누락

- `create_flow` 에 `on_complete_flow_id` 누락 (`paths/flows/main.yaml`).
- `create_call` (`calls.py:58-63`) 은 `flow_id` 만 노출하고 `actions`,
  `anonymous`, `variables` (`paths/calls/main.yaml:49-86`) 를 노출하지 않는다.
  또한 스펙이 `paths/calls/main.yaml:43-47` 에 명시한 flow_id 우선 규칙
  ("If both are supplied, flow_id takes precedence and actions is ignored")
  을 docstring 이 전혀 언급하지 않는다.

### 4-8. `engine_key` 가 평문으로 대화 컨텍스트를 통과한다 (보안)

`ais.py:39` 는 LLM 제공자 API 키를 일반 툴 파라미터로 받는다. 즉 키가 MCP
클라이언트의 대화 컨텍스트와 클라이언트측 로깅을 통과한다. 스펙은 이 필드를
명시적으로 write-only / 응답 미포함으로 표시한다
(openapi.yaml:2179 "Write-only; not returned in responses").

§5-3 이 accesskey 전송 위생을 다루는데, 정작 툴 표면이 모델에게 평문으로
비밀을 다루도록 유도하는 쪽이 더 크다. AI 계열 확장 시 노출이 배가되므로 설계
단계에서 정책을 정해야 한다 (예: 환경변수 참조 방식만 허용, 또는 키를 받지
않고 사전 등록된 credential 을 참조).

## 5. 클라이언트 계층 결함

`src/voipbin_mcp/client.py`:

### 5-1. 에러 envelope 파싱이 서버 형상과 불일치 (BLOCKER 급)

서버 실제 응답은 `bin-api-manager/lib/apierror/envelope.go:19-37` 에 따라
`{"error":{"status","reason","message","request_id"[,"details"]}}` 중첩이다
(라이브 재확인). `client.py:51` 은 최상위 `body.get("message")` 를 읽으므로
**항상 빈 문자열**이다. 결과적으로:

- 404 → `"Resource not found: "`
- 403 → 서버가 준 사람이 읽을 수 있는 이유를 버림 (§3-3 의 routes 실패가
  정확히 이 상태)
- `request_id` 유실 — 지원 문의 시 가장 중요한 값

### 5-2. 429 미처리

`client.py:55-61` error_map 에 429 가 없다.
`bin-api-manager/lib/middleware/customer_ratelimit.go:237` 은 429
RESOURCE_EXHAUSTED + `Retry-After` 헤더를 반환한다. 현재는 "API error 429: "
라는 뭉뚱그린 메시지가 되어 LLM 이 재시도 판단을 못 한다. Retry-After 를
메시지에 실어야 한다.

### 5-3. accesskey 를 쿼리 파라미터로만 전송

README Security Note 가 이 점을 인정하지만, 서버는 `accesskey` **쿠키**도
받는다 (`lib/middleware/authenticate.go:480-492` — 쿠키를 쿼리보다 먼저 확인.
라이브 200 확인). 쿠키 헤더로 보내면 액세스 로그/프록시 로그 노출이 줄어든다.
실질적 개선 여지가 있다.

### 5-4. `BASE_URL` 하드코딩 — 셀프호스팅에서 사용 불가

`client.py:9` 가 `https://api.voipbin.net/v1.0` 를 하드코딩하고 `client.py:31`
이 오버라이드 없이 대입한다. **"프로덕션급 셀프호스팅"은 VoIPBin 의 4대 지향점
중 하나다.** 자체 호스팅 고객은 이 MCP 서버를 아예 쓸 수 없다. 제품 관점에서
§9-B 의 여러 항목보다 우선순위가 높다. `VOIPBIN_API_BASE_URL` 환경변수
지원이 필요하다 (api-validator 가 이미 동일 이름을 쓴다).

### 5-5. `close()` 미호출 / `validate_page_size` 묵시적 클램프

`close()` 는 정의만 있고 호출되지 않는다 (stdio 프로세스 수명과 함께 종료되므로
경미). `server.py:28-34` 는 **정수 변환 실패 시에만** 10 으로 대체하고, 범위를
벗어난 정수는 `max(1, min(page_size, 100))` 로 클램프한다. 즉 `page_size=0` 은
10 이 아니라 1 이 된다. 어떤 툴 docstring 도 1–100 상한을 명시하지 않는다.

*PATCH 미지원은 결함이 아니다*: `openapi/paths/**` 전체에 `patch:` 오퍼레이션이
0건이다. 향후 항목으로만 남긴다.

## 6. 문서 / 테스트 / 인프라

1. **README 의 예시가 버그를 홍보한다.** "Add a new contact named John with
   phone number ..." 는 §4-1 로 전화번호가 저장되지 않는다.
2. **커버리지가 드러나지 않는다.** 사용자가 "AI 어시스턴트 만들어줘"를
   기대하고 붙였을 때 없다는 것을 런타임에 알게 된다. 지원 범위 명시 필요.
3. **테스트 42 passed 는 건강 지표가 아니다.** 테스트 파일은 4개
   (`test_client.py`, `test_server.py`, `test_tools_calls.py`,
   `test_tools_flows.py`) 뿐이며 **18개 툴 모듈 중 16개가 무테스트**다.
   결함이 확인된 contacts, campaigns, ais, emails, conferences 전부 포함된다.
   §4-1 의 조용한 유실이 살아남은 이유가 바로 이것이다.

   더 나쁜 것은 **기존 테스트가 틀린 서버 계약을 고정하고 있다**는 점이다.
   `tests/test_client.py:62,71,91` 은 평평한 `{"message": ...}` 바디를 모킹한다.
   실제 envelope 은 중첩이다(§5-1). `test_get_404_raises` 는 이 잘못된 픽스처
   덕분에 통과한다. 즉 새 테스트를 쓰기 전에 **기존 4개 파일의 계약부터
   교정**해야 하며, 그러지 않으면 새 테스트도 같은 허위 형상 위에 쌓인다.

4. **CI 가 구조적으로 §3-1 을 볼 수 없다.** `.github/workflows/ci.yml:18-20` 은
   `uv venv` → `uv pip install -e ".[dev]"` → `uv run pytest` 순서다. 실측 재현:

   ```
   $ uv pip install -e ".[dev]" && uv pip show mcp
   Version: 2.2.0                      # 의존성 범위대로 2.x 가 들어온다
   $ uv run pytest tests/ -q
   Installed 21 packages in 21ms       # uv run 이 uv.lock 으로 조용히 재동기화
   42 passed
   $ uv pip show mcp
   Version: 1.27.0                     # 1.x 로 강제 다운그레이드됨
   ```

   즉 CI 는 **lock 된 개발 환경만** 검증하고 배포되는 의존성 범위는 한 번도
   검증하지 않는다. 같은 트리를 평범한 `pip install -e ".[dev]"` 로 깔면 테스트
   4개 중 3개가 collection 단계에서 실패한다. §3-1 의 핀은 옳지만 그것만으로는
   재발을 막지 못한다. **빌드된 wheel 을 의존성 범위대로 설치해 import 하는
   잡**이 필요하다. Python 3.13 추가와 ruff 는 이 결함을 잡지 못한다.

5. **릴리스 게이트가 없다.** `.github/workflows/publish.yml` 은
   `release: published` 에 `uv build` → `pypa/gh-action-pypi-publish` 로 바로
   올린다. 그 사이에 **테스트도, import 스모크도 없다.** 액션은 SHA 로 고정되어
   있지 않고, environment 게이트도 없다. 지금 배포판이 깨진 채 올라간 경로가
   정확히 이것이다. 핀만 넣으면 다음 릴리스가 다시 회귀시킬 수 있다.

6. CI 는 Python 3.10/3.11/3.12 만 돈다. 3.13 추가 필요. ruff lint 게이트 없음.
7. 버전 0.1.1, `Development Status :: 4 - Beta`.

검증했으나 결함이 아닌 항목(재논의 방지 기록): DELETE 의 바디 처리는 올바르다
(`client.py:102` 가 `response.content` 를 가드하고, 스펙은 200 에 엔티티를
반환). `format_response`(`server.py:23-25`)는 `engine_key` 를 유출할 수 없다
(서버가 write-only 로 응답에서 제거하므로). 유출 경로는 요청/파라미터 쪽뿐이며
§4-8 이 그 점을 정확히 지적한다. `next_page_token` 은 바디 전체를 덤프하므로
온전히 전달되고, 모든 list 툴이 `page_token` 을 전달한다. `POST /calls` 의
`source`/`destinations` 형상은 `CommonAddress` 와 일치한다. conferences 의
`data` 는 항상 `{}` 로 전송된다. LICENSE(MIT)와 `pyproject.toml` 분류자는
일관되며 `twine check` 는 PASS 다. 툴 이름 충돌은 없다.

`/billings/{billing-id}` 파라미터 명명(openapi.yaml:8656)은 다른 리소스의
`{id}` 관례와 어긋나지만 클라이언트 영향은 없다(`billings.py:30` 위치 기반
보간). monorepo 쪽 스펙 위생 사항이며 MCP 결함이 아니다.

## 7. 커버리지 상세

`openapi/paths/` 는 66개 디렉터리다. MCP 가 다루는 것은 18개다. 나머지 48개 중
`ws`, `me`, `auth`, `provisioning` 4개는 일반 고객 리소스가 아니므로 의도적
제외 대상이다.

미노출 리소스 (44개, 전수): mcpservers, rags, aicalls, aimessages, aisummaries,
aiaudits, aipromptproposals, speakings, transcribes, transcripts, recordings,
recordingfiles, outdials, outplans, providers, providercalls, trunks, teams,
webchat_widgets, webchat_sessions, webchat_messages, storage_account,
storage_accounts, storage_files, timelines, timeline_analyses, groupcalls,
queuecalls, conferencecalls, campaigncalls, contact_cases, contact_addresses,
contact_interactions, contact_peer_events, conversation_accounts, accesskeys,
billing_account, billing_accounts, customers, available_numbers, transfers,
outbound_config, outbound_configs, service_agents.

## 8. 진행 타당성

진행해야 한다. 근거:

1. **§3-1 단독으로 즉시 수정 사유가 된다.** 배포판이 import 되지 않는다.
   README 가 안내하는 설치 경로가 그대로 실패한다.
2. §4-1 은 조용한 데이터 유실이다. 200 을 반환하므로 사용자가 신뢰를 잃는
   방식이 최악이다.
3. §5-1 은 모든 에러 메시지 품질을 동시에 떨어뜨린다. 수정 비용이 낮고 효과가
   전방위적이다.
4. §5-4 는 제품의 4대 지향점 중 하나(셀프호스팅)를 MCP 표면에서 부정한다.
5. MCP 서버는 "AI 가 쓰는 CPaaS" 포지셔닝의 정면 표면이다. 커버리지 12% 는
   제품 주장과 실제 사이의 가장 큰 간극이다.

커버리지 지표는 두 가지로 봐야 한다. **430 을 분모로 한 12.1% 는 아래에서
"노출해서는 안 된다"고 논증하는 표면까지 포함하므로 자기모순적이다.** 아래
제외 기준(superadmin 전용, `/auth/*`, `service_agents/*`)을 적용한
"노출 대상 표면(addressable surface)" 기준 분모를 설계 단계에서 확정하고, 그
기준으로 목표 커버리지를 정한다. 12.1% 보다 정직하고, 논거도 더 강해진다.

반대로 **378개 전부를 한 번에 채우는 것은 반대한다.** 근거:

- MCP 툴 목록은 LLM 컨텍스트를 직접 소모한다. 400+ 툴은 도구 선택 정확도를
  떨어뜨리고 일부 클라이언트의 실질 한계에 부딪힌다. "많이 노출"이 아니라
  "AI 가 실제로 쓸 것을 노출"이 정답이다.
- superadmin 전용 표면은 accesskey 로 못 쓰거나 써서는 안 되므로 노출 자체가
  오답이다. 코드로 확인한 superadmin 게이트: `/routes`
  (`pkg/servicehandler/route.go:51,86,129,177,217,267`), `/customers`
  (`customer.go:62,148,231,298,375,410,445,579,618`), `/providers`
  (`provider.go:59,89,133,173,207,266`). `balance_*_force` 및 `/auth/*`
  (인증 부트스트랩)도 제외 대상이다.
  **`/trunks` 는 superadmin 이 아니다** — `trunk.go:43,79,117,147,208` 은
  `PermissionCustomerAdmin|PermissionCustomerManager` 를 요구한다
  (`grep -c PermissionProjectSuperAdmin trunk.go` = 0). SIP 트렁크 설정은
  셀프호스팅/BYOC 고객의 핵심 리소스이므로 노출 대상이며, §9-B 우선순위에
  포함한다.
- `service_agents/*` 는 Agent JWT 표면이며 accesskey 계층이 아니다
  (`authenticate.go` 의 별도 bearer 경로). 별도 인증 모델이 필요하므로 이번
  스코프에서 제외한다.

## 9. 권고 스코프

### A. 필수 (반드시 이번에)

**A-1. 배포 복구 및 재발 방지**
1. `mcp>=1.0.0,<2` 핀 — 배포판 복구 (§3-1)
2. CI 에 **배포 경로 검증 잡** 추가: 빌드된 wheel 을 의존성 범위대로(lock 무시)
   설치해 `import voipbin_mcp.server` + `list_tools()` 스모크. 기존
   `uv run pytest` 잡은 lock 환경을 계속 검증하므로 유지하고 별도 잡으로 추가
   (§6-4)
3. `publish.yml` 에 릴리스 게이트 추가: 업로드 전 테스트 + wheel import 스모크,
   액션을 SHA 로 핀 (§6-5)

**A-2. 조용한 오동작 제거**
4. `create_contact` 를 `addresses` + `tag_ids` 스펙 형태로 교정 (§4-1)
5. `update_contact` docstring 의 허위 키 제거 + 주소/태그 하위 리소스를
   별도 툴로 노출. 파일 4개에 **operation 5개**다: `id_addresses.yaml` POST,
   `id_addresses_id.yaml` PUT + DELETE, `id_tags.yaml` POST,
   `id_tags_id.yaml` DELETE (§4-2)
6. 에러 envelope 중첩 파싱 + `request_id` 노출 + 429/Retry-After 처리
   (§5-1, §5-2). **기존 `tests/test_client.py` 의 평평한-바디 픽스처를 실제
   중첩 형상으로 먼저 교정** (§6-3)
7. `VOIPBIN_API_BASE_URL` 환경변수 지원 (§5-4)

**A-3. docstring 전수 교정 (§4-4, §4-4b)**
8. 무효 enum 값 11개 교정: campaigns type/end_handle, ais engine_model/stt_type,
   flows action types
9. `ais.py:52-53` 의 "may appear in the response" 삭제 — 스펙은 write-only
10. 과소 서술 교정: `calls.py` 주소 타입 전체, `campaigns.py:12` 모듈 요약문,
    `flows.py` 예시의 uuid 형식, `validate_page_size` 의 1–100 상한을 list 툴
    docstring 에 명시 (§5-5)

**A-4. required 필드 반영**
11. `create_campaign` required 4개 (§4-3)
12. `create_ai` required `parameter` (§4-5). *§4-5 의 신규 기능 필드(type,
    rag_id, tool_names, mcp_server_ids, vad_config)는 B 로 미루되, required
    위반은 §4-3/§4-6 과 동일한 결함 등급이므로 A 에서 닫는다.*
13. `send_email` attachments, `create_conference` required 전체 +
    `type` 하드코딩 해제 (§4-6)

**A-5. 정리 및 문서**
14. `list_routes`/`get_route` 처리 결정: 제거 또는 superadmin 전용 명시 (§3-3)
15. accesskey 를 쿠키 헤더 전송으로 변경 + README Security Note 갱신 (§5-3)
16. 위 전부에 대한 회귀 테스트 추가 (§6-3). contacts/campaigns/ais/emails/
    conferences 모듈 테스트 신설.
17. README 교정 (잘못된 예시, 설치 안내, 지원 범위)
18. CI: Python 3.13 추가, ruff 게이트 추가
19. 버전 bump

### B. 커버리지 확장 (설계 단계에서 우선순위 확정)
- AI 계열: `ais` 전체 CRUD + §4-5 신규 기능 필드, `aicalls`, `aisummaries`,
  `rags`, `mcpservers`, `aimessages`
- 통화 제어: `calls` 의 talk/hold/mute/moh/silence/recording_start·stop,
  `create_call` 의 actions/anonymous/variables (§4-7)
- `create_flow` 의 `on_complete_flow_id` (§4-7)
- 녹취/전사: `recordings`, `recordingfiles`, `transcribes`, `transcripts`
- 아웃바운드: `outdials`, `outplans`, campaign 하위 액션
- 나머지: `agents`/`queues`/`tags`/`extensions`/`numbers` 쓰기 경로,
  `conversations` 메시지, `contact_cases`, `contact_peer_events`,
  `groupcalls`, `conferences` 제어, `trunks` (SIP 트렁크 — 셀프호스팅/BYOC
  핵심, superadmin 아님, §8), `teams`, `webchat_*`, `storage_*`, `timelines`,
  `billing_account`

### B-2. 낮은 우선순위 (B 에 포함하되 후순위)
- `close()` 명시 호출 (§5-5, 경미)
- `Development Status` 분류자를 Beta 이상으로 조정 (§6-7)

### C. 설계 단계에서 결정할 정책 사항
- `engine_key` 취급 정책 (§4-8). A-3 의 docstring 교정과 별개로, 키를 툴
  파라미터로 계속 받을지 자체를 결정.
- 툴 개수 상한 및 노출 기준 (LLM 컨텍스트 예산)
- "노출 대상 표면" 분모 정의 및 목표 커버리지 (§8)
- B 단계를 A 와 같은 PR 에 넣을지 분할할지 (CLAUDE.md 기본값은 단일 PR;
  분할은 대표님 승인 필요)

## 10. 리뷰 이력

| 회차 | 판정 | 핵심 피드백 | 조치 |
|---|---|---|---|
| 1 | REQUEST_CHANGES | mcp 2.x 상한 부재로 배포판 import 불가(최우선 결함 누락) / `update_contact` 처방이 오답(PUT 에 addresses 없음) / 라이브 검증 범위 과장 및 GET 집계 오류 / campaign enum 2건 미확정·미발견 / emails·conferences required 누락 미발견 / BASE_URL 하드코딩 미언급 / engine_key 보안 미분석 / 테스트 42건을 건강지표로 오용(16/18 모듈 무테스트) / 결함 개수 불일치 및 PATCH 는 비결함 / "70+ 리소스" 근거 부족 | 전면 재작성. §3-1 신설(실측 재현), §4-2 처방 교정, §3-2 에 호출/미호출 명시, §4-4·§4-6 신설, §5-4·§4-8 신설, §6-3 신설, §7 을 66 기준으로 정정, PATCH 를 비결함으로 강등 |
| 2 | REQUEST_CHANGES | GET 집계가 여전히 틀림(list 18 → 실제 17, 싱글턴 1 누락) / docstring enum 축이 심각하게 과소집계 — campaigns 만 보고 ais·flows 를 놓쳐 무효 값이 2개가 아니라 9개 / `ais.py:52-53` 이 스펙과 정반대 서술(write-only 인데 "응답에 나타날 수 있다") / CI 가 `uv run` 의 lock 재동기화 때문에 배포 의존성 범위를 한 번도 검증하지 못함 → 핀만으로는 재발 방지 불가 / `publish.yml` 에 테스트·import 게이트 없음(깨진 배포의 직접 원인) / 기존 `test_client.py` 픽스처가 틀린 평평한 envelope 을 고정 / `create_ai` required 누락이 동일 결함 등급인데 B 로 밀림 / §5-3·§5-5·§4-7 이 어느 스코프에도 없음 / 요청 바디 결함 5 vs 6 불일치 / 커버리지 분모 자기모순 | GET 집계 정정(list 17 + by-id 17 + 싱글턴 1). §4-4 를 전수 조사 표로 전면 교체(4개 모듈 9개 무효 값 + tts 는 유효 확인 + 과소서술 3건). §4-4b 신설. §4-6·§4-7 확장(conference type 하드코딩, create_call actions/anonymous/variables, flow_id 우선 규칙). §6-3 에 기존 픽스처 결함 추가, §6-4(CI 실측 재현)·§6-5(릴리스 게이트) 신설. §6 에 clean 검증 항목 기록. §8 에 addressable-surface 분모 논의 추가. §9-A 를 A-1~A-5 19항목으로 재구성 — CI 배포검증잡·publish 게이트·create_ai required·쿠키 전송·page_size 상한 명시를 모두 A 로 승격 |
| 3 | REQUEST_CHANGES | 두 BLOCKER 재현 성공, 수치 대부분 재검증 통과. 그러나 §4-4 무효 enum 이 9가 아니라 **11개 (3개 모듈)** — 표 자체가 11로 합산되는데 헤드라인만 9 / `FlowManagerActionType` 은 56개가 아니라 **40개** (openapi.yaml:4943-4983) / **`/trunks` 는 superadmin 이 아님** (`trunk.go:43,79,117,147,208` = CustomerAdmin\|CustomerManager, superadmin 0건) — 분모에서 잘못 제외되고 B 에도 없음 / `pyproject.toml` 은 :23 이 아니라 :24 / tts "4개" 라 쓰고 5개 나열 / contacts 하위 파일 4개는 operation 5개 / `validate_page_size` 는 범위초과를 10 이 아니라 1·100 으로 클램프 / `close()`·Beta 분류자가 어느 스코프에도 없음 / §7 목록에 contact_peer_events·storage_account 누락 | 11개/3개 모듈로 정정(표에 개수 병기), action enum 40개로 정정, §8 에 trunks 를 superadmin 아님으로 명시하고 B 에 추가, :24 로 정정, tts 5개로 정정, A-5 를 operation 5개로 명시, §5-5 클램프 동작 정확히 재서술, B-2 신설하여 close()·분류자 수용, §7 을 44개 전수 목록으로 교체 |
