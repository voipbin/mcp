# VOIP-1574 디자인: 외부 AI agent의 실시간 전화 대화 tool (voipbin/mcp)

- 티켓: VOIP-1574 (후속: VOIP-1575 최대 통화 24시간 백엔드)
- 선행 문서: `2026-10-07-agent-live-phone-conversation-analysis.md` (분석 리뷰 Round 5, 6 연속 APPROVED, 9절 발신 실측, 10절 수신 실측)
- 대상 repo: voipbin/mcp 단독, PR 1개
- 상태: 디자인 리뷰 종료(Round 4, 5 연속 APPROVED), 구현 단계

## 1. 목표와 비목표

목표: Claude Code 같은 외부 AI agent가 이 MCP 서버의 tool만으로 사람과 전화 대화를 한다. agent가 직접 말하고(TTS) 직접 듣는다(STT). 발신과 수신 모두 지원한다.

비목표:
- VoIPBin 내부 AI(ai_talk, pipecat) 사용.
- 백엔드(monorepo) 변경. 분석에서 확인된 백엔드 동작(미응답 수신 통화가 sleep 이후에도 정리되지 않음, 응답 전 180 미전송 등)은 대표님 결정으로 이번 범위에서 다루지 않는다.
- 자동 맞장구(정적 완화). 필요 시 후속.
- 24시간 통화. VOIP-1575 이후 상한 상수만 올린다.

## 2. 확정 결정 (대표님, 2026-10-07)

| # | 결정 |
|---|---|
| D1 | A안: MCP 서버가 통화 세션(통화, STT, TTS, 이벤트 구독)을 관리하고 턴 기반 tool 제공. B안(pipecat 연결) 제외. |
| D2 | 최대 통화 시간: 기본 1시간, 상한 1시간(상수 `MAX_CALL_SECONDS = 3600`). 24시간은 VOIP-1575. |
| D3 | 자동 맞장구 제외. |
| D4 | 수신 통화 포함, /ws listen 방식. |
| D5 | tool 구성: `phone_call_start`, `phone_say_and_listen`, `phone_say`, `phone_listen`, `phone_hangup`, `phone_status` + 수신용 tool. |
| D6 | 분석/실측에서 나온 백엔드 이슈는 이번 작업에서 처리하지 않는다. |

## 3. 실측 근거 요약 (분석 문서 9, 10절)

- 통화 유지: inline `sleep` action. 연장 불가, 따라서 `sleep` 길이 = 대화 최대 길이.
- TTS: `/speakings` direction=`out`, say 후 약 1초 내 청취. flush는 재생을 끊지 못하고 stop은 0.5초 내 무음. 재생 완료 이벤트 없음.
- STT: `/transcribes` direction=`in`, provider=`aws` 고정(GCP는 약 5분 후 조용히 중단). agent TTS 혼입 없음. 한 발화가 여러 transcript로 쪼개져 0.5초 간격으로 올 수 있음.
- /ws: 4파트 이벤트 타입 topic 구독, 3파트 prefix 금지(중복 수신 실측). call 이벤트는 같은 id를 공유하므로 dedupe 키는 `(id, status)`.
- extension 목적지 발신은 `groupcalls`로 생성되어 call_id를 POST 응답의 `groupcalls[0].call_ids`(비면 `GET /groupcalls/{id}`)로 얻는다.
- 수신: 번호 flow를 answer 없는 `sleep`으로 두면 ringing 유지. `call_created`(incoming)로 0.3~0.5초에 감지. `POST /calls/{id}/talk`가 응답(Answer) 후 인사 재생. **STT/TTS는 응답 후에 생성해야 동작**(응답 전 생성은 API 성공하지만 무동작).
- mcp SDK: 1.2.0은 tool 요청을 직렬 처리, 1.3.0 이상은 동시 처리. 1.6.0~1.27.0 동시 처리 실측.

## 4. 아키텍처

```
agent (Claude Code)
  │ MCP stdio (tool calls)
  ▼
voipbin_mcp.server (FastMCP)
  ├─ tools/phone.py          ← 신규 tool 정의 (얇은 계층)
  └─ phone/                  ← 신규 패키지
       ├─ events.py   EventHub: /ws 단일 연결, 구독, 재연결, 정규화, dedupe, 라우팅
       ├─ session.py  PhoneSession: 통화 1건의 상태 머신, transcript 버퍼, TTS 제어
       ├─ manager.py  SessionManager: 세션 맵, 동시 통화 한도, 수신 대기자, watchdog, 종료 정리
       └─ text.py     say 텍스트 분할(5000바이트), 발화 시간 추정
  ▼
VoIPBin REST (기존 VoIPbinClient 재사용) + wss /ws
```

- 기존 `VoIPbinClient`(httpx, 인증 transport 처리)를 그대로 쓴다. WS 연결만 신규.
- 모든 상태는 프로세스 메모리. 프로세스가 죽으면 세션도 사라지며, 남은 통화는 `sleep` 상한이 마지막 안전장치다(4.6절).

### 4.1 EventHub (`phone/events.py`)

- 연결: `wss://<host>/v1.0/ws`. `VOIPBIN_API_BASE_URL`에서 scheme을 `https→wss`, `http→ws`로 바꾸고 path에 `/ws`를 붙인다. 인증은 `VOIPBIN_AUTH_TRANSPORT`를 따른다: cookie(기본)면 handshake `Cookie: accesskey=...` 헤더, query면 `?accesskey=` 쿼리.
- 시작: 첫 phone tool 호출 시 lazy 시작(`GET /customer`로 customer_id 확보 후 연결). 실행 중 event loop에 백그라운드 task로 상주(spike로 tool 호출 간 유지 확인).
- 구독: 단일 연결에 4파트 topic 5개를 한 번에 구독.
  - `customer_id:<cid>:call:call_created`
  - `customer_id:<cid>:call:call_progressing`
  - `customer_id:<cid>:call:call_hangup`
  - `customer_id:<cid>:transcript:transcript_created`
  - `customer_id:<cid>:transcribe:transcribe_speech_interim`
- 정규화(소켓 프레임에 type 필드가 없으므로 payload 형태로 판별):
  - `offset_ms` 키 존재 → `TranscriptEvent(id, transcribe_id, direction, message)`
  - `streaming_id` 키 존재 → `InterimEvent(transcribe_id, direction, message)` (구독 topic이 interim뿐이므로 started/ended는 들어오지 않는다)
  - `status` 키와 `direction` in (incoming, outgoing) → `CallEvent(id, status, direction, flow_id, destination, source, hangup_reason, hangup_by, groupcall_id)`
  - 그 외 → 무시(debug 로그).
