# VOIP-1574 이슈 분석: 외부 AI agent의 실시간 전화 대화 (MCP)

- 티켓: VOIP-1574
- 단계: 이슈 확인/분석 (Phase 0.8)
- 기준 코드: voipbin/mcp `41d96a2`, voipbin/monorepo `4fab6caf7` (main)
- 작성일: 2026-10-07

## 1. 요구사항 (대표님 확정)

Claude Code 같은 외부 AI agent가 VoIPBin MCP 서버의 tool만으로 직접 사람에게 전화를 걸고, 직접 말하고(TTS) 직접 듣는(STT) 대화를 하도록 한다.

- 확정: A안. voipbin/mcp 서버가 통화 세션(통화, STT, TTS, 이벤트 구독)을 관리하고 agent에게 턴 기반 tool을 제공한다.
- 제외: B안(pipecat 파이프라인에 외부 agent를 LLM으로 연결). VoIPBin 서비스 구성과 맞지 않는다는 대표님 판단.
- 폐기: VoIPBin 내부 AI(ai_talk)에게 통화를 위임하는 원샷 tool. 요구사항과 다름.

## 2. 이슈 유효성

유효하다. 현재 MCP에는 통화 중 말하기/듣기 수단이 전혀 없다.

- `src/voipbin_mcp/tools/calls.py`: `list_calls`, `get_call`, `create_call(source_type, source_target, destination_type, destination_target, flow_id="")`, `hangup_call`만 존재.
- `create_call(source_type, source_target, destination_type, destination_target, flow_id="")`은 inline `actions`를 받지 않는다 (calls.py:41-70).
- MCP 전체에 speakings, transcribes, websocket 관련 코드 없음 (grep 확인).

## 3. 백엔드 구성 요소 확인 결과 (코드 근거)

### 3.1 운영 환경 가용성 (실측)
- 운영 `https://api.voipbin.net/v1.0`에서 검증용 accesskey로 `GET /speakings`, `GET /transcribes`, `GET /extensions` 모두 HTTP 200 (2026-10-07 실측).
- api-manager의 /speakings, /transcribes 핸들러는 `hasPermission(CustomerAdmin|CustomerManager)` 검사이며 accesskey는 CustomerAdmin으로 취급되어 허용 (`bin-api-manager/pkg/servicehandler/speaking.go:38`, `transcribe.go:153` TranscribeStart, `models/auth/auth.go:189-190`).

### 3.2 듣기 (STT): 사용 가능
- `POST /transcribes` reference_type=call, direction=in|out|both(기본 both). call 상태 dialing/ringing/progressing에서만 허용 (`bin-transcribe-manager/pkg/transcribehandler/start.go:61-66` 정규화, `:159-160` 상태 검사).
- 같은 call+language로 진행 중 transcribe가 있으면 409 (`bin-transcribe-manager/pkg/transcribehandler/start.go:248-265`).
- `provider`(gcp|aws) 지정 가능 (`bin-api-manager/server/transcribes.go:49-51`). 지정 provider를 먼저 시도하고, 미초기화/무효면 경고 로그만 남기고 기본 순서(GCP→AWS)로 조용히 fallback (`bin-transcribe-manager/pkg/streaminghandler/start.go:100-125`).
- 이벤트 (`PublishWebhookEvent`로 고객에게 전달):
  - `transcribe_speech_started` / `_interim` / `_ended`: payload `streaming.WebhookMessage` (id, customer_id, transcribe_id, streaming_id, direction, message(omitempty), tm_event, tm_create).
  - `transcript_created`: final 결과마다 즉시 발행 (`bin-transcribe-manager/pkg/streaminghandler/result.go:65-87`, `bin-transcribe-manager/pkg/transcripthandler/transcript.go:46`). payload: id, transcribe_id, direction, message, offset_ms, tm_create.
- 발화 종료 판정은 자체 VAD 없이 provider(GCP/AWS) endpointing에 전적으로 의존 (`bin-transcribe-manager/pkg/streaminghandler/result.go:48-72`). 설정 가능한 timeout 없음.
- 통화 hangup 시 transcribe 자동 Stop (`bin-transcribe-manager/pkg/transcribehandler/event.go:51-80`).
- direction 의미: snoop spy 방향. `in` = 상대방 음성(채널로 들어오는 오디오)으로 추정. TTS를 whisper `out`으로 주입하면 `in` 전사에는 섞이지 않을 것으로 추정 (Asterisk 동작, 실측 필요).

