# voipbin/mcp 스코프 A 설계: 배포 복구 및 계약 정합성 (2026-09-28)

Status: Draft (설계 리뷰 루프 대기)

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

1. `pip install voipbin-mcp` → `voipbin-mcp` 실행이 성공한다.
2. CI 가 배포되는 의존성 범위를 실제로 검증한다. `mcp` 상한을 넣는 것만으로는
   재발을 막지 못하므로, 회귀를 잡는 게이트를 함께 만든다.
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
  **A-2.5 의 contacts 하위 리소스 5개는 예외** — `update_contact` 의 허위
  docstring 을 정직하게 고치려면 주소/태그를 바꿀 실제 수단이 있어야 한다.
  수단 없이 "이 필드는 못 바꿉니다"만 남기는 것은 결함을 문서화할 뿐이다.
- `engine_key` 를 툴 파라미터로 계속 받을지 여부 (감사 §9-C). 본 PR 은
  docstring 의 **거짓 서술만** 고친다.
- 툴 개수 상한 정책 (감사 §9-C).
- `close()` 명시 호출, `Development Status` 분류자 (감사 §9-B-2).
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

## 6. 설계 결정

### D-A. `mcp` 의존성: 상한 핀 + shim 병행? → **상한 핀만**

두 안이 있다.

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| (a) | `mcp>=1.0.0,<2` 핀만 | 단순. 검증된 1.x 만 지원. | 2.x 사용자는 못 씀. 나중에 2.x 대응 필요. |
| (b) | 핀 + import shim | 1.x/2.x 양쪽 동작 (§5.1 실증) | 테스트에 `inputSchema`/`input_schema` 폴백 필요. 2.x 전 표면을 검증한 것은 아님. |

**(a) 를 채택한다.** 근거:

- §5.1 이 증명한 것은 "툴 등록과 list_tools 가 동작한다"까지다. stdio 런타임
  전체, 에러 전파, 타입 강제 등 2.x 표면을 검증한 것이 아니다. 지금 필요한
  것은 **깨진 배포를 되돌리는 것**이지 2.x 지원을 새로 여는 것이 아니다.
- shim 은 "동작하는 것처럼 보이지만 검증되지 않은 경로"를 만든다. 이번 사건의
  원인이 정확히 "검증되지 않은 의존성 범위를 배포한 것"이다. 같은 실수를
  형태만 바꿔 반복하게 된다.
- 2.x 지원은 별도 작업으로, 전체 테스트를 2.x 에서 돌려 확인한 뒤 상한을
  올리는 것이 정직하다. A-1.2 의 CI 잡이 그때 근거를 제공한다.

§5.1 의 shim 실험 결과는 문서에 남긴다. 2.x 대응 작업의 출발점이 된다.

### D-B. CI: lock 환경과 배포 범위를 **둘 다** 검증

현재 CI 는 `uv pip install -e ".[dev]"` → `uv run pytest` 인데, `uv run` 이
`uv.lock` 으로 조용히 재동기화해서 배포 범위를 한 번도 보지 않는다 (감사 §6-4).

두 잡으로 나눈다.

1. `test` (기존 유지): lock 환경에서 전체 테스트. 개발 재현성 보장.
2. `dist-smoke` (신설): **lock 을 쓰지 않고** wheel 을 빌드해 의존성 범위대로
   설치한 뒤 import + `list_tools()` + 콘솔 스크립트 존재를 확인.

`dist-smoke` 는 `uv run` 을 쓰지 않는다. 그게 문제의 원인이었다. 순수
`python -m venv` + `pip install dist/*.whl` 로 간다.

### D-C. 에러 처리: envelope 파싱 + request_id 노출

`client.py` 의 에러 경로를 다음으로 바꾼다.

- 바디에서 `error.message` 를 먼저 찾고, 없으면 최상위 `message`, 그것도
  없으면 원문 일부를 쓴다. (서버가 envelope 을 쓰지 않는 경로 대비)
- `error.request_id` 와 `error.reason` 을 예외 메시지에 포함한다. 지원 문의
  시 이 값이 유일한 추적 키다.
- 429 를 error_map 에 추가하고 `Retry-After` 헤더를 메시지에 담는다.
  **자동 재시도는 하지 않는다.** 재시도는 클라이언트 책임이라는 기존 설계
  원칙을 따르고, MCP 툴 호출 안에서 조용히 지연되면 LLM 이 상황을 파악할 수
  없다. 대신 LLM 이 판단할 수 있도록 대기 시간을 메시지에 명시한다.

에러 메시지 형식(사람과 LLM 이 같이 읽음, plain text):

```
VoIPbin API error 404 (CALL_NOT_FOUND): The call was not found. [request_id: req_LBCAH...]
VoIPbin API error 429 (RATE_LIMITED): ... Retry after 30 seconds. [request_id: ...]
```

### D-D. 인증 전송: 쿼리 → 쿠키

§5.2 로 동작이 확인됐다. 쿼리 파라미터는 서버 액세스 로그와 프록시 로그에
키를 남긴다. 쿠키 헤더는 그 노출을 없앤다. README Security Note 를 함께
갱신한다.

### D-E. `create_contact` / contacts 하위 리소스

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

### D-F. `list_routes` / `get_route`: 유지 + 권한 명시