- dedupe: 최근 키 LRU(4096개). 키는 call=`("call", id, status)`, transcript=`("transcript", id)`, interim=`("interim", id)`(interim id는 이벤트마다 새 UUID).
- 라우팅: `CallEvent`는 `call_id`로, transcript/interim은 `transcribe_id`로 세션을 찾는다. 수신 대기자(`IncomingWaiter`)는 `CallEvent(status=ringing, direction=incoming)`를 `flow_id` == 대기 번호의 MCP flow id로만 매칭한다(destination 매칭은 하지 않는다. 다른 flow를 실행하는 통화를 가로채지 않기 위함). 매칭되지 않는 이벤트는 버린다. 단 call_id 미확정 발신 세션을 위해 call 이벤트는 30초 보관 버퍼(최대 256개)에 남긴다(4.3 race 처리).
- 재연결: 연결 끊김 시 지수 backoff(0.5s→최대 10s)로 재연결, 재구독. 재연결 직후 각 활성 세션에 `reconcile()`을 호출해 `GET /calls/{id}`(상태)와 `GET /transcripts?transcribe_id=`(누락 transcript, id로 dedupe)로 보정한다.
- 서버 ping(10초)에는 websockets 라이브러리가 자동 pong.
- 구독 ack가 없으므로 subscribe 전송 후 0.3초 동안 연결이 유지되면 준비 완료로 본다(잘못된 topic이면 서버가 연결을 끊음).
- reconcile의 `GET /transcripts`는 `page_size=100`과 `page_token`을 끝까지 따라간다.

### 4.2 PhoneSession (`phone/session.py`)

상태: `dialing → answered → ended` (수신은 `ringing → answered → ended`).

필드: `state`(pending/dialing/ringing/answered/ended), `call_id`, `groupcall_id`(pending), `direction`, `language`, `voice_id`, `transcribe_id`, `speaking_id`, `transcripts: deque`, `last_interim_at`, `last_stt_event_at`, `say_at`, `speaking_until`(추정 재생 종료 시각), `greeting_until`, `barge_in`(설정), `barge_gen`, `pending_stop`, `stale_speaking_ids`, `unreported_barge_in`, `ended_reason`, `ended_at`, `deadline`(최대 통화 시각), `last_tool_at`, `lock`.

응답 후 미디어 시작(`start_media()`):
1. `POST /transcribes {reference_type: call, reference_id, language, direction: "in", provider: "aws"}`. 응답의 `provider`는 요청값이 그대로 저장된 것이라(`bin-transcribe-manager/pkg/transcribehandler/start.go:294`, 실제 fallback은 이후 비동기 `runSTT`에서 일어나며 레코드 미갱신) 실제 provider 판별에 쓸 수 없다. 대신 휴리스틱: 통화 진행 중 마지막 interim/transcript 이후 `STT_SILENT_WARN_SECONDS = 240` 이상 아무 STT 이벤트가 없으면 listen/status 결과에 `stt_silent_seconds`를 넣어 agent가 전사 중단 가능성을 알 수 있게 한다.
   - `start_media` 직후 1회 `reconcile()`로 transcribe_id 확정 전에 도착해 버려진 transcript를 회수한다(transcript/interim 보관 버퍼는 두지 않는다. call 이벤트 보관 버퍼만 유지).
2. `POST /speakings {reference_type: call, reference_id, language, voice_id?, direction: "out"}`.
3. 둘 중 하나라도 실패하면 통화를 끊고 오류 반환(반쪽 대화 금지).

말하기 `say(text)`:
- `text.py`로 UTF-8 5000바이트 이하 조각으로 분할(문장 경계 우선, 없으면 바이트 경계에서 UTF-8 안전 분할). 조각마다 `POST /speakings/{id}/say`.
- `speaking_until = max(now, speaking_until) + estimate(text)`. 추정치: 영어 계열 15자/초, CJK 6자/초, 최소 1초 + 고정 지연 1초. 재생 완료 이벤트가 없어(G1) 추정만 쓴다.
- speaking 세션이 없으면(barge-in으로 stop된 경우) 새로 생성 후 say.

끊기(barge-in):
- 실행 주체: EventHub의 라우팅 콜백(백그라운드)이 판정한다. WS 수신 루프를 막지 않도록 HTTP를 직접 await하지 않는다. 판정 시 순서: (1) `old_id = speaking_id` 캡처, (2) 즉시 `speaking_id=None`, `speaking_until=now`, (3) `old_id`로 stop을 수행하는 task를 생성해 세션의 `pending_stop`에 보관. 세션 lock은 잡지 않는다. 다음 say는 (세션 lock 안에서) `pending_stop`이 있으면 먼저 await한 뒤(이전 speaking이 active로 남아 생성 거부되는 것 방지, `bin-tts-manager/pkg/speakinghandler/speaking.go:38-54`), `speaking_id`가 None이면 새 세션을 만든다. 세션에 `barge_gen` 카운터를 두고 barge-in 판정마다 1 증가시킨다. say는 시작 시 세대를 캡처하고, 조각을 보낼 때마다 세대가 바뀌었으면 남은 조각 전송을 중단한다. 세대가 바뀐 뒤에는 어떤 재시도도 하지 않는다(상대가 끼어든 뒤 agent가 다시 말하는 것 방지). 세션은 stop 미확인 id 목록 `stale_speaking_ids`를 유지한다(barge-in 시 old_id 추가, stop 성공 시 제거). 재시도는 세대가 같고 speaking 생성이 '이미 active' 오류로 거부된 경우(`speaking.go:38-54`)에만, `stale_speaking_ids` 전체에 stop을 재시도한 뒤 생성 1회 재시도로 한정한다.
- 오탐 완화: interim message가 2단어 이상 또는 4자 이상일 때만 끼어듦으로 본다.
- `barge_in` 설정은 세션 속성으로 유지되며 say/say_and_listen 호출 시 인자로 갱신한다.
- 판정 조건: 판정 창은 `say_at + 1.0초`(`say_at`은 speaking_until이 비어 있던 상태에서 시작한 첫 say의 시각, 연속 say는 창을 연장만 함)부터 `speaking_until`까지(실측상 say 후 첫 오디오까지 0.9~1.3초, 그 전 interim은 agent 음성을 듣기 전에 시작된 발화). 이 창에서 `InterimEvent`(위 길이 조건 충족)가 오면 끼어듦으로 보고 위 순서로 정리한다(실측: 새 speaking 생성+say 0.6초, 첫 오디오까지 추가 0.3~0.9초). 수신 인사(`talk`) 재생 구간은 speakings로 멈출 수 없으므로 barge-in 판정에서 제외하고 `during_agent_speech` 표시에만 쓴다.
- 보고: barge-in이 일어나면 세션의 `unreported_barge_in`을 세운다. 다음 say, say_and_listen, listen 결과 중 먼저 반환되는 것에 `barge_in: true`로 1회 노출하고 지운다(`phone_say(wait=false)` 반환 후 발생한 barge-in도 agent에게 전달).
- 자동 barge-in은 `barge_in` 인자로 끌 수 있다(기본 켜짐). 끄면 agent 말을 끝까지 재생하고 그 사이 상대 발화는 그대로 전사·버퍼링된다.

