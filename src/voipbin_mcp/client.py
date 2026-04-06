"""HTTP client for the VoIPbin REST API."""

import json
import os
from typing import Any

import httpx

BASE_URL = "https://api.voipbin.net/v1.0"


class VoIPbinAPIError(Exception):
    """Raised when a VoIPbin API call fails."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
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
        self.base_url = BASE_URL
        self._client = httpx.AsyncClient(timeout=30.0)

    def __repr__(self) -> str:
        base_url = getattr(self, "base_url", "<not initialised>")
        return f"VoIPbinClient(base_url={base_url!r})"

    def _params_with_auth(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add accesskey to query parameters."""
        result = dict(params) if params else {}
        result["accesskey"] = self.api_key
        return result

    def _handle_error(self, response: httpx.Response) -> None:
        """Raise descriptive errors for non-2xx responses."""
        if response.is_success:
            return

        try:
            body = response.json()
            detail = body.get("message", "")
        except Exception:
            detail = response.text

        error_map = {
            401: "Invalid or expired API key",
            402: "Insufficient credits",
            403: "Permission denied",
            404: f"Resource not found: {detail}",
            409: f"Conflict: {detail}",
        }

        message = error_map.get(response.status_code, f"API error {response.status_code}: {detail}")
        raise VoIPbinAPIError(response.status_code, message)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """Send a GET request."""
        response = await self._client.get(
            f"{self.base_url}{path}",
            params=self._params_with_auth(params),
        )
        self._handle_error(response)
        return response.json()

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict:
        """Send a POST request."""
        response = await self._client.post(
            f"{self.base_url}{path}",
            params=self._params_with_auth(),
            json=json,
        )
        self._handle_error(response)
        return response.json()

    async def put(self, path: str, json: dict[str, Any] | None = None) -> dict:
        """Send a PUT request."""
        response = await self._client.put(
            f"{self.base_url}{path}",
            params=self._params_with_auth(),
            json=json,
        )
        self._handle_error(response)
        return response.json()

    async def delete(self, path: str) -> dict:
        """Send a DELETE request."""
        response = await self._client.delete(
            f"{self.base_url}{path}",
            params=self._params_with_auth(),
        )
        self._handle_error(response)
        return response.json()

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()
