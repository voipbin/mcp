# VoIPbin MCP Server

[![PyPI version](https://img.shields.io/pypi/v/voipbin-mcp)](https://pypi.org/project/voipbin-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/voipbin-mcp)](https://pypi.org/project/voipbin-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An MCP (Model Context Protocol) server that enables AI assistants to interact with the VoIPbin CPaaS platform. It exposes VoIPbin's communication APIs -- calls, flows, messaging, conferencing, AI, and more -- as tools that any MCP-compatible client can use to manage and automate cloud communications.

## Installation

The client configurations below all launch the server with `uvx`, which fetches
and runs it without a manual install:

```bash
uvx voipbin-mcp
```

To install it into an environment instead:

```bash
pip install voipbin-mcp
```

Requires Python 3.10 or newer.

## Configuration

### Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "voipbin": {
      "command": "uvx",
      "args": ["voipbin-mcp"],
      "env": {
        "VOIPBIN_API_KEY": "your-access-key"
      }
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project directory:

```json
{
  "mcpServers": {
    "voipbin": {
      "command": "uvx",
      "args": ["voipbin-mcp"],
      "env": {
        "VOIPBIN_API_KEY": "your-access-key"
      }
    }
  }
}
```

### Generic MCP Client

Any MCP-compatible client can connect by running the `voipbin-mcp` command with the `VOIPBIN_API_KEY` environment variable set:

```bash
VOIPBIN_API_KEY=your-access-key voipbin-mcp
```

## Available Tools

| Resource | Tools |
|---|---|
| Calls | `list_calls`, `get_call`, `create_call`, `hangup_call` |
| Flows | `list_flows`, `get_flow`, `create_flow`, `update_flow`, `delete_flow` |
| Active Flows | `list_activeflows`, `get_activeflow`, `stop_activeflow` |
| Agents | `list_agents`, `get_agent` |
| Numbers | `list_numbers`, `get_number` |
| Contacts | `list_contacts`, `get_contact`, `create_contact`, `update_contact`, `delete_contact`, `add_contact_address`, `update_contact_address`, `delete_contact_address`, `add_contact_tag`, `delete_contact_tag` |
| Messages | `list_messages`, `get_message`, `send_message` |
| Emails | `list_emails`, `get_email`, `send_email` |
| Conversations | `list_conversations`, `get_conversation` |
| Conferences | `list_conferences`, `get_conference`, `create_conference`, `delete_conference` |
| Campaigns | `list_campaigns`, `get_campaign`, `create_campaign`, `update_campaign`, `update_campaign_actions`, `delete_campaign` |
| Queues | `list_queues`, `get_queue` |
| Routes | `list_routes`, `get_route` (require project superadmin permission) |
| Billings | `list_billings`, `get_billing` |
| AIs | `list_ais`, `get_ai`, `create_ai` |
| Customer | `get_customer` |
| Tags | `list_tags`, `get_tag` |
| Extensions | `list_extensions`, `get_extension` |
| Phone (live conversation) | `phone_call_start`, `phone_incoming_configure`, `phone_wait_incoming`, `phone_say_and_listen`, `phone_say`, `phone_listen`, `phone_hangup`, `phone_status` |

## Example Usage

Once configured, you can ask your AI assistant to interact with VoIPbin directly:

**List active calls:**
> "Show me all my active calls"

The AI uses `list_calls` to fetch and display your current calls.

**Create a flow:**
> "Create a flow that answers and plays a greeting"

The AI uses `create_flow` to build a call flow with answer and play actions.

**Check billing:**
> "What are my recent billing charges?"

The AI uses `list_billings` to retrieve your billing history.

**Manage contacts:**
> "Add a new contact named John with phone number +1234567890"

The AI uses `create_contact`, passing the number as an address of type `tel`.

## Live phone conversations

The `phone_*` tools let the agent itself talk on a phone call: it speaks
through text-to-speech and hears the other party through live speech-to-text.
The MCP server owns the call session (the call, transcription, speech and the
event stream), so every tool call is one conversational turn.

**Cost.** A live conversation is billed as a call plus speech-to-text plus
text-to-speech. Interim speech-recognition events are also delivered to your
account's webhook if one is configured, so a long conversation can send a
large number of webhook requests.

**Outgoing call:**

> "Call extension 2001 from +15550001234 and ask whether the delivery arrived."

The agent calls `phone_call_start` (it returns once the call is answered),
then repeats `phone_say_and_listen(call_id, text)` for each turn, and ends with
`phone_hangup`.

**Incoming calls:**

1. `phone_incoming_configure(number_id, enabled=true)` once. This replaces the
   number's call flow with one that keeps callers ringing until the agent
   answers; the original flow id is stored on the replacement flow.
2. `phone_wait_incoming(number_id)` answers the next call with a greeting.
   Call it again to wait for the next one.
3. `phone_incoming_configure(number_id, enabled=false)` gives the number back
   its original call flow.

Incoming calls are handled only while an MCP server is running and waiting.
Only one MCP process may wait on a given number. While the server runs, an
incoming call to a number that this server process has already waited on is
rejected if no `phone_wait_incoming` picks it up within 15 seconds. Calls to
configured numbers the process has not waited on are left ringing.

**Safety limits.** At most 4 concurrent calls per server process. A call is
hung up after 5 minutes without any phone tool call, at its
`max_duration_seconds` (1 hour at most), and when the MCP server exits (stdin
closed, SIGTERM or SIGINT).

**Claude Code.** Allow the phone tools ahead of time (for example with
`/permissions` or an allowlist in your settings). A permission prompt is not
skipped during a call: the time it waits adds to the pause the other party
hears and counts towards the 5 minute idle hangup.

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `VOIPBIN_API_KEY` | required | Your VoIPbin access key. |
| `VOIPBIN_API_BASE_URL` | `https://api.voipbin.net/v1.0` | Full base URL including the `/v1.0` suffix. Set this to point at a self-hosted deployment. |
| `VOIPBIN_AUTH_TRANSPORT` | `cookie` | How the key is transmitted: `cookie` or `query`. An unrecognised value makes the server exit at startup. |

## Security Note

Your VoIPbin API key is sent in a `Cookie` header on every request, over
HTTPS. Earlier releases sent it as a URL query parameter (`accesskey=`), which
leaves the key in server access logs and proxy logs.

If a proxy or ingress in front of your deployment strips or rewrites cookies,
set `VOIPBIN_AUTH_TRANSPORT=query` to fall back to the query parameter. Prefer
fixing the proxy: the query form has the logging exposure described above.

Avoid sharing unredacted debug output, and rotate your key if you suspect it
has been exposed. When configuring an AI agent with `create_ai`, pass an
environment variable reference for `engine_key` rather than a literal provider
key; that value is stored by the API and can appear in responses.

## Known limitations

- Around 12% of the VoIPbin REST API is exposed as tools. Coverage expands in
  later releases.
- `list_routes` and `get_route` are platform-level and answer 403 for a normal
  customer access key.
- `create_conference` does not expose the `data` field; it always sends an
  empty object.
- Attachments on `send_email` reference existing VoIPbin objects (such as a
  recording) rather than uploading file contents.
- Live phone conversations take about 4 seconds per turn: roughly 1 second to
  finish transcribing, the agent's own thinking time, and about 1 second
  before synthesized speech is heard.
- The platform reports no "speech finished" event, so when the agent stops
  talking is estimated from the text length. Barge-in (stopping the agent's
  speech when the other party talks over it) can be slightly early or late.
- On a speakerphone the agent's own voice can echo back and be transcribed;
  such speech is marked `during_agent_speech`.
- An incoming call that starts ringing while the server's event connection
  to VoIPbin is down is not seen: it is neither answered nor rejected, and
  the caller keeps ringing until the platform's call timeout. Call
  `phone_wait_incoming` again once the connection is back to take later
  calls.
- Without a running MCP server, an incoming call to a number prepared with
  `phone_incoming_configure` keeps ringing (up to the platform's one hour call
  timeout), and the caller may hear no ringback tone.
- Where the aws speech-to-text provider is not available (some self-hosted
  setups), transcription silently falls back to a provider that stops after
  about 5 minutes. This cannot be detected through the API; phone results
  include `stt_silent_seconds` when nothing has been recognised for 4 minutes.
- If the MCP server is killed (SIGKILL), it cannot hang up its calls. An
  outgoing call stays up until its `max_duration_seconds` (the `sleep` placed
  on the call ends). An incoming call stays up until the incoming flow's one
  hour `sleep` ends or the platform's one hour channel timeout hangs it up,
  whichever comes first, so the longest incoming conversation is one hour
  minus the time the call spent ringing.
- PSTN calling on the hosted platform follows the existing account policy.

## Getting an API Key

Sign up at [voipbin.net](https://voipbin.net) and create an access key through the API or the admin console at [admin.voipbin.net](https://admin.voipbin.net).

## Development

```bash
git clone https://github.com/voipbin/mcp.git
cd mcp
uv venv
uv pip install -e ".[dev]"
uv run pytest tests/ -v
```

The release gate builds the distribution and installs it at both ends of the
declared `mcp` range, because a package can pass in the lock environment and
still be broken for everyone installing from PyPI. To reproduce that locally:

```bash
uv build
uv run python scripts/stdio_smoke.py
```

Tool descriptions are pinned by exact text in `tests/golden_docstrings.json`,
so any docstring edit fails the suite until the golden file is regenerated.
That is intentional: ten documented behaviours in this package turned out to
contradict the backend, so an edit is the moment to re-read the Go source named
beside the claim in `PINNED_CLAIMS` and confirm it still holds. Once verified:

```bash
# optional but recommended: check every pinned Go reference still resolves
VOIPBIN_MONOREPO=/path/to/monorepo uv run pytest tests/ -k resolve
uv run python scripts/update_golden_docstrings.py
```

## License

MIT
