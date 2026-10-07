# VOIP-1574 디자인: 외부 AI agent의 실시간 전화 대화 tool (voipbin/mcp)

- 티켓: VOIP-1574 (후속: VOIP-1575 최대 통화 24시간 백엔드)
- 선행 문서: `2026-10-07-agent-live-phone-conversation-analysis.md` (분석 리뷰 Round 5, 6 연속 APPROVED, 9절 발신 실측, 10절 수신 실측)
- 대상 repo: voipbin/mcp 단독, PR 1개
- 상태: 디자인 리뷰 대기

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
- extension 목적지 발신은 `groupcalls`로 생성되어 call_id를 `GET /groupcalls/{id}`의 `call_ids`로 얻는다.
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
  - `status` 키와 `direction` in (incoming, outgoing) → `CallEvent(id, status, direction, destination, source, hangup_reason, hangup_by, groupcall_id)`
  - 그 외 → 무시(debug 로그).
- dedupe: 최근 키 LRU(4096개). 키는 call=`("call", id, status)`, transcript=`("transcript", id)`, interim=`("interim", id)`(interim id는 이벤트마다 새 UUID).
- 라우팅: `CallEvent`는 `call_id`로, transcript/interim은 `transcribe_id`로 세션을 찾는다. 수신 대기자(`IncomingWaiter`)는 `CallEvent(status=ringing, direction=incoming)`를 `destination.target`으로 매칭. 매칭되지 않는 이벤트는 버린다. 단 call_id 미확정 발신 세션을 위해 call 이벤트는 30초 보관 버퍼(최대 256개)에 남긴다(4.3 race 처리).
- 재연결: 연결 끊김 시 지수 backoff(0.5s→최대 10s)로 재연결, 재구독. 재연결 직후 각 활성 세션에 `reconcile()`을 호출해 `GET /calls/{id}`(상태)와 `GET /transcripts?transcribe_id=`(누락 transcript, id로 dedupe)로 보정한다.
- 서버 ping(10초)에는 websockets 라이브러리가 자동 pong.
- 구독 ack가 없으므로 subscribe 전송 후 0.3초 동안 연결이 유지되면 준비 완료로 본다(잘못된 topic이면 서버가 연결을 끊음).
- reconcile의 `GET /transcripts`는 `page_size=100`과 `page_token`을 끝까지 따라간다.

### 4.2 PhoneSession (`phone/session.py`)

상태: `dialing → answered → ended` (수신은 `ringing → answered → ended`).

필드: `call_id`, `direction`, `language`, `voice_id`, `transcribe_id`, `speaking_id`, `stt_provider`, `transcripts: deque`, `last_interim_at`, `speaking_until`(추정 재생 종료 시각), `ended_reason`, `deadline`(최대 통화 시각), `last_tool_at`.

응답 후 미디어 시작(`start_media()`):
1. `POST /transcribes {reference_type: call, reference_id, language, direction: "in", provider: "aws"}`. 응답의 `provider`는 요청값이 그대로 저장된 것이라(`bin-transcribe-manager/pkg/transcribehandler/start.go:294`, 실제 fallback은 이후 비동기 `runSTT`에서 일어나며 레코드 미갱신) 실제 provider 판별에 쓸 수 없다. 대신 휴리스틱: 통화 진행 중 마지막 interim/transcript 이후 `STT_SILENT_WARN_SECONDS = 240` 이상 아무 STT 이벤트가 없으면 listen/status 결과에 `stt_silent_seconds`를 넣어 agent가 전사 중단 가능성을 알 수 있게 한다.
   - `start_media` 직후 1회 `reconcile()`로 transcribe_id 확정 전에 도착해 버려진 transcript를 회수한다. transcript/interim도 call 이벤트처럼 30초 보관 버퍼에 두고 transcribe_id 확정 시 재라우팅한다.
