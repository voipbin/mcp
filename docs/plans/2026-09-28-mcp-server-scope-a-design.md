# voipbin/mcp 스코프 A 설계: 배포 복구 및 계약 정합성 (2026-09-28)

Status: v2 (설계 리뷰 라운드 1·2 반영, 라운드 3 대기)

선행 문서: `2026-09-28-mcp-server-audit.md` (이슈 분석, 5라운드 2연속 APPROVE 종료)

## 1. Problem statement

`voipbin-mcp` 0.1.1 은 PyPI 에 배포되어 있으나 **설치하면 실행되지 않는다.**
그 위에, 실행되더라도 조용히 데이터를 유실하고, LLM 에게 존재하지 않는 enum
값을 지시하며, 에러 메시지를 항상 빈 문자열로 만들고, 셀프호스팅에서는 아예
쓸 수 없다. 감사 문서가 확인한 결함은 전부 "커버리지 부족"이 아니라
**이미 노출된 표면의 정확성 결함**이다.

본 설계는 감사 문서 §9-A (A-1 ~ A-5, 19항목) 만 다룬다. 커버리지 확장(B)과
정책 결정(C)은 범위 밖이다.

## 2. Goals

1. `pip install voipbin-mcp` → `voipbin-mcp` 실행이 성공한다. **선언한 의존성
   범위의 양 끝(최저·최신) 모두에서** 성공한다.
2. CI 가 배포되는 의존성 범위를 실제로 검증한다. `mcp` 상한을 넣는 것만으로는
   재발을 막지 못하므로, 회귀를 잡는 게이트를 함께 만든다. 범위의 한쪽 끝만
   검증하는 게이트는 만들지 않는다.
3. 릴리스가 테스트·import 스모크를 통과하지 않으면 PyPI 에 올라가지 않는다.
4. `create_contact` 가 주소와 태그를 실제로 저장한다.
5. 에러가 서버의 실제 메시지와 `request_id` 를 담아 전달된다. 429 는 429 로
   식별되고 `Retry-After` 가 보존된다.
6. 셀프호스팅 배포를 가리킬 수 있다.
7. 모든 툴 docstring 의 enum 값이 스펙에 존재하는 값만 지시한다.
8. required 필드를 누락한 채 요청을 보내는 툴이 없다.
9. 위 전부에 회귀 테스트가 붙는다. 기존 테스트가 고정한 틀린 계약을 먼저
   교정한다.

## 3. Non-goals

- 커버리지 확장 (감사 §9-B). 새 리소스 툴 추가는 하지 않는다. 단
  **contacts 하위 리소스 5개 operation 은 A 에 포함된다** — 자의적 예외가
  아니라 **감사 §9-A item 5 가 이미 A 필수로 명령한 항목**이다(감사는 5라운드
  2연속 APPROVE 로 승인됨). `update_contact` 의 허위 docstring 을 정직하게
  고치려면 주소/태그를 바꿀 실제 수단이 있어야 하고, 수단 없이 "이 필드는 못
  바꿉니다"만 남기는 것은 결함을 문서화할 뿐이다. (v1 은 이를 "본 설계가 스스로
  부여한 예외"처럼 서술했는데, 선행 승인 사항을 재논쟁으로 되돌린 오류였다.)
- `engine_key` 를 툴 파라미터로 계속 받을지 여부 (감사 §9-C). 본 PR 은
  docstring 의 **거짓 서술만** 고친다.
- 툴 개수 상한 정책 (감사 §9-C).
- `close()` 명시 호출 (감사 §9-B-2). `Development Status` 분류자는 **A 로
  가져왔다** (D-J 참조) — 버전 bump 가 분류자를 재주장하게 되므로 이 릴리스에서
  판단하는 것이 맞다.
- `create_call` 의 actions/anonymous/variables, `create_flow` 의
  `on_complete_flow_id` (감사 §4-7). 신규 기능 표면이므로 B.

## 4. Decisions locked (2026-09-28, 대표님 승인)

| # | 결정 | 근거 |
|---|---|---|
| D1 | A 를 단독 PR 로 먼저 릴리스한다. B 는 후속. | 지금 이 순간 `pip install` 이 실패 중. B 를 기다릴 이유가 없다. CLAUDE.md 단일 PR 기본값의 명시적 예외로 대표님 승인. |
| D2 | `engine_key` 는 환경변수 참조만 허용하는 방향으로 간다. | CPO 권고안. 단 **구현은 C/후속** — 본 PR 은 거짓 docstring 만 제거. |
| D3 | 툴 개수 상한 100개 내외. | CPO 권고안. A 는 툴을 5개만 늘리므로(52→57) 제약이 걸리지 않는다. |

## 5. 사실 검증 (Phase 1.6, 실측)

설계가 의존하는 외부 가정을 전부 실행해서 확인했다. 문서나 추측이 아니다.

### 5.1 mcp 1.x / 2.x API 차이는 클래스 이름뿐이다

```
$ /tmp/mcpapi/bin/python -c "from mcp.server.fastmcp import FastMCP; ..."
mcp version: 1.30.0
tool sig: (name=None, title=None, description=None, annotations=None,
           icons=None, meta=None, structured_output=None) -> Callable
run sig: (transport: Literal['stdio','sse','streamable-http'] = 'stdio',
          mount_path: str|None = None) -> None

$ /tmp/mcp2api/bin/python -c "from mcp.server import MCPServer; ..."
mcp version: 2.2.0
tool sig: (name=None, title=None, description=None, annotations=None,
           icons=None, meta=None, structured_output=None) -> Callable
run sig: (transport: Literal['stdio','sse','streamable-http'] = 'stdio',
          **kwargs) -> None
```

