"""Tests for the error envelope, auth transport and base URL contracts."""

import httpx
import pytest
import respx

from voipbin_mcp.client import VoIPbinClient, VoIPbinAPIError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
    return VoIPbinClient()


class TestErrorEnvelope:
    """The server wraps errors in {"error": {message, reason, request_id}}."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_envelope_fields_are_structured_attributes(self, client):
        respx.get("https://api.voipbin.net/v1.0/calls/x").mock(
            return_value=httpx.Response(
                404,
                json={
                    "error": {
                        "message": "The call was not found.",
                        "reason": "CALL_NOT_FOUND",
                        "request_id": "req_ABC123",
                        "status": "NOT_FOUND",
                    }
                },
            )
        )
        with pytest.raises(VoIPbinAPIError) as excinfo:
            await client.get("/calls/x")

        err = excinfo.value
        assert err.status_code == 404
        # Structured, so callers do not have to re-parse the message.
        assert err.reason == "CALL_NOT_FOUND"
        assert err.request_id == "req_ABC123"
        # And also rendered for the LLM to read.
        assert "CALL_NOT_FOUND" in err.message
        assert "The call was not found." in err.message
        assert "req_ABC123" in err.message

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_carries_the_server_message(self, client):
        """The old error_map dropped the server's message for 401/402/403."""
        respx.get("https://api.voipbin.net/v1.0/routes").mock(
            return_value=httpx.Response(
                403,
                json={
                    "error": {
                        "message": "You do not have permission.",
                        "reason": "PERMISSION_DENIED",
                    }
                },
            )
        )
        with pytest.raises(VoIPbinAPIError) as excinfo:
            await client.get("/routes")

        assert "PERMISSION_DENIED" in excinfo.value.message
        assert "You do not have permission." in excinfo.value.message

    @respx.mock
    @pytest.mark.asyncio
    async def test_top_level_message_fallback(self, client):
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(400, json={"message": "bare message"})
        )
        with pytest.raises(VoIPbinAPIError, match="bare message"):
            await client.get("/calls")

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_json_body_is_truncated_and_flattened(self, client):
        html = "<html>\n<body>\n" + ("x" * 500) + "\n</body>\n</html>"
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(502, text=html)
        )
        with pytest.raises(VoIPbinAPIError) as excinfo:
            await client.get("/calls")

        message = excinfo.value.message
        assert "\n" not in message
        # 200 chars of detail plus the ellipsis and the fixed prefix.
        assert len(message) < 300
        assert message.endswith("...")


class TestRateLimit:
    @respx.mock
    @pytest.mark.asyncio
    async def test_429_with_retry_after(self, client):
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(
                429,
                headers={"Retry-After": "30"},
                json={
                    "error": {
                        "message": "Too many requests.",
                        "reason": "RATE_LIMIT_EXCEEDED",
                    }
                },
            )
        )
        with pytest.raises(VoIPbinAPIError) as excinfo:
            await client.get("/calls")

        assert excinfo.value.retry_after == "30"
        assert "Retry after 30 seconds." in excinfo.value.message

    @respx.mock
    @pytest.mark.asyncio
    async def test_429_without_retry_after_does_not_raise_internally(self, client):
        """One of the three 429 paths sets no Retry-After header."""
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(
                429,
                json={"error": {"message": "Too many requests.",
                                "reason": "RATE_LIMIT_EXCEEDED"}},
            )
        )
        with pytest.raises(VoIPbinAPIError) as excinfo:
            await client.get("/calls")

        assert excinfo.value.retry_after is None
        assert "Retry after" not in excinfo.value.message
        assert "RATE_LIMIT_EXCEEDED" in excinfo.value.message

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_automatic_retry(self, client):
        route = respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "1"}, json={"error": {}}
            )
        )
        with pytest.raises(VoIPbinAPIError):
            await client.get("/calls")
        assert route.call_count == 1


class TestAuthTransport:
    @respx.mock
    @pytest.mark.asyncio
    async def test_query_mode_puts_key_in_url(self, monkeypatch):
        monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
        monkeypatch.setenv("VOIPBIN_AUTH_TRANSPORT", "query")
        client = VoIPbinClient()

        route = respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/calls")
        request = route.calls[0].request
        assert "accesskey=test-key-123" in str(request.url)
        assert "Cookie" not in request.headers

    def test_unrecognised_transport_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
        monkeypatch.setenv("VOIPBIN_AUTH_TRANSPORT", "header")
        with pytest.raises(ValueError, match="VOIPBIN_AUTH_TRANSPORT"):
            VoIPbinClient()

    def test_transport_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
        monkeypatch.setenv("VOIPBIN_AUTH_TRANSPORT", "  Cookie ")
        assert VoIPbinClient().auth_transport == "cookie"

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_verbs_also_authenticate(self, client):
        """Auth is injected in a shared helper, so cover every verb."""
        post = respx.post("https://api.voipbin.net/v1.0/contacts").mock(
            return_value=httpx.Response(201, json={})
        )
        put = respx.put("https://api.voipbin.net/v1.0/contacts/c1").mock(
            return_value=httpx.Response(200, json={})
        )
        delete = respx.delete("https://api.voipbin.net/v1.0/contacts/c1").mock(
            return_value=httpx.Response(200, json={})
        )

        await client.post("/contacts", json={})
        await client.put("/contacts/c1", json={})
        await client.delete("/contacts/c1")

        for route in (post, put, delete):
            request = route.calls[0].request
            assert request.headers["Cookie"] == "accesskey=test-key-123"
            assert "accesskey" not in str(request.url)


class TestBaseURL:
    def test_default(self, client):
        assert client.base_url == "https://api.voipbin.net/v1.0"

    def test_override(self, monkeypatch):
        monkeypatch.setenv("VOIPBIN_API_KEY", "k")
        monkeypatch.setenv("VOIPBIN_API_BASE_URL", "https://voip.example.com/v1.0")
        assert VoIPbinClient().base_url == "https://voip.example.com/v1.0"

    def test_trailing_slash_is_normalised(self, monkeypatch):
        monkeypatch.setenv("VOIPBIN_API_KEY", "k")
        monkeypatch.setenv("VOIPBIN_API_BASE_URL", "https://voip.example.com/v1.0/")
        assert VoIPbinClient().base_url == "https://voip.example.com/v1.0"

    @respx.mock
    @pytest.mark.asyncio
    async def test_override_is_actually_used(self, monkeypatch):
        monkeypatch.setenv("VOIPBIN_API_KEY", "k")
        monkeypatch.setenv("VOIPBIN_API_BASE_URL", "https://voip.example.com/v1.0")
        client = VoIPbinClient()

        route = respx.get("https://voip.example.com/v1.0/calls").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/calls")
        assert route.call_count == 1