2. `POST /speakings {reference_type: call, reference_id, language, voice_id?, direction: "out"}`.
3. 둘 중 하나라도 실패하면 통화를 끊고 오류 반환(반쪽 대화 금지).

말하기 `say(text)`:
- `text.py`로 UTF-8 5000바이트 이하 조각으로 분할(문장 경계 우선, 없으면 바이트 경계에서 UTF-8 안전 분할). 조각마다 `POST /speakings/{id}/say`.
- `speaking_until = max(now, speaking_until) + estimate(text)`. 추정치: 영어 계열 15자/초, CJK 6자/초, 최소 1초 + 고정 지연 1초. 재생 완료 이벤트가 없어(G1) 추정만 쓴다.
- speaking 세션이 없으면(barge-in으로 stop된 경우) 새로 생성 후 say.

끊기(barge-in):
- 실행 주체: EventHub의 라우팅 콜백(백그라운드)이 판정과 stop을 수행한다. 세션 lock은 잡지 않는다(listen 대기 중에도 즉시 끊어야 함). 판정 시점에 `old_id = speaking_id`를 캡처하고 즉시 `speaking_id=None`으로 바꾼 뒤 `old_id`로 stop을 호출한다. 다음 say는 `speaking_id`가 None이면 새 세션을 만든다(say는 세션 lock 안에서 실행).
- 오탐 완화: interim message가 2단어 이상 또는 4자 이상일 때만 끼어듦으로 본다.
- `barge_in` 설정은 세션 속성으로 유지되며 say/say_and_listen 호출 시 인자로 갱신한다.
- `say` 이후 `speaking_until` 전에 `InterimEvent`(위 조건 충족)가 오면 상대가 끼어든 것으로 본다. 해당 턴 결과에 `barge_in: true`를 표시하고 `POST /speakings/{id}/stop` 후 `speaking_id=None`, `speaking_until=now`. 다음 say 때 새 세션을 만든다(실측: 생성+say 0.6초, 첫 오디오까지 추가 0.3~0.9초).
- 자동 barge-in은 `barge_in` 인자로 끌 수 있다(기본 켜짐). 끄면 agent 말을 끝까지 재생하고 그 사이 상대 발화는 그대로 전사·버퍼링된다.

듣기 `listen(timeout, end_silence)`:
- 버퍼에 이미 transcript가 있으면 그것부터 사용. say_and_listen에서는 say 이전에 쌓인 잔여 transcript를 `earlier_heard` 필드로 분리해 반환하고 `heard`에는 say 이후 발화만 넣는다.
- 첫 transcript 수신 후 `end_silence`(기본 0.8초) 동안 새 transcript나 interim이 없으면 턴 종료로 보고, 버퍼의 transcript를 공백으로 이어 반환한다(한 발화가 여러 transcript로 쪼개지는 실측 반영).
- interim이 오고 있으면 턴 종료를 미룬다(상대가 아직 말하는 중).
- `timeout`(기본 30초, 상한 120초) 안에 아무 transcript도 없으면 `heard: ""`, `timed_out: true`. 단 timeout 시점에 interim이 진행 중이면 최대 5초 연장해 발화 확정을 기다린다.
- `end_silence_ms` 범위 300~3000.
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
4. 취소 안전 등록: POST /calls 응답을 받는 즉시(어떤 await보다 먼저) `pending` 세션을 등록한다. 등록에는 `call_id` 또는 `groupcall_id`를 담는다. POST부터 등록까지는 `asyncio.shield`로 보호한다. 이후 어느 단계에서든 `CancelledError`(Claude Code Esc의 `notifications/cancelled`, EOF 취소)나 예외가 나면 정리 후 재전파: groupcall이면 `POST /groupcalls/{id}/hangup`, 아니면 `POST /calls/{id}/hangup`.
5. call_id 확정:
   - 응답 `calls[0].id`가 있으면 그것.
   - 없으면 groupcall: `GET /groupcalls/{id}`를 0.25초 간격으로 polling하며 `call_ids` 전체를 후보 집합으로 유지(ring 방식이 linear면 점진적으로 늘어남). 후보 중 처음 `call_progressing`이 온 call을 확정하고, 보조로 `answer_call_id`를 확인한다. 후보 전원이 hangup되었을 때만 실패.
   - 응답 대기: 보관 버퍼와 이후 이벤트에서 `call_progressing` 대기, 보조로 1초마다 GET. 단일 call이 `call_hangup`이면 `hangup_reason`과 함께 실패 반환. `answer_timeout` 초과면 4의 정리 경로로 hangup 후 `no_answer`.