제거와 유지 중 **유지**를 택한다. §5.4 에서 403 이 확정됐지만, 툴을 지우면
LLM 은 "그런 기능이 없다"고 답하고, 남기면 "권한이 없다"고 답한다. 후자가
정확하다. 셀프호스팅 superadmin 계정에서는 실제로 동작하기도 한다. docstring
에 superadmin 전용임을 명시하는 것으로 충분하다.

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

## 7. 영향 파일

| 파일 | 변경 | 관련 |
|---|---|---|
| `pyproject.toml` | `mcp>=1.0.0,<2`, 버전 bump | A-1.1, A-5.19 |
| `.github/workflows/ci.yml` | `dist-smoke` 잡 신설, Python 3.13, ruff | A-1.2, A-5.18 |
| `.github/workflows/publish.yml` | 테스트+스모크 게이트, 액션 SHA 핀 | A-1.3 |
| `src/voipbin_mcp/client.py` | envelope 파싱, request_id, 429, base URL 환경변수, 쿠키 전송 | A-2.6, A-2.7, A-5.15 |
| `src/voipbin_mcp/server.py` | page_size 상한 docstring 반영 지원 | A-3.10 |
| `src/voipbin_mcp/tools/contacts.py` | `addresses`/`tag_ids`, 하위 리소스 5툴 | A-2.4, A-2.5 |
| `src/voipbin_mcp/tools/campaigns.py` | enum 4값, required 4필드, 모듈 요약문 | A-3.8, A-4.11 |
| `src/voipbin_mcp/tools/ais.py` | enum 5값, write-only 서술, required `parameter` | A-3.8, A-3.9, A-4.12 |
| `src/voipbin_mcp/tools/flows.py` | action type 2값, uuid 예시 | A-3.8, A-3.10 |
| `src/voipbin_mcp/tools/calls.py` | 주소 타입 전체 | A-3.10 |
| `src/voipbin_mcp/tools/emails.py` | `attachments` | A-4.13 |
| `src/voipbin_mcp/tools/conferences.py` | required 전체, `type` 하드코딩 해제 | A-4.13 |
| `src/voipbin_mcp/tools/routes.py` | superadmin 명시 | A-5.14 |
| 모든 list 툴 | page_size 1–100 명시 | A-3.10 |
| `tests/test_client.py` | **기존 픽스처 교정** + envelope/429/base URL 테스트 | A-2.6, A-5.16 |
| `tests/test_tools_contacts.py` 외 4개 | 신설 | A-5.16 |
| `README.md` | 예시, 설치, Security Note, 지원 범위 | A-5.17 |

## 8. 검증 계획

1. `pytest tests/ -q` 전체 통과.
2. **배포 경로 실증**: `uv build` → 새 venv → `pip install dist/*.whl` →
   `python -c "import voipbin_mcp.server"` → `voipbin-mcp --help` 또는 프로세스
   기동 확인. 이것이 §2-1 의 유일한 참 증거다.
3. **회귀 재현 테스트**: `create_contact` 가 `addresses` 를 보내는지 요청 바디
   단위로 단언. 기존 `phone_numbers` 형태면 실패해야 한다.
4. **enum 교차 검증 테스트**: docstring 에 적은 값이 실제 OpenAPI enum 의
   부분집합인지 검사하는 테스트를 추가한다. 스펙 파일 경로가 이 저장소에
   없으므로, 값 목록을 테스트에 상수로 박고 "변경 시 스펙 재확인" 주석을
   남긴다. (스펙 저장소를 런타임 의존성으로 만들지 않는다.)
5. **뮤테이션 확인**: 새 테스트가 tautology 가 아닌지, 고친 코드를 일부러
   되돌려 실패하는지 확인한다.
6. 라이브 스모크: 실계정 GET 몇 건으로 쿠키 전송과 에러 형식 확인.

## 9. 롤아웃 / 리스크

| 리스크 | 정도 | 대응 |
|---|---|---|
| 쿠키 전송이 일부 프록시에서 막힘 | 낮음 | 실측 200 확인. 문제 시 쿼리 폴백을 후속에서 추가. |
| `mcp<2` 상한이 2.x 사용자를 배제 | 예상됨(의도) | 현재 2.x 는 어차피 동작 안 함. 배제가 아니라 정직한 표시. |
| enum 값을 테스트에 하드코딩 → 스펙 드리프트 재발 | 중간 | 본질적 한계. 주석과 §6 D-G 의 열거 정책으로 완화. 근본 해결은 스펙 기반 코드 생성이며 B 이후 과제. |
| contacts 하위 툴 5개 추가가 A 범위를 넘는다는 지적 | 있음 | §3 에 근거 명시. 수단 없는 정직함은 결함 문서화일 뿐. |
| 버전 bump 후 릴리스가 또 깨짐 | 낮음 | A-1.3 의 publish 게이트가 정확히 이것을 막는다. |

## 10. Open questions (리뷰어 판단 요청)

1. D-A: shim 대신 상한 핀만 채택한 판단이 타당한가. 2.x 지원을 지금 여는 것이
   더 나은가.
2. D-F: `list_routes` 유지 판단이 타당한가. 403 만 반환하는 툴이 LLM 컨텍스트를
   차지하는 비용 대비 가치가 있는가.
3. §8-4 의 enum 교차 검증 방식. 스펙을 런타임 의존성으로 만들지 않으면서 드리프트를
   잡는 더 나은 방법이 있는가.
4. contacts 하위 툴 5개를 A 에 포함하는 것이 스코프 규율 위반인가.

## 11. 리뷰 이력

| 회차 | 판정 | 핵심 피드백 | 조치 |
|---|---|---|---|