### 3.3 말하기 (TTS): 사용 가능, 단 제약 존재
- `POST /speakings` (reference_type=call, direction, language, provider, voice_id). 기본 direction은 `none`(`""`) (`bin-api-manager/server/speakings.go:116-128`).
- 원격 상대가 들으려면 direction=`out` 필요 (`bin-tts-manager/models/streaming/streaming.go:43-46` 주석: out = "the call can hear the streaming"). 기본 none은 무음 추정. MCP는 반드시 `out`을 명시해야 한다.
- `say`: 최대 5000바이트(Go `len`, UTF-8 기준. 한국어 약 1666자). MCP의 say 분할 기준도 바이트로 한다. 서버 오류 문구는 characters로 표기되지만 실제 기준은 바이트(`bin-tts-manager/pkg/speakinghandler/speaking.go:187-188`). active 상태에서만, 서버 큐 없이 ElevenLabs WS로 즉시 전송 (`bin-tts-manager/pkg/speakinghandler/speaking.go:178-200`, `bin-tts-manager/pkg/streaminghandler/elevenlabs.go:388-410`).
- `flush`: ElevenLabs에 빈 text+flush 전송뿐. 이미 생성된 오디오는 계속 재생될 수 있음 (코드 TODO `bin-tts-manager/pkg/streaminghandler/say.go:88`). 즉 barge-in 시 즉시 중단 보장 안 됨.
- `stop`: externalmedia 중단 + status stopped. 즉시 무음화 수단은 stop.
- 같은 reference에 active 세션이 있으면 생성 거부 (`bin-tts-manager/pkg/speakinghandler/speaking.go:38-54`).
- provider 기본 elevenlabs, 모델 `eleven_multilingual_v2`, voice는 voice_id > activeflow 변수 > language 매핑 > 기본 순 (`bin-tts-manager/pkg/streaminghandler/elevenlabs.go:463-484`).
- 통화 hangup 시 speaking 레코드 자동 정리 없음: tts-manager에 call hangup 구독자(subscribehandler)가 없고, Asterisk WS 종료(`ConnAstDone`) 시 streaming만 정리되며 speaking DB status는 갱신되지 않는다. MCP가 종료 시 명시적으로 stop 해야 한다. `speakinghandler.Stop`은 streaming stop 오류를 이미 로그만 남기고 무시한 뒤 status를 stopped로 갱신한다(`bin-tts-manager/pkg/speakinghandler/speaking.go:246-280`). 따라서 MCP는 speaking not found나 DB 오류만 처리하면 된다.

### 3.3.1 대안 TTS 경로 비교: `POST /calls/{id}/talk`
- 동작: call-manager가 tts-manager로 wav 파일을 합성(요청 timeout 10초)한 뒤 Asterisk Play로 채널에 재생 (`bin-call-manager/pkg/callhandler/media.go:19-72`, `bin-api-manager/server/calls.go:281-350`). provider는 gcp|aws(기본 GCP, 실패 시 상대 provider fallback). API 경로는 runNext=false로 호출되며(`bin-call-manager/pkg/listenhandler/v1_calls.go:820`), 이때 무작위 playback id를 써서 진행 중 action(sleep)을 진행시키지 않는다(코드 확인: `bin-call-manager/pkg/callhandler/arievent.go:78-81`이 PlaybackFinished의 actionID가 `c.Action.ID`와 다르면 무시. 실측은 청취 여부만).
- 장점: direction 모호성 없음(채널 Play), reference당 1개 제한 없음, 세션 정리(G5) 부담 없음, ElevenLabs 의존 없음.
- 단점: 문장 전체 합성 후 재생이라 지연 큼. 재생 중단용 공개 API 없음(`/calls/{id}/*`는 recording_start/stop, hangup, hold, media_stream, moh, mute, silence, talk뿐). 재생 완료 고객 이벤트 노출 여부 미확인. (`DELETE /calls/{id}`도 있으나 재생 중단 수단 아님.) 부작용: call이 progressing이 아니면 Talk이 먼저 `channelHandler.Answer`를 호출하므로(`media.go:32-38`) progressing 이후에만 사용.
- 판단: barge-in(중단)과 저지연이 필요한 대화 용도로는 `/speakings`를 기본으로 한다. 단 실측 1번에서 speaking `out`이 들리지 않으면 먼저 `POST /calls/{id}/silence`(ARI silence generator, `bin-call-manager/pkg/callhandler/silence.go:12`)로 송출 프레임을 만든 상태에서 재검증하고(bridge 없는 Stasis 대기 채널은 송출 프레임이 없어 whisper가 섞이지 않을 수 있다는 추정), 그래도 실패하면 `/calls/{id}/talk`를 fallback으로 쓴다(이 경우 barge-in 불가를 제약으로 수용). 둘 다 실패하면 7절 에스컬레이션 규칙 적용.
- `POST /calls/{id}/media_stream`(원시 오디오 WS)은 MCP가 STT/TTS를 직접 수행해야 하므로 범위에서 제외.