6. `start_media()`. 실패 시 hangup 후 오류.
7. 결과: `{call_id, status: "answered", max_duration_seconds, hint}`.

### 4.4 수신 흐름

수신 준비 tool `phone_incoming_configure(number_id, enabled, restore_call_flow_id="", clear=false)`:
- MCP flow는 번호마다 1개. 이름 `voipbin-mcp incoming <number_id>`, `detail`에 JSON `{"voipbin_mcp": "incoming", "number_id": ..., "previous_call_flow_id": ...}`를 저장해 원래 flow를 서버 측에 보존한다(agent 컨텍스트가 사라져도 복구 가능). 이름 탐색은 하지 않는다.
- `enabled=true`:
  1. `GET /numbers/{id}`로 현재 `call_flow_id`를 읽는다.
  2. 그 flow가 이미 MCP flow(`GET /flows/{id}`의 detail 마커 확인)면 아무것도 바꾸지 않고 detail의 `previous_call_flow_id`를 그대로 반환한다(재호출해도 원래 flow를 잃지 않음).
  3. 아니면 `POST /flows {name, detail, actions: [{"type":"sleep","option":{"duration":3600000}}]}`(answer 없음)로 만들고 `PUT /numbers/{id}/flow_ids {call_flow_id}`(부분 갱신, message_flow_id 유지)로 연결한다.
- `enabled=false`:
  1. 현재 flow가 MCP flow가 아니면 오류(이미 해제됨).
  2. 복원 대상: `restore_call_flow_id`가 있으면 그것, 없으면 detail의 `previous_call_flow_id`. 저장값이 nil이면 `clear=true`가 명시된 경우에만 nil로 해제하고, 아니면 오류(조용한 수신 라우팅 해제 방지).
  3. `PUT /numbers/{id}/flow_ids`로 복원 후 MCP flow 삭제.
- docstring에 "번호의 기존 call flow를 대체하며, MCP가 실행 중일 때만 수신이 처리된다"를 명시한다.
- 한 번호에는 MCP 프로세스 1개만 수신 대기해야 한다(README 명시).

수신 대기 tool `phone_wait_incoming(number_id, timeout_seconds=300, greeting="Hello.", greeting_language="en-US", language="en-US", voice_id="", max_duration_seconds=3600)`:
1. `number_id`로 번호를 조회해 E.164 문자열을 얻는다(configure와 인자 대칭, 표기 정규화는 서버 값 기준). 번호당 대기자 1명. 이미 대기 중이면 오류. 세션 한도 도달 시 오류.
2. `call_created`(direction=incoming, status=ringing, destination.target == 번호) 대기. 매칭은 앞의 `+`를 제거한 숫자 문자열 비교. timeout이면 `timed_out`.
3. 감지 즉시 `pending` 세션 등록(취소 안전, 4.3의 4와 동일 규칙, 정리 수단은 `POST /calls/{id}/hangup`).
4. `POST /calls/{id}/talk {text: greeting, language: greeting_language}`로 응답 겸 인사. talk는 gcp/aws 음성 체계라 speakings(ElevenLabs)의 `voice_id`를 넘기지 않는다. greeting은 비울 수 없다(talk가 응답 수단). 인사 추정 재생 시간을 `speaking_until`에 반영해 첫 say와 겹치지 않게 하고 barge-in 판정에도 쓴다.
5. `call_progressing` 확인(최대 5초, 보조 GET) 후 `start_media()`(응답 후 생성 규칙).
6. 3~5 중 어느 단계든 실패/취소 시 `POST /calls/{id}/hangup` 후 오류 반환.
7. 결과: `{call_id, status: "answered", caller: source}`.

