import pytest
import httpx
import respx

from voipbin_mcp.client import VoIPbinClient, VoIPbinAPIError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
    return VoIPbinClient()


class TestClientInit:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("VOIPBIN_API_KEY", raising=False)
        with pytest.raises(ValueError, match="VOIPBIN_API_KEY"):
            VoIPbinClient()

    def test_base_url(self, client):
        assert client.base_url == "https://api.voipbin.net/v1.0"

    def test_api_key_from_env(self, client):
        assert client.api_key == "test-key-123"


class TestClientGet:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_success(self, client):
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        result = await client.get("/calls")
        assert result == {"result": []}

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_sends_key_as_cookie_not_query(self, client):
        route = respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        await client.get("/calls")
        request = route.calls[0].request
        assert request.headers["Cookie"] == "accesskey=test-key-123"
        assert "accesskey" not in str(request.url)

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_with_params(self, client):
        route = respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        await client.get("/calls", params={"page_size": 10})
        request = route.calls[0].request
        assert "page_size=10" in str(request.url)
        assert "accesskey" not in str(request.url)
        assert request.headers["Cookie"] == "accesskey=test-key-123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_401_raises(self, client):
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"message": "unauthorized", "reason": "AUTH_FAILED"}},
            )
        )
        with pytest.raises(VoIPbinAPIError, match="AUTH_FAILED"):
            await client.get("/calls")

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_404_raises(self, client):
        respx.get("https://api.voipbin.net/v1.0/calls/bad-id").mock(
            return_value=httpx.Response(
                404, json={"error": {"message": "not found"}}
            )
        )
        with pytest.raises(VoIPbinAPIError, match="not found"):
            await client.get("/calls/bad-id")


class TestClientPost:
    @respx.mock
    @pytest.mark.asyncio
    async def test_post_success(self, client):
        respx.post("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"id": "abc-123"})
        )
        result = await client.post("/calls", json={"source": {}})
        assert result == {"id": "abc-123"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_402_raises(self, client):
        respx.post("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(
                402, json={"error": {"message": "insufficient credits"}}
            )
        )
        with pytest.raises(VoIPbinAPIError, match="insufficient credits"):
            await client.post("/calls", json={})


class TestClientPut:
    @respx.mock
    @pytest.mark.asyncio
    async def test_put_success(self, client):
        respx.put("https://api.voipbin.net/v1.0/flows/flow-1").mock(
            return_value=httpx.Response(200, json={"id": "flow-1"})
        )
        result = await client.put("/flows/flow-1", json={"name": "updated"})
        assert result == {"id": "flow-1"}


class TestClientDelete:
    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_success(self, client):
        respx.delete("https://api.voipbin.net/v1.0/flows/flow-1").mock(
            return_value=httpx.Response(200, json={"id": "flow-1"})
        )
        result = await client.delete("/flows/flow-1")
        assert result == {"id": "flow-1"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_204_no_content(self, client):
        respx.delete("https://api.voipbin.net/v1.0/flows/flow-1").mock(
            return_value=httpx.Response(204)
        )
        result = await client.delete("/flows/flow-1")
        assert result == {}