듣기 `listen(timeout, end_silence)`:
- 버퍼에 이미 transcript가 있으면 그것부터 사용. say_and_listen에서는 say 이전에 쌓인 잔여 transcript를 `earlier_heard` 필드로 분리해 반환하고 `heard`에는 say 이후 발화만 넣는다.
- 첫 transcript 수신 후 `end_silence`(기본 0.8초) 동안 새 transcript나 interim이 없으면 턴 종료로 보고, 버퍼의 transcript를 공백으로 이어 반환한다(한 발화가 여러 transcript로 쪼개지는 실측 반영).
- interim이 오고 있으면 턴 종료를 미룬다(상대가 아직 말하는 중).
- `timeout`(기본 30초, 상한 120초) 안에 아무 transcript도 없으면 `heard: ""`, `timed_out: true`. timeout으로 끝날 때 세션 `reconcile()`을 1회 실행해 서버 구독 버퍼 drop으로 잃은 transcript를 회수한다(transcript id dedupe). 단 timeout 시점에 interim이 진행 중이면 최대 5초 연장해 발화 확정을 기다린다.
- `end_silence_ms` 범위 300~3000.
- timeout 기준점: say_and_listen에서는 `max(now, speaking_until)`부터 `listen_timeout`을 센다(긴 발화 중에 timeout이 소모되지 않게). say_and_listen 전체 블로킹은 130초를 넘지 않는다(Claude Code 130초 블로킹 실측 범위)(추정 발화 시간이 길면 listen 구간을 줄임).
- 발화가 긴 경우: say_and_listen에서 추정 발화 종료가 130초 상한 이후면 listen 없이 `still_speaking: true`로 반환한다(agent는 phone_listen으로 이어 듣는다). `phone_say(wait=true)`의 대기도 120초 상한이며 넘으면 `still_speaking: true`로 반환한다.
- 전체 대기 상한: 어떤 경우에도 기준점 + `timeout + 5초`를 넘기지 않는다. 배경 소음이나 긴 독백으로 interim/transcript가 계속 오면 상한 시점까지의 버퍼를 `heard`로 반환하고 `truncated: true`를 표시하며, 이후 발화는 다음 listen으로 넘긴다.
- 통화가 끝나면 즉시 남은 버퍼와 함께 `call_ended: true`로 반환.
- agent가 말하는 도중(`speaking_until` 이전)에 확정된 transcript는 `during_agent_speech: true`로 표시한다(스피커폰 에코 판단 보조).

종료 `hangup()`:
- `POST /speakings/{id}/stop`(실패 무시) → `POST /calls/{id}/hangup`(404/이미 종료 무시). transcribe는 서버가 hangup 시 자동 중지.
- `call_hangup` 이벤트 수신 시에도 같은 정리(speaking stop)를 한다. speaking은 서버가 정리하지 않기 때문(분석 3.3).

### 4.3 발신 흐름 (`phone_call_start`)

1. 인자 검증: `max_duration_seconds` 30~3600(기본 3600), `answer_timeout_seconds` 5~60(기본 60), destination_type in {tel, sip, extension}.
2. EventHub 준비(연결, 구독 완료).
3. `POST /calls`
   ```json
   {"source": {"type": "tel", "target": "<source_number>"},
    "destinations": [{"type": "<t>", "target": "<v>"}],
    "actions": [{"type": "sleep", "option": {"duration": <max_duration_ms>}}]}
   ```
   destination_type=extension이면 `{"type": "extension", "target_name": "<v>"}`(실측: `target`이 아니라 `target_name`).
4. 취소 안전 등록: POST /calls 요청과 그 응답으로 `pending` 세션(`call_id` 또는 `groupcall_id`)을 등록하는 구간 전체를 `with anyio.CancelScope(shield=True):`로 감싼다(POST가 서버에서 성공했는데 등록이 없는 상태 방지). 이후 어느 단계에서든 취소(Claude Code Esc의 `notifications/cancelled`, mcp 1.27 EOF 취소)나 예외가 나면 4.7의 정리 규칙으로 hangup 후 재전파한다. 정리 대상: call_id 확정 후에는 `POST /calls/{id}/hangup`, 확정 전에는 `POST /groupcalls/{id}/hangup`(그 groupcall의 call_ids와 groupcall_ids를 모두 끊음, `bin-call-manager/pkg/groupcallhandler/hangup.go:46-76`). call 생성이 goroutine으로 진행되므로(`bin-call-manager/pkg/groupcallhandler/start.go:344-362` `startWithDestination`) 정리용 `move_on_after(5, shield=True)` 안에서 0.5초 간격으로 `GET /groupcalls/{id}`의 call_ids를 조회해 hangup이 아닌 call을 개별 `POST /calls/{id}/hangup`(404 무시)으로 끊고, 모두 hangup이거나 404가 되면 멈춘다.
5. call_id 확정:
   - 응답 `calls[0].id`가 있으면 그것.
   - 없으면 groupcall: 후보 집합은 POST 응답 `groupcalls[0].call_ids`(extension은 항상 RingAll이고 call_ids는 `Create`에서 확정되어 `createCallsOutgoingGroupcall`의 동기 `Start` 이후 응답에 포함, `bin-call-manager/pkg/groupcallhandler/dial.go:158-159`, `pkg/callhandler/outgoing_call.go:530`). 비어 있을 때만 `GET /groupcalls/{id}` 1회로 보완. 후보 중 처음 `call_progressing`이 온 call을 확정하고, 보조로 `answer_call_id`를 확인한다. 실패 판정은 groupcall status hangup 또는 후보 전원 hangup. destination_type이 {tel, sip, extension}으로 제한되므로 linear ring과 중첩 groupcall은 발생하지 않아 다루지 않는다.
   - 응답 대기: 보관 버퍼와 이후 이벤트에서 `call_progressing` 대기, 보조로 1초마다 GET(단일 call은 `GET /calls/{id}`, groupcall은 `GET /groupcalls/{id}`의 status와 `answer_call_id`. 후보 call 생성 goroutine 실패 시 call 레코드와 이벤트가 없을 수 있으므로 groupcall status가 유일한 실패 신호이며, 후보 판정에서 404 call은 종료로 센다). 단일 call이 `call_hangup`이면 `hangup_reason`과 함께 실패 반환. `answer_timeout` 초과면 4의 정리 경로로 hangup 후 `no_answer`.
6. `start_media()`. 실패 시 hangup 후 오류.
7. 결과: `{call_id, status: "answered", max_duration_seconds, hint}`.