대기자 없는 수신 통화(분석 10절 설계 함의): EventHub가 실행 중이고 이 프로세스가 configure했거나 현재 call_flow_id가 MCP flow인 번호로 incoming ringing 통화가 왔는데, `UNCLAIMED_INCOMING_GRACE_SECONDS = 15` 안에 대기자가 잡지 않으면 MCP가 `POST /calls/{id}/hangup`으로 거절한다. 대기 tool 재호출 사이의 짧은 공백은 grace로 흡수하고, 다른 통화 중이거나 한도 도달 시 발신자가 장시간 ringing에 남지 않게 한다. MCP 프로세스가 없으면 거절 주체도 없으므로 백엔드 동작(채널 1시간 timeout, `bin-call-manager/pkg/callhandler/start.go:230-241`)에 맡긴다(D6).
- 수신 대화 최대 길이: 응답 후에는 flow sleep(1시간)과 채널 1시간 timeout이 상한이고, `max_duration_seconds`가 짧으면 MCP 타이머로 hangup한다.

### 4.5 동시성과 한도

- 동시 활성 세션 상한 `MAX_SESSIONS = 4`(상수). 초과 시 `phone_call_start`/`phone_wait_incoming`은 오류.
- 세션별 `asyncio.Lock`으로 같은 call_id에 대한 say/listen 동시 호출을 직렬화한다. `phone_hangup`과 `phone_status`는 lock 없이 즉시 처리(listen 대기 중에도 끊을 수 있어야 함).
- 모든 tool은 `async def`. 블로킹 대기는 `asyncio.wait_for`.
- mcp 의존성 하한을 `mcp>=1.27.0,<2`로 올린다. 근거: 1.2.0은 직렬 처리(실측), 1.26 이하는 EOF 뒤에도 진행 중 listen/wait(최대 120/600초)를 끝까지 기다려 종료 정리가 늦고 그 사이 수신 응답 위험. `scripts/dist_meta.py`가 floor를 자동 추출하므로 CI floor leg가 1.27.0으로 따라온다.

### 4.6 고아 통화 방지 (G7)

| 계층 | 장치 |
|---|---|
| 1 | 최대 길이 타이머: `deadline` 도달 시 MCP가 hangup. |
| 2 | 무활동 watchdog: 세션에 대한 tool 호출이 `IDLE_HANGUP_SECONDS = 300`(상수) 동안 없으면 hangup. listen 대기 중은 활동으로 본다. |
| 3 | 정상 종료: (a) SIGTERM/SIGINT: 첫 phone tool 호출 시 실행 중 loop에 `loop.add_signal_handler`를 등록해, 같은 loop와 같은 client로 전 세션 hangup(5초 제한) 후 종료한다(Python 기본 SIGTERM은 finally 없이 즉시 종료하므로 필수). (b) stdin EOF: mcp 1.27.0부터 EOF 시 진행 중 핸들러를 취소한다(`lowlevel/server.py` `tg.cancel_scope.cancel()`, 1.26 이하에는 없음 확인). 취소된 tool은 4.3/4.4의 취소 정리로 자기 통화를 끊고, `mcp.run()` 반환 후 `main()`의 finally가 남은 세션을 새 event loop + 새 VoIPbinClient로 hangup한다(보조). |
| 4 | 최종 backstop: SIGKILL 등으로 위 장치가 모두 실패하면 발신은 `sleep`(max_duration) 후 서버가 종료, 수신은 flow sleep(1시간) 후 종료. |

