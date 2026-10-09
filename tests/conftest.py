"""Shared pytest configuration."""

import pytest

# Variables the client and server read from the process environment. A developer
# who uses the server locally has these exported, and an ambient value would
# otherwise leak into every test that relies on a default (for example the
# base URL), failing dozens of tests for reasons unrelated to the code under
# test. Tests that need a value set it themselves with monkeypatch.setenv.
# VOIPBIN_MONOREPO is deliberately left alone: it is the opt-in switch for the
# source-pinned claim tests.
_AMBIENT_VARS = (
    "VOIPBIN_API_KEY",
    "VOIPBIN_API_BASE_URL",
    "VOIPBIN_AUTH_TRANSPORT",
)


@pytest.fixture(autouse=True)
def _isolate_voipbin_env(monkeypatch):
    for name in _AMBIENT_VARS:
        monkeypatch.delenv(name, raising=False)