`@server.tool()` 데코레이터 시그니처와 `run(transport=...)` 가 동일하다.
이 코드베이스가 쓰는 API 표면은 그 둘뿐이다(`server.py:43` 이 유일한 run 호출).

**shim 실동작 검증** (양쪽 venv 에서 동일 스크립트 실행):

```python
try:
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:
    from mcp.server import MCPServer as _Server
m = _Server("voipbin")

@m.tool()
def ping(x: int = 1) -> str:
    """Ping."""
    return f"pong {x}"

tools = asyncio.run(m.list_tools())
```

```
mcp 1.30.0 | class FastMCP  | tools ['ping'] | props ['x']
mcp 2.2.0  | class MCPServer | tools ['ping'] | props ['x']
```

양쪽 모두 툴 등록과 스키마 추론이 동작한다.

**주의 (shim 채택 시 함정):** `Tool` 객체의 속성명이 1.x `inputSchema` →
2.x `input_schema` 로 바뀌었다. 첫 시도에서 `AttributeError: 'Tool' object
has no attribute 'inputSchema'` 로 실패했다. 런타임 코드는 이 속성을 읽지
않지만 **테스트 코드는 읽는다.** shim 을 채택하면 테스트에 동일한 폴백이
필요하다.

### 5.1b `mcp.server.fastmcp` 는 1.2.0 부터 존재한다 (v1 설계의 오류)

v1 설계는 `mcp>=1.0.0,<2` 를 처방했다. **이 처방 자체가 버그를 고치지
못한다.** 리뷰 라운드 1·2 가 독립적으로 지적했고, 실측으로 확정했다.

```
$ for v in 1.0.0 1.1.0 1.1.2 1.1.3 1.2.0 1.2.1; do
    venv + pip install mcp==$v + find_spec('mcp.server.fastmcp')
  done
1.0.0 installed 1.0.0 | fastmcp False
1.1.0 installed 1.1.0 | fastmcp False
1.1.2 installed 1.1.2 | fastmcp False
1.1.3 installed 1.1.3 | fastmcp False
1.2.0 installed 1.2.0 | fastmcp True
1.2.1 installed 1.2.1 | fastmcp True
```

`>=1.0.0` 은 `mcp.server.fastmcp` 가 없는 **네 개 이상의 버전을 여전히
허용**한다. mcp 1.0.0 으로 설치하면 2.2.0 과 **완전히 동일한
`ModuleNotFoundError`** 가 난다. v1 설계 §6 D-A 표가 옵션 (a) 를 "검증된 1.x
만 지원"이라고 쓴 것은 거짓이었다. `>=1.0.0` 은 하한에서 한 번도 검증된 적이
없고, v1 은 그 하한을 `pyproject.toml:24` 에서 무비판적으로 상속했다.

**빌드된 wheel 로 하한 확정:**

```
$ uv build --out-dir /tmp/wh   # voipbin_mcp-0.1.1-py3-none-any.whl
$ for v in 1.2.0 1.2.1 1.9.0 1.27.0 1.30.0; do
    venv + pip install "mcp==$v" /tmp/wh/*.whl + import + list_tools()
  done
[1.2.0]  mcp 1.2.0  | tools 52 | OK
[1.2.1]  mcp 1.2.1  | tools 52 | OK
[1.9.0]  mcp 1.9.0  | tools 52 | OK
[1.27.0] mcp 1.27.0 | tools 52 | OK
[1.30.0] mcp 1.30.0 | tools 52 | OK
```

1.2.0 부터 52개 툴이 모두 정상 등록된다. `mcp==1.2.0` 의
`Requires-Python: >=3.10` 이므로 이 저장소의 `requires-python = ">=3.10"`
(`pyproject.toml:7`) 과 충돌하지 않는다.

따라서 올바른 핀은 **`mcp>=1.2.0,<2`** 다.

1.9.0/1.27.0 에서 pydantic-settings 의 `IncompleteFieldDefinitionWarning`
(lifespan 필드 forward reference)이 출력되지만 툴 등록에는 영향이 없다. 경고를
에러로 승격하는 설정은 넣지 않는다.

### 5.2 accesskey 쿠키 전송이 실제로 동작한다

운영 API(`https://api.voipbin.net/v1.0`) 실측:

```
query  -> 200 {"result":[{"id":"4bbb30d2-...","customer_id":"27690e2e-...
cookie -> 200 {"result":[{"id":"4bbb30d2-...","customer_id":"27690e2e-...
```

`Cookie: accesskey=<key>` 헤더만으로 200 이 떨어진다. 쿼리 파라미터와 동일한
결과다. 서버 측 근거는 `authenticate.go:480-492` 가 쿠키를 쿼리보다 먼저
확인한다는 점이며, 이는 실측으로 확정됐다.

### 5.3 에러 envelope 실측 형상

```
401 {"error":{"message":"Authentication is required.",
              "reason":"AUTHENTICATION_REQUIRED",
              "request_id":"req_DMWHBNCFZNIEHV6B3GSZZPM2QE",
              "status":"UNAUTHENTICATED"}}
404 {"error":{"message":"The call was not found.",
              "reason":"CALL_NOT_FOUND",
              "request_id":"req_LBCAH56TXY63AZ5STTPLS7C7SQ",
              "status":"NOT_FOUND"}}
400 {"error":{"message":"The provided id is not a valid UUID.",
              "reason":"INVALID_ID",
              "request_id":"req_E3P4DCEWXU3IR2IJ3GW5T3ZEKU",
              "status":"INVALID_ARGUMENT"}}
403 {"error":{"message":"You do not have permission to access this resource.",
              "reason":"PERMISSION_DENIED",
              "request_id":"req_RKCJ7EDS6ZSWBVMSS25HHAVG6M",
              "status":"PERMISSION_DENIED"}}
```