### 3.4 이벤트 수신 (/ws): 사용 가능, 단 제약 존재
- 인증: `wss://api.voipbin.net/v1.0/ws?accesskey=<key>` (`bin-api-manager/lib/middleware/authenticate.go:480-494`).
- 구독: `{"type":"subscribe","topics":[...]}`. ack 없음, 잘못된 토픽은 연결 종료 (`bin-api-manager/pkg/websockhandler/subscription.go:158-184`). 매칭은 문자열 prefix (`bin-api-manager/pkg/pubsubhandler/subscriber.go:28-33`).
- 토픽 형식: 이벤트 하나당 토픽 두 개가 발행된다 (`bin-api-manager/pkg/subscribehandler/webhookmanager.go:165-272` `createTopics`, 실사용 경로 `processEventWebhookManagerRoutingKeyedEvent`에서 service=resource로 호출 `:142`).
  - 구 형식: `customer_id:<cid>:<resource>:<resource_id>` (resource = event type 첫 `_` 앞, resource_id = 이벤트 객체 자신의 id)
  - 신 형식: `customer_id:<cid>:<service>:<event_type>:<resource_id>` (예: `customer_id:<cid>:transcribe:transcribe_speech_ended:<id>`)
  - 결과 1 (중복): 3파트 prefix(`customer_id:<cid>:transcribe`)는 두 토픽에 모두 매칭되고, broker는 토픽마다 deliver하므로(`bin-api-manager/pkg/pubsubhandler/broker.go:22-31`) 같은 이벤트를 2번 받는다. 3파트 prefix는 사용하지 않는다.
  - 결과 2 (이벤트 타입 구분 가능): 4파트 prefix `customer_id:<cid>:<service>:<event_type>`는 accesskey로 검증 통과(`bin-api-manager/pkg/websockhandler/etc.go:38-87`). 소켓 프레임에는 토픽이 실리지 않으므로(`bin-api-manager/pkg/pubsubhandler/run.go:52`), 필요한 이벤트 타입만 구독하고 타입별로 WS 연결을 분리하면(또는 payload 형태로 판별) 이벤트 타입을 정확히 구분할 수 있다. 예: `transcript:transcript_created`, `transcribe:transcribe_speech_interim`, `call:call_progressing`, `call:call_hangup`.
  - 구독 방침 (3파트 prefix 금지):
    - call: POST /calls 전에 4파트 `customer_id:<cid>:call:call_progressing`, `customer_id:<cid>:call:call_hangup`을 선구독하고 payload `id`로 해당 call만 필터링 (call race 동시 해결). 신 형식 실제 토픽은 5파트 `customer_id:<cid>:call:call_hangup:<call_id>`. 구 형식 정확 토픽 `customer_id:<cid>:call:<call_id>`는 POST 이후에만 가능하므로 보조 수단이며, 4파트 타입 구독과 같은 연결에 함께 쓰면 이벤트 1건이 2회 전달되므로 `(id, status)` 키 중복 제거 필요. POST /calls 응답보다 먼저 도착한 call 이벤트는 call_id를 알 때까지 버퍼링한다.
    - transcript/speech: 4파트 `customer_id:<cid>:transcript:transcript_created`, `customer_id:<cid>:transcribe:transcribe_speech_interim` 등으로 구독하고 payload `transcribe_id`로 클라이언트 필터링.
    - transcribe 리소스 이벤트가 필요하면 `customer_id:<cid>:transcribe:transcribe_done` 등 4파트로 명시 구독.
  - 중복 제거는 여전히 필수: payload에 `owner_id`가 있으면 webhook-manager가 `customer_id.*`와 `agent_id.*` 라우팅 키로 각각 발행하고(`bin-webhook-manager/pkg/webhookhandler/routingkey.go` `createRoutingKeys`), 같은 api-manager pod에서 다른 연결이 `agent_id:` 토픽을 구독 중이면 pod가 두 메시지를 받아 customer 토픽을 두 번 만들 수 있다. 키는 `id`만으로는 부족하다(call의 여러 이벤트가 같은 `id`를 공유하므로 hangup이 버려질 수 있음). 타입별로 분리된 연결 안에서는 `id`, 그 외에는 `(id, status 또는 tm_update)` 또는 payload 해시를 키로 쓴다.
  - 바인딩: WS 구독은 `bin-api-manager/pkg/websockhandler/bindpattern.go`에서 `customer_id.<cid>.#`로 pod queue에 바인딩되므로 4파트 구독도 기능상 문제 없음.