watchdog과 deadline은 EventHub와 분리된 supervisor task에서 1초 주기로 검사한다(EventHub가 재연결 중이거나 죽어도 동작). 백그라운드 task 참조는 SessionManager가 강하게 보유하고, lazy start는 `asyncio.Lock`으로 1회만 실행한다. EventHub가 죽어 재시작 중이면 tool 결과에 `events_connected: false`를 노출한다.

## 5. Tool 명세

공통: 결과는 JSON 문자열(`format_response`). 오류는 기존 `VoIPbinAPIError` 메시지를 그대로 노출하거나 `{"error": "...", "reason": "..."}` 형태. 모든 phone tool docstring 첫 줄에 "live phone conversation" 용도와 비용 발생(통화, STT, TTS 과금)을 명시한다.

| tool | 인자 | 동작 | 반환 |
|---|---|---|---|
| `phone_call_start` | `source_number`, `destination_type`, `destination_target`, `language="en-US"`, `voice_id=""`, `max_duration_seconds=3600`, `answer_timeout_seconds=60` | 4.3 | `call_id`, `status` |
| `phone_incoming_configure` | `number_id`, `enabled`, `restore_call_flow_id=""`, `clear=false` | 4.4 | `number`, `call_flow_id`, `previous_call_flow_id` |
| `phone_wait_incoming` | `number_id`, `timeout_seconds=300`, `greeting="Hello."`, `greeting_language="en-US"`, `language="en-US"`, `voice_id=""`, `max_duration_seconds=3600` | 4.4 | `call_id`, `caller`, `status` 또는 `timed_out` |
| `phone_say_and_listen` | `call_id`, `text`, `listen_timeout_seconds=30`, `end_silence_ms=800`, `barge_in=true` | say 후 listen. 1턴 = 1호출 | `heard`, `earlier_heard`, `barge_in`, `timed_out`, `call_ended`, `during_agent_speech`, `stt_silent_seconds?` |
| `phone_say` | `call_id`, `text`, `wait=false`, `barge_in=true` | say. `wait=true`면 `speaking_until`까지(또는 barge-in까지) 대기 | `queued`, `estimated_seconds`, `barge_in?` |
| `phone_listen` | `call_id`, `timeout_seconds=30`, `end_silence_ms=800` | listen | `heard`, `timed_out`, `call_ended` |
| `phone_hangup` | `call_id` | 4.2 종료 | `status: ended` |
| `phone_status` | `call_id=""` | 하나 또는 전체 세션 요약 | 상태, 경과, 남은 시간, 버퍼 transcript 수 |

인자 범위 검증은 tool 진입 시 수행하고 위반 시 API를 호출하지 않는다. `max_duration_seconds` 30~3600, `answer_timeout_seconds` 5~60(서버 dial timeout 60초, `defaultDialTimeout`), say `text`는 빈 문자열 거부, 1회 호출 총 20000바이트 상한(비용 안전). `listen_timeout_seconds`/`timeout_seconds` 상한 120초(실측: Claude Code 130초 블로킹 정상이나 client별 timeout 차이를 고려), `phone_wait_incoming.timeout_seconds` 상한 600초(장시간 대기는 반복 호출 권장, docstring 명시).

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
| `src/voipbin_mcp/server.py` | `main()`에 종료 정리(4.6 계층 3) 추가 |
| `pyproject.toml` | `mcp>=1.27.0,<2`, `websockets>=13,<16` 추가 |
| `uv.lock` | 갱신(`uv lock --check` CI 통과) |
| `.github/workflows/ci.yml` | floor leg에 websockets 하한 고정 추가 |
| `tests/test_phone_text.py` | 신규 |
| `tests/test_phone_events.py` | 신규. 로컬 websockets 서버로 연결/구독/정규화/dedupe/재연결 |
| `tests/test_phone_session.py` | 신규. EventHub 주입 가짜 이벤트 + respx로 say/listen/barge-in/hangup/종료 정리 |
| `tests/test_tools_phone.py` | 신규. tool 인자 검증, 발신(일반/groupcall), 수신, 한도 |
| `tests/golden_docstrings.json` | 신규 tool docstring 추가(`scripts/update_golden_docstrings.py`) |
| `README.md` | tool 표에 Phone 행, "Live phone conversations" 절(사용 예, 비용, 수신 설정, 한 번호당 MCP 프로세스 1개), Claude Code 사용 주의(phone tool allowlist 권장: 권한 승인 대기도 대화 지연과 idle 시간에 포함), interim 이벤트가 고객 webhook으로도 대량 발송되는 비용 영향, Known limitations 추가 |