필드는 `message`, `reason`, `request_id`, `status` 4개. `details` 는 선택
(`apierror/envelope.go:19-37`). 현재 `client.py:51` 은 최상위 `message` 를
읽으므로 **네 경우 모두 빈 문자열**이 된다.

### 5.4 권한 실측

```
trunks -> 200 {"result":[],"next_page_token":""}
routes -> 403 PERMISSION_DENIED
```

감사 §8 의 판정과 일치한다. `/trunks` 는 accesskey 로 접근 가능(단 B 범위),
`/routes` 는 403. A-5.14 의 근거가 실측으로 확정됐다.

### 5.4b accesskey 는 **어떤 배포에서도** superadmin 게이트를 통과할 수 없다

v1 설계 D-F 는 "셀프호스팅 superadmin 계정에서는 실제로 동작하기도 한다"고
적었다. **거짓이다.** 리뷰 라운드 1·2 가 독립적으로 지적했고 코드로 확정했다.

`bin-api-manager/models/auth/auth.go:102-120`:

```go
// Accesskey: hardcoded CustomerAdmin (matches current behavior).
func (a *AuthIdentity) HasPermission(p amagent.Permission) bool {
	switch a.Type {
	case TypeAgent:
		...
		return a.Agent.HasPermission(p)
	case TypeAccesskey:
		return (amagent.PermissionCustomerAdmin & p) != 0
	...
```

`bin-agent-manager/models/agent/agent.go`:

```
63: PermissionProjectSuperAdmin Permission = 0x0001
71: PermissionCustomerAdmin     Permission = 0x0020
```

`0x0020 & 0x0001 == 0` 이므로 accesskey 인증은 **구조적으로** superadmin 권한을
가질 수 없다. 키를 소유한 계정이 무엇이든, 배포가 관리형이든 셀프호스팅이든
무관하다. 그리고 이 MCP 클라이언트는 accesskey 만 보낸다(`client.py:25`).

결론: `list_routes`/`get_route` 는 **모든 MCP 사용자에게 100% 403** 이다.
D-F 의 판단(유지/제거)은 다시 논증해야 하며, docstring 에 "superadmin 전용"이라
쓰면 LLM 이 "권한을 올리면 된다"고 오해하고 재시도한다. §6 D-F 를 개정했다.

### 5.5 `VOIPBIN_API_BASE_URL` 선례 확인 (감사 §5-4 의 주장 검증)

감사 문서는 "api-validator 가 이미 동일 이름을 쓴다"고 적었다. 리뷰 라운드 2 가
코드에 없다고 지적했는데, **부분적으로 틀렸다.** 실제 코드에 존재한다:

```
api-validator/scripts/generate_recording.py:457
    base_url = env.get("VOIPBIN_API_BASE_URL", os.getenv("VOIPBIN_API_BASE_URL", ""))
api-validator/tests/scenarios/test_rate_limit.py:20,53
api-validator/tests/scenarios/test_error_envelope_smoke.py
api-validator/tests/scenarios/test_customer_unregister_e2e.py
api-validator/tests/scenarios/test_factories.py
api-validator/.env.example:2
    VOIPBIN_API_BASE_URL=https://api.voipbin.net/v1.0
```

선례는 실재하며, **계약은 "`/v1.0` 접미사를 포함한 전체 URL"** 이다
(`.env.example:2` 가 확정적 근거). 이 계약을 그대로 채택한다. §6 D-H 에 명시.

## 6. 설계 결정

### D-A. `mcp` 의존성: **`mcp>=1.2.0,<2`** (하한 교정 + shim 기각)

v1 의 `>=1.0.0,<2` 는 §5.1b 로 반증됐다. 하한을 **1.2.0** 으로 올린다.
1.2.0 이 `mcp.server.fastmcp` 를 처음 포함한 버전이며, wheel 설치 후 52개 툴
등록까지 실증했다.

shim 채택 여부는 별도 문제다. 두 안:

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| (a) | `mcp>=1.2.0,<2` 핀만 | 단순. 실증된 범위만 지원. | 2.x 사용자는 못 씀. |
| (b) | 핀 + import shim | 1.x/2.x 양쪽 동작 (§5.1 실증) | 테스트에 `inputSchema`/`input_schema` 폴백 필요. 2.x 전 표면 미검증. |

**(a) 를 채택한다.** 근거:

- §5.1 이 증명한 것은 "툴 등록과 list_tools 가 동작한다"까지다. stdio 런타임
  전체, 에러 전파, 타입 강제 등 2.x 표면을 검증한 것이 아니다. 지금 필요한
  것은 **깨진 배포를 되돌리는 것**이지 2.x 지원을 새로 여는 것이 아니다.
- shim 은 "동작하는 것처럼 보이지만 검증되지 않은 경로"를 만든다. 이번 사건의
  원인이 정확히 "검증되지 않은 의존성 범위를 배포한 것"이다. 같은 실수를
  형태만 바꿔 반복하게 된다.
- **§5.1b 가 이 논거를 강화한다.** v1 이 하한을 검증 없이 상속해서 틀린 처방을
  냈다. 검증 범위를 넘어서는 주장을 하지 않는 것이 이 PR 의 핵심 교훈이다.
- 2.x 지원은 별도 작업으로, 전체 테스트를 2.x 에서 돌려 확인한 뒤 상한을
  올리는 것이 정직하다. D-B 의 매트릭스 잡이 그때 근거를 제공한다.

§5.1 의 shim 실험 결과는 문서에 남긴다. 2.x 대응 작업의 출발점이 된다.

### D-B. CI: lock 환경 + 배포 범위 **양 끝** 검증

