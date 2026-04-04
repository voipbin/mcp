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
