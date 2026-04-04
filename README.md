# VoIPbin MCP Server

An MCP (Model Context Protocol) server that enables AI assistants to interact with the VoIPbin CPaaS platform. It exposes VoIPbin's communication APIs -- calls, flows, messaging, conferencing, AI, and more -- as tools that any MCP-compatible client can use to manage and automate cloud communications.

## Installation

```bash
pip install voipbin-mcp
```

Or run directly without installing:

```bash
uvx voipbin-mcp
```

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
| Contacts | `list_contacts`, `get_contact`, `create_contact`, `update_contact`, `delete_contact` |
| Messages | `list_messages`, `get_message`, `send_message` |
| Emails | `list_emails`, `get_email`, `send_email` |
| Conversations | `list_conversations`, `get_conversation` |
| Conferences | `list_conferences`, `get_conference`, `create_conference`, `delete_conference` |
| Campaigns | `list_campaigns`, `get_campaign`, `create_campaign`, `update_campaign`, `delete_campaign` |
| Queues | `list_queues`, `get_queue` |
| Routes | `list_routes`, `get_route` |
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

The AI uses `create_contact` to create the contact in your account.

## Getting an API Key

Sign up at [voipbin.net](https://voipbin.net) and create an access key through the API or the admin console at [admin.voipbin.net](https://admin.voipbin.net).

## Development

```bash
git clone https://github.com/voipbin/voipbin-mcp.git
cd voipbin-mcp
uv venv
uv pip install -e ".[dev]"
uv run pytest tests/ -v
```

## License

MIT