현재 CI 는 `uv pip install -e ".[dev]"` → `uv run pytest` 인데, `uv run` 이
`uv.lock` 으로 조용히 재동기화해서 배포 범위를 한 번도 보지 않는다 (감사 §6-4).

v1 은 `dist-smoke` 하나를 제안했으나, 리뷰가 정확히 지적했듯 `pip install
dist/*.whl` 은 **범위의 최신 끝만** 해석한다. 그러면 §5.1b 의 버그를 잡지
못한다. 형태만 바꾼 동일한 맹점이다.

세 잡으로 나눈다.

1. `test` (기존 유지): lock 환경에서 전체 테스트. 개발 재현성 보장.
2. `dist-smoke-latest` (신설): wheel 빌드 → 범위 최신으로 설치 → import +
   `list_tools()` 개수 + 콘솔 스크립트 entry point 해석 확인.
3. `dist-smoke-floor` (신설): 동일 wheel → **하한 명시 고정**
   (`pip install "mcp==1.2.0" dist/*.whl`) → 같은 검사.

`uv run` 은 쓰지 않는다. 그게 문제의 원인이었다. 순수 `python -m venv` +
`pip install` 로 간다.

**중요:** `uv pip install --resolution lowest-direct dist/*.whl` 은 이 목적에
쓸 수 없다. 리뷰 라운드 1 이 실측한 바로, wheel 의 의존성은 "direct" 로
취급되지 않아 여전히 mcp 2.2.0 을 해석한다. 하한은 **명시 핀**으로 고정한다.

하한 값은 `pyproject.toml` 의 하한과 한 곳에서 파생되어야 한다. CI 가 별도
상수를 들고 있으면 둘이 어긋난다. 워크플로에서 `pyproject.toml` 을 파싱해
하한을 추출하는 스텝을 둔다(단일 write-through).

### D-C. 에러 처리: envelope 파싱 + request_id 노출 + **error_map 재구성**

`client.py` 의 에러 경로를 다음으로 바꾼다.

- 바디에서 `error.message` 를 먼저 찾고, 없으면 최상위 `message`, 그것도
  없으면 원문을 쓴다. **원문 폴백은 200자로 절단하고 개행을 공백으로
  정규화한다.** 중간 프록시의 HTML 에러 페이지가 LLM 컨텍스트로 덤프되는 것을
  막는다.
- `error.request_id` 와 `error.reason` 을 **`VoIPbinAPIError` 의 구조화 속성으로
  보존하고**(`reason`, `request_id`) 메시지에도 포함한다. 문자열 보간만 하면
  테스트와 후속 도구가 파싱을 다시 해야 한다.
- **기존 `error_map` (`client.py:55-61`) 을 재구성한다.** 현재
  `401: "Invalid or expired API key"`, `402: "Insufficient credits"`,
  `403: "Permission denied"` 는 `{detail}` 보간이 없어 **서버 메시지·reason·
  request_id 를 전부 버린다.** 파싱만 고치고 이 표를 그대로 두면 403 은 여전히
  아무 정보도 전달하지 못하며, D-F 의 논거("권한이 없다고 답한다")가 성립하지
  않는다. 모든 상태코드가 동일한 형식을 쓰도록 통일한다.
- 429 를 추가하고 `Retry-After` 헤더를 메시지에 담는다.
  **자동 재시도는 하지 않는다.** 재시도는 클라이언트 책임이라는 기존 설계
  원칙을 따르고, MCP 툴 호출 안에서 조용히 지연되면 LLM 이 상황을 파악할 수
  없다. 대신 LLM 이 판단할 수 있도록 대기 시간을 메시지에 명시한다.

에러 메시지 형식(사람과 LLM 이 같이 읽음, plain text). **reason 은 서버가 준
값을 그대로 echo 한다. 하드코딩하지 않는다.**

```
VoIPbin API error 404 (CALL_NOT_FOUND): The call was not found. [request_id: req_LBCAH...]
VoIPbin API error 429 (RATE_LIMIT_EXCEEDED): ... Retry after 30 seconds. [request_id: ...]
```

v1 은 429 예시에 `RATE_LIMITED` 라고 적었는데 **존재하지 않는 값**이다. 서버는
`RATE_LIMIT_EXCEEDED` 를 준다(`customer_ratelimit.go:238`, status
`RESOURCE_EXHAUSTED`). 429 사이트는 두 곳(`customer_ratelimit.go:237`,
`ratelimit.go:163`)이며 둘 다 `Retry-After` 를 설정한다. 이 오류는 본 PR 이
고치려는 결함("스펙에 없는 값을 문서에 적는 것")과 정확히 같은 종류였다.

### D-D. 인증 전송: **쿠키 + 쿼리 폴백 스위치**

§5.2 로 쿠키 동작이 확인됐다. 쿼리 파라미터는 서버 액세스 로그와 프록시 로그에
키를 남기므로 기본값을 쿠키로 바꾼다.

**단 v1 의 "쿠키 전용" 은 위험하다.** 리뷰 라운드 2 의 지적이 맞다. D-H 의
`VOIPBIN_API_BASE_URL` 이 같은 릴리스에 들어가므로, 쿠키 경로가 **검증된 적
없는 고객 ingress**(nginx/traefik/k8s)에서 즉시 돌게 된다. Cookie 헤더를
제거·재작성하는 프록시 설정은 흔하고, 그 경우 사용자는 **전면 인증 실패**를
겪으며 릴리스 내 우회 수단이 없다.