### 4.4 수신 흐름

수신 준비 tool `phone_incoming_configure(number_id, enabled, restore_call_flow_id="", clear=false)`:
- MCP flow는 번호마다 1개. 이름 `voipbin-mcp incoming <number_id>`, `detail`에 JSON `{"voipbin_mcp": "incoming", "number_id": ..., "previous_call_flow_id": ...}`를 저장해 원래 flow를 서버 측에 보존한다(agent 컨텍스트가 사라져도 복구 가능). 이름 탐색은 하지 않는다.
- `enabled=true`:
  1. `GET /numbers/{id}`로 현재 `call_flow_id`를 읽는다.
  2. 그 flow가 이미 MCP flow(`GET /flows/{id}`의 detail 마커 확인)면 아무것도 바꾸지 않고 detail의 `previous_call_flow_id`를 그대로 반환한다(재호출해도 원래 flow를 잃지 않음).
  3. 아니면 `POST /flows {name, detail, actions: [{"type":"sleep","option":{"duration":3600000}}]}`(answer 없음)로 만들고 `PUT /numbers/{id}/flow_ids {call_flow_id}`(부분 갱신, message_flow_id 유지)로 연결한다. PUT이 실패하면 방금 만든 flow를 삭제한다.
- `enabled=false`:
  0. 이 프로세스에 해당 번호의 대기자나 활성 수신 세션이 있으면 오류(먼저 대기 종료 또는 통화 종료 필요).
  1. 현재 flow가 MCP flow가 아니면 오류(이미 해제됨).
  2. 복원 대상: `restore_call_flow_id`가 있으면 그것, 없으면 detail의 `previous_call_flow_id`. 저장값이 nil이면 `clear=true`가 명시된 경우에만 `"00000000-0000-0000-0000-000000000000"`을 보내 해제하고(JSON null이나 생략은 무변경), 아니면 오류(조용한 수신 라우팅 해제 방지). number-manager는 flow 존재를 검증하지 않으므로 복원 전에 `GET /flows/{prev}`로 존재를 확인하고, 없으면 오류.
  3. `PUT /numbers/{id}/flow_ids`로 복원 후 MCP flow 삭제.
- docstring에 "번호의 기존 call flow를 대체하며, MCP가 실행 중일 때만 수신이 처리된다"를 명시한다.
- 한 번호에는 MCP 프로세스 1개만 수신 대기해야 한다(README 명시).

수신 대기 tool `phone_wait_incoming(number_id, timeout_seconds=120, greeting="Hello.", greeting_language="en-US", language="en-US", voice_id="", max_duration_seconds=3600)`:
1. `GET /numbers/{number_id}`로 현재 `call_flow_id`를 읽고 그 flow가 MCP 마커 flow인지 확인한다(아니면 오류: 먼저 configure 필요). 이 flow id가 매칭 키다. 번호당 대기자 1명. 이미 대기 중이면 오류. 세션 한도 도달 시 오류.
2. 먼저 grace 안의 같은 flow_id 미청구 ringing 통화, 그다음 call 이벤트 보관 버퍼(30초)의 같은 flow_id incoming ringing 통화(첫 wait 직전 도착분, `GET /calls/{id}`로 status ringing 확인 후)를 가져가고, 없으면 `call_created`(direction=incoming, status=ringing, `flow_id` == 매칭 키) 대기. timeout이면 `timed_out`. 인수한 통화가 이미 발신자 쪽에서 끊겨 4단계 talk가 실패하면 오류를 반환하지 않고 남은 timeout 안에서 다음 통화를 계속 기다린다.
3. 감지 즉시 `pending` 세션 등록(4.7 규칙, 정리 수단은 `POST /calls/{id}/hangup`).
4. `POST /calls/{id}/talk {text: greeting, language: greeting_language}`로 응답 겸 인사. talk는 gcp/aws 음성 체계라 speakings(ElevenLabs)의 `voice_id`를 넘기지 않는다. greeting은 비울 수 없다(talk가 응답 수단). 인사 추정 재생 시간은 `greeting_until`로 별도 보관해 `during_agent_speech` 표시에만 쓴다(barge-in 판정 대상 아님, 4.2).
5. `call_progressing` 확인(최대 5초, 보조 GET) 후 `start_media()`(응답 후 생성 규칙).
6. 우선순위: talk 실패 후 `GET /calls/{id}`의 status가 hangup이거나 404면 2단계 규칙(남은 timeout 안에서 계속 대기)을 따른다. 그 외 3~5단계 실패/취소는 4.7 규칙으로 `POST /calls/{id}/hangup` 후 오류 반환.
7. 결과: `{call_id, status: "answered", caller: source}`.

대기자 없는 수신 통화(분석 10절 설계 함의): 판정은 `call_created` payload의 `flow_id`(수신 통화에는 번호의 call_flow_id가 들어감, `bin-call-manager/pkg/callhandler/start.go:609,679-690`)가 이 프로세스가 `phone_wait_incoming`에서 매칭 키로 사용한 적이 있는 MCP flow id 집합(로컬 집합, 추가 API 조회 없음)에 속하는 경우로만 한다(발신 전용 프로세스는 거절하지 않음). 조건을 만족하는 incoming ringing 통화가 `UNCLAIMED_INCOMING_GRACE_SECONDS = 15` 안에 대기자에게 잡히지 않으면, 만료 시점에 `GET /calls/{id}`의 status가 `ringing`일 때만 `POST /calls/{id}/hangup`으로 거절한다. 해당 call의 `call_progressing` 또는 `call_hangup`을 받으면 타이머를 취소한다(다른 프로세스가 응답한 통화를 끊지 않음). 새 대기자는 grace 안에 남아 있는 미청구 ringing 통화를 먼저 가져간다(대기 tool 재호출 사이 공백 흡수). MCP 프로세스가 없으면 거절 주체도 없으므로 백엔드 동작(채널 1시간 timeout, `bin-call-manager/pkg/callhandler/start.go:230-241`)에 맡긴다(D6).
- 수신 대화 최대 길이: 응답 후에는 flow sleep(1시간)과 채널 1시간 timeout이 상한이고, `max_duration_seconds`가 짧으면 MCP 타이머로 hangup한다.

### 4.5 동시성과 한도