- 소켓 메시지는 봉투 없는 리소스 JSON. **event type 필드 없음** (`bin-webhook-manager/pkg/webhookhandler/routingkey.go` 하단 envelope.Data 발행, `bin-api-manager/pkg/subscribehandler/webhookmanager.go:150`). payload만으로는 speech_started와 speech_ended를 구분할 수 없으나, 위 4파트 토픽 구독으로 구분 가능. payload 형태 보조 판별(보조 수단일 뿐, 주 판별은 4파트 토픽): transcript(`offset_ms` 보유, `streaming_id` 없음), interim(`streaming_id` 보유 + message 비어있지 않음. `message`가 omitempty라 빈 interim은 판별 불가), transcribe 리소스(`status` 보유), call(`status` 필드).
- 고객 단위 prefix 구독 부작용: 같은 고객의 다른 통화(AI 통화 포함) interim/transcript도 모두 유입된다. 구독자 버퍼 2000, 가득 차면 조용히 drop (`bin-api-manager/pkg/pubsubhandler/subscriber.go:43-47`). transcript 유실 대비 `GET /transcripts` 보정 경로가 필요하다. 또 고객에게 webhook_uri가 설정돼 있으면 interim 이벤트가 고객 webhook으로도 대량 발송된다(기존 동작, 이번 작업이 트래픽을 늘림).
- call 구독 race: call_id는 POST /calls 응답 후 알 수 있으므로 구독 전에 progressing으로 넘어갈 수 있다. 위 4파트 이벤트 타입 선구독으로 해결하고, `GET /calls/{id}`로 보정한다. customer_id는 `GET /customer`로 획득.
- 인증 전송: `getAccesskey`는 cookie를 우선 읽는다 (`bin-api-manager/lib/middleware/authenticate.go:480-494`). MCP 기본 transport도 cookie(`client.py:61`, `VOIPBIN_AUTH_TRANSPORT`). WS도 handshake Cookie 헤더를 기본으로 하고 query는 transport=query일 때만 사용(URL 로그 노출 방지).
- speaking/tts 계열 이벤트는 `PublishEvent`(내부 전용)라 고객 /ws, webhook으로 **전달되지 않음** (`bin-tts-manager/pkg/speakinghandler/speaking.go:133,280`, `bin-tts-manager/pkg/streaminghandler/elevenlabs.go:246,253`). 즉 "agent의 말이 재생 완료됐다"를 알 수 있는 고객 이벤트가 없다.
- ping 10초, pong 60초 미수신 시 종료.
- 실측 (2026-10-07): 운영 `wss://api.voipbin.net/v1.0/ws?accesskey=` 연결 0.67초, 3파트 prefix 구독 후 8초간 연결 유지(검증 실패 시 서버가 끊으므로 토픽 유효로 판단). 연결/토픽 검증만 확인했으며 중복 수신, 4파트 구독 동작, 이벤트 수신은 실통화 시 검증 필요.