따라서:
- 기본값: 쿠키 전송.
- `VOIPBIN_AUTH_TRANSPORT=query` 로 쿼리 전송으로 되돌릴 수 있다. 문서화한다.
- 전송 방식은 **요청별 `Cookie` 헤더**로 고정한다. `AsyncClient(cookies=...)`
  의 도메인 스코프 쿠키 저장소는 생성 시점에 도메인이 고정되어, 셀프호스팅
  URL 이나 리다이렉트를 건널 때 조용히 아무것도 보내지 않을 수 있다. 구현자
  두 명이 서로 다른 동작을 만들지 않도록 명시한다.
- 검증은 GET 뿐 아니라 POST/PUT/DELETE 전부에 대해 한다. 인증 주입 지점이
  공유 헬퍼(`client.py:79,89,99` 가 같은 경로를 쓴다)이므로 한 곳이 틀리면
  전부 틀린다.

README Security Note 를 함께 갱신한다.

### D-E. `create_contact` / contacts 하위 리소스 + **free-form dict 차단**

`create_contact` 는 `addresses` + `tag_ids` 로 교정한다. `CommonAddress` 는
`type`, `target`, `target_name` 등을 갖고 POST 본문은 `is_primary` 를 더한다.

`update_contact` 은 PUT 본문에 주소/태그가 **없으므로** docstring 의 허위 키만
제거하고, 하위 리소스 5개 operation 을 새 툴로 노출한다 (§3 에서 밝힌 예외).

| 새 툴 | operation |
|---|---|
| `add_contact_address` | POST `/contacts/{id}/addresses` |
| `update_contact_address` | PUT `/contacts/{id}/addresses/{address_id}` |
| `delete_contact_address` | DELETE `/contacts/{id}/addresses/{address_id}` |
| `add_contact_tag` | POST `/contacts/{id}/tags` |
| `delete_contact_tag` | DELETE `/contacts/{id}/tags/{tag_id}` |

52 + 5 = 57개. D3 의 상한 100 내.

**추가 결정 (리뷰 라운드 2 finding 11): free-form `fields` dict 를 차단한다.**

`update_contact(contact_id, fields: dict[str, Any])` (`contacts.py:85-95`) 와
`update_campaign(campaign_id, fields)` (`campaigns.py:70`) 는 임의 dict 를
그대로 PUT 본문으로 보낸다.

```python
async def update_contact(contact_id: str, fields: dict[str, Any]) -> str:
    """...
        fields: Fields to update (first_name, last_name, display_name,
                company, job_title, notes, phone_numbers, emails)
    """
    return format_response(await client.put(f"/contacts/{contact_id}", fields))
```

docstring 에서 `phone_numbers`/`emails` 를 지우는 것은 **초대장만 없애고 경로는
그대로 두는 것**이다. LLM 이 `fields={"phone_numbers": [...]}` 를 넘기면 여전히
200 이 떨어지고 필드는 조용히 사라진다. 이번 릴리스가 없애겠다고 선언한 결함
그 자체다. 대표님의 "부분 MVP 보다 완전 해결" 원칙에도 어긋난다.

→ `update_contact` 과 `update_campaign` 을 **명시적 파라미터**로 바꾼다
(`create_contact` 이 이미 그 형태다). 스펙 PUT 본문의 필드만 파라미터로
노출하면, 스펙에 없는 키는 **애초에 전달할 방법이 없다.** 타입 스키마가
경계를 강제하므로 별도 검증 코드가 필요 없다.

`update_contact` 파라미터: `first_name`, `last_name`, `display_name`,
`company`, `job_title`, `external_id`, `notes` (= `paths/contacts/id.yaml:49-63`
전체). v1 은 `external_id` 를 빠뜨렸다. `create_contact` 에는 스펙에 있으나
현재 툴과 docstring 양쪽에 없는 `source`(enum `manual|import|api|sync`) 와
`external_id` 를 추가한다. A-3.10 이 다른 모듈에서 고치는 것과 같은 종류의
누락이다.

### D-F. `list_routes` / `get_route`: 유지 + **"accesskey 로 사용 불가" 명시**

제거와 유지 중 **유지**를 택한다. 툴을 지우면 LLM 은 "그런 기능이 없다"고
답하고, 남기면 "권한이 없다"고 답한다. 후자가 정확하다.

**단 v1 의 근거 문장은 거짓이었다.** v1 은 "셀프호스팅 superadmin 계정에서는
실제로 동작하기도 한다"고 적었는데, §5.4b 가 반증했다. accesskey 인증은
`auth.go:109-110` 의 하드코딩 때문에 **어떤 계정·어떤 배포에서도** superadmin
게이트를 통과할 수 없다. 100% 403 이다.

따라서 docstring 은 "superadmin 전용"이라고 쓰면 안 된다. 그렇게 쓰면 LLM 이
"권한을 올리면 된다"고 판단해 재시도하거나, 사용자에게 권한 상향을 안내하는데
**존재하지 않는 경로**다. 대신 이렇게 쓴다:

```
Not available via accesskey authentication. This endpoint requires
project super-admin permission, which accesskey identities never hold.
Always returns 403. Provided so that permission errors are reported
accurately rather than as a missing feature.
```

유지 결정 자체는 두 근거 중 하나(LLM 이 "기능 없음" 대신 "권한 없음"이라고
정확히 답한다)만으로 성립하므로 유효하다.

### D-G. docstring 정책: 열거 대신 위임

무효 enum 11개를 고칠 때, 값을 다시 하드코딩하면 스펙이 바뀔 때 또 틀어진다.
이번 사건이 그 증거다(gpt-4o-mini 는 한때 유효했을 것이다).

원칙:
- **닫힌 소집합이고 잘 안 바뀌는 것**(campaign type/end_handle, address type)
  → 전체 값을 열거한다.
