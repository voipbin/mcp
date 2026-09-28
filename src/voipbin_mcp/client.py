"""HTTP client for the VoIPbin REST API."""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.voipbin.net/v1.0"

# Longest raw (non-JSON) error body we forward. An intermediate proxy can answer
# with a full HTML page; dumping that into an LLM context is pure noise.
MAX_RAW_DETAIL = 200

# The auth transports this client can speak. Declared as a frozenset of
# separate literals rather than a two-string tuple: secret scanners read
# `NAME_WITH_AUTH_IN_IT = ("a", "b")` as a username/password pair and flag it,
# which is a false positive on a list of allowed values but a noisy one.
VALID_AUTH_TRANSPORTS = frozenset({"cookie", "query"})


class VoIPbinAPIError(Exception):
    """Raised when a VoIPbin API call fails.

    The server answers errors with an envelope::

        {"error": {"message": ..., "reason": ..., "request_id": ..., "status": ...}}

    ``reason`` and ``request_id`` are kept as attributes so that callers do not
    have to re-parse the rendered message.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        reason: str | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ):
        self.status_code = status_code
        self.message = message
        self.reason = reason
        self.request_id = request_id
        self.retry_after = retry_after
        super().__init__(message)


class VoIPbinClient:
    """Async HTTP client for the VoIPbin REST API."""

    def __init__(self):
        self.api_key = os.environ.get("VOIPBIN_API_KEY")
        if not self.api_key:
            raise ValueError(
                "VOIPBIN_API_KEY environment variable is required. "
                "Set it to your VoIPbin access key."
            )

        self.base_url = os.environ.get("VOIPBIN_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

        transport = os.environ.get("VOIPBIN_AUTH_TRANSPORT", "cookie").strip().lower()
        if transport not in VALID_AUTH_TRANSPORTS:
            raise ValueError(
                f"VOIPBIN_AUTH_TRANSPORT must be one of "
                f"{', '.join(sorted(VALID_AUTH_TRANSPORTS))}; got {transport!r}. "
                "Leave it unset to send the key as a cookie."
            )
        self.auth_transport = transport

        self._client = httpx.AsyncClient(timeout=30.0)

    def __repr__(self) -> str:
        base_url = getattr(self, "base_url", "<not initialised>")
        return f"VoIPbinClient(base_url={base_url!r})"

    def _auth_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build query parameters, adding the access key when in query mode."""
        result = dict(params) if params else {}
        if self.auth_transport == "query":
            result["accesskey"] = self.api_key
        return result

    def _auth_headers(self) -> dict[str, str]:
        """Build request headers, adding the access key when in cookie mode.

        The cookie is set per request rather than on the client's cookie jar:
        a jar scopes cookies to the domain seen at construction time and would
        silently send nothing across a self-hosted URL or a redirect.
        """
        if self.auth_transport == "cookie":
            return {"Cookie": f"accesskey={self.api_key}"}
        return {}

    @staticmethod
    def _extract_detail(response: httpx.Response) -> tuple[str, str | None, str | None]:
        """Pull (message, reason, request_id) out of an error response."""
        try:
            body = response.json()
        except Exception:
            raw = " ".join(response.text.split())
            if len(raw) > MAX_RAW_DETAIL:
                raw = raw[:MAX_RAW_DETAIL] + "..."
            return raw, None, None

        if not isinstance(body, dict):
            return "", None, None

        envelope = body.get("error")
        if isinstance(envelope, dict):
            return (
                envelope.get("message") or "",
                envelope.get("reason") or None,
                envelope.get("request_id") or None,
            )

        # Some paths answer with a bare top-level message.
        return body.get("message") or "", body.get("reason") or None, None

    def _handle_error(self, response: httpx.Response) -> None:
        """Raise a descriptive error for non-2xx responses."""
        if response.is_success:
            return

        detail, reason, request_id = self._extract_detail(response)

        parts = [f"VoIPbin API error {response.status_code}"]
        if reason:
            parts.append(f" ({reason})")
        parts.append(f": {detail}" if detail else ":")

        retry_after = None
        if response.status_code == 429:
            # Only two of the three rate-limit paths set this header, so its
            # absence is a normal case and must not raise.
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                parts.append(f" Retry after {retry_after} seconds.")

        if request_id:
            parts.append(f" [request_id: {request_id}]")

        raise VoIPbinAPIError(
            response.status_code,
            "".join(parts),
            reason=reason,
            request_id=request_id,
            retry_after=retry_after,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """Send a GET request."""
        response = await self._client.get(
            f"{self.base_url}{path}",
            params=self._auth_params(params),
            headers=self._auth_headers(),
        )
        self._handle_error(response)
        return response.json()

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict:
        """Send a POST request."""
        response = await self._client.post(
            f"{self.base_url}{path}",
            params=self._auth_params(),
            headers=self._auth_headers(),
            json=json,
        )
        self._handle_error(response)
        return response.json()

    async def put(self, path: str, json: dict[str, Any] | None = None) -> dict:
        """Send a PUT request."""
        response = await self._client.put(
            f"{self.base_url}{path}",
            params=self._auth_params(),
            headers=self._auth_headers(),
            json=json,
        )
        self._handle_error(response)
        return response.json()

    async def delete(self, path: str) -> dict:
        """Send a DELETE request."""
        response = await self._client.delete(
            f"{self.base_url}{path}",
            params=self._auth_params(),
            headers=self._auth_headers(),
        )
        self._handle_error(response)
        return response.json() if response.content else {}

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()
