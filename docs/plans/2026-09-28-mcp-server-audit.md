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
  GET 35 (list 18 + by-id 17), POST 10, PUT 3, DELETE 4.
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
| 요청 바디 정확성 | **5건 결함** | §4 |
| LLM 대면 docstring 정확성 | **2건 잘못된 enum** | §4-4 |
| 클라이언트 계층 | **5건 결함** | §5 |
| 셀프호스팅 지원 | **불가** | §5-4 |
| 테스트 커버리지 | 18 모듈 중 16개 무테스트 | §6-3 |

## 3. 치명 결함: 배포판이 import 되지 않는다

### 3-1. `mcp` 의존성 상한 부재 (BLOCKER)

`pyproject.toml:23` 은 `mcp>=1.0.0` 으로 **상한이 없다**. `mcp` 2.x 는
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
- list GET 18개 중 17개 → 200. (`/routes` 는 403, §3-3)
- by-id GET 17개 중 13개 → 200. (실계정에 인스턴스가 없는 conferences,
  emails, messages 및 routes 는 by-id 미확인)
- `GET /customer` → 200.
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

### 4-4. LLM 대면 enum 2건이 실제 스펙과 다르다 (확정)

- `campaigns.py:52`: `"call", "sms", "email"` → 실제
  `CampaignManagerCampaignType` (openapi.yaml:1695-1704) 은 **`[call, flow]`**.
  "sms"/"email" 은 존재하지 않고, "flow" 는 문서화되지 않았다.
- `campaigns.py:55`: `"stop", "loop", "next"` → 실제
  `CampaignManagerCampaignEndHandle` (openapi.yaml:1663-1672) 은
  **`[stop, continue]`**. "loop"/"next" 둘 다 없다.

docstring 은 LLM 이 읽는 유일한 스펙이다. 현재 상태는 모델에게 400 을 유발할
값을 적극적으로 지시하고 있다.

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
  pre_flow_id, post_flow_id 전부를 required 로 선언한다.

### 4-7. 경미

`create_flow` 에 `on_complete_flow_id` 누락.

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
경미). `server.py:28-34` 는 잘못된 page_size 를 조용히 10 으로, 상한을 100 으로
클램프하는데 어떤 툴 docstring 도 상한을 명시하지 않는다.

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
   §4-1 의 조용한 유실이 살아남은 이유가 바로 이것이다. 따라서 수정 PR 에는
   회귀 테스트가 반드시 동반되어야 한다.
4. CI 는 Python 3.10/3.11/3.12 만 돈다. 3.13 추가 필요. ruff lint 게이트 없음.
5. 버전 0.1.1, `Development Status :: 4 - Beta`.

`/billings/{billing-id}` 파라미터 명명(openapi.yaml:8656)은 다른 리소스의
`{id}` 관례와 어긋나지만 클라이언트 영향은 없다(`billings.py:30` 위치 기반
보간). monorepo 쪽 스펙 위생 사항이며 MCP 결함이 아니다.

## 7. 커버리지 상세

`openapi/paths/` 는 66개 디렉터리다. 이 중 `ws`, `me`, `auth`, `provisioning`
등은 일반 고객 리소스가 아니다. MCP 가 다루는 것은 18개다.