### 3.5 통화 수명주기
- 응답 = call `status: progressing`, 종료 = `status: hangup` (+hangup_reason, hangup_by) (`bin-call-manager/pkg/callhandler/db.go:288-303,466`).
- action 없는 POST /calls는 응답 직후 flow 종료로 즉시 hangup (`bin-call-manager/pkg/callhandler/action.go:84-86`). 통화 유지를 위해 inline `actions: [{"type":"sleep","option":{"duration":<ms>}}]` 필요. sleep 상한 없음.
- 최대 통화 시간 1시간 하드코딩 (`bin-call-manager/pkg/callhandler/main.go:177`).
- 현재 MCP `create_call`은 inline actions 미지원이므로 새 tool이 POST /calls body를 직접 구성해야 한다.

## 4. 진행 타당성

진행 타당. 백엔드 변경 없이 MCP만으로 기본 대화 루프(발신, 말하기, 듣기, 종료)가 구성 가능하다. 다만 아래 제약은 품질에 영향을 준다.

| # | 제약 | 영향 | MCP 단독 대응 | 백엔드 근본 해결 |
|---|---|---|---|---|
| G1 | TTS 재생 완료 이벤트가 고객에게 없음 | agent 발화가 끝난 시점을 모름. 듣기 시작 시점, 상대 발화가 agent 발화와 겹쳤는지 판단 곤란 | 듣기는 TTS와 무관하게 상시 `in` 전사로 수집하므로 기능상 치명적이지 않음. 필요 시 글자수 기반 추정 | say 단위 재생 완료 이벤트 신설 (기존 message_play_* 는 say 단위가 아니라 vendor 세션 단위라 노출만으로는 불충분, `elevenlabs.go:246-253`) |
| G2 | /ws 메시지에 event type 없음, 3파트 prefix 구독 시 중복 수신 | 이벤트 오분류, transcript 중복 | 4파트 이벤트 타입 prefix 구독(타입별 연결 분리)으로 근본 해결 가능 + 중복 제거(타입별 연결 안에서는 `id`, 그 외 `(id, status/tm_update)` 또는 payload 해시) | 불필요 |
| G3 | flush가 재생 중단을 보장하지 않음 | barge-in 시 agent 말이 계속 나갈 수 있음 | 상대 발화 감지 시 speaking stop 후 다음 say 전에 재생성 | tts-manager flush 개선 |
| G4 | GCP 스트리밍 STT 재연결 없음 (약 5분 제한 추정) | 긴 통화 중 전사가 조용히 멈출 가능성. 스트림 오류 시 `gcpProcessResult`는 `cancel()`만 하고 조용히 종료(`bin-transcribe-manager/pkg/streaminghandler/gcp.go:87-123`), `runSTT`도 상태 갱신 없음 → 레코드가 progressing으로 남고 transcribe_done도 안 올 가능성. 같은 call+language 재시작은 409(`start.go:248-265`). 자동 AWS fallback은 초기화 실패 시에만 동작(`bin-transcribe-manager/pkg/streaminghandler/start.go:103-156` runSTT: 상태 갱신 없음, 첫 handler가 nil 반환하면 즉시 return하여 다음 provider 미시도. 명시 지정과 별개. 단 provider=aws 지정 시 AWS handler가 오류를 반환하면 GCP로 넘어가므로(`:146-150`) AWS 조기 실패 시 사용자 모르게 GCP로 동작할 수 있음) | 상태 감시로는 감지 불가. 1순위: transcribe 시작 시 `provider=aws` 명시 지정(AWS Transcribe streaming 한도는 약 4시간으로 알려짐, 외부 사실이라 실측 필요, 최대 통화 1시간보다 김). 2순위 fallback: 약 4.5분 주기 선제 Stop 후 Start(새 transcribe_id로 필터 교체, 교체 구간 발화 유실 감수) | transcribe-manager 재연결 |
| G5 | speaking 레코드 hangup 시 미정리 | 리소스 잔존 | MCP가 종료 시 stop 호출 | tts-manager hangup 구독 |
| G6 | 발화 종료 판정이 provider endpointing 고정 | 상대가 잠깐 쉬면 턴이 끊길 수 있음 | transcript 수신 후 짧은 유예(조용함 대기) 후 반환 | provider 옵션 노출 |
| G7 | 통화 고아화 | sleep으로 유지되는 통화는 MCP 프로세스 종료, agent 세션 종료, client 재시작 시 hangup이 호출되지 않아 상대가 무음 회선에 최대 1시간 방치되고 과금 지속 (고객 피해 + 비용) | sleep duration 상한, 무활동 watchdog 자동 hangup, 프로세스 종료(stdin EOF, SIGTERM 핸들러) 시 전 세션 hangup. SIGKILL은 복구 불가이므로 최종 backstop은 sleep 상한. 주의: 실행 중 call/activeflow에 action을 추가/연장하는 공개 API가 없으므로 sleep duration은 생성 시 확정되는 대화 최대 길이이며 연장 불가(기본값은 디자인에서 결정) | (없음, MCP 책임) |