- **자주 바뀌는 벤더 목록**(engine_model, stt_type, tts_type) → 대표 값 몇 개
  + "스펙 참조" 를 함께 적고, 예시가 전부가 아님을 명시한다.
- **대형 enum**(action type 40개) → 전체 열거 대신 카테고리와 대표 값,
  그리고 "잘못된 값은 400 이 난다"는 사실을 적는다.

### D-H. `VOIPBIN_API_BASE_URL` 계약 명시

§5.5 가 api-validator 의 실제 선례를 확인했다. 그 계약을 그대로 채택한다.

- **값은 `/v1.0` 접미사를 포함한 전체 base URL** 이다.
  예: `VOIPBIN_API_BASE_URL=https://api.voipbin.net/v1.0`
  (`api-validator/.env.example:2` 와 동일)
- 끝 슬래시는 `rstrip("/")` 로 정규화한다.
- 미설정 시 기본값은 현재 하드코딩된 `https://api.voipbin.net/v1.0` 이다.
  기존 사용자에게 파괴적이지 않다.
- 값 검증은 하지 않는다(스킴 강제 등). 잘못된 값은 httpx 가 즉시 에러를 낸다.

구현자 두 명이 서로 다른 계약을 만들지 않도록 README 와 docstring 양쪽에
"`/v1.0` 포함" 을 명시한다.

### D-I. 릴리스 게이트 구체화 (v1 은 "테스트+스모크 게이트" 한 줄이었다)

리뷰 라운드 2 의 지적대로 v1 의 서술은 구현자마다 다른 것을 만든다. 확정한다.

- **build-once-then-publish**: 게이트가 검증한 **그 wheel 을** 업로드한다.
  게이트 후 재빌드하면 검증한 산출물과 배포 산출물이 달라져 게이트가 무의미해
  진다. `upload-artifact`/`download-artifact` 로 동일 파일을 넘긴다.
- **잡 본문 중복 금지**: `dist-smoke` 로직은 재사용 가능한 워크플로
  (`workflow_call`) 로 빼서 `ci.yml` 과 `publish.yml` 이 같은 것을 호출한다.
  복붙하면 나중에 한쪽만 고쳐져 갈라진다.
- **`environment:` 보호 게이트 추가**: 감사 §6-5 가 지적한 세 결함 중 v1 이
  이것을 누락했다. PyPI 업로드 잡에 GitHub environment 를 걸어 수동 승인
  지점을 만든다.
- **태그 ↔ 버전 일치 검사**: 릴리스 태그와 `pyproject.toml:3` 의 `version` 이
  다르면 실패시킨다. PyPI 파일은 불변이므로 잘못 올리면 버전을 태워야 한다.
- 액션은 commit SHA 로 핀한다.

### D-J. 버전 번호와 롤백 절차

v1 은 "버전 bump" 라고만 적어 구현자가 `0.1.2` 와 `0.2.0` 중 아무거나 고를 수
있었다. **`0.2.0` 으로 확정한다.** 근거: `create_contact` 의 파라미터가
`phone_numbers`/`emails` → `addresses`/`tag_ids` 로 바뀌고 `update_contact` 이
free-form dict 에서 명시 파라미터로 바뀌므로 **툴 호출 계약의 파괴적 변경**이다.
에러 메시지 형식도 바뀐다. patch bump 는 이를 숨긴다.

`CHANGELOG.md` 를 신설하고 파괴적 변경을 명시한다. (§7 에 추가)

**롤백**: PyPI 파일은 불변이므로 되돌릴 수 없다. 나쁜 업로드는
(1) `pypi.org` 에서 해당 릴리스를 yank, (2) 수정 후 다음 패치 버전으로 재배포
한다. yank 는 기존 핀 사용자를 깨지 않으면서 신규 해석에서만 제외한다. README
에 이 절차를 적지 않고, `docs/RELEASING.md` 에 적는다.

`Development Status` 분류자는 B-2 에서 판단하기로 감사 단계에서 정했으나,
이 릴리스가 버전 bump 와 함께 `4 - Beta` 를 재주장하게 된다. **A 에서 함께
처리한다**: 배포가 실제로 동작하고 릴리스 게이트가 서는 시점에는 Beta 주장이
정당해지므로, `4 - Beta` 를 유지하되 `pyproject.toml:17-19` 의 Python 분류자에
3.13 을 추가해 CI 가 주장하는 범위와 메타데이터를 일치시킨다.

## 7. 영향 파일