- 동시 활성 세션 상한 `MAX_SESSIONS = 4`(상수). 슬롯은 POST(또는 대기 시작) 전에 동기적으로 예약하고 실패/취소/종료 시 반납한다(동시 호출 경쟁 방지). 초과 시 `phone_call_start`/`phone_wait_incoming`은 오류.
- 종료된 세션은 60초간 맵에 보존해 늦게 온 listen/status가 `call_ended`를 받게 한다(MAX_SESSIONS 계산에서 제외).
- 세션별 `asyncio.Lock`으로 같은 call_id에 대한 say/listen 동시 호출을 직렬화한다. `phone_hangup`과 `phone_status`는 lock 없이 즉시 처리(listen 대기 중에도 끊을 수 있어야 함).
- 모든 tool은 `async def`. 블로킹 대기는 anyio 취소 모델에 맞춰 `anyio.fail_after`/`move_on_after`를 쓴다.
- mcp 의존성 하한을 `mcp>=1.27.0,<2`로 올린다. 근거: 1.2.0은 직렬 처리(실측), 1.26 이하는 EOF 뒤에도 진행 중 listen/wait(최대 120/600초)를 끝까지 기다려 종료 정리가 늦고 그 사이 수신 응답 위험. `scripts/dist_meta.py`가 floor를 자동 추출하므로 CI floor leg가 1.27.0으로 따라온다.

### 4.6 고아 통화 방지 (G7)

| 계층 | 장치 |
|---|---|
| 1 | 최대 길이 타이머: `deadline` 도달 시 MCP가 hangup. |
| 2 | 무활동 watchdog: 세션에 대한 tool 호출이 `IDLE_HANGUP_SECONDS = 300`(상수) 동안 없으면 hangup. listen 대기 중은 활동으로 본다. |
| 3 | 정상 종료: (a) SIGTERM/SIGINT: 첫 phone tool 호출 시 실행 중 loop에 `loop.add_signal_handler`를 등록한다(anyio 아래 동작 확인). handler는 정리 task를 만들어 전 세션 hangup을 병렬로 실행하되 전체 5초 상한(`move_on_after(5, shield=True)`) 후 `os._exit(128+signum)`으로 종료한다. 정리 중 두 번째 시그널은 무시한다. Python 기본 SIGTERM은 finally 없이 종료하므로 필수이고, SIGINT 등록 시 KeyboardInterrupt 기본 동작이 사라지는 것은 의도된 대체다. phone tool을 한 번도 쓰지 않은 프로세스는 등록하지 않아 기존 동작 불변. (b) stdin EOF: mcp 1.27.0부터 EOF 시 진행 중 핸들러를 취소한다(`lowlevel/server.py:690` `tg.cancel_scope.cancel()`, 1.26 이하에는 없음 확인). 취소된 tool은 4.7 규칙으로 자기 통화를 끊고, `main()`은 `mcp.run()` 대신 `anyio.run(_serve)`를 쓰고 `_serve`는 `try: await mcp.run_stdio_async() finally:`에서 같은 loop와 기존 client로 남은 세션을 병렬 hangup(전체 5초, shield)한다(새 loop를 만들지 않으므로 signal handler 공백 구간 없음). 세션이 없으면 finally는 client 생성이나 네트워크 없이 즉시 끝나는 no-op이어야 한다(기존 `scripts/stdio_smoke.py`, `scripts/fault_matrix.py` 종료 검증 유지). 종료 정리 대상에는 활성 세션 외에 pending(groupcall_id만 있는) 세션과 grace 중인 미청구 수신 ringing 통화(status ringing일 때만)도 포함한다. SessionManager는 진행 중 POST /calls 수를 세고, 종료 정리는 5초 예산 안에서 그 등록 완료를 기다린 뒤 hangup한다. |
| 4 | 최종 backstop: SIGKILL 등으로 위 장치가 모두 실패하면 발신, 수신 모두 채널 생성 시점부터 1시간 delayed hangup(`bin-call-manager/pkg/callhandler/start.go:239`). 발신은 추가로 `sleep`(max_duration) 종료 후 서버가 끊는다. |

watchdog과 deadline은 EventHub와 분리된 supervisor task에서 1초 주기로 검사한다(EventHub가 재연결 중이거나 죽어도 동작). 백그라운드 task 참조는 SessionManager가 강하게 보유하고, lazy start는 `asyncio.Lock`으로 1회만 실행한다. supervisor는 EventHub task가 예외로 끝나면(`done()` 감지) 재시작한다. EventHub가 죽어 재시작 중이면 tool 결과에 `events_connected: false`를 노출한다.

### 4.7 취소와 정리 규칙 (anyio)

FastMCP/mcp는 anyio cancel scope로 handler를 취소하며, scope가 취소된 동안 모든 await 지점에 취소가 반복 전달된다(리뷰 Round 2에서 재현: `except CancelledError` 안의 단순 `await hangup`은 즉시 다시 취소되고, `asyncio.shield`는 바깥이 즉시 반환되어 EOF 경로에서 정리가 실행되지 않음. `anyio.move_on_after(5, shield=True)`만 완료).

- POST와 pending 등록 구간: `with anyio.CancelScope(shield=True):`.
- 모든 정리 경로(hangup, speaking stop, groupcall hangup, 4.6 계층 3의 전 세션 정리): `with anyio.move_on_after(5, shield=True):` 안에서 실행하고, 정리 실패는 로그만 남긴다.
- 대화 tool(`phone_say_and_listen`, `phone_say`, `phone_listen`)이 취소되면(Esc, client timeout) 통화는 유지하고(idle watchdog에 맡김), 진행 중 say는 남은 조각 전송을 멈추며, 세션 lock은 해제된다. `phone_wait_incoming`이 통화 감지 전에 취소되면 대기자를 제거하고 슬롯을 반납한다(무해). 감지 후 취소는 위 정리 규칙으로 hangup.
- `asyncio.shield`는 사용하지 않는다.
- 테스트는 `task.cancel()` 1회가 아니라 anyio task group의 `cancel_scope.cancel()`로 취소해 반복 취소 환경에서 정리 HTTP가 실제로 나가는지 검증한다.

## 5. Tool 명세

공통: 결과는 JSON 문자열(`format_response`). 오류는 기존 `VoIPbinAPIError` 메시지를 그대로 노출하거나 `{"error": "...", "reason": "..."}` 형태. 모든 phone tool docstring 첫 줄에 "live phone conversation" 용도와 비용 발생(통화, STT, TTS 과금)을 명시한다.

