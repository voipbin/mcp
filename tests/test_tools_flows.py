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


class TestListFlows:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_flows_returns_formatted_json(self):
        payload = {"result": [{"id": "flow-1"}, {"id": "flow-2"}], "next_page_token": "abc"}
        respx.get("https://api.voipbin.net/v1.0/flows").mock(
            return_value=httpx.Response(200, json=payload)
        )
        from voipbin_mcp.tools.flows import list_flows

        result = await list_flows()
        parsed = json.loads(result)
        assert parsed == payload

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_flows_passes_page_size(self):
        route = respx.get("https://api.voipbin.net/v1.0/flows").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        from voipbin_mcp.tools.flows import list_flows

        await list_flows(page_size=25)
        url = str(route.calls[0].request.url)
        assert "page_size=25" in url

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_flows_passes_page_token(self):
        route = respx.get("https://api.voipbin.net/v1.0/flows").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        from voipbin_mcp.tools.flows import list_flows

        await list_flows(page_token="cursor-xyz")
        url = str(route.calls[0].request.url)
        assert "page_token=cursor-xyz" in url


class TestGetFlow:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_flow_builds_correct_url(self):
        flow_id = "550e8400-e29b-41d4-a716-446655440000"
        route = respx.get(f"https://api.voipbin.net/v1.0/flows/{flow_id}").mock(
            return_value=httpx.Response(200, json={"id": flow_id, "name": "My Flow"})
        )
        from voipbin_mcp.tools.flows import get_flow

        result = await get_flow(flow_id=flow_id)
        parsed = json.loads(result)
        assert parsed["id"] == flow_id
        assert route.called


class TestCreateFlow:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_flow_sends_correct_body(self):
        route = respx.post("https://api.voipbin.net/v1.0/flows").mock(
            return_value=httpx.Response(200, json={"id": "new-flow-id"})
        )
        from voipbin_mcp.tools.flows import create_flow

        actions = [
            {"id": "a1", "next_id": "a2", "type": "answer", "option": {}},
            {"id": "a2", "next_id": "", "type": "hangup", "option": {}},
        ]
        result = await create_flow(name="Test Flow", detail="A test flow", actions=actions)
        parsed = json.loads(result)
        assert parsed["id"] == "new-flow-id"

        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["name"] == "Test Flow"
        assert body["detail"] == "A test flow"
        assert len(body["actions"]) == 2
        assert body["actions"][0]["type"] == "answer"
        assert body["actions"][1]["type"] == "hangup"

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_flow_includes_actions_with_options(self):
        route = respx.post("https://api.voipbin.net/v1.0/flows").mock(
            return_value=httpx.Response(200, json={"id": "new-flow-id"})
        )
        from voipbin_mcp.tools.flows import create_flow

        actions = [
            {"id": "a1", "next_id": "a2", "type": "answer", "option": {}},
            {
                "id": "a2",
                "next_id": "",
                "type": "play",
                "option": {"urls": ["https://example.com/audio.wav"]},
            },
        ]
        await create_flow(name="Play Flow", detail="Plays audio", actions=actions)

        body = json.loads(route.calls[0].request.content)
        assert body["actions"][1]["option"]["urls"] == ["https://example.com/audio.wav"]


class TestUpdateFlow:
    @respx.mock
    @pytest.mark.asyncio
    async def test_update_flow_sends_correct_body(self):
        flow_id = "550e8400-e29b-41d4-a716-446655440000"
        route = respx.put(f"https://api.voipbin.net/v1.0/flows/{flow_id}").mock(
            return_value=httpx.Response(200, json={"id": flow_id, "name": "Updated Flow"})
        )
        from voipbin_mcp.tools.flows import update_flow

        actions = [
            {"id": "a1", "next_id": "", "type": "hangup", "option": {}},
        ]
        result = await update_flow(
            flow_id=flow_id, name="Updated Flow", detail="Updated description", actions=actions
        )
        parsed = json.loads(result)
        assert parsed["name"] == "Updated Flow"

        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["name"] == "Updated Flow"
        assert body["detail"] == "Updated description"
        assert len(body["actions"]) == 1
        assert body["actions"][0]["type"] == "hangup"
        assert route.called


class TestDeleteFlow:
    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_flow_calls_correct_url(self):
        flow_id = "550e8400-e29b-41d4-a716-446655440000"
        route = respx.delete(f"https://api.voipbin.net/v1.0/flows/{flow_id}").mock(
            return_value=httpx.Response(200, json={"id": flow_id})
        )
        from voipbin_mcp.tools.flows import delete_flow

        result = await delete_flow(flow_id=flow_id)
        parsed = json.loads(result)
        assert parsed["id"] == flow_id
        assert route.called
