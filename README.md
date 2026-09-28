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

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `VOIPBIN_API_KEY` | required | Your VoIPbin access key. |
| `VOIPBIN_API_BASE_URL` | `https://api.voipbin.net/v1.0` | Full base URL including the `/v1.0` suffix. Set this to point at a self-hosted deployment. |
| `VOIPBIN_AUTH_TRANSPORT` | `cookie` | How the key is transmitted: `cookie` or `query`. An unrecognised value fails at startup. |

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

## License

MIT