| tool | 인자 | 동작 | 반환 |
|---|---|---|---|
| `phone_call_start` | `source_number`, `destination_type`, `destination_target`, `language="en-US"`, `voice_id=""`, `max_duration_seconds=3600`, `answer_timeout_seconds=60` | 4.3 | `call_id`, `status`, `max_duration_seconds`, `hint` |
| `phone_incoming_configure` | `number_id`, `enabled`, `restore_call_flow_id=""`, `clear=false` | 4.4 | `number`, `call_flow_id`, `previous_call_flow_id` |
| `phone_wait_incoming` | `number_id`, `timeout_seconds=120`, `greeting="Hello."`, `greeting_language="en-US"`, `language="en-US"`, `voice_id=""`, `max_duration_seconds=3600` | 4.4 | `call_id`, `caller`, `status` 또는 `timed_out` |
| `phone_say_and_listen` | `call_id`, `text`, `listen_timeout_seconds=30`, `end_silence_ms=800`, `barge_in=true` | say 후 listen. 1턴 = 1호출 | `heard`, `earlier_heard`, `still_speaking?`, `barge_in`, `timed_out`, `truncated`, `call_ended`, `during_agent_speech`, `stt_silent_seconds?`, `events_connected` |
| `phone_say` | `call_id`, `text`, `wait=false`, `barge_in=true` | say. `wait=true`면 `speaking_until`까지(또는 barge-in까지) 대기 | `queued`, `estimated_seconds`, `barge_in?`, `still_speaking?` |
| `phone_listen` | `call_id`, `timeout_seconds=30`, `end_silence_ms=800` | listen | `heard`, `barge_in`, `timed_out`, `truncated`, `call_ended`, `during_agent_speech`, `stt_silent_seconds?`, `events_connected` |
| `phone_hangup` | `call_id` | 4.2 종료 | `status: ended` |
| `phone_status` | `call_id=""` | 하나 또는 전체 세션 요약 | 상태, 경과, 남은 시간, 버퍼 transcript 수, `events_connected` |

인자 범위 검증은 tool 진입 시 수행하고 위반 시 API를 호출하지 않는다. `max_duration_seconds` 30~3600, `answer_timeout_seconds` 5~60(서버 dial timeout 60초, `defaultDialTimeout`), say `text`는 빈 문자열 거부, 1회 호출 총 20000바이트 상한(비용 안전). `listen_timeout_seconds`/`timeout_seconds` 상한 120초(실측: Claude Code 130초 블로킹 정상이나 client별 timeout 차이를 고려), `phone_wait_incoming.timeout_seconds` 기본 120초, 상한 600초(client timeout으로 취소되어도 감지 전이면 무해하고, 감지 후면 hangup으로 정리됨. 장시간 대기는 반복 호출 권장, docstring 명시).

## 6. 파일 변경

| 파일 | 변경 |
|---|---|
| `src/voipbin_mcp/phone/__init__.py` | 신규 |
| `src/voipbin_mcp/phone/events.py` | 신규. EventHub |
| `src/voipbin_mcp/phone/session.py` | 신규. PhoneSession |
| `src/voipbin_mcp/phone/manager.py` | 신규. SessionManager, IncomingWaiter, watchdog, shutdown |
| `src/voipbin_mcp/phone/text.py` | 신규. 분할, 발화 시간 추정 |
| `src/voipbin_mcp/tools/phone.py` | 신규. 8개 tool |
| `src/voipbin_mcp/tools/__init__.py` | `phone` import 추가 |
| `src/voipbin_mcp/server.py` | `main()`을 `anyio.run(_serve)`로 바꾸고 `_serve`의 finally에 종료 정리(4.6 계층 3) 추가 |
| `pyproject.toml` | `mcp>=1.27.0,<2`, `websockets>=13,<16` 추가 |
| `uv.lock` | 갱신(`uv lock --check` CI 통과) |
| `.github/workflows/dist-smoke.yml` | floor leg(75~124행)에 `websockets==13.*` 고정 추가 |
| `tests/test_phone_text.py` | 신규 |
| `tests/test_phone_events.py` | 신규. 로컬 websockets 서버로 연결/구독/정규화/dedupe/재연결 |
| `tests/test_phone_session.py` | 신규. EventHub 주입 가짜 이벤트 + respx로 say/listen/barge-in/hangup/종료 정리 |
| `tests/test_tools_phone.py` | 신규. tool 인자 검증, 발신(일반/groupcall), 수신, 한도 |
| `tests/golden_docstrings.json` | 신규 tool docstring 추가(`scripts/update_golden_docstrings.py`) |
| `README.md` | tool 표에 Phone 행, "Live phone conversations" 절(사용 예, 비용, 수신 설정, 한 번호당 MCP 프로세스 1개), Claude Code 사용 주의(phone tool allowlist 권장: 권한 승인 대기도 대화 지연과 idle 시간에 포함), interim 이벤트가 고객 webhook으로도 대량 발송되는 비용 영향, Known limitations 추가 |

websockets 버전: `additional_headers` 인자를 쓰는 신규 asyncio client(`websockets.asyncio.client.connect`)는 13.0부터 제공. Python 3.10~3.13 지원 범위와 호환. CI floor leg에 `websockets==13.*` 고정을 추가해 하한을 검증한다(`.github/workflows/dist-smoke.yml`의 floor job).

## 7. 테스트 계획

단위(CI, 네트워크 없음):
- text: 5000바이트 분할(한국어 다바이트 경계), 문장 경계 우선, 추정치.
- events: 로컬 WS 서버로 Cookie/query 인증 헤더, 구독 메시지 형식(4파트 topic 5개, 3파트 없음), payload 정규화 4종, `(id,status)` dedupe(같은 call의 progressing과 hangup은 둘 다 전달), 연결 끊김 후 재구독과 reconcile 호출.
- session: listen 턴 병합(0.5초 간격 2 transcript → 1턴), interim 중 턴 종료 지연, timeout, call_ended 즉시 반환, barge-in 시 stop 호출과 다음 say의 speaking 재생성, `barge_in=false`, during_agent_speech 표시, hangup 시 speaking stop 오류 무시, call_hangup 이벤트 정리, `stt_silent_seconds` 휴리스틱, start_media 직후 reconcile로 선도착 transcript 회수, listen 전체 상한(interim이 계속 올 때 `truncated`), barge-in의 pending_stop await 후 speaking 재생성, 다중 조각 say 도중 barge-in 시 남은 조각 중단, say POST 진행 중 barge-in 시 재시도 없음, 미보고 barge-in의 다음 결과 1회 노출, 판정 창 say+1초 이전 interim 무시, 긴 say에서 listen timeout 기준점.
- tools: 발신 body(sleep duration ms, extension의 target_name), POST 응답 call_ids로 후보 확정(빈 경우 GET 1회 보완), answer_timeout hangup, 수신 매칭(다른 번호/outgoing 무시), greeting 빈 값 거부, 대기자 중복 거부, MAX_SESSIONS, 인자 범위 위반 시 무호출, incoming_configure의 previous flow 반환과 원복.
- manager: idle watchdog, deadline, shutdown 시 전 세션 hangup, supervisor가 EventHub 장애와 독립 동작, 세션 없을 때 main finally no-op.
- 취소: anyio task group `cancel_scope.cancel()`로 반복 취소를 재현해, `phone_call_start`가 POST 중/직후/응답 대기 중 취소되면 hangup(call_id 확정 후는 calls hangup, 전은 groupcalls hangup), `phone_wait_incoming`이 talk 이후 취소되면 hangup이 실제로 전송되는지 검증.
- groupcall: 두 번째 leg가 응답한 경우 그 leg로 확정, groupcall status hangup 또는 후보 전원 hangup 시 실패, 확정 전 취소 시 groupcall hangup 후 call_ids 개별 hangup 반복(모두 종료까지).
- 한도: 동시 phone_call_start 2건이 슬롯 예약으로 한도를 넘지 않음, 종료 세션 60초 보존.
- 기타: 대화 tool 취소 시 통화 유지와 lock 해제, wait 감지 전 취소 시 대기자 제거와 슬롯 반납, 첫 wait 직전 도착 통화 인수, 수신 talk 실패 후 call hangup이면 계속 대기, 활성 수신 중 configure(enabled=false) 오류, stale_speaking_ids 재시도, 긴 발화 still_speaking, listen timeout 시 reconcile, SIGTERM 시 진행 중 POST 완료 대기 후 hangup과 미청구 수신 통화 정리.
- 수신: flow_id가 다르고 destination만 같은 통화 무시, 인수 통화 talk 실패 시 다음 통화 계속 대기, 대기자 없는 incoming은 grace 후 status ringing일 때만 hangup, progressing/hangup 수신 시 타이머 취소, 발신 전용 프로세스는 거절 안 함, MCP flow가 아닌 flow_id 통화 무시, 새 대기자의 미청구 통화 인수, configure 재호출 시 previous 보존, disable 시 detail 복원과 MCP flow 삭제, 저장값 nil이면 clear 없이 오류.
- 시간 의존 테스트는 시계와 상수(end_silence, grace, idle)를 주입해 결정적으로 만든다.
- `tools/phone.py`는 정확히 `@mcp.tool()` 표기를 써서 기존 등록 카운트 테스트를 통과한다. docstring의 백엔드 사실(응답 후 STT/TTS 생성, 미응답 수신 잔존 등)은 repo 관례대로 `PINNED_CLAIMS`에 Go 근거와 함께 고정한다.
- 기존 golden docstring/contract 테스트 통과.