| 파일 | 변경 | 관련 |
|---|---|---|
| `pyproject.toml` | **`mcp>=1.2.0,<2`**, 버전 `0.2.0`, ruff dev 의존성 + `[tool.ruff]` 설정, Python 3.13 분류자 | A-1.1, A-5.18, A-5.19, D-J |
| `uv.lock` | **재생성** (`mcp` specifier 변경으로 무효화됨: `uv.lock:900` 의 `specifier = ">=1.0.0"`, `:322-323` 의 1.27.0). 재생성하지 않으면 `test` 잡이 relock 하며 트리가 dirty 해진다 | A-1.1 |
| `.github/workflows/ci.yml` | `dist-smoke-latest` + `dist-smoke-floor` 잡 신설, Python 3.13, ruff 게이트 | A-1.2, A-5.18 |
| `.github/workflows/dist-smoke.yml` | **신설** (`workflow_call` 재사용 워크플로, D-I) | A-1.2, A-1.3 |
| `.github/workflows/publish.yml` | 재사용 스모크 호출, build-once-then-publish, `environment:` 게이트, 태그↔버전 검사, 액션 SHA 핀 | A-1.3, D-I |
| `src/voipbin_mcp/client.py` | envelope 파싱 + `reason`/`request_id` 구조화 속성, **error_map 전면 재구성**, 429 + `Retry-After`, 원문 폴백 200자 절단, `VOIPBIN_API_BASE_URL`, 요청별 `Cookie` 헤더 + `VOIPBIN_AUTH_TRANSPORT` 스위치 | A-2.6, A-2.7, A-5.15, D-C, D-D, D-H |
| `src/voipbin_mcp/tools/contacts.py` | `addresses`/`tag_ids`, `source`/`external_id` 추가, **`update_contact` free-form dict → 명시 파라미터**, 하위 리소스 5툴 | A-2.4, A-2.5, D-E |
| `src/voipbin_mcp/tools/campaigns.py` | enum 4값, required 4필드, **`update_campaign` free-form dict → 명시 파라미터**, 모듈 요약문 | A-3.8, A-4.11, D-E |
| `src/voipbin_mcp/tools/ais.py` | enum 5값, write-only 서술, required `parameter` | A-3.8, A-3.9, A-4.12 |
| `src/voipbin_mcp/tools/flows.py` | action type 2값, uuid 예시 | A-3.8, A-3.10 |
| `src/voipbin_mcp/tools/calls.py` | 주소 타입 전체 | A-3.10 |
| `src/voipbin_mcp/tools/emails.py` | `attachments` | A-4.13 |
| `src/voipbin_mcp/tools/conferences.py` | required 전체, `type` 하드코딩 해제 | A-4.13 |
| `src/voipbin_mcp/tools/routes.py` | **"accesskey 로 사용 불가" 명시** (superadmin 전용 아님, §5.4b) | A-5.14, D-F |
| 모든 list 툴 | page_size 1–100 명시 | A-3.10 |
| `tests/test_client.py` | **기존 픽스처 교정**: envelope 중첩 형태로 교체(`:62,71,91`), **accesskey URL 단언 재작성**(`:45,:56` → Cookie 헤더 존재 + URL 에 accesskey **부재**), base URL 기본값 단언(`:21-22`) 유지 + 환경변수 오버라이드 추가, 429 테스트 | A-2.6, A-5.15, A-5.16 |
| `tests/test_tools_contacts.py` 외 4개 | 신설 | A-5.16 |
| `tests/data/openapi_enums.json` | **신설** (스펙에서 생성한 enum 스냅샷, §8-4) | A-5.16 |
| `scripts/regen_openapi_enums.py` | **신설** (스냅샷 재생성 스크립트, §8-4) | A-5.16 |
| `README.md` | 예시, **격리 설치(uvx/pipx) 우선 안내 + 공유 venv 다운그레이드 경고**, Security Note, `VOIPBIN_API_BASE_URL`/`VOIPBIN_AUTH_TRANSPORT`, 지원 범위 | A-5.17, D-K |
| `CHANGELOG.md` | **신설** (0.2.0 파괴적 변경 명시) | D-J |
| `docs/RELEASING.md` | **신설** (yank + 재배포 절차) | D-J |

`src/voipbin_mcp/server.py` 는 A 범위 변경이 없다. v1 표의 "page_size 상한
docstring 반영 지원" 행은 실제 변경을 서술하지 않았다. `validate_page_size`
(`server.py:28-34`) 의 동작은 그대로 두고(§9 에 수용 근거 기재), docstring 변경은
"모든 list 툴" 행이 담당한다.

## 8. 검증 계획

1. `pytest tests/ -q` 전체 통과.
2. **배포 경로 실증 (범위 양 끝)**: `uv build` → 새 venv 2개 →
   (a) `pip install dist/*.whl` (최신 해석), (b) `pip install "mcp==1.2.0"
   dist/*.whl` (하한 고정) → 각각에서:
   - `python -c "import voipbin_mcp.server"` 성공
   - `list_tools()` 개수 == 57
   - 콘솔 스크립트 entry point 가 해석되는지
     (`importlib.metadata.entry_points` 로 `voipbin-mcp` → `main` 확인)

   **`voipbin-mcp --help` 는 쓰지 않는다.** `server.py:41-43` 에 인자 파싱이
   없어 `--help` 는 무시되고, stdin 상태에 따라 exit 0 이 되거나 블록된다.
   리뷰 라운드 1 이 "exit 0 은 stdin 이 tty 가 아니었기 때문"임을 실측했다.
   아무것도 증명하지 않는 검사다.
   README 가 광고하는 `uvx voipbin-mcp` 경로(`README.md:18,31,49`)도 함께
   실행해 확인한다.
3. **회귀 재현 테스트**: `create_contact` 가 `addresses` 를 보내는지 요청 바디
   단위로 단언. 기존 `phone_numbers` 형태면 실패해야 한다.
4. **enum 스냅샷 드리프트 테스트**: v1 은 "교차 검증"이라 불렀으나 **교차가
   아니었다.** 기대값을 테스트 파일에 상수로 박으면 docstring 과 상수 둘 다 이
   저장소 안에 있어, 검사는 "둘 중 하나를 안 고쳤는지"만 본다. 정작 막아야 하는
   **스펙 드리프트**(`gpt-4o-mini` 가 낡는 일)는 원리적으로 못 잡는다. v1 §9 가
   이를 "본질적 한계" 라 쓴 것은 틀렸다. 런타임 의존성과 CI 검사를 혼동한
   것이다.

   → `tests/data/openapi_enums.json` 에 스펙에서 **생성한** enum 스냅샷을
   커밋하고, `scripts/regen_openapi_enums.py` 로 재생성한다(monorepo 가 있을 때
   실행, diff 를 리뷰에서 확인). 테스트는 docstring 값이 스냅샷의 부분집합인지
   본다. 스펙은 **런타임 의존성이 되지 않으면서** 드리프트가 기계적으로
   보이게 된다. 스냅샷 재생성 diff 가 곧 드리프트 알림이다.