판단: G1~G3, G5~G7은 MCP 단독으로 기능상 우회 가능하다. G4는 선제 재시작 우회가 실측으로 확인돼야만 MCP 단독이 성립한다. 대표님 결정(A안, mcp repo 범위)에 맞게 이번 작업은 MCP 단독으로 구현하고, 백엔드 근본 해결 항목은 실측 결과로 실제 문제가 확인된 것만 별도 후속 티켓으로 제안한다(오버엔지니어링 지양). 단 G4는 긴 통화에서 대화가 멈추는 치명 이슈일 수 있어 실측 우선순위를 높게 둔다.

추가 리스크 (디자인에서 다룸):
- 음향 에코: 상대가 스피커폰이면 TTS가 `in` 전사로 재유입될 수 있다. agent 발화 중 수신된 transcript는 구분 표시.
- 동시 다중 통화: call_id별 세션 맵 필요. speaking은 reference당 active 1개 제한(`bin-tts-manager/pkg/speakinghandler/speaking.go:38-54`).
- 비용: TTS는 과금(`CostTypeTTS`), STT/통화도 과금. barge-in 시 stop 후 재생성은 지연과 비용 증가.

## 5. MCP 측 구조적 고려사항

- MCP 서버는 stdio 기반 장수 프로세스이므로 tool 호출 사이에 통화 세션 상태(WS 연결, transcript 버퍼)를 유지할 수 있다(추정, spike로 검증).
- 현재 tool은 모두 `async def`. 잠금된 mcp는 1.27.0(uv.lock)이고 요청을 `tg.start_soon`으로 동시 처리한다. pyproject 하한 `mcp>=1.2.0`에서도 동시 처리되는지는 미확인 → 하한 상향 여부 판단 필요.
- 검증할 것: 백그라운드 WS task가 tool 호출 사이 살아남는지(anyio task group 수명), `listen` 블로킹 중 hangup/transcript 버퍼링, 대상 client(Claude Code)의 MCP tool timeout, 신규 의존성(websocket 클라이언트) 추가.
- 블로킹 tool(`listen`)은 MCP client의 tool timeout에 걸릴 수 있다. listen에 timeout 인자를 두고 기본값을 client timeout보다 짧게 둔다.
- 턴 지연은 agent 추론 시간이 지배한다(추정, 실측 전). 정적 완화(맞장구) 기능 필요성을 정하는 핵심 변수이므로 spike에서 측정하고 디자인 단계에서 판단.
- 비용: 실제 통화는 비용이 발생하므로 자동 테스트는 respx mock 기반 단위 테스트로 하고, 실통화 검증은 extension 대상 수동 E2E로 한정한다. 호스티드 PSTN 제한으로 PSTN 테스트는 하지 않는다.

## 6. 실측이 필요한 load-bearing 가정 (디자인 확정 전 검증, 우선순위 순)

