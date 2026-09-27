# voipbin/mcp 스코프 A 설계: 배포 복구 및 계약 정합성 (2026-09-28)

Status: v6 (설계 리뷰 라운드 1–10 반영)

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

  **원칙의 적용 범위 (stopping rule).** 이 원칙은 리뷰 라운드를 거치며 A 를
  두 번 확장시켰다(+5 contacts, +1 `update_campaign_actions`). 종료 조건이
  없으면 계속 확장되므로 여기서 확정한다. 다음 세 조건을 **모두** 만족할
  때에만 A 에 새 툴을 추가한다.

  1. 제거 대상 키가 **현재 배포된 docstring 에 실제로 존재**하고, 그 키를
     넘기면 서버가 200/201 을 돌려주면서 값을 **조용히 버린다**. 에러가 나는
     키는 이미 정직하므로 해당 없음.
  2. 그 값을 바꾸는 **기존 operation 이 스펙에 이미 존재**한다. 신규 서버
     기능을 요구하면 해당 없음.
  3. 그 operation 이 **바디 필드 1개 수준의 단순 래퍼**로 노출 가능하다.
     새 중첩 모델이나 새 파라미터 클래스가 필요하면 B.

  세 조건을 만족하지 않으면 docstring 에서 키를 제거하고 README 의 알려진
  제약 목록에 기재한 뒤 B 로 넘긴다.

  **본 릴리스에서 이 규칙으로 확정된 신규 툴은 6개이며(contacts 5 +
  `update_campaign_actions` 1), 이후 발견되는 동종 결함은 A 를 다시 확장하지
  않고 B 로 보낸다.** 근거: A 의 go/no-go 는 감사 §3-1(배포 불능) 단독으로
  성립하므로, A 의 추가 확장은 배포 복구를 지연시키는 순손실이다.

  **전수 sweep 결과(라운드 9 가 4계층 체크리스트로 쓰기 툴 17개 전 필드를
  추적, CPO 재확인):** **신규 툴 추가를 요하는** 동종 결함(silent drop)은
  더 없다. 라운드 5~9 가 찾은 추가 결함들(`target_name`, `stt_type`,
  `engine_model`, `calls.py`, `conferences.py`)은 전부 **docstring 수정으로
  닫히며 새 툴을 요구하지 않으므로** 이 규칙의 대상이 아니다(D-M1~D-M6). `update_flow`(`flows.py:66`)의
  `name`/`detail`/`actions` 는 세 필드 모두 `paths/flows/id.yaml:49-61` 에
  존재하며 required 전체 교체로 정직하게 선언되어 있다. 같은 파일의
  `on_complete_flow_id` 는 **없는 필드가 아니라 노출하지 않은 필드**이므로
  거짓 서술이 아니고 B 에 속한다(§3 마지막 항목).
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
| D3 | 툴 개수 상한 100개 내외. | CPO 권고안. A 는 툴을 6개 늘리므로(52→58) 제약이 걸리지 않는다. |

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

**단 이 "동일하다" 는 1.30.0 과 2.2.0 사이의 비교이며, 핀 범위 전체에
성립하지 않는다.** 하한 1.2.0 을 실측하면 다르다:

```
1.2.0  tool: (name=None, description=None)
1.2.0  run : (transport: Literal['stdio','sse'] = 'stdio')
1.30.0 tool: (name, title, description, annotations, icons, meta,
              structured_output)
1.30.0 run : (transport: Literal['stdio','sse','streamable-http'],
              mount_path=None)
```

핀 범위 `>=1.2.0,<2` 는 시그니처가 균일하지 않다. 1.2.0 → 1.30.0 사이에
`tool()` 은 파라미터가 늘었고 `run()` 의 `transport` 리터럴도 확장됐다.

**범위 전체에 공통인 불변식은 다음 둘뿐이다:**
- 인자 없는 `@mcp.tool()` 호출
- `run(transport="stdio")`

