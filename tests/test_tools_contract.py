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
    delete_contact_address,
    delete_contact_tag,
    update_contact,
    update_contact_address,
)
from voipbin_mcp.tools.emails import send_email
from voipbin_mcp.tools.flows import create_flow
from voipbin_mcp.tools.routes import get_route, list_routes
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


class TestDocumentedBehaviourMatchesTheBackend:
    """Claims that a reader would act on, each pinned to a Go source fact.

    Every one of these replaced a sentence that was wrong: the docstrings are
    the only contract an LLM sees, so a false claim here is a product defect,
    not a comment typo.
    """

    def test_conference_timeout_zero_is_not_clamped(self):
        # conferencehandler/conference.go:83 is `if timeout > 0 && timeout < 60`,
        # so 0 survives and means no auto-delete.
        doc = create_conference.__doc__ or ""
        assert "0 means the conference is never auto-deleted" in doc

    def test_create_campaign_explains_the_service_level_pacing_gate(self):
        # execute.go:379-382 returns true unconditionally when queueID is Nil,
        # so passing queue_id ADDS the gate rather than enabling dialing, and
        # execute.go:403 uses integer division: agents * service_level / 100.
        doc = create_campaign.__doc__ or ""
        assert "Omitting queue_id skips that check" in doc
        assert "available_agents * service_level >= 100" in doc
        # The earlier text claimed omitting queue_id prevented dialing, which is
        # backwards. Guard against that sentence returning.
        assert "queue_id the campaign is still created" not in doc
        # queue_id's own entry must not carry a "never dials" warning: that was
        # the inverted claim. The Args entries for outplan and outdial still do,
        # which TestCampaignReferences pins.
        queue_entry = doc.split("queue_id: UUID of the queue", 1)[1].split(
            "next_campaign_id", 1
        )[0]
        assert "never dials" not in queue_entry

    def test_create_contact_warns_addresses_are_best_effort(self):
        # contacthandler/contact.go:113-115 logs and continues when
        # AddressCreate fails, then returns success anyway.
        doc = create_contact.__doc__ or ""
        assert "best-effort" in doc
        assert "add_contact_address" in doc

    def test_send_email_warns_attachments_resolve_after_the_response(self):
        # emailhandler/email.go:65 is `go h.Send(...)`, so the 201 precedes any
        # attachment resolution, and engine_sendgrid.go:75-79 logs and continues
        # when getAttachment fails. Its default branch rejects "".
        doc = send_email.__doc__ or ""
        assert 'Must be "recording"' in doc
        assert "resolved AFTER the API has answered success" in doc
        assert "still delivered" in doc
        # "" used to be documented as the way to attach nothing. It reaches the
        # default branch, fails, and is skipped after the email is accepted.
        assert 'or "" for none' not in doc

    def test_create_ai_says_an_unsupported_value_is_a_500(self):
        # aihandler/chatbot.go returns a bare fmt.Errorf for engine_model,
        # tts_type and stt_type, which listenhandler/main.go:198 maps to
        # simpleResponse(500) -> error_translate.go:94 -> INTERNAL.
        doc = create_ai.__doc__ or ""
        # Pin the DIRECTION, not the tokens: asserting only "500" and a stray
        # phrase let a mutant say "a 500 is transient, retry it" and still pass.
        # Whitespace-normalised so rewrapping the docstring does not break it.
        flat = " ".join(doc.split())
        assert "treat a 500 from this tool as a rejected argument rather than a transient fault" in flat
        assert "do not retry it unchanged" in flat
        assert "400 naming the field" not in flat

    def test_create_ai_does_not_suggest_env_var_references_for_the_key(self):
        # Nothing in ai-manager or pipecat expands the value: aihandler/db.go
        # stores engine_key verbatim, so "$OPENAI_API_KEY" is sent literally.
        flat = " ".join((create_ai.__doc__ or "").split())
        assert "stored and transmitted verbatim" in flat
        assert "Nothing expands environment-variable references" in flat
        assert "Pass an environment variable reference rather than" not in flat

    def test_create_flow_does_not_list_transfer_as_an_action_type(self):
        # models/action/action.go has no TypeTransfer; models/flow/flow.go:47
        # does, but that is a FLOW type. actionhandler rejects it with
        # INVALID_ACTION_TYPE, so listing it as an example hands out a 400.
        flat = " ".join((create_flow.__doc__ or "").split())
        assert '"transfer" is a FLOW type, not an action type' in flat
        # Catch it wherever it is reintroduced, not just in the call-control
        # group: a mutant that moved it next to the media examples survived an
        # earlier, position-specific assertion.
        examples = flat.split("There are around 40 action types", 1)[1].split(
            "Note that", 1
        )[0]
        assert "transfer" not in examples

    def test_create_conference_says_post_flow_id_is_not_executed(self):
        # grep PostFlowID across the monorepo yields only writes, a filter key
        # and a webhook echo. conferencecallhandler/terminate.go never reads it,
        # while service.go:49 does read PreFlowID on join.
        flat = " ".join((create_conference.__doc__ or "").split())
        assert "Stored but NOT executed" in flat
        assert "pre_flow_id, by contrast, is genuinely executed" in flat
        assert "flow ID to execute when a participant leaves" not in flat

    def test_contact_source_is_documented_as_unvalidated(self):
        # contacthandler/contact.go:70-71 only defaults "" to manual;
        # PostContactsJSONBodySource.Valid() has zero non-test callers.
        flat = " ".join((create_contact.__doc__ or "").split())
        assert "no layer validates this field" in flat
        assert "One of: manual, import, api, sync." not in flat

    def test_route_tools_warn_about_the_superadmin_requirement(self):
        # servicehandler/route.go checks PermissionProjectSuperAdmin, which an
        # accesskey identity never holds, so these 403 for ordinary customers.
        for doc in (list_routes.__doc__ or "", get_route.__doc__ or ""):
            flat = " ".join(doc.split())
            # Pin BOTH statements: a keyword check on "superadmin" passed even
            # when one of the two sentences was flipped to "customer permission".
            assert "Requires project superadmin permission." in flat
            assert (
                "answers 403 PERMISSION_DENIED unless your key carries project "
                "superadmin permission" in flat
            )
            assert "Requires customer permission" not in flat


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
    async def test_email_attachments_empty_list_is_transmitted(self):
        """[] is distinct from omission: the field is required by the API."""
        route = respx.post("https://api.voipbin.net/v1.0/emails").mock(
            return_value=httpx.Response(201, json={})
        )
        await send_email("a@example.com", "s", "c", attachments=[])
        assert sent_body(route)["attachments"] == []

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
        # Scoped to outplan and outdial: those two genuinely stop dialing when
        # absent (execute.go:52,63). queue_id does NOT -- see
        # TestDocumentedBehaviourMatchesTheBackend for that claim.
        doc = create_campaign.__doc__ or ""
        assert "outplan_id: UUID of the outplan" in doc
        assert "outdial_id: UUID of the outdial list" in doc
        # Both of these genuinely stop dialing, so both carry the warning.
        after_outplan = doc.split("outplan_id: UUID of the outplan", 1)[1]
        assert "never dials" in after_outplan.split("outdial_id", 1)[0]
        assert "never dials" in after_outplan.split("outdial_id", 1)[1].split(
            "queue_id", 1
        )[0]

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_string_reference_is_transmitted_not_dropped(self):
        """An empty string is a deliberate value, distinct from omission.

        A truthiness check here would silently swallow it, which is the sentinel
        bug this tool was rewritten to avoid.
        """
        route = respx.post("https://api.voipbin.net/v1.0/campaigns").mock(
            return_value=httpx.Response(200, json={})
        )
        await create_campaign(
            name="c",
            detail="d",
            campaign_type="call",
            actions=[],
            outplan_id="",
            queue_id="",
        )
        body = sent_body(route)
        assert body["outplan_id"] == ""
        assert body["queue_id"] == ""
        assert "outdial_id" not in body


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