0. MCP 런타임 spike (통화 비용 없음, 로컬): 백그라운드 WS task가 tool 호출 사이 유지되는지, 블로킹 listen 중 이벤트 버퍼링, Claude Code MCP tool timeout, mcp 최소 버전, Claude Code의 턴 지연(tool 호출 간격) 측정.
1. sleep action으로 통화 유지 + speaking direction=`out`이 피호출자에게 들리는지(기본 none은 무음인지). 실제 운용 구성(sleep으로 bridge 없이 Stasis 대기 채널)과 동일 조건에서 검증. 실패 시 silence on 상태에서 재검증, 그래도 실패하면 fallback `/calls/{id}/talk`가 들리는지 확인. listen 대기가 길어진 뒤의 say가 정상 재생되는지도 확인(ElevenLabs keepalive는 구현됨, `bin-tts-manager/pkg/streaminghandler/elevenlabs.go:226,533`).
2. /ws 실통화 이벤트 수신(call, transcribe speech, transcript)과 지연, 4파트 이벤트 타입 prefix 구독 동작, 중복 수신 여부, accesskey로 생성한 call/transcribe/transcript payload에 `owner_id`가 있는지.
3. G4: (a) 운영에서 aws provider가 실제 초기화돼 있는지, AWS 조기 실패로 GCP로 넘어가지 않는지(둘 다 조용히 GCP로 fallback하므로 동작/로그로 판별), aws 지정 시 5분 넘게 전사 유지되는지, 한국어/영어 aws 전사 품질과 interim 지연. (b) GCP 약 5분 제한 여부, 스트림 종료 후 status 잔존 여부, Stop 직후 Start 성공 여부(2순위 fallback 판단용).
4. transcribe direction=`in`에 TTS(whisper out) 음성이 섞이지 않는지.
5. 시작 시점: transcribe/speaking을 응답 전(dialing/ringing)에 시작 가능한지, 응답 후 시작 시 첫 발화("여보세요") 유실 여부.

검증 방법: 테스트 extension을 API로 생성(CRUD 허용 범위)하고 로컬 SIP UA(baresip 설치 필요, 미설치)로 등록해 수신, 녹음한다. 사람 귀 확인이 필요한 1번은 대표님 softphone 협조가 필요할 수 있다.

## 7. 범위 결론

- 대상 repo: voipbin/mcp 단독 (1 PR).
- 신규 기능: 통화 세션 관리자(WS 구독, transcript 버퍼, 상태), 턴 기반 phone tool 세트.
- 비범위: 백엔드 변경, B안, VoIPBin AI 위임 tool.
- 에스컬레이션 규칙: 실측 결과 MCP 단독 우회가 불가능한 항목(특히 G4)이 나오면 구현을 멈추고 대표님께 보고해 백엔드 수정(monorepo 별도 PR) 여부를 결정받는다.

## 8. 리뷰 이력
- Round 1: CHANGES_REQUESTED. MAJOR 3(G4 우회 불가, MCP 런타임 가정 미검증, 통화 고아화 G7 누락), MINOR 9. 전부 반영.
- Round 2: CHANGES_REQUESTED. MAJOR 1(G4 대안 provider=aws 명시 지정 누락), MINOR 7(턴 지연 실측, sleep 연장 불가, SIGKILL 한계, G5 서술, 경로 모호, create_call 시그니처, G표 순서). 전부 반영. provider fallback 동작은 코드로 직접 재확인.
- Round 3: CHANGES_REQUESTED. MAJOR 2(/ws 이중 토픽 발행으로 인한 중복 수신과 4파트 타입 구독 가능성 누락, `/calls/{id}/talk` 대안 미검토), MINOR 5(시그니처 잔존, 경로 정정, media_stream 제외 명시). 전부 반영, createTopics/getServiceNamespace/Talk 코드 직접 재확인.
- Round 4: CHANGES_REQUESTED. MAJOR 1(3파트 prefix 금지 후에도 68/69/71행이 3파트 권장, 절반 반영), MINOR 6(중복 제거 키, owner_id 이중 라우팅 예외, 경로 누락, Talk Answer 부작용, G4 중복 인용과 AWS 조기 실패, 이력 순서). 전부 반영.
- Round 5: APPROVED. MINOR 6(say 5000바이트, speech payload 필드, Talk runNext 근거, call 이중 구독 키와 선도착 버퍼링, 중복 문단, 긴 대기 후 say 실측). 전부 반영.
- Round 6: APPROVED. MINOR 3(silence 중간 fallback, Talk runNext 근거 코드 확인으로 격상, 바이트 기준 오류 문구). 전부 반영.
- 결과: Round 5, 6 연속 APPROVED로 분석 리뷰 루프 종료(최소 2회 충족).

## 9. 실측 결과 (2026-10-07, 운영 api.voipbin.net, 분석 리뷰 종료 후 수행)