현재 코드가 정확히 이 교집합 안에 있어서 §5.1b 의 스모크가 전 구간 통과했다.
향후 `tool(title=...)` 이나 `transport="streamable-http"` 같은 신규 인자를
쓰면 **하한에서 깨진다.** 구현자는 이 교집합을 벗어나지 않아야 하며, 벗어나야
할 때는 하한을 함께 올려야 한다.

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
1.1.1 installed 1.1.1 | fastmcp False
1.1.2 installed 1.1.2 | fastmcp False
1.1.3 installed 1.1.3 | fastmcp False
1.2.0 installed 1.2.0 | fastmcp True
1.2.1 installed 1.2.1 | fastmcp True
```

(리뷰 라운드 3 이 전 wheel 을 조사해 1.2.0 이상 53개 non-prerelease 1.x 에
빠짐없이 존재함을 확인했다. 하한 위쪽에 구멍은 없다.)

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
   `list_tools()` 개수 + 콘솔 스크립트 entry point 해석 + **stdio 런타임 기동
   확인**.
3. `dist-smoke-floor` (신설): 동일 wheel → **하한 명시 고정** → 같은 검사
   **+ 하한에서 전체 pytest 실행**.

**런타임 기동을 반드시 포함한다 (리뷰 라운드 3 MAJOR 2).** entry point
"해석"은 `load()` 가 함수 객체를 돌려주는 것까지이고 **실제로 호출하지
않는다.** `server.py:43` 의 `run(transport="stdio")` 는 이 코드베이스의 유일한
run 호출이며 §5.1 에서 확인한 대로 `transport` 리터럴이 핀 범위 안에서
변했다. import 만 검증하면 런타임 진입점은 양 끝 모두 미검증이다.
→ stdio 로 JSON-RPC `initialize` 프레임을 한 건 넣고 응답을 받거나, timeout
아래에서 traceback 없이 종료하는지 단언한다.

**하한에서 pytest 를 돌린다 (같은 finding).** `test` 잡은 `uv.lock` 의 단일
mcp 버전만 본다. §5.1 이 기록한 `inputSchema` → `input_schema` 속성 개명은
**테스트 코드에만 보이는** 종류의 드리프트이고, import 스모크로는 절대 잡히지
않는다. `dist-smoke-floor` 에서 `pip install "mcp==<floor>" dist/*.whl ".[dev]"`
후 pytest 를 실행한다.

`uv run` 은 쓰지 않는다. 그게 문제의 원인이었다. 순수 `python -m venv` +
`pip install` 로 간다.

**중요:** `uv pip install --resolution lowest-direct dist/*.whl` 은 이 목적에
쓸 수 없다. 리뷰 라운드 1 이 실측한 바로, wheel 의 의존성은 "direct" 로
취급되지 않아 여전히 mcp 2.2.0 을 해석한다. 하한은 **명시 핀**으로 고정한다.

하한 값은 `pyproject.toml` 의 하한과 한 곳에서 파생되어야 한다. CI 가 별도
상수를 들고 있으면 둘이 어긋난다. 워크플로에서 `pyproject.toml` 을 파싱해
하한을 추출하는 스텝을 둔다(단일 write-through). 예:

```bash
FLOOR=$(python -c "import tomllib,re;d=tomllib.load(open('pyproject.toml','rb'));\
print([re.search(r'>=([0-9.]+)',x).group(1) for x in d['project']['dependencies'] \
if x.startswith('mcp')][0])")
pip install "mcp==${FLOOR}" dist/*.whl
```

(위 예시는 파생 형태를 보이기 위한 것이다. 리터럴 `mcp==1.2.0` 을 워크플로에
박으면 `pyproject.toml` 과 어긋날 수 있다.)

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
`RATE_LIMIT_EXCEEDED` 를 준다(`customer_ratelimit.go:239`, status
`RESOURCE_EXHAUSTED`).

**429 발생 지점은 세 곳이며, 셋째는 `Retry-After` 를 설정하지 않는다**
(리뷰 라운드 3 MINOR). v2 가 "둘 다 `Retry-After` 를 설정한다"고 쓴 것은
불완전했다:

| 위치 | `Retry-After` |
|---|---|
| `lib/middleware/customer_ratelimit.go:239` | 설정함 |
| `lib/middleware/ratelimit.go:163` | 설정함 |
| `server/error_translate.go:89-90` (→ `rpc.go:68-69` 가 429 로 매핑) | **설정 안 함** |

따라서 클라이언트는 **`Retry-After` 부재를 정상 경로로 처리해야 한다.**
헤더가 없으면 대기 시간 문구를 생략하고 나머지 메시지만 만든다. 헤더를 필수로
가정해 파싱하면 세 번째 경로에서 예외가 난다.

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

`create_contact` 는 `addresses` + `tag_ids` 로 교정한다. `addresses` 원소의
유효 계약은 **`type`(`tel`|`email`), `target`, `name`, `detail`,
`is_primary` 5개뿐**이다(`server/contacts.go:151-164` 가 읽는 필드 전부).
`CommonAddress` 스펙에 있는 `target_name` 은 **보내도 201 이 나오고 저장되지
않으므로 광고하지 않는다**(D-M1). 스펙이 아니라 게이트웨이가 권위다.

`update_contact` 은 PUT 본문에 주소/태그가 **없으므로** docstring 의 허위 키만
제거하고, 하위 리소스 5개 operation 을 새 툴로 노출한다 (§3 에서 밝힌 예외).

| 새 툴 | operation | 요청 바디 (스펙 실측) |
|---|---|---|
| `add_contact_address` | POST `/contacts/{id}/addresses` | **required** `type`(enum `tel`\|`email`), `target`; 선택 `name`, `detail`, `is_primary`. **`target_name` 없음** |
| `update_contact_address` | PUT `/contacts/{id}/addresses/{address_id}` | `target`, `name`, `detail`, `is_primary`. **`type` 없음** (주소 종류는 변경 불가) |
| `delete_contact_address` | DELETE `/contacts/{id}/addresses/{address_id}` | 바디 없음 |
| `add_contact_tag` | POST `/contacts/{id}/tags` | **required** `tag_id` (uuid) |
| `delete_contact_tag` | DELETE `/contacts/{id}/tags/{tag_id}` | 바디 없음 |

**바디를 여기에 명시하는 이유 (리뷰 라운드 4 BLOCKER 3):** v1/v2 는 경로만
적었다. 구현자가 스펙의 `CommonAddress` 정의를 그대로 재사용하면 POST 에
**게이트웨이가 읽지 않는 `target_name`** 을 보내고
PUT 에 **받지 않는 `type`** 을 보낸다. 둘 다 조용히 버려진다. 이 PR 이 없애려는
결함을 새로 만드는 것이다. 근거:
`paths/contacts/id_addresses.yaml:21-45`, `id_addresses_id.yaml:29-45`,
`id_tags.yaml:19-24`.

**`create_contact` 의 `addresses` 는 `list[dict[str, Any]]` 로 남는다.** 배열
원소까지 타입으로 강제하려면 별도 모델 클래스가 필요하고, MCP 툴 스키마가
중첩 객체를 다루는 방식이 mcp 버전에 따라 다르다(§5.1 의 불균일성). A 에서는
docstring 에 `CommonAddress` 필드를 정확히 열거하는 데까지만 한다. **의도적
수용이며 누락이 아니다.** 원소 스키마 강제는 B 로 넘긴다.

52 + 5 = 57개. 여기에 아래 `update_campaign_actions` 를 더해 **58개**.
D3 의 상한 100 내.

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

### D-E2. `update_campaign` 도 같은 결함이며, 같은 원칙을 적용한다

**리뷰 라운드 3·4 가 독립적으로 찾은 BLOCKER다. 감사 문서도, v1/v2 설계도
놓쳤다.**

`campaigns.py:70-78` 현재 상태:

```python
async def update_campaign(campaign_id: str, fields: dict[str, Any]) -> str:
    """Update a campaign.
        fields: Dictionary of fields to update (name, detail, actions, etc.).
    """
    result = await client.put(f"/campaigns/{campaign_id}", json=fields)
```

`PUT /campaigns/{id}` 바디는 `name`, `detail`, `type`, `service_level`,
`end_handle` **5개뿐이다** (`paths/campaigns/id.yaml:48-65` 실측). `actions` 는
**없다.** 즉 docstring 이 광고하는 `actions` 는 `create_contact` 의
`phone_numbers` 와 **완전히 동일한 조용한 유실**이다.

그리고 `actions` 는 별도 operation 을 갖는다:
`PUT /campaigns/{id}/actions` (`paths/campaigns/id_actions.yaml`,
`actions` required, `FlowManagerAction` 배열).

§3 이 세운 원칙("수단 없이 '이 필드는 못 바꿉니다'만 남기는 것은 결함을
문서화할 뿐이다")이 contacts 에 적용된 근거가 campaigns 에 그대로 적용된다.
`create_campaign` 은 이미 `actions` 를 필수로 받으므로(`campaigns/main.yaml`),
고치지 않으면 **LLM 이 actions 를 넣어 캠페인을 만들 수는 있는데 이후 절대
바꿀 수 없고, 그 사실을 알려주는 툴도 없는** 상태가 된다.

→ **`update_campaign_actions` 툴을 추가한다** (PUT `/campaigns/{id}/actions`).
contacts 와 동일한 논리이며, 한쪽만 적용하면 설계가 자기모순이다.

`update_campaign` 의 명시 파라미터: `name`, `detail`, `type`,
`service_level`, `end_handle` (= `paths/campaigns/id.yaml:48-65` 전체).
`actions` 는 파라미터에서 **제외**하고 docstring 에서도 제거한다. 명시
파라미터로 바꾸기만 하고 `actions` 를 파라미터로 남기면 타입 주석이 붙은 채
같은 버그를 배포하게 된다.

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
  → **유효 값**을 전체 열거한다. 스펙 enum 이 아니라 **서버가 실제로 받아
  저장하는 값**이다(D-M).
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
| `pyproject.toml` | **`mcp>=1.2.0,<2`**, 버전 `0.2.0`, ruff dev 의존성 + `[tool.ruff]` (`select = ["E4","E7","E9","F"]`, D-L), Python 3.13 분류자 | A-1.1, A-5.18, A-5.19, **D-A**, D-J, D-L |
| `uv.lock` | **재생성** (`mcp` specifier 변경으로 무효화됨: `uv.lock:900` 의 `specifier = ">=1.0.0"`, `:322-323` 의 1.27.0). 재생성하지 않으면 `test` 잡이 relock 하며 트리가 dirty 해진다 | A-1.1, **D-A** |
| `.github/workflows/ci.yml` | `dist-smoke-latest` + `dist-smoke-floor` 잡 신설(런타임 기동 + 하한 pytest 포함, 하한 잡은 **Python 3.10·3.13** 두 개, D-P), Python 3.13, ruff 게이트(`--select E4,E7,E9,F`) | A-1.2, A-5.18, D-B, D-L, D-P |
| `.github/workflows/dist-smoke.yml` | **신설** (`workflow_call` 재사용 워크플로, D-I) | A-1.2, A-1.3 |
| `.github/workflows/publish.yml` | 재사용 스모크 호출, build-once-then-publish, `environment:` 게이트, 태그↔버전 검사, 액션 SHA 핀 | A-1.3, D-I |
| `src/voipbin_mcp/client.py` | envelope 파싱 + `reason`/`request_id` 구조화 속성(`VoIPbinAPIError.__init__` 를 `(status_code, message, reason=None, request_id=None)` 로 확장, 기존 2-인자 호출 호환), **error_map 전면 재구성**, 429 + **`Retry-After` 부재 허용**, 원문 폴백 200자 절단, `VOIPBIN_API_BASE_URL`, 요청별 `Cookie` 헤더 + `VOIPBIN_AUTH_TRANSPORT` 스위치(**미인식 값은 즉시 실패**, D-P), **`import json` 섀도잉 정리**(`:3,:75,:85`) | A-2.6, A-2.7, A-5.15, D-C, D-D, D-H, D-L, D-P |
| `src/voipbin_mcp/tools/contacts.py` | `addresses`/`tag_ids`, `source`/`external_id` 추가, **`update_contact` free-form dict → `None` sentinel 명시 파라미터 7개**(D-N), 하위 리소스 5툴(바디는 D-E 표), `addresses[].type` 을 **`tel`\|`email` 로 한정**, **`target_name` 완전 제거**(D-M1: 201 후 소실), 구 `phone_numbers`/`emails`/`fields` 를 **받아서 거부**(D-O) | A-2.4, A-2.5, D-E, D-M, D-N, D-O |
| `src/voipbin_mcp/tools/campaigns.py` | enum 4값, 신규 4필드는 **`str\|None=None` 으로 노출** + **생략 시 캠페인이 생성되나 발신하지 않음을 docstring 에 명시**(D-O1), **`update_campaign` free-form dict → `None` sentinel 명시 파라미터 5개(`actions` 제외, `service_level` 은 `int\|None`)**(D-N), **`update_campaign_actions` 툴 신설**(D-E2), 구 `fields` 받아서 거부(D-O), 모듈 요약문 | A-3.8, A-4.11, D-E, D-E2, D-G, D-N, D-O |
| `src/voipbin_mcp/tools/ais.py` | `stt_type` 은 핸들러 기준 **4값**(cartesia, deepgram, elevenlabs, google — `ValidValues()` 가 `""` 를 제외한다), `engine_model` 은 값 목록이 아니라 **`<provider>.<model>` 형식 + provider 목록**(D-M3, 감사 A-3.8 의 `anthropic.*` 무효 판정 철회), write-only 서술, `parameter` 를 **`dict\|None=None` 으로 신규 노출**(D-O2) | A-3.8(개정), A-3.9, A-4.12, **D-G**, D-M3 |
| `src/voipbin_mcp/tools/flows.py` | action type 2값, uuid 예시. `update_flow` 는 required 전체 교체로 이미 정직하므로 변경 없음(D-N sweep) | A-3.8, A-3.10, **D-G** |
| `src/voipbin_mcp/tools/calls.py` | 주소 타입을 **`tel`\|`sip`\|`agent`\|`extension`** 로 열거(D-M4, 감사 A-3.10 의 "전체" 대체), source_type 은 미검증임을 명시 | A-3.10(개정), **D-G**, D-M4 |
| `src/voipbin_mcp/tools/emails.py` | `attachments` 를 **`list[dict]\|None=None` 으로 신규 노출**(D-O2) | A-4.13, D-G, D-O2 |
| `src/voipbin_mcp/tools/conferences.py` | required 전체, `type` 하드코딩 해제 → **`conference`\|`connect`**(기본값 `"conference"` 보존, D-O2), `queue` 는 `connect` 로 정규화됨 + 서버 검증 없음을 명시(D-M5), **`timeout` 을 3600000ms → 3600s 로 정정**(D-M6) | A-4.13, D-M5, D-M6, D-O2 |
| `src/voipbin_mcp/tools/routes.py` | **"accesskey 로 사용 불가" 명시** (superadmin 전용 아님, §5.4b) | A-5.14, D-F |
| 모든 list 툴 | page_size 1–100 명시 | A-3.10, **D-G** |
| `tests/test_client.py` | **기존 픽스처 교정**: envelope 중첩 형태로 교체(`:62,71,91`), **accesskey URL 단언 재작성**(`:45,:56` → Cookie 헤더 존재 + URL 에 accesskey **부재**), **POST/PUT/DELETE 테스트(`:80,:100,:111`)에도 인증 단언 추가**(공유 헬퍼 `client.py:79,89,99`), base URL 기본값 단언(`:21-22`) 유지 + 환경변수 오버라이드 추가, 429(`Retry-After` 유/무 both) 테스트, `import os` 미사용 정리(`:1`) | A-2.6, A-5.15, A-5.16, D-D, D-L |
| `tests/test_tools_contacts.py` 외 4개 | 신설 | A-5.16 |
| `tests/data/openapi_enums.json` | **신설** (스펙에서 생성한 enum 스냅샷, §8-4) | A-5.16 |
| `scripts/regen_openapi_enums.py` | **신설** (스냅샷 재생성 스크립트, §8-4) | A-5.16 |
| `README.md` | 예시(새 파라미터 형태로), **격리 설치(uvx/pipx) 우선 안내 + 공유 venv 다운그레이드 경고**, Security Note, `VOIPBIN_API_BASE_URL`/`VOIPBIN_AUTH_TRANSPORT`, 지원 범위, **툴 목록 표(`README.md:69-88`)에 신규 6개 행 추가**, **"알려진 제약" 섹션 신설**(§3 stopping rule·D-M1 이 여기에 기재하라고 지시하나 v5 까지 둘 곳이 없었다: `target_name`, `on_complete_flow_id`, `conferences.type` 미검증, 캠페인 4필드 생략 시 미발신) | A-5.17, D-K, D-M1, D-O1 |
| `CHANGELOG.md` | **신설** (0.2.0 파괴적 변경 + 마이그레이션 before/after 코드 + 0.3.0 에서 구 파라미터 제거 예정) | D-J, D-O |
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
   - `list_tools()` 개수 == 58 (52 기존 + contacts 하위 5 + `update_campaign_actions` 1)

     **개수는 한 곳에서만 권위를 갖는다.** 설계 문서와 CI 양쪽에 숫자를 박으면
     어긋난다(설계가 mcp 하한에 대해 지적한 것과 같은 문제). CI 는 리터럴을
     쓰지 않고 `grep -c '@mcp.tool()' src/voipbin_mcp/tools/*.py` 로 기대값을
     파생해 `list_tools()` 결과와 비교한다. 즉 검사는 "소스에 선언된 툴이
     빠짐없이 등록되는가" 이며, 숫자 자체는 아무 곳에도 하드코딩되지 않는다.
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
   커밋하고, `scripts/regen_openapi_enums.py` 로 재생성한다. 테스트는 docstring
   값이 스냅샷의 부분집합인지 본다.

   **트리거를 넣으려 했으나 철회한다 (리뷰 라운드 4 제안 → 라운드 6 반박,
   라운드 6 채택).** v3 는 주간 `schedule:` 잡을 추가했다. 라운드 6 이 세 가지
   이유로 반박했고, 확인 결과 전부 맞다.

   - **필요한 순간에 스스로 꺼진다.** `voipbin/mcp` 는 public 이고 GitHub 는
     저장소 비활동 60일 후 `schedule:` 워크플로를 자동 비활성화한다. 이
     저장소의 이력이 바로 반례다: 마지막 푸시 2026-04-06, 다음 활동
     2026-09-28 (약 6개월). 이 PR 에 주간 잡을 넣었더라도 그 기간 중 넉 달은
     **아무 신호 없이 죽어 있었다.** 드리프트가 생기는 조용한 기간에 정확히
     실패하는 메커니즘이다.
   - **리스크 등급과 모순된다.** §9 는 enum 드리프트를 `낮음` 으로 둔다.
     낮은 리스크에 상시 워크플로를 새로 세우는 것은 오버엔지니어링이다.
   - **구현 불가다.** 스크립트가 스펙을 어디서 읽는지 정해지지 않았다. 로컬
     monorepo 체크아웃 경로는 스케줄 런에서 존재하지 않는다.

   → **`.github/workflows/enum-drift.yml` 을 A 에서 뺀다.** 유지하는 것은
   `tests/data/openapi_enums.json` + `scripts/regen_openapi_enums.py` +
   부분집합 테스트뿐이며, 이들은 **기존 PR CI 안에서 돌아가고 새 상시 체계를
   만들지 않는다.**

   **정직한 한계 서술:** 이 구성은 **docstring ↔ 스냅샷 divergence 만** 잡는다.
   진짜 스펙 드리프트 감지는 monorepo 쪽 훅이 필요하며 **B 로 이연**한다.
   B 의 근거가 될 실측 트리거를 기록해 둔다: 지난 12개월간
   `bin-openapi-manager/openapi/openapi.yaml` 의 `enum:` 변경 커밋 **48건**.
   드리프트 발생 가능성은 사실상 확실하고, 영향은 서버 400 이므로 낮다.

   스냅샷 생성 스크립트의 스펙 입력은 **`voipbin/monorepo` raw URL 을 ref 로
   핀해서** 읽는다(인증 없이 200). 로컬 경로가 있으면 그것을 우선한다. 기대
   enum 집합은 **툴 → 스펙 스키마명 매핑 표**를 스크립트가 들고 있으며,
   docstring 산문을 파싱하지 않는다. D-G 가 docstring 형식을 의도적으로
   불균일하게 두므로 산문 파싱은 불가능하다. 검사 대상은 **닫힌 소집합 enum**
   (campaign type/end_handle, address type, stt/tts/engine_model)로 한정한다.
5. **뮤테이션 확인**: 새 테스트가 tautology 가 아닌지, 고친 코드를 일부러
   되돌려 실패하는지 확인한다.
6. 라이브 스모크: 실계정으로 **GET/POST/PUT/DELETE 전부** 최소 1건씩 호출해
   쿠키 전송과 에러 형식을 확인한다. 인증 주입이 공유 헬퍼이므로 GET 만으로는
   부족하다. `VOIPBIN_AUTH_TRANSPORT=query` 폴백도 함께 확인한다.

## 9. 롤아웃 / 리스크

| 리스크 | 정도 | 대응 |
|---|---|---|
| **0.2.0 의 파괴적 툴 계약 변경이 기존 사용자를 깨뜨림** | **높음** | mcp 1.x 가 이미 깔린 환경의 0.1.1 사용자는 **지금 정상 동작 중**이다(PyPI 파손은 새로 해석하는 설치에만 발생). 그들에게 `create_contact(phone_numbers=...)` → `addresses=...`, `update_contact(fields=...)` → 명시 kwargs 는 저장된 에이전트 설정·프롬프트 템플릿을 깨뜨린다. → **능동 마이그레이션(D-O)**: 구 파라미터를 한 마이너 버전 동안 **받아서 거부**하고 지시적 에러를 돌려준다. 이것이 없으면 pydantic 이 구 인자를 조용히 버려 **빈 업데이트가 200 으로 성공**한다(실측). `create_campaign` 신규 4필드는 `None` 기본값으로 노출해 hard-fail 을 0건으로 만든다(D-O1). CHANGELOG·README 는 보조 수단이다. |
| 쿠키 전송이 일부 프록시에서 막힘 | **중간** (셀프호스팅 ingress 는 미검증) | 관리형 엔드포인트 실측 200 확인. `VOIPBIN_AUTH_TRANSPORT=query` 스위치를 **같은 릴리스에** 넣어 우회 경로를 제공(D-D). |
| `mcp<2` 상한이 공유 venv 의 mcp 2.x 를 **다운그레이드** | **중간** | 단순 배제가 아니라 다른 MCP 서버를 깨뜨릴 수 있다. README 가 격리 설치(uvx/pipx)를 우선 안내하고 공유 venv 경고를 명시한다(D-K). |
| **공식 문서가 서드파티 fork 를 안내 중** | **중간** | `bin-api-manager/docsdev/source/ai_overview.rst:685` 가 `https://github.com/nrjchnd/voipbin-mcp` 를 가리킨다. 공식 저장소의 0.2.0 을 내면서 문서는 남의 fork 를 권하는 상태는 일관되지 않다. **본 PR 범위 밖(다른 저장소)이므로 후속 필수 항목으로 등록한다.** |
| enum 드리프트 재발 | **발생 가능성 높음 / 영향 낮음** (지난 12개월 `enum:` 변경 48커밋, 증상은 서버 400) | A 는 `tests/data/openapi_enums.json` 부분집합 테스트로 docstring↔스냅샷 divergence 만 잡는다. 진짜 스펙 드리프트 감지는 monorepo 훅이 필요하며 **B 로 이연**(§8-4). 상시 스케줄 잡은 60일 비활동 시 자동 비활성화되어 정작 필요한 기간에 죽으므로 채택하지 않는다. |
| **라이브 스모크가 운영 계정에 테스트 리소스를 남김** | 중간 | §8-6 이 실계정 POST/PUT/DELETE 를 요구한다. 생성한 리소스는 **같은 스모크 스크립트가 DELETE 로 회수**하고, 이름에 `mcp-smoke-` 접두사를 붙여 식별 가능하게 한다. 회수 실패 시 스모크를 실패로 처리한다. |
| 버전 bump 후 릴리스가 또 깨짐 | 낮음 | D-I 의 publish 게이트 + D-B 의 양 끝 스모크(런타임 기동·하한 pytest 포함). 실패 시 D-J 의 yank 절차. |
| `validate_page_size` 의 조용한 값 보정 유지 | 수용 | `page_size=0` → 1, 비정수 → 10 으로 조용히 바뀐다. 데이터 유실과 달리 결과가 왜곡되지 않고 도구 재호출로 복구되므로 A 에서는 docstring 명시만 한다. **의도적 수용이며 누락이 아니다.** |
| `create_contact` 의 `addresses` 가 `list[dict]` 로 남음 | 수용 | 원소 스키마 강제는 중첩 모델이 필요하고 mcp 버전 간 처리가 다르다(§5.1). docstring 열거로 완화, 강제는 B. |

(v2 의 "contacts 하위 툴 5개 추가가 A 범위를 넘는다는 지적 | 해소됨" 행은
삭제했다. 리스크가 아니라 리뷰 처리 결과이며 표를 부풀렸다. 근거는 §3 에 있다.)

### D-K. README 설치 안내 순서

`pip install voipbin-mcp` (`README.md:11-13`) 를 첫 안내로 두면, mcp 2.x 가 있는
공유 venv 사용자는 `mcp<2` 상한 때문에 **mcp 가 1.x 로 다운그레이드되어 다른
모든 MCP 서버가 깨진다.** `uvx` (`README.md:18`) 는 격리 실행이라 안전하다.

→ README 는 `uvx`/`pipx` 격리 설치를 **먼저** 안내하고, `pip install` 은 전용
venv 를 전제로 한 대안으로 배치한다. 공유 환경 다운그레이드 경고를 명시한다.

### D-L. ruff 규칙 집합 확정 (`--select E4,E7,E9,F`)

v2 는 "ruff 게이트"만 적어 규칙 집합을 정하지 않았다. 리뷰 라운드 3 이 실측한
대로, 이는 게이트가 머지 시점에 빨간불이거나 §7 이 부인한 파일들을 건드리게
만든다. 직접 확인했다(ruff 0.16.9, 현재 트리):

```
$ ruff check .                            → 26 errors
$ ruff check --select E4,E7,E9,F .        → 4 errors (2 files)
```

기본 규칙셋은 `I001`(import 정렬)을 `tools/{activeflows,agents,billings,
conversations,customer,extensions,messages,numbers,queues,tags}.py` 등 **§7 이
"변경 없음"으로 선언한 모듈 전부**에서 발생시키고, `server.py` 에도
`RUF100` 을 낸다.

(v3 는 52건/8건으로 적었는데, 그것은 **base 저장소** 측정치였다. 이 worktree
기준으로는 26건/4건이다. 결론은 같지만 구현자가 재현할 수 없는 숫자였다.)

→ **`--select E4,E7,E9,F` 로 고정한다.** 이 집합의 위반은 전부 §7 이 이미
변경 대상으로 올린 파일 안에 있다:

```
src/voipbin_mcp/client.py:3:8   F401  `json` imported but unused
src/voipbin_mcp/client.py:75:37 F811  Redefinition of unused `json`
src/voipbin_mcp/client.py:85:36 F811  Redefinition of unused `json`
tests/test_client.py:1:8        F401  `os` imported but unused
```

`client.py` 의 `json` 은 **실제 결함**이다. 모듈 수준 `import json` 이
메서드 파라미터 `json` 에 가려져 있다(`:75`, `:85`). D-C 가 어차피 이 파일의
에러 경로를 재작성하므로 함께 정리한다.

스타일 규칙(`I001` 등)을 넣으려면 전 모듈 포매팅이 따라와야 하므로, 그것은
별도 작업으로 분리한다. A 는 **실제 버그를 잡는 규칙만** 켠다.

### D-M. 유효 값의 권위는 스펙이 아니라 핸들러다

**이 결함 클래스는 여덟 라운드에 걸쳐 다섯 번 나왔고, 그 중 세 번은 클래스를
없애려고 쓴 섹션이 재도입했다**(R3 → D-E 의 `target_name`, R5 → D-M 의
`addresses[].type`, R7 → D-M 의 `target_name` 재발). 원인은 매번 같다.
**스펙을 권위로 삼은 것이다.**

**일반 규칙 (다섯 건이 모두 위반한 불변식):**

> docstring 은 **게이트웨이가 읽고, RPC 구조체가 나르고, 핸들러가 저장하는**
> 값만 광고한다. 네 계층 중 하나라도 끊기면 스펙에 있어도 유효하지 않다.
> 판정은 **스펙이 아니라 Go 소스**로 한다.

**검증 방법을 산문이 아니라 체크리스트로 고정한다** (라운드 7 제안 채택).
산문 규칙은 세 번 실패했으므로, 구현자는 쓰기 필드마다 다음 네 칸을 Go
소스에서 직접 채우고 **하나라도 ✗ 면 docstring 에서 뺀다.**

| 필드 | 스펙에 있나 | 게이트웨이가 읽나 | RPC 가 나르나 | 핸들러가 저장하나 | 단위·형식이 문서와 맞나 |
|---|---|---|---|---|---|

#### D-M1. 다섯 번째 인스턴스: `create_contact.addresses[].target_name`

**BLOCKER (라운드 7).** 201 이 떨어지고 값은 사라진다. 직접 확인했다.

| 계층 | 위치 | 결과 |
|---|---|---|
| 스펙 | `openapi.yaml:3659` | `CommonAddress.target_name` 존재 |
| 생성 바디 | `gen.go:9000-9001` | `TargetName *string` 바인딩됨 |
| 게이트웨이 | `server/contacts.go:151-164` | `Type/Target/IsPrimary/Name/Detail` 만 복사. **`TargetName` 을 읽지 않음** |
| RPC 구조체 | `bin-contact-manager/pkg/listenhandler/models/request/contacts.go:35-41` | `AddressCreate` 에 **`TargetName` 필드 자체가 없음** |
| 응답 | `server/contacts.go:195` | `c.JSON(201, res)` |

v4 의 D-M 은 이것을 "`CommonAddress` 를 참조하므로 거기서는 legal" 이라고
**광고하라고 지시**하고 있었다. 스펙 기준의 판정이며, D-M 자신의 불변식이
금지하는 바다. → **`target_name` 을 `create_contact` docstring 에서 완전히
제거**하고 README 알려진 제약에 기재한다. 비대칭 서술도 뒤집는다: 양쪽 표면
모두에서 버려지며, 하위 리소스는 400, `create_contact` 는 **201 후 소실**로
방식만 다르다.

#### D-M2. v4 의 근거가 틀렸다: drop 이 아니라 400 이다

v4 는 `addresses[].type` 이 `contacthandler/contact.go:88-93` 의 `continue`
때문에 조용히 버려진다고 썼다. **MCP 경로에서는 거짓이다.** 게이트웨이가 먼저
막는다(`server/contacts.go:140-145`):

```go
if addrType != "tel" && addrType != "email" {
    abortWithError(c, cerrors.InvalidArgument(..., "INVALID_ADDRESS_TYPE",
        "Address type must be 'tel' or 'email'."))
    return
}
```

`{"type":"sip"}` 는 **400** 이고, 도메인 서비스의 `continue` 는 이 클라이언트
에서 도달 불가다. 처방(`tel`|`email` 한정)은 그대로 옳지만 근거가 틀렸고,
§3 stopping rule 조건 1("에러가 나는 키는 이미 정직하므로 해당 없음")에
비추면 **이 건은 애초에 규칙 대상이 아니었다.** 근거를 정정한다.

#### D-M3. 반대 방향도 존재한다: 유효 ⊋ 스펙

v4 의 D-M 은 스펙 ⊋ 유효 한 방향만 다뤘다. 반대도 있고, §8-4 의 부분집합
테스트가 **틀린 답을 강제**한다.

- **`stt_type`**: 스펙 enum 은 4값(`openapi.yaml:2955-2962`: `""`, cartesia,
  deepgram, elevenlabs)인데 핸들러는 **`google` 을 추가로 받는다**
  (`bin-ai-manager/models/ai/main.go:310-320` `validSTTTypes`,
  `aihandler/chatbot.go:60` 에서 실제 호출). D-M 규칙상 `google` 은 광고
  대상인데 부분집합 테스트는 이를 탈락시킨다. → **스냅샷 집합은 스펙이 아니라
  핸들러의 `validSTTTypes` 에서 뽑는다.**
- **`engine_model`**: 검증이 **prefix 기반**이다
  (`ai/main.go:205-218` `IsValidEngineModel` 는 `.` 앞부분이 19개
  `EngineModelTargets` 중 하나면 통과, `chatbot.go:41,119` 에서 호출). 따라서
  `ais.py:51` 의 `anthropic.claude-3-5-sonnet` 은 **유효하다**(`anthropic` 은
  target 목록에 있음). 스펙의 11값 enum 은 권위가 아니다.
  **감사 §A-3.8 이 이 값을 무효로 판정한 것은 틀렸으며 여기서 철회한다.**
  → `engine_model` 은 **§8-4 스냅샷 대상에서 제외**하고, docstring 은 값
  목록이 아니라 **`<provider>.<model>` 형식과 provider 목록**을 설명한다.

#### D-M4. `create_call` 주소 타입 (라운드 8, open question 4 종결)

`calls.py:51,53` 은 source/destination 양쪽에 `tel, sip, agent` 를 광고한다.
실제 dispatch(`bin-call-manager/pkg/callhandler/outgoing_call.go:73-96`):
`tel`/`sip` → 직접 발신, `IsGroupcallTypeAddress` → groupcall, 그 외 →
`default:` 에서 에러. groupcall 타입은 `agent`, `extension`
(`groupcallhandler/start.go:244-252`).

→ **유효 집합은 `tel` | `sip` | `agent` | `extension`** 이다. `extension` 이
지원되는데 문서에 없다. **감사 A-3.10 의 "주소 타입 전체" 는 이 네 값으로
대체한다**(전체 9값을 쓰면 D-M 위반). source_type 은 어느 계층에서도 검증되지
않으므로(정규화·변수 노출만) docstring 에 "검증되지 않음" 을 명시한다.

이로써 §10 의 open question 4 는 종결된다.

#### D-M5. `conferences.py` 의 `type` 하드코딩 해제

§7 이 하드코딩 해제를 지시하면서 값 집합을 정하지 않았다. `conference.Type`
은 **어느 계층에서도 검증되지 않는다**(`IsValidConferenceType` 는 vendor
사본에만 존재하고 실제 호출 0건). 임의 문자열이 저장된다. 그리고
`conferencehandler/conference.go:70-73` 은 `conference` 가 아닌 모든 값을
`TypeConnect` confbridge 로 매핑한다.

→ 저장은 되므로 D-M 불변식을 통과하지만, **`queue` 는 저장만 될 뿐 동작은
`connect` 와 구별되지 않는다.** docstring 에는 `conference` | `connect` 를
쓰고, `queue` 는 **정규화되어 `connect` 와 동일하게 동작함**을 명시한다.
검증이 없다는 사실도 적는다.

#### D-M6. 체크리스트가 못 잡는 종류: 단위 오류 (`create_conference.timeout`)

**라운드 9 가 4계층 체크리스트를 전수 적용해 여섯 번째 silent-drop 은 없음을
확인했다**(쓰기 툴 17개 전 필드 추적). 대신 **다른 종류의 결함**을 찾았다.
값이 정상 저장되므로 체크리스트 네 칸이 모두 ✓ 인데도 틀린 경우다.

`conferences.py:38,47` 은 `timeout: int = 3600000` 을 "**milliseconds**
(default 1 hour)" 로 문서화한다. 서버 단위는 **초**다:

- `paths/conferences/id.yaml:62-67`: "Auto-termination timeout in **seconds**."
- `conferencehandler/conference.go:83-85`: `if timeout > 0 && timeout < 60 {
  timeout = defaultConferenceTimeout }` (`:24` = `86400`). 60 미만을 이상치로
  보는 것 자체가 초 단위라는 증거다.
- `conference.go:143-144`:
  `ConferenceV1ConferenceDeleteDelay(ctx, id, res.Timeout*1000)` — **저장값에
  1000 을 곱해 ms 로 바꾼다.** 저장 단위가 초임을 확정한다.

즉 현재 기본값은 1시간이 아니라 **약 41.6일** 후 자동 종료다.

→ `timeout: int = 3600` 으로 고치고 docstring 을 "**seconds**" 로 바꾼다.
60 미만은 서버가 86400 으로 대체한다는 점도 적는다.

**체크리스트에 다섯째 칸을 더한다: *단위·형식이 문서와 일치하나.***
"저장되는가" 만 보면 단위 오류를 놓친다.

### D-N. 부분 업데이트 sentinel: `None` 기본값 + 비-None 만 전송

**리뷰 라운드 6 이 찾은 BLOCKER다. v3 는 "명시 파라미터로 바꾼다" 고만 쓰고
값이 없을 때의 표현을 정하지 않았다.** 그 결과 합리적인 구현자 둘이
**데이터를 파괴하는 쪽과 그렇지 않은 쪽으로 갈린다.**

서버는 PATCH 형 의미를 갖는다. `PutContactsIdJSONBody` 는 전 필드가
`*string ... omitempty` 이고(생성 코드), `bin-contact-manager/pkg/
listenhandler/v1_contacts.go:205-225` 는 **nil 이 아닌 포인터만** 업데이트
맵에 넣는다. 즉 **키가 `""` 로 존재하면 빈 문자열을 쓰는 실제 업데이트**다.

```go
fields := make(map[contact.Field]any)
if reqData.FirstName != nil { fields[contact.FieldFirstName] = *reqData.FirstName }
...
```

구현자 갈림:
- **A**: 저장소 내 선례인 `create_contact`(`contacts.py:63-79`, `if first_name:`)
  를 따라 `str = ""` + falsy-omit. 결과는 대체로 맞지만 **필드를 비울 수
  없다.**
- **B**: 선언된 파라미터 전부로 바디를 만든다. `update_contact(id,
  first_name="Kim")` 이 나머지 6개를 `""` 로 보내 **company/job_title/notes/
  external_id/last_name/display_name 를 지운다.** 이 PR 이 없애려는 조용한
  유실보다 **더 나쁘다.**

`update_campaign` 은 더 위험하다. `service_level: int` 는 **`0` 이 유효
값**이므로(`create_campaign` 의 기본값, `campaigns.py:43`) falsy-omit 은
`service_level=0` 을 설정 불가로 만들고, `int = 0` + 항상 전송은 이름만
바꿀 때마다 service level 을 0 으로 되돌린다.

**확정 규칙 (두 툴 및 향후 모든 부분 업데이트 툴에 동일 적용):**

> 모든 선택 파라미터의 기본값은 `None` 이며 타입은 `str | None` /
> `int | None` 이다. 요청 바디에는 **`None` 이 아닌 파라미터만** 포함한다.
> 빈 문자열 `""` 은 유효한 값(필드 비우기)으로 그대로 전달한다.
> `create_contact` 의 기존 falsy-omit 패턴도 이 규칙으로 통일한다.

`update_flow`(`flows.py:66`)는 `name`, `detail`, `actions` 를 **required 로
선언하고 전체 교체임을 docstring 에 명시**하고 있으므로 이 규칙의 대상이
아니다. 세 필드 모두 스펙에 존재한다(`paths/flows/id.yaml:49-61`).

### D-O. 0.2.0 능동 마이그레이션: 구 파라미터를 받아서 거부한다

**리뷰 라운드 6 지적을 수용한다.** v3 의 완화책(CHANGELOG, minor bump,
README)은 전부 **수동적**이다. 사용자가 무언가를 읽어야 작동한다. 그런데
위험 집단으로 특정한 사람들은 **지금 정상 동작 중이라 아무것도 읽지 않고
자동 업그레이드되는** 집단이다. 그들의 첫 증상은 `fields=` / `phone_numbers=`
에 대한 불투명한 MCP 스키마 검증 실패다.

→ `phone_numbers`, `emails`(`create_contact`), `fields`(`update_contact`,
`update_campaign`) 를 **한 마이너 버전 동안 파라미터로 계속 받되, 값이 오면
서버에 보내지 않고 지시적 에러를 던진다.**

**v4 의 근거는 정반대였다 (라운드 8 실측).** v4 는 구 파라미터의 첫 증상이
"불투명한 MCP 스키마 검증 실패" 라고 썼다. 직접 측정한 결과
**검증 에러는 아예 발생하지 않는다.** pydantic 이 모르는 인자를 조용히
버린다:

```
LEGACY-ARG   -> 성공. 서버로 간 바디: {"contact_id": "c1", "first_name": null}
MISSING-REQ  -> ToolError: 2 validation errors ... outplan_id Field required
```

(mcp 1.2.0 과 1.30.0 양쪽 동일.)

즉 D-O 가 없으면 `update_contact(contact_id=..., fields={...})` 는
**빈 업데이트를 보내고 200 을 받는다.** 사용자는 수정됐다고 믿는다. 이것은
**감사 §4-1 이 없애려는 결함(성공 응답 뒤의 조용한 유실)과 정확히 같은
클래스**다. D-O 는 장식이 아니라 **0.2.0 이 같은 버그를 재생산하지 않게
막는 부품**이다.

```
phone_numbers was removed in 0.2.0. Use addresses=[{"type": "tel",
"target": "+1...", "is_primary": true}] instead. See CHANGELOG.
```

근거:
- §3 의 원칙과 일관된다. **명시적 에러도 수단**이며, 조용한 실패보다 낫다.
- LLM 이 행동 가능한 형태다. 스키마 검증 실패는 LLM 이 고칠 방법을 모르지만,
  이 메시지는 다음 호출을 정확히 지시한다.
- 조용한 유실 경로를 되살리지 않는다. **아무것도 서버로 전달되지 않는다.**
- 6줄 수준이고 새 인프라가 없다.

0.3.0 에서 이 파라미터들을 완전히 제거한다. CHANGELOG 에 제거 예정을 명시한다.

#### D-O1. `create_campaign` 의 신규 required 4개

D-J 의 파괴적 변경 목록은 위 3개만 들었는데, **누락이 있다.** A-4.11 이
`create_campaign` 에 `outplan_id`, `outdial_id`, `queue_id`,
`next_campaign_id` 를 required 로 추가한다. 위 실측이 보여주듯 구 파라미터와
달리 **이것은 기존 호출자를 즉시 깨뜨린다**(`ToolError: Field required`).
같은 위험이 새로 노출되는 다른 required 필드에도 있다(D-O2).
그리고 D-O 방식으로는 덮을 수 없다. 없는 필드를 "받아서 거부" 할 수 없다.

→ **`str | None = None` 으로 노출하고 `None` 이면 바디에서 생략한다**(D-N 과
동일 규칙). 클라이언트에서 required 로 못박으면 이득 없이 모든 기존 호출을
깨뜨린다.

**v5 의 근거는 틀렸다 (라운드 9).** v5 는 "서버가 `isValidOutplanID` 계열로
직접 검증해 진짜 에러를 돌려주므로 서버가 집행한다" 고 썼다. **서버는
집행하지 않는다.** 네 검증자 전부 nil UUID 에서 단락한다:

```go
// bin-campaign-manager/pkg/campaignhandler/campaign.go:567
if outdialID == uuid.Nil {
    // no outdial id has given. nothing to verify.
    return true
}
```
`:606`(outplan), `:639`(queue), `:672`(next_campaign) 동일.

게이트웨이는 `PostCampaignsJSONBody` 의 **비포인터 `string`**
(`gen.go:8626-8635`)에 `uuid.FromStringOrNil` 을 적용하므로, 생략 → `""` →
`uuid.Nil` → **전 검증 건너뜀**이다. 결과는 outplan·outdial·queue 가 없는
캠페인이 **201 로 생성되고 영원히 발신하지 않는 것**이다. 성공 응답 뒤의
조용히 잘못 구성된 리소스이며, §1 과 D-O 가 없애려는 바로 그 클래스다.

처방은 그대로 유지한다(hard-fail 논거는 여전히 유효). 대신 **docstring 이
사실을 말해야 한다**: 이 네 필드를 생략하면 캠페인이 생성되지만 **실행되지
않으며, 서버는 경고하지 않는다.** `outplan_id` 와 `outdial_id` 는 발신에
필수이므로 docstring 에서 "사실상 필수" 로 강조한다.

이 결정으로 0.2.0 의 hard-fail 파괴적 변경은 **0건**이 된다. 단 §7 이 새로
노출하는 스펙 required 필드(`create_ai.parameter`, `send_email.attachments`,
`create_conference.type`)에도 **같은 규칙을 적용해야** 이 수치가 성립한다
(D-O2).

#### D-O2. 새로 노출하는 스펙 required 필드도 기본값을 준다

§7 은 `create_ai` 에 `parameter`, `send_email` 에 `attachments`,
`create_conference` 에 `type` 하드코딩 해제를 지시한다. 셋 다 **현재 툴
파라미터로 존재하지 않는다.** 구현자가 "스펙 required 니까 required 로
선언" 하면 세 툴에서 각각 `ToolError: Field required` 가 나고, D-O1 이
없애려는 실패를 세 번 더 만든다.

→ **새로 노출하는 필드는 전부 기본값을 주고 `None` 이면 생략한다.**
`parameter: dict | None = None`, `attachments: list[dict] | None = None`,
`type: str = "conference"`(현재 하드코딩 값을 기본값으로 보존).
스펙상 required 라는 사실은 docstring 에 적는다.

**구 형태 호출의 0.2.0 동작을 명시한다**(v3 는 이것을 정하지 않아 구현자
갈림 지점이었다): 구 파라미터를 **값과 함께** 넘기면 위 에러가 난다. 구
파라미터를 넘기지 않으면 아무 영향이 없다.

### D-P. 남은 구현자 갈림 지점 확정

리뷰 라운드 6 이 지적한 나머지 모호점을 여기서 닫는다.

- **`dist-smoke-floor` 의 Python 버전**: `3.10`(= `requires-python` 하한) 과
  `3.13` 두 개로 돌린다. 하한 의존성 × 하한 런타임 조합이 가장 깨지기 쉽고,
  3.13 은 A-5.18 이 CI 에 추가하는 최신 버전이다. 중간 버전은 `test` 잡의
  기존 매트릭스가 덮는다.
- **`VOIPBIN_AUTH_TRANSPORT` 미인식 값**: **즉시 실패**한다(기동 시 `ValueError`).
  조용히 쿠키로 폴백하면 사용자는 `VOIPBIN_AUTH_TRANSPORT=quiery` 오타를
  영원히 모른다. 허용 값은 `cookie`(기본), `query` 둘뿐이다.
- **`format_response` / `validate_page_size`**: 변경 없음. §7 에 행을 두지
  않는다.


## 10. Open questions (리뷰어 판단 요청)

1. D-A: shim 대신 상한 핀만 채택한 판단이 타당한가. 2.x 지원을 지금 여는 것이
   더 나은가.
2. D-F: `list_routes` 유지 판단이 타당한가. 100% 403 인 툴이 LLM 컨텍스트를
   차지하는 비용 대비 가치가 있는가(§5.4b 로 근거가 하나 줄었다).
3. D-J: `Development Status :: 4 - Beta` 유지 판단이 타당한가. 배포가 처음으로
   실제 동작하게 되는 릴리스이므로 Beta 주장이 정당해진다고 봤으나, 릴리스
   이력이 한 번 깨진 전례를 감안하면 Alpha 강등이 정직한 선택일 수도 있다.
(v4 의 질문 4 는 라운드 7·8 이 **다섯 번째 인스턴스를 실제로 찾아내** 해소됐다.
D-M1(`target_name`), D-M3(`stt_type`/`engine_model`), D-M4(`create_call`),
D-M5(`conferences`) 로 각각 처리했고, D-M 의 판정 기준을 산문에서 **4계층
체크리스트**로 바꿨다. v3 의 질문 4·5 는 해소됐다. 4 → §8-4 에서 스케줄 잡을 **철회**하고 한계를
정직하게 서술 + B 이연 근거(48커밋) 기록. 5 → §3 에 stopping rule 3조건을
명문화하고 "본 릴리스 신규 툴 6개로 확정, 이후 동종 결함은 B" 로 종료.)

(v1 의 질문 3·4 는 해소됐다. 3 → §8-4 에서 스펙 생성 스냅샷으로 해결. 4 →
감사 §9-A item 5 가 이미 명령한 항목이므로 스코프 위반이 아니다.)

## 11. 리뷰 이력

| 회차 | 판정 | 핵심 피드백 | 조치 |
|---|---|---|---|
| 1 | CHANGES_REQUESTED | BLOCKER: `mcp>=1.0.0,<2` 가 버그를 못 고침 (fastmcp 는 1.2.0 부터). MAJOR: dist-smoke 가 범위 한쪽 끝만 검증 / accesskey superadmin 주장 거짓 / enum "교차 검증"이 교차 아님. MINOR 6건 | 하한 `>=1.2.0` 교정(§5.1b 실측), dist-smoke 2개 분리(D-B), §5.4b 신설 + D-F 재논증, §8-4 스펙 스냅샷 방식으로 교체, §7 표에 uv.lock/ruff/CHANGELOG/RELEASING 추가, server.py 행 삭제, 429 reason `RATE_LIMIT_EXCEEDED` 정정, `--help` 검사 폐기 |
| 2 | CHANGES_REQUESTED | 동일 BLOCKER 2건 독립 발견. MAJOR: free-form `fields` dict 로 조용한 유실 경로 잔존 / 쿠키 전용은 셀프호스팅 우회수단 없음 / base URL 계약 미정의 / 릴리스 게이트 서술 불충분 + environment 게이트 누락 / error_map 이 메시지 폐기 / mcp<2 가 공유 venv 다운그레이드 / 버전·롤백 미정 | D-E 에 명시 파라미터 전환 추가, D-D 에 `VOIPBIN_AUTH_TRANSPORT` 폴백 + 요청별 Cookie 헤더 고정, D-H 신설(§5.5 로 선례 실증), D-I 신설, D-C 에 error_map 재구성 명시, D-K 신설, D-J 신설(0.2.0 + yank 절차), §3 에서 contacts 예외 프레이밍 철회 |
| 3 | REQUEST_CHANGES | MAJOR: §5.1 의 "시그니처 동일" 주장이 새 하한 1.2.0 에서 거짓 / D-B 가 런타임 진입점·하한 pytest 를 여전히 미검증 / `update_campaign` 의 `actions` 가 동일 유실 결함인데 수단 없이 제거됨 / ruff 규칙셋 미지정으로 게이트가 §7 과 모순. MINOR: 429 사이트 3곳이며 셋째는 `Retry-After` 없음, 하한 CI 예시가 리터럴, 테스트 행이 POST/PUT/DELETE 인증 미포함 | §5.1 에 버전별 시그니처 실측표 + 교집합 불변식 명시, D-B 에 stdio 기동·하한 pytest 추가 + 파생 예시로 교체, **D-E2 신설**(`update_campaign_actions` 툴 추가), **D-L 신설**(`--select E4,E7,E9,F` 실측 확정), 429 3사이트 표 + 헤더 부재 허용 명시, §7 테스트 행 확장 |
| 4 | REQUEST_CHANGES | BLOCKER: `update_campaign` 의 `actions` 허위 키 미명명 / §3 원칙이 campaigns 에 미적용(자기모순) / 신규 5툴 바디 미명세로 `target_name`·`type` 오전송 유발. MAJOR: enum 스냅샷에 트리거가 없어 v1 비판과 구조 동일 / 리스크표에 0.2.0 파괴적 변경 행 없음. MINOR: 공식문서가 서드파티 fork 안내, 툴 개수 2곳 중복, README 툴표 누락, 에러 생성자 미정 | D-E2 에 결함 명명 + 파라미터 5개 열거, D-E 표에 3개 바디 스펙 실측 열 추가 + `list[dict]` 수용 명시, §8-4 에 **주간 스케줄 트리거** 추가, 리스크표에 0.2.0 행(높음)·fork 행 추가 + 리뷰처리 행 삭제, 툴 개수를 grep 파생으로 단일화, §7 에 README 툴표·에러 생성자 시그니처 명시 |
| 5 | REQUEST_CHANGES | BLOCKER: 동일 결함 클래스 **4번째** 인스턴스 — `create_contact.addresses[].type` 이 스펙 9값인데 서버는 3값만 받고 `continue` 로 버림, 그리고 D-E/D-G 가 9값 열거를 지시 / §7 이 D-A·D-G 미추적. MINOR: ruff 52건은 base 측정치(worktree 26건), campaigns id.yaml 인용 off-by-3 | **D-M 신설**(스펙 enum ≠ 유효 enum 일반 규칙 + `tel`\|`email` 한정 + `target_name` 비대칭), D-G 를 "유효 값 열거" 로 수정, §7 에 D-A·D-G 추적 추가, 측정치·인용 정정 |
| 6 | REQUEST_CHANGES | BLOCKER: 부분 업데이트 sentinel 미정 → 구현자 B 는 미지정 필드를 `""` 로 덮어써 **데이터 파괴**(`service_level=0` 문제 포함) / §3 원칙에 정지 규칙 없음 / 주간 스케줄 잡은 60일 비활동 시 자동 비활성화되어 무용 + 구현 불가. MAJOR: 0.2.0 완화책이 전부 수동적 | **D-N 신설**(`None` sentinel 확정), **§3 에 stopping rule 3조건 + 신규 툴 6개 확정** 명문화, §8-4 에서 **스케줄 잡 철회** + 한계 서술 + B 이연 근거 48커밋 기록, **D-O 신설**(구 파라미터 받아서 거부), **D-P 신설**(하한 잡 Python 3.10·3.13, `VOIPBIN_AUTH_TRANSPORT` 미인식 값 즉시 실패), 리스크표에 라이브 스모크 잔여물 행 추가 + enum 등급 정직화 |
| 7 | REQUEST_CHANGES | BLOCKER: **다섯 번째 인스턴스** — `create_contact.addresses[].target_name` 이 201 후 소실되는데 D-M 이 광고하라고 지시(클래스를 없애려는 섹션이 **세 번째로** 재도입). 또한 D-M 근거가 사실오류(`continue` 가 아니라 게이트웨이 400). MAJOR: D-M 이 한 방향만 다룸 — `stt_type` 은 핸들러가 `google` 을 추가 허용, `engine_model` 은 prefix 검증이라 스펙 enum 이 권위 아님 / conference `type` 에 결정 없음. MINOR: `create_call` 의 `extension` 미문서화 | **D-M 전면 재작성**: 판정 권위를 스펙 → Go 소스로 명시, 산문 규칙을 **4계층 체크리스트**로 교체, **D-M1**(`target_name` 제거) **D-M2**(근거 정정) **D-M3**(유효 ⊋ 스펙 방향 + 감사 A-3.8 `anthropic.*` 판정 철회) **D-M5**(conference `conference`\|`connect`) 신설, §7 에 D-A·D-G·D-M 계열 추적 추가 |
| 8 | REQUEST_CHANGES | BLOCKER: **D-O 근거가 정반대** — 구 인자는 검증 에러를 내지 않고 pydantic 이 조용히 버려 **빈 업데이트가 200 으로 성공**(실측). 즉 D-O 는 장식이 아니라 §4-1 결함 재생산을 막는 부품 / `create_campaign` 신규 required 4개가 파괴적 변경 목록에 없음 — **실제로 hard-fail 하는 유일한 변경** / open question 4 의 다섯 번째 인스턴스는 `calls.py` 이며 §7 이 값 집합 없이 편집을 지시. 판정: 나머지는 구현 가능, §3 stopping rule 은 실제로 구속력 있음(숫자 종료 조건) | D-O 에 **실측 결과로 근거 교체**, **D-O1 신설**(신규 4필드를 `str\|None=None` 로 노출 → hard-fail 0건), **D-M4 신설**(`tel`\|`sip`\|`agent`\|`extension`, 감사 A-3.10 "전체" 대체), open question 4 종결, 리스크표 0.2.0 행 갱신 |
| 9 | REQUEST_CHANGES | BLOCKER: **D-O1 근거가 사실오류** — `isValidOutplanID` 계열 4개가 전부 nil UUID 에서 `return true` 로 단락(`campaign.go:567,606,639,672`)하고 게이트웨이가 비포인터 `string` 에 `FromStringOrNil` 을 쓰므로, 생략 시 검증이 **전부 건너뛰어지고** 발신 불가 캠페인이 201 로 생성됨. MAJOR: `create_conference.timeout` 이 ms 로 문서화됐으나 서버는 초(`Timeout*1000` 이 증거) → 기본값이 1시간이 아니라 41.6일. MINOR: `stt_type` 은 4값(`ValidValues()` 가 `""` 제외), README 알려진제약 섹션 미제공, §3 sweep 주장 stale, D-E 인용 dangling. **여섯 번째 silent-drop 은 없음**(쓰기 툴 17개 전 필드 4계층 추적) | D-O1 근거를 Go 실측으로 교체 + docstring 이 "생략 시 미발신" 을 말하도록 규정, **D-M6 신설**(단위 오류 = 체크리스트 5번째 칸), §7 `stt_type` 4값 정정, README 알려진제약 섹션 신설, §3 sweep 주장 재범위화, dangling 인용 정리 |
| 10 | **APPROVE** | 라운드 8 요구 3건 전부 substance 충족 확인(pydantic 동작 독립 재현, `outgoing_call.go`/`groupcallhandler` 로 주소집합 확인). 스코프 A 는 여전히 올바르게 경계지어짐(라운드 7~10 은 표면을 **좁히기만** 했음). 오버엔지니어링 없음(스케줄 잡 철회, 잔존 항목 전부 실측 트리거 보유). **리뷰 churn 리스크가 미발견 결함 리스크를 상회**하므로 구현 착수 권고. 단 D-O1 의 "hard-fail 0건" 이 새로 노출되는 3개 required 필드에는 미적용 | **D-O2 신설**(신규 노출 required 필드도 기본값 + 생략) |