수동 E2E(PR 전, 운영, 비용 최소): 분석 실측 스크립트와 같은 방식으로 실제 MCP 서버를 stdio로 띄워 (1) extension 대상 발신 대화 2턴과 barge-in, (2) 임시 virtual number 수신 대화 2턴, (3) MCP 프로세스 종료 시 hangup을 확인. 임시 리소스는 정리한다. 가능하면 Claude Code로 1회 실제 구동.

## 8. 리스크와 알려진 제약 (README에 기재)

- 턴 지연 약 4초(받아쓰기 약 1초 + agent 추론 약 2초 + TTS 약 1초).
- barge-in은 추정 재생 시간 기반이라 짧게 빗나갈 수 있다.
- 스피커폰 에코: agent 음성이 상대 쪽에서 되울리면 전사될 수 있다(`during_agent_speech`로 표시).
- MCP가 실행 중이면 대기자 없는 수신 전화는 15초 후 거절한다. MCP 프로세스가 없으면 수신 전화는 ringing으로 남고(최대 채널 1시간 timeout), 응답 전 180 Ringing이 없어 발신자가 링백을 못 들을 수 있다(백엔드 동작, D6).
- STT provider가 aws로 초기화되지 않은 환경(셀프호스팅 등)에서는 조용히 GCP로 동작해 약 5분 후 전사가 멈출 수 있다. API로 판별 불가하므로 `stt_silent_seconds` 휴리스틱으로만 알린다.
- 비용: 통화, STT, TTS 과금. 호스티드 환경의 PSTN 발신 제한은 기존 정책을 따른다.
- 프로세스 강제 종료 시 통화는 최대 길이까지 남을 수 있다.
- 수신 대화의 실제 상한은 1시간에서 ringing 시간을 뺀 값이다(채널 timeout 기준).

## 9. 미결 사항 (결정됨)

1. 단일 WS 연결 + payload 형태 판별: 채택(Round 1, 4 리뷰어 동의).
2. `MAX_SESSIONS = 4`, `IDLE_HANGUP_SECONDS = 300`, `UNCLAIMED_INCOMING_GRACE_SECONDS = 15`: 채택.
3. configure의 flow 대체: 마커 flow, previous 보존, 한 번호당 MCP 프로세스 1개 규칙(README)으로 채택.

## 10. 리뷰 이력
- Round 1: CHANGES_REQUESTED. MAJOR 7(stt_warning 무효, mcp 1.6~1.26 EOF 종료 정리 불성립과 SIGTERM, tool 취소 시 고아 통화, groupcall 다중 leg, configure의 설정 유실, 대기자 없는 수신 통화, 수신 부분 실패 정리와 voice_id 체계 혼용), MINOR 10. 전부 반영.
- Round 2: CHANGES_REQUESTED. MAJOR 3(anyio 반복 취소로 asyncio.shield 정리 불성립, 대기자 없는 수신 거절이 타 프로세스 응답 통화를 끊을 위험, listen 전체 상한 부재), MINOR 8(barge-in 순서와 pending_stop, clear의 nil UUID와 flow 존재 확인, CI 경로 dist-smoke.yml, SIGTERM 종료 방식과 finally no-op, 중첩 groupcall, 미청구 통화 인수, tool 표 반환 필드, transcript 보관 버퍼 제거). 전부 반영.
- Round 3: CHANGES_REQUESTED. MAJOR 2(4.1과 4.4 수신 매칭 규칙 불일치 및 destination 보조 매칭의 가로채기 위험, barge-in 이후 재시도/다중 조각으로 agent 발화 재개와 미보고 barge-in), MINOR 10(판정 창 시작과 talk 구간 제외, listen 기준점, groupcall hangup race, linear 실패 판정, 슬롯 예약, run_stdio_async 기반 종료 단순화, supervisor 재시작과 종료 세션 보존, 마커 조회 제거, 인수 실패 처리, 절 순서와 anyio 대기). 전부 반영.
- Round 4: APPROVED. MINOR 9(수신 talk 실패 우선순위, groupcall 과설계 축소, 재-hangup shield와 인용 위치, stale_speaking_ids, 긴 발화 still_speaking, SIGTERM 진행 중 POST와 미청구 통화, 활성 수신 중 disable, listen timeout reconcile, 표 정합성과 9절 종결). 전부 반영.
- Round 5: APPROVED. MINOR 8(3절 문구, 테스트 문구, groupcall 보조 GET 대상과 404, 대화 tool 취소 규칙, 블로킹 상한 근거 정합 130초와 wait 기본 120초, 수신 backstop 근거 채널 timeout, 4.2 필드 목록 동기화, 첫 wait 직전 도착 통화). 전부 반영.
- 결과: Round 4, 5 연속 APPROVED로 디자인 리뷰 루프 종료.

## 11. Implementation notes (구현 중 해소한 모호점)

