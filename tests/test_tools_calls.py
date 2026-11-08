import json

import httpx
import pytest
import respx

import voipbin_mcp.server


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    """Reset the global client before each test and set the API key."""
    voipbin_mcp.server._client = None
    monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
    yield
    voipbin_mcp.server._client = None


class TestListCalls:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_calls_returns_formatted_json(self):
        payload = {"result": [{"id": "call-1"}, {"id": "call-2"}], "next_page_token": "abc"}
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json=payload)
        )
        from voipbin_mcp.tools.calls import list_calls

        result = await list_calls()
        parsed = json.loads(result)
        assert parsed == payload

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_calls_passes_page_size(self):
        route = respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        from voipbin_mcp.tools.calls import list_calls

        await list_calls(page_size=25)
        url = str(route.calls[0].request.url)
        assert "page_size=25" in url

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_calls_passes_page_token(self):
        route = respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        from voipbin_mcp.tools.calls import list_calls

        await list_calls(page_token="cursor-xyz")
        url = str(route.calls[0].request.url)
        assert "page_token=cursor-xyz" in url


class TestGetCall:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_call_builds_correct_url(self):
        call_id = "550e8400-e29b-41d4-a716-446655440000"
        route = respx.get(f"https://api.voipbin.net/v1.0/calls/{call_id}").mock(
            return_value=httpx.Response(200, json={"id": call_id, "status": "active"})
        )
        from voipbin_mcp.tools.calls import get_call

        result = await get_call(call_id=call_id)
        parsed = json.loads(result)
        assert parsed["id"] == call_id
        assert route.called


class TestCreateCall:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_call_sends_correct_body(self):
        route = respx.post("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"id": "new-call-id"})
        )
        from voipbin_mcp.tools.calls import create_call

        result = await create_call(
            source_type="tel",
            source_target="+14155551234",
            destination_type="tel",
            destination_target="+14155555678",
        )
        parsed = json.loads(result)
        assert parsed["id"] == "new-call-id"

        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["source"] == {"type": "tel", "target": "+14155551234"}
        assert body["destinations"] == [{"type": "tel", "target": "+14155555678"}]
        assert "flow_id" not in body

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_call_includes_flow_id(self):
        route = respx.post("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"id": "new-call-id"})
        )
        from voipbin_mcp.tools.calls import create_call

        await create_call(
            source_type="tel",
            source_target="+14155551234",
            destination_type="sip",
            destination_target="user@example.com",
            flow_id="flow-abc-123",
        )
        body = json.loads(route.calls[0].request.content)
        assert body["flow_id"] == "flow-abc-123"


class TestHangupCall:
    @respx.mock
    @pytest.mark.asyncio
    async def test_hangup_call_posts_to_correct_url(self):
        call_id = "550e8400-e29b-41d4-a716-446655440000"
        route = respx.post(f"https://api.voipbin.net/v1.0/calls/{call_id}/hangup").mock(
            return_value=httpx.Response(200, json={"id": call_id, "status": "hangup"})
        )
        from voipbin_mcp.tools.calls import hangup_call

        result = await hangup_call(call_id=call_id)
        parsed = json.loads(result)
        assert parsed["status"] == "hangup"
        assert route.called


class TestSendMessage:
    """source_number is optional; omitting it omits the whole source key."""

    URL = "https://api.voipbin.net/v1.0/messages"

    @staticmethod
    def _body(route):
        return json.loads(route.calls[0].request.content)

    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
    async def test_blank_source_omits_the_source_key(self, blank):
        route = respx.post(self.URL).mock(
            return_value=httpx.Response(200, json={"id": "m-1"})
        )
        from voipbin_mcp.tools.messages import send_message

        await send_message("+1555****0002", "hi", source_number=blank)
        assert self._body(route) == {
            "destinations": [{"type": "tel", "target": "+1555****0002"}],
            "text": "hi",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_omitted_source_omits_the_source_key(self):
        route = respx.post(self.URL).mock(
            return_value=httpx.Response(200, json={"id": "m-1"})
        )
        from voipbin_mcp.tools.messages import send_message

        await send_message("+1555****0002", "hi")
        assert self._body(route) == {
            "destinations": [{"type": "tel", "target": "+1555****0002"}],
            "text": "hi",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_given_source_is_sent_as_before(self):
        route = respx.post(self.URL).mock(
            return_value=httpx.Response(200, json={"id": "m-1"})
        )
        from voipbin_mcp.tools.messages import send_message

        await send_message("+1555****0002", "hi", "+1555****0001")
        assert self._body(route) == {
            "source": {"type": "tel", "target": "+1555****0001"},
            "destinations": [{"type": "tel", "target": "+1555****0002"}],
            "text": "hi",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_given_source_is_not_trimmed(self):
        route = respx.post(self.URL).mock(
            return_value=httpx.Response(200, json={"id": "m-1"})
        )
        from voipbin_mcp.tools.messages import send_message

        await send_message("+1555****0002", "hi", source_number=" +1555****0001 ")
        assert self._body(route)["source"] == {
            "type": "tel",
            "target": " +1555****0001 ",
        }

    @pytest.mark.asyncio
    async def test_schema_does_not_require_source_number(self):
        tools = {t.name: t for t in await voipbin_mcp.server.mcp.list_tools()}
        schema = tools["send_message"].inputSchema
        assert schema["required"] == ["destination_number", "text"]
        assert "source_number" in schema["properties"]
