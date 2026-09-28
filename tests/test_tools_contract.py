"""Tests for the tool-level contract decisions.

Covers the None sentinel for partial updates, the active rejection of the
0.1.x parameters, and the unit corrections.
"""

import httpx
import pytest
import respx

import voipbin_mcp.tools  # noqa: F401  (registers every tool)
from voipbin_mcp.tools.ais import create_ai
from voipbin_mcp.tools.campaigns import (
    create_campaign,
    update_campaign,
    update_campaign_actions,
)
from voipbin_mcp.tools.conferences import create_conference
from voipbin_mcp.tools.contacts import (
    add_contact_address,
    add_contact_tag,
    create_contact,
    delete_contact_tag,
    update_contact,
    update_contact_address,
)
from voipbin_mcp.tools.emails import send_email
from voipbin_mcp.server import mcp


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")


def sent_body(route):
    """The JSON body of the first request captured by a respx route."""
    import json

    return json.loads(route.calls[0].request.content)


class TestUpdateContactSentinel:
    """A PUT sends only non-None fields; the server treats absence as no change."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_only_passed_fields_are_sent(self):
        route = respx.put("https://api.voipbin.net/v1.0/contacts/c1").mock(
            return_value=httpx.Response(200, json={"id": "c1"})
        )
        await update_contact("c1", first_name="Kim")

        # The other six fields must be absent, not empty strings: the server
        # only updates keys present in the body, so sending "" would wipe them.
        assert sent_body(route) == {"first_name": "Kim"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_string_is_transmitted_to_clear_a_field(self):
        route = respx.put("https://api.voipbin.net/v1.0/contacts/c1").mock(
            return_value=httpx.Response(200, json={"id": "c1"})
        )
        await update_contact("c1", notes="")
        assert sent_body(route) == {"notes": ""}

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_arguments_sends_an_empty_body(self):
        route = respx.put("https://api.voipbin.net/v1.0/contacts/c1").mock(
            return_value=httpx.Response(200, json={"id": "c1"})
        )
        await update_contact("c1")
        assert sent_body(route) == {}


class TestUpdateCampaignSentinel:
    @respx.mock
    @pytest.mark.asyncio
    async def test_only_passed_fields_are_sent(self):
        route = respx.put("https://api.voipbin.net/v1.0/campaigns/x1").mock(
            return_value=httpx.Response(200, json={"id": "x1"})
        )
        await update_campaign("x1", name="Renamed")
        assert sent_body(route) == {"name": "Renamed"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_service_level_zero_is_transmitted(self):
        """0 is a valid service level, so a falsy check would lose it."""
        route = respx.put("https://api.voipbin.net/v1.0/campaigns/x1").mock(
            return_value=httpx.Response(200, json={"id": "x1"})
        )
        await update_campaign("x1", service_level=0)
        assert sent_body(route) == {"service_level": 0}

    @respx.mock
    @pytest.mark.asyncio
    async def test_campaign_type_maps_to_the_type_wire_key(self):
        route = respx.put("https://api.voipbin.net/v1.0/campaigns/x1").mock(
            return_value=httpx.Response(200, json={"id": "x1"})
        )
        await update_campaign("x1", campaign_type="flow")
        assert sent_body(route) == {"type": "flow"}


class TestRemovedParameters:
    """Old parameters are accepted, then refused without touching the server."""

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"phone_numbers": [{"number": "+14155551234"}]}, "phone_numbers"),
            ({"emails": [{"address": "a@b.com"}]}, "emails"),
        ],
    )
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_contact_rejects_and_sends_nothing(self, kwargs, expected):
        route = respx.post("https://api.voipbin.net/v1.0/contacts").mock(
            return_value=httpx.Response(201, json={})
        )
        with pytest.raises(ValueError) as excinfo:
            await create_contact(first_name="Kim", **kwargs)

        message = str(excinfo.value)
        assert expected in message
        assert "0.2.0" in message
        # The replacement must be actionable, not just "removed".
        assert "addresses=[" in message
        # Nothing may reach the API: a partial create is worse than an error.
        assert route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_contact_rejects_fields(self):
        route = respx.put("https://api.voipbin.net/v1.0/contacts/c1").mock(
            return_value=httpx.Response(200, json={})
        )
        with pytest.raises(ValueError) as excinfo:
            await update_contact("c1", fields={"first_name": "Kim"})

        assert "fields" in str(excinfo.value)
        assert "first_name" in str(excinfo.value)
        assert route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_campaign_rejects_fields_and_points_at_actions_tool(self):
        route = respx.put("https://api.voipbin.net/v1.0/campaigns/x1").mock(
            return_value=httpx.Response(200, json={})
        )
        with pytest.raises(ValueError) as excinfo:
            await update_campaign("x1", fields={"name": "x"})

        assert "update_campaign_actions" in str(excinfo.value)
        assert route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_omitting_the_removed_parameter_is_a_no_op(self):
        route = respx.post("https://api.voipbin.net/v1.0/contacts").mock(
            return_value=httpx.Response(201, json={"id": "c1"})
        )
        await create_contact(first_name="Kim")
        assert route.call_count == 1
        assert sent_body(route) == {"first_name": "Kim"}


class TestUnits:
    @respx.mock
    @pytest.mark.asyncio
    async def test_conference_timeout_default_is_one_hour_in_seconds(self):
        route = respx.post("https://api.voipbin.net/v1.0/conferences").mock(
            return_value=httpx.Response(201, json={})
        )
        await create_conference("standup")
        assert sent_body(route)["timeout"] == 3600

    def test_conference_timeout_docstring_says_seconds(self):
        doc = create_conference.__doc__
        assert "SECONDS" in doc
        assert "millisecond" not in doc.lower()

    def test_service_level_docstrings_say_percentage(self):
        for fn in (create_campaign, update_campaign):
            doc = fn.__doc__
            assert "percentage" in doc.lower(), fn.__name__
            assert "millisecond" not in doc.lower(), fn.__name__


class TestNewlyExposedRequiredFields:
    """Spec-required fields get defaults so existing calls keep working."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_conference_type_defaults_to_conference(self):
        route = respx.post("https://api.voipbin.net/v1.0/conferences").mock(
            return_value=httpx.Response(201, json={})
        )
        await create_conference("standup")
        body = sent_body(route)
        assert body["type"] == "conference"
        # data stays hardcoded and is not exposed as a parameter.
        assert body["data"] == {}

    @respx.mock
    @pytest.mark.asyncio
    async def test_conference_type_is_overridable(self):
        route = respx.post("https://api.voipbin.net/v1.0/conferences").mock(
            return_value=httpx.Response(201, json={})
        )
        await create_conference("bridge", conference_type="connect")
        assert sent_body(route)["type"] == "connect"

    @respx.mock
    @pytest.mark.asyncio
    async def test_ai_parameter_is_omitted_when_absent(self):
        route = respx.post("https://api.voipbin.net/v1.0/ais").mock(
            return_value=httpx.Response(201, json={})
        )
        await create_ai(
            name="bot",
            detail="d",
            engine_model="openai.gpt-4o-mini",
            engine_key="$OPENAI_KEY",
            init_prompt="hi",
            tts_type="google",
            tts_voice_id="en-US-Standard-A",
            stt_type="google",
        )
        assert "parameter" not in sent_body(route)

    @respx.mock
    @pytest.mark.asyncio
    async def test_email_attachments_are_omitted_when_absent(self):
        route = respx.post("https://api.voipbin.net/v1.0/emails").mock(
            return_value=httpx.Response(201, json={})
        )
        await send_email("a@b.com", "subject", "body")
        assert "attachments" not in sent_body(route)

    @respx.mock
    @pytest.mark.asyncio
    async def test_email_attachments_pass_through(self):
        route = respx.post("https://api.voipbin.net/v1.0/emails").mock(
            return_value=httpx.Response(201, json={})
        )
        attachment = {"reference_type": "recording", "reference_id": "r1"}
        await send_email("a@b.com", "s", "b", attachments=[attachment])
        assert sent_body(route)["attachments"] == [attachment]