디자인 본문은 변경하지 않았고, 구현 시 명시가 없던 부분만 아래와 같이 정했다.

1. `POST /calls/{id}/talk`는 본문 없이 200을 반환한다(`bin-api-manager/server/calls.go:349` `c.Status`). 공용 `VoIPbinClient.post`는 성공 응답을 JSON으로 디코드하므로, phone 패키지의 `api_post`가 성공 후 디코드 실패만 `{}`로 처리한다(client.py 무변경).
2. speaking 생성 거부의 "이미 active" 판정: 오류가 RPC를 거쳐 오므로 메시지에 `already`가 있거나 status 409/500이면 해당으로 본다. 재시도 조건(같은 세대, `stale_speaking_ids` 비어 있지 않음, 1회)은 4.2 그대로다.
3. "interim 진행 중"(listen timeout 연장, 턴 종료 판단 보조)의 정의: 마지막 transcript보다 새로운 interim이 `interim_active_seconds = 2.0`초 안에 있었던 경우. 상한 도달 시 heard가 비어 있으면 `timed_out: true`, interim 진행 중이었으면 `truncated: true`도 함께 표시한다. timeout 시 reconcile로 transcript를 회수하면 그것을 heard로 반환하고 `timed_out: false`로 둔다. 회수 즉시 반환하며(이미 지난 침묵을 다시 기다리지 않음), `truncated`는 reconcile 직전에 interim이 진행 중이었을 때만 true다(회수 자체로는 true가 되지 않음). reconcile은 `reconcile_timeout = 3.0`초로 제한하고, `phone_say_and_listen`은 이 몫을 130초 상한 안에 미리 남겨 둔다.
4. signal handler는 /ws 연결을 여는 첫 phone tool(`phone_call_start`, `phone_wait_incoming`) 시점에 등록한다. 통화가 생길 수 있는 첫 시점이며, phone tool을 쓰지 않는 프로세스의 기존 동작이 바뀌지 않는다는 4.6의 의도와 같다.
5. `deadline`(최대 통화 시각)은 응답 시각 + `max_duration_seconds`로 계산한다(발신 `sleep`도 응답 후 flow 실행 시점부터 흐름).
6. groupcall 응답 대기의 1초 보조 폴링은 `GET /groupcalls/{id}`와 함께 아직 종료가 확인되지 않은 후보 call의 `GET /calls/{id}`도 조회한다("후보 판정에서 404 call은 종료로 센다" 규칙을 적용하기 위함).
7. 미청구 수신 통화는 grace 만료 시점에 대기 맵에서 먼저 제거한 뒤(그 사이 새 대기자가 가져가지 못하게) shield 안에서 `GET`(ringing 확인) 후 hangup한다.
8. 반환 형식 보충: `phone_incoming_configure`는 `enabled` 필드를 추가하고, `enabled=false`의 `previous_call_flow_id`는 제거한 MCP flow id다. `phone_call_start`/`phone_wait_incoming` 결과에 `events_connected`를 함께 넣고, `phone_say_and_listen`은 `still_speaking`을 항상(true/false) 넣는다.
9. 시간 의존 상수, 시계, 발화 시간 추정 함수는 `voipbin_mcp.phone.PhoneConfig`로 주입한다. 정리 경로의 5초는 4.7 그대로 `anyio.move_on_after(5, shield=True)` 리터럴이다.
10. `PINNED_CLAIMS`에는 Go 근거가 있는 백엔드 사실 2건을 고정했다: `phone_call_start`의 1시간 채널 상한(`start.go:289`, `:239`, `main.go:177`), `phone_wait_incoming`의 MCP 부재 시 ringing 잔존(`start.go:239`, `main.go:177`). "STT/TTS는 응답 후 생성해야 동작"은 실측 사실(분석 10절)로 Go 행 근거가 없어 docstring의 백엔드 주장으로 쓰지 않고 코드 주석에만 둔다.
11. `main()`이 `anyio.run(_serve)`로 바뀌어 기존 `test_client_contract.py`의 startup 테스트는 `mcp.run` 대신 `mcp.run_stdio_async`를 stub한다. `scripts/fault_matrix.py`의 가짜 서버 tool 수 리터럴 58은 소스의 `@mcp.tool()` 수에서 계산하도록 바꿨다(66개가 되면서 정상 케이스가 모두 실패하던 문제).
12. shield는 정리 진입점에만 둔다: `PhoneSession._stop_speaking`, `PhoneSession.hangup`, speaking 생성(`_post_and_record`, POST와 id 기록을 `CancelScope(shield=True)`로 묶음), `SessionManager._cleanup_outgoing`, `_reject_if_ringing`, `shutdown`, configure 롤백, `call_start`의 POST. 이들이 부르는 `_send_stop`, `_hangup_now`, `_hangup_groupcall_now`, `_reject_now`는 shield 없는 내부 함수다. 중첩 shield는 바깥 shield가 있으면 관측 불가능한(테스트로 구분되지 않는) 중복이므로, 각 shield가 어떤 경로의 유일한 보호가 되도록 정리했다(통화 종료 시 speaking 정지는 id별 `_stop_speaking` task로 바뀌어 `_stop_all`은 제거). speaking 생성 응답이 통화 종료 또는 barge-in 이후에 도착하면 그 id는 사용하지 않고 즉시 정지한다.
13. `shutdown`이 시작되면(`_closing`) `phone_call_start`/`phone_wait_incoming`은 `reason: "shutting_down"`으로 거절되고, 대기 중인 `phone_wait_incoming`은 새 통화를 받지 않고 같은 reason으로 끝난다.
14. 대기자가 받지 않은 ringing 통화(대기 시작 전 버퍼 분, 취소나 다른 통화 수락으로 남은 후보)는 대기 종료 시 미청구 경로(grace 후 ringing 재확인 거절)로 넘긴다. 버퍼 분은 상태 확인 GET이 끝난 뒤에만 버퍼에서 뺀다.
15. idle watchdog 기준 시각은 응답 시점이다(`phone_call_start`/`phone_wait_incoming` 반환 직전 `touch`). `phone_say_and_listen`의 130초 상한, `phone_listen`의 timeout, `phone_say(wait=true)`의 120초 대기 상한은 lock 대기와 say 요청 시간을 포함해 tool 호출 시점부터 센다.
16. `shutdown`은 같은 5초 shield 예산 안에서, 통화 정리 후 이미 돌고 있는 background 정리 task(종료/barge-in 후 speaking stop, grace가 지나 거절 중인 미청구 통화, watchdog hangup)를 취소하지 않고 끝날 때까지 기다린 뒤, 정지가 확인되지 않은 speaking(`stale_speaking_ids`)을 한 번 더 stop한다. 프로세스가 shutdown 직후 종료되므로 이 task들이 중간에 끊기지 않게 하기 위함이다.