5. **뮤테이션 확인**: 새 테스트가 tautology 가 아닌지, 고친 코드를 일부러
   되돌려 실패하는지 확인한다.
6. 라이브 스모크: 실계정으로 **GET/POST/PUT/DELETE 전부** 최소 1건씩 호출해
   쿠키 전송과 에러 형식을 확인한다. 인증 주입이 공유 헬퍼이므로 GET 만으로는
   부족하다. `VOIPBIN_AUTH_TRANSPORT=query` 폴백도 함께 확인한다.

## 9. 롤아웃 / 리스크

| 리스크 | 정도 | 대응 |
|---|---|---|
| 쿠키 전송이 일부 프록시에서 막힘 | **중간** (셀프호스팅 ingress 는 미검증) | 관리형 엔드포인트 실측 200 확인. `VOIPBIN_AUTH_TRANSPORT=query` 스위치를 **같은 릴리스에** 넣어 우회 경로를 제공(D-D). |
| `mcp<2` 상한이 공유 venv 의 mcp 2.x 를 **다운그레이드** | **중간** | 단순 배제가 아니라 다른 MCP 서버를 깨뜨릴 수 있다. README 가 격리 설치(uvx/pipx)를 우선 안내하고 공유 venv 경고를 명시한다(D-K). |
| enum 드리프트 재발 | 낮음 | §8-4 의 스펙 생성 스냅샷 + 재생성 스크립트로 기계적으로 가시화. |
| contacts 하위 툴 5개 추가가 A 범위를 넘는다는 지적 | 해소됨 | 감사 §9-A item 5 가 이미 명령한 항목이다(§3 재인용). 자의적 예외가 아니다. |
| 버전 bump 후 릴리스가 또 깨짐 | 낮음 | D-I 의 publish 게이트 + D-B 의 양 끝 스모크. 실패 시 D-J 의 yank 절차. |
| `validate_page_size` 의 조용한 값 보정 유지 | 수용 | `page_size=0` → 1, 비정수 → 10 으로 조용히 바뀐다. 데이터 유실과 달리 결과가 왜곡되지 않고 도구 재호출로 복구되므로 A 에서는 docstring 명시만 한다. **의도적 수용이며 누락이 아니다.** |

### D-K. README 설치 안내 순서

`pip install voipbin-mcp` (`README.md:11-13`) 를 첫 안내로 두면, mcp 2.x 가 있는
공유 venv 사용자는 `mcp<2` 상한 때문에 **mcp 가 1.x 로 다운그레이드되어 다른
모든 MCP 서버가 깨진다.** `uvx` (`README.md:18`) 는 격리 실행이라 안전하다.

→ README 는 `uvx`/`pipx` 격리 설치를 **먼저** 안내하고, `pip install` 은 전용
venv 를 전제로 한 대안으로 배치한다. 공유 환경 다운그레이드 경고를 명시한다.

## 10. Open questions (리뷰어 판단 요청)

1. D-A: shim 대신 상한 핀만 채택한 판단이 타당한가. 2.x 지원을 지금 여는 것이
   더 나은가.
2. D-F: `list_routes` 유지 판단이 타당한가. 100% 403 인 툴이 LLM 컨텍스트를
   차지하는 비용 대비 가치가 있는가(§5.4b 로 근거가 하나 줄었다).
3. D-J: `Development Status :: 4 - Beta` 유지 판단이 타당한가. 배포가 처음으로
   실제 동작하게 되는 릴리스이므로 Beta 주장이 정당해진다고 봤으나, 릴리스
   이력이 한 번 깨진 전례를 감안하면 Alpha 강등이 정직한 선택일 수도 있다.

(v1 의 질문 3·4 는 해소됐다. 3 → §8-4 에서 스펙 생성 스냅샷으로 해결. 4 →
감사 §9-A item 5 가 이미 명령한 항목이므로 스코프 위반이 아니다.)

## 11. 리뷰 이력

| 회차 | 판정 | 핵심 피드백 | 조치 |
|---|---|---|---|
| 1 | CHANGES_REQUESTED | BLOCKER: `mcp>=1.0.0,<2` 가 버그를 못 고침 (fastmcp 는 1.2.0 부터). MAJOR: dist-smoke 가 범위 한쪽 끝만 검증 / accesskey superadmin 주장 거짓 / enum "교차 검증"이 교차 아님. MINOR 6건 | 하한 `>=1.2.0` 교정(§5.1b 실측), dist-smoke 2개 분리(D-B), §5.4b 신설 + D-F 재논증, §8-4 스펙 스냅샷 방식으로 교체, §7 표에 uv.lock/ruff/CHANGELOG/RELEASING 추가, server.py 행 삭제, 429 reason `RATE_LIMIT_EXCEEDED` 정정, `--help` 검사 폐기 |
| 2 | CHANGES_REQUESTED | 동일 BLOCKER 2건 독립 발견. MAJOR: free-form `fields` dict 로 조용한 유실 경로 잔존 / 쿠키 전용은 셀프호스팅 우회수단 없음 / base URL 계약 미정의 / 릴리스 게이트 서술 불충분 + environment 게이트 누락 / error_map 이 메시지 폐기 / mcp<2 가 공유 venv 다운그레이드 / 버전·롤백 미정 | D-E 에 명시 파라미터 전환 추가, D-D 에 `VOIPBIN_AUTH_TRANSPORT` 폴백 + 요청별 Cookie 헤더 고정, D-H 신설(§5.5 로 선례 실증), D-I 신설, D-C 에 error_map 재구성 명시, D-K 신설, D-J 신설(0.2.0 + yank 절차), §3 에서 contacts 예외 프레이밍 철회 |