class TestCampaignReferences:
    @respx.mock
    @pytest.mark.asyncio
    async def test_references_are_sent_when_given(self):
        route = respx.post("https://api.voipbin.net/v1.0/campaigns").mock(
            return_value=httpx.Response(200, json={})
        )
        await create_campaign(
            name="c",
            detail="d",
            campaign_type="call",
            actions=[],
            outplan_id="op1",
            outdial_id="od1",
            queue_id="q1",
        )
        body = sent_body(route)
        assert body["outplan_id"] == "op1"
        assert body["outdial_id"] == "od1"
        assert body["queue_id"] == "q1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_omitted_references_are_absent(self):
        route = respx.post("https://api.voipbin.net/v1.0/campaigns").mock(
            return_value=httpx.Response(200, json={})
        )
        await create_campaign(name="c", detail="d", campaign_type="call", actions=[])
        body = sent_body(route)
        for key in ("outplan_id", "outdial_id", "queue_id", "next_campaign_id"):
            assert key not in body

    def test_docstring_warns_that_omitting_them_means_no_dialing(self):
        doc = create_campaign.__doc__
        assert "never dial" in doc


class TestContactSubResources:
    @respx.mock
    @pytest.mark.asyncio
    async def test_add_address_maps_address_type_to_type(self):
        route = respx.post("https://api.voipbin.net/v1.0/contacts/c1/addresses").mock(
            return_value=httpx.Response(201, json={})
        )
        await add_contact_address("c1", "tel", "+14155551234")
        assert sent_body(route) == {"type": "tel", "target": "+14155551234"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_add_address_never_sends_target_name(self):
        """The gateway ignores target_name, so the tool must not offer it."""
        route = respx.post("https://api.voipbin.net/v1.0/contacts/c1/addresses").mock(
            return_value=httpx.Response(201, json={})
        )
        await add_contact_address(
            "c1", "email", "a@b.com", name="Work", detail="d", is_primary=True
        )
        assert "target_name" not in sent_body(route)

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_address_cannot_change_type(self):
        route = respx.put(
            "https://api.voipbin.net/v1.0/contacts/c1/addresses/a1"
        ).mock(return_value=httpx.Response(200, json={}))
        await update_contact_address("c1", "a1", target="+14155559999")
        assert sent_body(route) == {"target": "+14155559999"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_address_is_primary_false_is_transmitted(self):
        route = respx.put(
            "https://api.voipbin.net/v1.0/contacts/c1/addresses/a1"
        ).mock(return_value=httpx.Response(200, json={}))
        await update_contact_address("c1", "a1", is_primary=False)
        assert sent_body(route) == {"is_primary": False}

    @respx.mock
    @pytest.mark.asyncio
    async def test_add_tag(self):
        route = respx.post("https://api.voipbin.net/v1.0/contacts/c1/tags").mock(
            return_value=httpx.Response(201, json={})
        )
        await add_contact_tag("c1", "t1")
        assert sent_body(route) == {"tag_id": "t1"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_tag_uses_the_path(self):
        route = respx.delete(
            "https://api.voipbin.net/v1.0/contacts/c1/tags/t1"
        ).mock(return_value=httpx.Response(200, json={}))
        await delete_contact_tag("c1", "t1")
        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_campaign_actions_uses_the_actions_endpoint(self):
        route = respx.put("https://api.voipbin.net/v1.0/campaigns/x1/actions").mock(
            return_value=httpx.Response(200, json={})
        )
        actions = [{"type": "answer"}]
        await update_campaign_actions("x1", actions)
        assert sent_body(route) == {"actions": actions}


class TestRegistration:
    @pytest.mark.asyncio
    async def test_every_tool_is_registered(self):
        """Guards against a new tool that is written but never imported."""
        from pathlib import Path

        tools_dir = Path(create_contact.__module__.replace(".", "/")).parent
        source_root = Path(__file__).resolve().parent.parent / "src" / tools_dir
        declared = sum(
            path.read_text().count("@mcp.tool()")
            for path in source_root.glob("*.py")
        )
        registered = len(await mcp.list_tools())
        assert registered == declared

    @pytest.mark.asyncio
    async def test_contact_sub_resource_tools_are_exposed(self):
        names = {tool.name for tool in await mcp.list_tools()}
        assert {
            "add_contact_address",
            "update_contact_address",
            "delete_contact_address",
            "add_contact_tag",
            "delete_contact_tag",
            "update_campaign_actions",
        } <= names