방법: 임시 extension을 API로 생성하고 로컬 SIP UA(pyVoIP 1.6.8, NAT 우회 패치)로 등록, `POST /calls`(source=고객 virtual number, destination=`{"type":"extension","target_name":...}`, inline `sleep` action)로 발신. 컨트롤러가 /transcribes(direction=in), /speakings(direction=out)를 구동하고 /ws를 이벤트 타입별 연결로 구독. UA는 수신 오디오를 녹음(RMS 측정)하고 정해진 시점에 사람 음성(영문 TTS 7.4초)을 송출. 실험 후 extension 삭제, speaking 전부 stopped 확인.

| # | 항목 | 결과 |
|---|---|---|
| 0 | MCP 런타임 | FastMCP stdio에서 백그라운드 asyncio task가 tool 호출 사이 유지, 블로킹 중 이벤트 큐 버퍼링 정상. **mcp 서버 1.2.0은 요청을 직렬 처리**(블로킹 tool 중 다른 tool이 3초 대기), 1.3.0 이상은 동시 처리 → pyproject 하한 상향 필요. Claude Code 2.1.220에서 130초 블로킹 tool이 timeout 없이 완료. Claude Code의 tool 호출 간 턴 지연(짧은 응답 작성 포함) 약 1.7~2.7초. |
| 1 | 통화 유지 + TTS 청취 | inline sleep으로 통화 유지 확인. speaking direction=`out`의 say가 UA에서 약 0.9~1.3초 후 오디오로 수신(RMS로 확인, silence 보조 불필요). 25초 무발화 후 say도 정상(keepalive 동작, 단 keepalive 시점에 짧은 잡음 1회 관측). |
| 2 | /ws 이벤트 | 4파트 이벤트 타입 구독(`call:call_progressing`, `call:call_hangup`, `transcript:transcript_created`, `transcribe:transcribe_speech_*`) 모두 수신, 각 1회(중복 없음). 3파트 `customer_id:<cid>:call` 구독은 **같은 이벤트 2회 수신 실측 확인**. call 이벤트 지연 약 0.2~0.4초. transcribe/transcript payload에는 `owner_id` 없음, call payload의 owner_id는 nil UUID. |
| 2a | extension 발신 시 call_id | `POST /calls`(destination extension) 응답은 `calls: []`, `groupcalls: [1]`. call payload의 `groupcall_id`는 nil. **call_id는 `GET /groupcalls/{id}`의 `call_ids`로 획득**(응답 직후 0.2초 내 확보). tel/sip 목적지는 `calls`에 바로 들어올 것으로 추정(미실측). |
| 3 | G4 STT 5분 | **GCP: 약 5분 이후 전사가 조용히 멈춤 확인**(330초 시점 발화 미전사, transcribe status는 progressing 유지, 종료 후 done). **AWS(provider=aws 명시): 330초 시점 발화 정상 전사.** → provider=aws 고정으로 G4 해소, 2순위 우회 불필요. AWS도 interim 이벤트 발생. |
| 4 | TTS의 STT 혼입 | direction=in 전사에 agent TTS 문장은 한 번도 나타나지 않음(GCP, AWS 모두). |
| 5 | 시작 시점 | transcribe를 응답 전(dialing) 시작 가능, 응답 후 정상 전사. 사람 발화 시작부터 첫 interim 약 0.8초(GCP), 발화 종료부터 transcript_created 약 0.5~1.1초. |
| G3 | barge-in | **flush는 재생 중인 오디오를 끊지 못함**(실측). **stop은 0.5초 이내 즉시 무음.** stop 후 새 speaking 생성+say API 0.6초, 첫 오디오까지 추가 약 0.3~0.9초. |

설계 함의:
- STT provider는 aws 고정(G4 해소). 단 aws 미초기화 시 조용히 GCP로 fallback하므로 transcribe 응답의 `provider` 필드를 검증하고, gcp로 떨어지면 통화 길이를 5분 미만으로 제한하거나 경고한다.
- barge-in은 stop 후 재생성으로 구현(flush 사용 안 함).
- extension 목적지는 groupcall 경유 call_id 조회가 필요.
- mcp 의존성 하한을 1.3.0 이상(권장: 검증한 최신 계열)으로 상향.
- 턴 지연 합계 추정: 사람 발화 종료→transcript 약 1초 + agent 추론 약 2초 + TTS 첫 오디오 약 1초 = 약 4초.