class TestNewFieldsActuallyTransmitted:
    """Positive-path body assertions for every newly exposed field.

    The removal machinery and the omission cases were covered first, which left
    a gap: a mutant that stopped SENDING a new field kept the suite green. These
    assert the exact wire body, so dropping or misnaming a key fails here.
    """

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_contact_sends_addresses_verbatim(self):
        route = respx.post("https://api.voipbin.net/v1.0/contacts").mock(
            return_value=httpx.Response(201, json={"id": "c1"})
        )
        addresses = [
            {
                "type": "tel",
                "target": "+14155551234",
                "name": "mobile",
                "detail": "personal",
                "is_primary": True,
            }
        ]
        await create_contact(first_name="Kim", addresses=addresses)
        assert sent_body(route)["addresses"] == addresses

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_contact_sends_source_external_id_and_tags(self):
        route = respx.post("https://api.voipbin.net/v1.0/contacts").mock(
            return_value=httpx.Response(201, json={"id": "c1"})
        )
        await create_contact(
            first_name="Kim",
            source="crm",
            external_id="ext-9",
            tag_ids=["t1", "t2"],
        )
        body = sent_body(route)
        assert body["source"] == "crm"
        assert body["external_id"] == "ext-9"
        assert body["tag_ids"] == ["t1", "t2"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_add_address_sends_name_and_detail(self):
        route = respx.post("https://api.voipbin.net/v1.0/contacts/c1/addresses").mock(
            return_value=httpx.Response(201, json={})
        )
        await add_contact_address(
            "c1", "tel", "+14155551234", name="mobile", detail="personal"
        )
        body = sent_body(route)
        # The create path does carry these, unlike the update path.
        assert body["name"] == "mobile"
        assert body["detail"] == "personal"

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_address_targets_the_single_address(self):
        route = respx.delete(
            "https://api.voipbin.net/v1.0/contacts/c1/addresses/a1"
        ).mock(return_value=httpx.Response(200, json={}))
        await delete_contact_address("c1", "a1")
        assert route.call_count == 1
        assert route.calls[0].request.url.path == "/v1.0/contacts/c1/addresses/a1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_campaign_reference_wire_keys_are_exact(self):
        route = respx.post("https://api.voipbin.net/v1.0/campaigns").mock(
            return_value=httpx.Response(200, json={"id": "x1"})
        )
        await create_campaign(
            name="n",
            detail="d",
            campaign_type="call",
            actions=[],
            outplan_id="o1",
            outdial_id="od1",
            queue_id="q1",
            next_campaign_id="nc1",
        )
        body = sent_body(route)
        # A typo in any of these names would leave the campaign unable to dial
        # while still answering 200.
        assert body["outplan_id"] == "o1"
        assert body["outdial_id"] == "od1"
        assert body["queue_id"] == "q1"
        assert body["next_campaign_id"] == "nc1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_ai_parameter_is_sent_when_given(self):
        route = respx.post("https://api.voipbin.net/v1.0/ais").mock(
            return_value=httpx.Response(201, json={})
        )
        await create_ai(
            name="n",
            detail="d",
            engine_model="openai.gpt-4o",
            engine_key="k",
            init_prompt="p",
            tts_type="google",
            tts_voice_id="v",
            stt_type="google",
            stt_language="en-US",
            parameter={"temperature": 0.2},
        )
        assert sent_body(route)["parameter"] == {"temperature": 0.2}

    @respx.mock
    @pytest.mark.asyncio
    async def test_ai_parameter_empty_dict_is_transmitted(self):
        route = respx.post("https://api.voipbin.net/v1.0/ais").mock(
            return_value=httpx.Response(201, json={})
        )
        await create_ai(
            name="n",
            detail="d",
            engine_model="openai.gpt-4o",
            engine_key="k",
            init_prompt="p",
            tts_type="google",
            tts_voice_id="v",
            stt_type="google",
            stt_language="en-US",
            parameter={},
        )
        # {} is a meaningful value here, distinct from omission.
        assert sent_body(route)["parameter"] == {}


class TestUpdateAddressLimitations:
    """name and detail are deliberately not exposed on the update path.

    The gateway accepts them and answers 200, but the RPC layer copies only
    target and is_primary, so offering them would report success while the
    labels stayed unchanged.
    """

    def test_name_and_detail_are_not_parameters(self):
        import inspect

        params = inspect.signature(update_contact_address).parameters
        assert "name" not in params
        assert "detail" not in params

    def test_docstring_explains_the_workaround(self):
        doc = update_contact_address.__doc__ or ""
        assert "name and detail" in doc
        assert "add_contact_address" in doc


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


def _content(result):
    """Normalise call_tool's return across mcp versions.

    1.2.0 returns a list of content blocks; 1.30.0 returns
    (content, structured_result). A test that assumed either shape would pass on
    one end of the declared range and fail on the other, so the floor CI leg
    would go red for a reason unrelated to the code under test.
    """
    if isinstance(result, tuple):
        return result[0]
    return result


class TestCallToolRoundTrip:
    """Exercise tools the way an MCP client does, not by direct invocation.

    Direct calls bypass the schema layer entirely. These go through call_tool,
    which is where pydantic coercion, unknown-argument handling and the error
    surface an LLM actually sees all live.
    """

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_successful_call_returns_the_payload_as_text(self):
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(200, json={"result": [{"id": "c1"}]})
        )
        content = _content(await mcp.call_tool("list_calls", {}))
        assert len(content) == 1
        assert "c1" in content[0].text

    @pytest.mark.asyncio
    async def test_a_removed_parameter_reaches_the_llm_as_an_error(self):
        """The message must name the replacement, not just fail.

        A silent drop is what shipped in 0.1.x: pydantic ignored the argument
        and the contact was created with no way to reach it.
        """
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as excinfo:
            await mcp.call_tool(
                "create_contact",
                {"first_name": "Kim", "phone_numbers": [{"number": "+14155551234"}]},
            )

        message = str(excinfo.value)
        assert "removed in voipbin-mcp 0.2.0" in message
        assert "addresses" in message

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_api_error_reaches_the_llm_with_the_server_reason(self):
        respx.get("https://api.voipbin.net/v1.0/calls").mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": {
                        "message": "The provided credentials are invalid.",
                        "reason": "INVALID_CREDENTIALS",
                        "request_id": "req_1",
                        "status": 401,
                    }
                },
            )
        )
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as excinfo:
            await mcp.call_tool("list_calls", {})

        message = str(excinfo.value)
        assert "401" in message
        assert "INVALID_CREDENTIALS" in message
        # The envelope's message used to be dropped, leaving the LLM with "".
        assert "credentials are invalid" in message
        assert "req_1" in message

    @respx.mock
    @pytest.mark.asyncio
    async def test_the_schema_omits_the_fields_dict_that_was_removed(self):
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        schema = tools["update_contact"].inputSchema
        assert "fields" in schema["properties"], (
            "the parameter must stay in the schema so a legacy call raises a "
            "named error instead of an unactionable validation failure"
        )
        # But the campaign actions tool must exist as the real replacement.
        assert "update_campaign_actions" in tools