미노출 주요 리소스: mcpservers, rags, aicalls, aimessages, aisummaries,
aiaudits, aipromptproposals, speakings, transcribes, transcripts, recordings,
recordingfiles, outdials, outplans, providers, providercalls, trunks, teams,
webchat_widgets/sessions/messages, storage_accounts/files, timelines,
timeline-analyses, groupcalls, queuecalls, conferencecalls, campaigncalls,
contact_cases, contact_addresses, contact_interactions, conversation_accounts,
accesskeys, billing_account(s), customers, available_numbers, transfers,
outbound_config(s), 그리고 service_agents/* 전체.

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

반대로 **378개 전부를 한 번에 채우는 것은 반대한다.** 근거:

- MCP 툴 목록은 LLM 컨텍스트를 직접 소모한다. 400+ 툴은 도구 선택 정확도를
  떨어뜨리고 일부 클라이언트의 실질 한계에 부딪힌다. "많이 노출"이 아니라
  "AI 가 실제로 쓸 것을 노출"이 정답이다.
- superadmin 전용(`/routes`, `/customers`, `/providers`, `/trunks`,
  `balance_*_force`)과 인증 부트스트랩(`/auth/*`)은 accesskey 로 못 쓰거나
  써서는 안 되는 표면이다. 노출 자체가 오답이다.
- `service_agents/*` 는 Agent JWT 표면이며 accesskey 계층이 아니다
  (`authenticate.go` 의 별도 bearer 경로). 별도 인증 모델이 필요하므로 이번
  스코프에서 제외한다.

## 9. 권고 스코프

### A. 필수 (반드시 이번에)
1. `mcp>=1.0.0,<2` 핀 — 배포판 복구 (§3-1)
2. `create_contact` 를 `addresses` + `tag_ids` 스펙 형태로 교정 (§4-1)
3. `update_contact` docstring 의 허위 키 제거 + 주소/태그 하위 리소스 4개를
   별도 툴로 노출 (§4-2)
4. 에러 envelope 중첩 파싱 + `request_id` 노출 + 429/Retry-After 처리
   (§5-1, §5-2)
5. `VOIPBIN_API_BASE_URL` 환경변수 지원 (§5-4)
6. campaign enum docstring 2건 교정 (§4-4)
7. `create_campaign` required 4개, `send_email` attachments,
   `create_conference` required 반영 (§4-3, §4-6)
8. `list_routes`/`get_route` 처리 결정: 제거 또는 superadmin 전용 명시 (§3-3)
9. 위 전부에 대한 회귀 테스트 추가 (§6-3). contacts/campaigns/ais/emails/
   conferences 모듈 테스트 신설.
10. README 교정 (잘못된 예시, 설치 안내, 지원 범위)
11. CI: Python 3.13 추가, ruff 게이트 추가
12. 버전 bump

### B. 커버리지 확장 (설계 단계에서 우선순위 확정)
- AI 계열: `ais` 전체 CRUD + §4-5 신규 필드, `aicalls`, `aisummaries`, `rags`,
  `mcpservers`, `aimessages`
- 통화 제어: `calls` 의 talk/hold/mute/moh/silence/recording_start·stop
- 녹취/전사: `recordings`, `recordingfiles`, `transcribes`, `transcripts`
- 아웃바운드: `outdials`, `outplans`, campaign 하위 액션
- 나머지: `agents`/`queues`/`tags`/`extensions`/`numbers` 쓰기 경로,
  `conversations` 메시지, `contact_cases`, `groupcalls`, `conferences` 제어,
  `teams`, `webchat_*`, `storage_*`, `timelines`, `billing_account`

### C. 설계 단계에서 결정할 정책 사항
- `engine_key` 취급 정책 (§4-8)
- 툴 개수 상한 및 노출 기준 (LLM 컨텍스트 예산)
- B 단계를 A 와 같은 PR 에 넣을지 분할할지 (CLAUDE.md 기본값은 단일 PR;
  분할은 대표님 승인 필요)

## 10. 리뷰 이력

| 회차 | 판정 | 핵심 피드백 | 조치 |
|---|---|---|---|
| 1 | REQUEST_CHANGES | mcp 2.x 상한 부재로 배포판 import 불가(최우선 결함 누락) / `update_contact` 처방이 오답(PUT 에 addresses 없음) / 라이브 검증 범위 과장 및 GET 집계 오류 / campaign enum 2건 미확정·미발견 / emails·conferences required 누락 미발견 / BASE_URL 하드코딩 미언급 / engine_key 보안 미분석 / 테스트 42건을 건강지표로 오용(16/18 모듈 무테스트) / 결함 개수 불일치 및 PATCH 는 비결함 / "70+ 리소스" 근거 부족 | 전면 재작성. §3-1 신설(실측 재현), §4-2 처방 교정, §3-2 에 호출/미호출 명시, §4-4·§4-6 신설, §5-4·§4-8 신설, §6-3 신설, §7 을 66 기준으로 정정, PATCH 를 비결함으로 강등 |