websockets 버전: `additional_headers` 인자를 쓰는 신규 asyncio client(`websockets.asyncio.client.connect`)는 13.0부터 제공. Python 3.10~3.13 지원 범위와 호환. CI floor leg에 `websockets==13.*` 고정을 추가해 하한을 검증한다(`.github/workflows/ci.yml`).

## 7. 테스트 계획

단위(CI, 네트워크 없음):
- text: 5000바이트 분할(한국어 다바이트 경계), 문장 경계 우선, 추정치.
- events: 로컬 WS 서버로 Cookie/query 인증 헤더, 구독 메시지 형식(4파트 topic 5개, 3파트 없음), payload 정규화 4종, `(id,status)` dedupe(같은 call의 progressing과 hangup은 둘 다 전달), 연결 끊김 후 재구독과 reconcile 호출.
- session: listen 턴 병합(0.5초 간격 2 transcript → 1턴), interim 중 턴 종료 지연, timeout, call_ended 즉시 반환, barge-in 시 stop 호출과 다음 say의 speaking 재생성, `barge_in=false`, during_agent_speech 표시, hangup 시 speaking stop 오류 무시, call_hangup 이벤트 정리, `stt_silent_seconds` 휴리스틱, transcribe_id 확정 전 도착 transcript 재라우팅.
- tools: 발신 body(sleep duration ms, extension의 target_name), groupcall call_id polling, answer_timeout hangup, 수신 매칭(다른 번호/outgoing 무시), greeting 빈 값 거부, 대기자 중복 거부, MAX_SESSIONS, 인자 범위 위반 시 무호출, incoming_configure의 previous flow 반환과 원복.
- manager: idle watchdog, deadline, shutdown 시 전 세션 hangup, supervisor가 EventHub 장애와 독립 동작.
- 취소: `phone_call_start`가 POST 직후/응답 대기 중 취소되면 hangup(단일 call은 calls hangup, groupcall은 groupcalls hangup), `phone_wait_incoming`이 talk 이후 취소되면 hangup.
- groupcall: 두 번째 leg가 응답한 경우 그 leg로 확정, 모든 leg hangup 시에만 실패.
- 수신: 대기자 없는 incoming은 grace 후 hangup, configure 재호출 시 previous 보존, disable 시 detail 복원과 MCP flow 삭제, 저장값 nil이면 clear 없이 오류.
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

## 9. 미결 사항 (리뷰어 판단 요청)

1. 단일 WS 연결 + payload 형태 판별 vs 이벤트 타입별 연결 분리. 본 설계는 단일 연결(구독 topic을 5개로 한정하여 판별 모호성 제거).
2. `MAX_SESSIONS = 4`, `IDLE_HANGUP_SECONDS = 300`, `UNCLAIMED_INCOMING_GRACE_SECONDS = 15` 기본값의 타당성.
3. `phone_incoming_configure`가 번호의 기존 flow를 대체하는 방식의 안전성.

## 10. 리뷰 이력
- Round 1: CHANGES_REQUESTED. MAJOR 7(stt_warning 무효, mcp 1.6~1.26 EOF 종료 정리 불성립과 SIGTERM, tool 취소 시 고아 통화, groupcall 다중 leg, configure의 설정 유실, 대기자 없는 수신 통화, 수신 부분 실패 정리와 voice_id 체계 혼용), MINOR 10. 전부 반영.
