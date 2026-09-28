"""Tests for the tool-level contract decisions.

Covers the None sentinel for partial updates, the active rejection of the
0.1.x parameters, and the unit corrections.
"""

import importlib
import json
import os
import pathlib
import pkgutil
import re
from pathlib import Path

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



# Claims that carry a verified Go fact are pinned by EXACT TEXT, not by keyword.
#
# Three earlier designs failed the same way. Asserting that a phrase is present
# (and some reversal absent) can only ban wording the author already imagined,
# and a reviewer produced a fresh evasion every round: text that keeps every
# pinned phrase, avoids every banned phrase, and still tells the reader the
# opposite of the truth by APPENDING a scope ("that applied to 0.1.x only"),
# a qualifier ("except when the engine is a hosted vendor"), or a retraction
# ("the gate is disabled on production deployments").
#
# Exact-text pinning is closed under text nobody anticipated: any edit to a
# claim-bearing region fails, additive or subtractive. The cost is intentional
# -- rewording a claim requires re-checking it against the Go source named
# beside it and updating the pin deliberately.
#
# Each entry: (accessor, go_source_reference, exact normalised claim text).
PINNED_CLAIMS: tuple[tuple[str, str, str], ...] = (
    (
        "conferences.create_conference",
        "bin-conference-manager/pkg/conferencehandler/conference.go:83-84,24",
        "timeout: Conference lifetime in SECONDS (default 3600, one hour). 0 "
        "means the conference is never auto-deleted. Any other value below 60 "
        "is replaced by the server default of 86400.",
    ),
    (
        "conferences.create_conference",
        "conference.go:110 writes it; no reader outside webhook/filter",
        "post_flow_id: Stored but NOT executed. The field is accepted and "
        "persisted, and the API answers success, but no code path runs it when "
        "a participant leaves. Do not rely on it for cleanup work. "
        "(pre_flow_id, by contrast, is genuinely executed on join.)",
    ),
    (
        "campaigns.create_campaign",
        "bin-campaign-manager/pkg/campaignhandler/execute.go:71-84",
        "next_campaign_id: Stored and validated, but NOT acted on. When a "
        "campaign runs out of targets the server branches only on end_handle "
        "(stop, or re-execute after 5s); nothing starts the campaign named "
        "here. Chain campaigns yourself instead of relying on this field.",
    ),
    (
        "campaigns.create_campaign",
        "execute.go:379-382 (nil queue => dialable), :403, :405",
        "queue_id behaves differently, and not the way it reads. It is where "
        "answered calls are delivered, and passing it ENABLES a pacing gate: "
        "the server then only dials while available_agents * service_level / "
        "100 > calls_already_dialing, using integer division. Omitting "
        "queue_id skips that check entirely. So a queued campaign needs "
        "available_agents * service_level >= 100 before it dials at all, which "
        "the default service_level of 0 never satisfies, and neither does one "
        "available agent at service_level 50.",
    ),
    (
        "emails.send_email",
        "emailhandler/email.go:65 (go h.Send); send.go:16-22,42 (sendgrid then "
        "mailgun); engine_sendgrid.go:75-79 (logs and continues) vs "
        "engine_mailgun.go:79-82 (returns an error, aborting the send)",
        "Attachments are resolved AFTER the API has answered success: the send "
        "runs in the background, so nothing about an attachment is reported "
        "back. What happens to an unresolvable attachment (an unsupported "
        "reference_type, or a reference_id that does not exist) depends on "
        "which provider handles the message. The primary logs it and sends the "
        "email without it; the fallback, used when the primary fails, treats "
        "it as an error and sends NOTHING. So a success response here confirms "
        "only that the email was accepted, never that an attachment was "
        "included, and never that the email went out at all. To send with no "
        "attachments, omit this argument entirely rather than passing a "
        "placeholder entry.",
    ),
    (
        "ais.create_ai",
        "aihandler/chatbot.go:41,56,62 -> listenhandler/main.go:198 (500)",
        "An unsupported engine_model, tts_type or stt_type comes back as a 500 "
        "INTERNAL error with no indication of which field was wrong, so treat "
        "a 500 from this tool as a rejected argument rather than a transient "
        "fault, and do not retry it unchanged.",
    ),
    (
        "ais.create_ai",
        "aihandler/db.go:71 stores verbatim; no expansion anywhere",
        "engine_key: API key for the LLM provider, stored and transmitted "
        "verbatim. Nothing expands environment-variable references, so passing "
        '"$OPENAI_API_KEY" sends that literal string to the provider and the '
        "agent fails to authenticate. The value is returned in API responses "
        "and appears in server logs, so treat it as exposed.",
    ),
    (
        "flows.create_flow",
        "bin-flow-manager/models/flow/flow.go:47 vs models/action/action.go; actionhandler/action.go:22-37",
        'Note that "transfer" is a FLOW type, not an action type, and is '
        "rejected here. An unsupported type is rejected with a 400 naming the "
        "action; consult the VoIPbin API documentation for the full list and "
        "each type's option schema.",
    ),
    (
        "contacts.create_contact",
        "bin-contact-manager/pkg/contacthandler/contact.go:70-71; gen.go Valid() has no caller",
        "source: Where the contact came from. The values the platform uses are "
        "manual, import, api and sync, but no layer validates this field, so "
        "an unrecognised string is stored as given. Omit it to get manual.",
    ),
    (
        "routes.list_routes",
        "bin-api-manager/pkg/servicehandler/route.go:51,86,129; error_translate.go:81-82",
        "Requires project superadmin permission. Routes are a platform-level "
        "resource. A customer access key cannot read them, so this tool "
        "answers 403 PERMISSION_DENIED unless your key carries project "
        "superadmin permission.",
    ),
)


def _resolve(accessor: str):
    import importlib

    module_name, attr = accessor.split(".")
    module = importlib.import_module(f"voipbin_mcp.tools.{module_name}")
    return getattr(module, attr)


def normalised_doc(fn) -> str:
    return " ".join((fn.__doc__ or "").split())


# A claim is pinned by its DIRECTION, not its keywords. Three times running, a
# mutant kept every pinned token and appended a contradiction ("...is a myth",
# "on current production it IS executed"), so each guard below names the
# specific reversal of the specific claim it protects. A blanket phrase list was
# tried first and rejected: "rejected with a 400" is a TRUE statement about flow
# action types and address types, so banning it outright fails honest text.
def assert_claim(doc: str, *, says: tuple[str, ...], never: tuple[str, ...]) -> str:
    """Whitespace-normalise a docstring; require every claim, refuse reversals."""
    flat = " ".join((doc or "").split())
    lowered = flat.lower()
    for phrase in says:
        assert phrase.lower() in lowered, (
            f"docstring no longer states {phrase!r}. Full text: {flat}"
        )
    for phrase in never:
        assert phrase.lower() not in lowered, (
            f"docstring contains {phrase!r}, which reverses the claim this test "
            f"pins. Full text: {flat}"
        )
    return flat


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
        assert_claim(
            create_conference.__doc__,
            says=("0 means the conference is never auto-deleted",),
            never=("is a myth", "clamped to 60", "0 is in fact"),
        )

    def test_create_campaign_says_next_campaign_id_does_not_chain(self):
        # execute.go:72-84 branches only on EndHandle when targets run out;
        # NextCampaignID is validated (status_run.go:35) and stored, but no
        # code path starts the campaign it names.
        assert_claim(
            create_campaign.__doc__,
            says=("stored and validated, but not acted on", "branches only on"),
            never=(
                "campaign to chain to when this one ends",
                "automatically starts",
                "chains to",
            ),
        )

    def test_create_campaign_explains_the_service_level_pacing_gate(self):
        # execute.go:379-382 returns true unconditionally when queueID is Nil,
        # so passing queue_id ADDS the gate rather than enabling dialing, and
        # execute.go:403 uses integer division: agents * service_level / 100.
        doc = assert_claim(
            create_campaign.__doc__,
            says=(
                "omitting queue_id skips that check",
                "available_agents * service_level >= 100",
            ),
            never=(
                "makes the campaign dial sooner",
                "always pass queue_id",
                "dials sooner",
            ),
        )
        # The earlier text claimed omitting queue_id prevented dialing, which is
        # backwards. Guard against that sentence returning.
        assert "queue_id the campaign is still created" not in doc
        # queue_id's own entry must not carry a "never dials" warning: that was
        # the inverted claim. The Args entries for outplan and outdial still do,
        # which TestCampaignReferences pins. Slice to the NEXT Args entry rather
        # than to a named sibling: naming one couples this test to argument
        # order, so moving or renaming that sibling would silently widen the
        # slice instead of failing.
        after_queue_id = doc.split("queue_id: UUID of the queue", 1)
        assert len(after_queue_id) == 2, "queue_id Args entry not found"
        queue_entry = re.split(r"\b\w+_?\w*: ", after_queue_id[1], maxsplit=1)[0]
        assert queue_entry.strip(), "sliced an empty queue_id entry"
        assert "never dials" not in queue_entry

    def test_create_contact_warns_addresses_are_best_effort(self):
        # contacthandler/contact.go:113-115 logs and continues when
        # AddressCreate fails, then returns success anyway.
        doc = create_contact.__doc__ or ""
        assert "best-effort" in doc
        assert "add_contact_address" in doc

    def test_send_email_warns_attachments_resolve_after_the_response(self):
        # emailhandler/email.go:65 is `go h.Send(...)`, so the 201 precedes any
        # attachment resolution. The outcome is PROVIDER-DEPENDENT:
        # emailhandler/send.go:16-22 tries sendgrid then mailgun;
        # engine_sendgrid.go:75-79 logs and continues on a failed attachment,
        # but engine_mailgun.go:79-82 returns an error, aborting the whole send,
        # after which send.go:42 only logs "all email providers failed".
        doc = assert_claim(
            send_email.__doc__,
            says=(
                'must be "recording"',
                "resolved after the api has answered success",
                "sends nothing",
            ),
            never=(
                "does confirm the attachment",
                "201 therefore confirms",
                "confirms the attachment was included",
            ),
        )
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
        flat = assert_claim(
            doc,
            says=(
                "treat a 500 from this tool as a rejected argument rather than "
                "a transient fault",
                "do not retry it unchanged",
            ),
            never=("400 naming the field", "a 500 is transient", "retry it unchanged after"),
        )
        assert flat

    def test_create_ai_does_not_suggest_env_var_references_for_the_key(self):
        # Nothing in ai-manager or pipecat expands the value: aihandler/db.go
        # stores engine_key verbatim, so "$OPENAI_API_KEY" is sent literally.
        assert_claim(
            create_ai.__doc__,
            says=("stored and transmitted verbatim", "nothing expands environment-variable references"),
            never=(
                "pass an environment variable reference rather than",
                "expands it",
                "at the mcp layer",
            ),
        )

    def test_create_flow_does_not_list_transfer_as_an_action_type(self):
        # models/action/action.go has no TypeTransfer; models/flow/flow.go:47
        # does, but that is a FLOW type. actionhandler rejects it with
        # INVALID_ACTION_TYPE, so listing it as an example hands out a 400.
        flat = assert_claim(
            create_flow.__doc__,
            says=('"transfer" is a FLOW type, not an action type',),
            never=("accepted here as an alias", "is an alias for"),
        )
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
        assert_claim(
            create_conference.__doc__,
            says=("stored but not executed", "pre_flow_id, by contrast, is genuinely executed"),
            never=(
                "flow id to execute when a participant leaves",
                "is executed on leave",
                "on current production",
            ),
        )

    def test_contact_source_is_documented_as_unvalidated(self):
        # contacthandler/contact.go:70-71 only defaults "" to manual;
        # PostContactsJSONBodySource.Valid() has zero non-test callers.
        flat = assert_claim(
            create_contact.__doc__,
            says=("no layer validates this field", "stored as given"),
            never=("one of: manual, import, api, sync.", "the server validates it"),
        )
        # The source sentence specifically must not claim rejection.
        source_sentence = flat.split("source: Where the contact came from", 1)[1].split(
            "external_id:", 1
        )[0]
        assert "rejected with a 400" not in source_sentence.lower()

    def test_route_tools_warn_about_the_superadmin_requirement(self):
        # servicehandler/route.go checks PermissionProjectSuperAdmin, which an
        # accesskey identity never holds, so these 403 for ordinary customers.
        for doc in (list_routes.__doc__ or "", get_route.__doc__ or ""):
            flat = assert_claim(
                doc,
                says=("requires project superadmin permission.",),
                never=(
                    "in practice every customer",
                    "carries this permission, so you can call this freely",
                    "requires customer permission",
                ),
            )
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


class TestEnvironmentSanity:
    """Guard the harness itself.

    A stale editable install once pointed pytest at a COPY of the source tree
    while mutations were written to the worktree, so an entire 28-mutant sweep
    reported every mutant as surviving. Nothing else in the suite can detect
    that: every test passes happily against the copy.

    Identity is checked by CONTENT, not by path. A wheel-install leg legitimately
    imports from site-packages, so requiring the worktree path would fail the
    very runs that exercise the shipped artifact -- but in both cases the bytes
    must match the checkout, which is exactly what a stale copy violates.
    """

    def test_the_suite_imports_the_source_under_test(self):
        import voipbin_mcp.tools.campaigns as imported_module

        imported = Path(imported_module.__file__).resolve()
        checkout = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "voipbin_mcp"
            / "tools"
            / "campaigns.py"
        ).resolve()
        if imported == checkout:
            return
        assert imported.read_bytes() == checkout.read_bytes(), (
            f"tests import {imported}, whose contents differ from {checkout}. "
            "Edits to the checkout are not being exercised: reinstall from this "
            "directory before trusting any result, especially a mutation run."
        )


class TestServerIdentity:
    def test_server_reports_the_installed_distribution_version(self):
        # FastMCP takes no version parameter, so without an override serverInfo
        # advertises the mcp SDK's version (e.g. "1.30.0") as the server's own,
        # leaving a client unable to tell a fixed install from the broken 0.1.x.
        from importlib.metadata import version

        import voipbin_mcp.server as server

        assert server.__version__ == version("voipbin-mcp")
        assert server.mcp._mcp_server.version == server.__version__
        # And it must not be the SDK's version, which is what the bug looked like.
        assert server.mcp._mcp_server.version != version("mcp")


class TestPinnedClaims:
    """Exact-text pinning for every claim backed by a verified Go fact.

    A keyword guard can only ban reversals the author imagined; three rounds of
    review produced three fresh evasions that kept the keywords and appended a
    scope, a qualifier or a retraction. Comparing the whole claim verbatim is
    closed under text nobody anticipated.
    """

    @pytest.mark.parametrize(
        "accessor,go_ref,claim",
        PINNED_CLAIMS,
        ids=[f"{a}:{r.split(':')[0]}" for a, r, _ in PINNED_CLAIMS],
    )
    def test_claim_text_is_unchanged(self, accessor, go_ref, claim):
        doc = normalised_doc(_resolve(accessor))
        assert claim in doc, (
            f"the pinned claim for {accessor} changed.\n\n"
            f"Verified against: {go_ref}\n\n"
            f"Expected verbatim:\n  {claim}\n\n"
            "If the backend genuinely changed, re-read the Go source above, "
            "then update BOTH the docstring and PINNED_CLAIMS together. Do not "
            "update the pin to match a reworded docstring without re-verifying."
        )

    @pytest.mark.parametrize(
        "accessor,go_ref,claim",
        PINNED_CLAIMS,
        ids=[f"{a}:{r.split(':')[0]}" for a, r, _ in PINNED_CLAIMS],
    )
    def test_claim_is_not_followed_by_a_retraction(self, accessor, go_ref, claim):
        # Appending after a pinned claim is how every evasion worked: the claim
        # survives intact and the sentence after it takes the meaning back.
        # Require the claim to be followed by the end of the docstring or by a
        # new Args entry, never by more prose about the same field.
        doc = normalised_doc(_resolve(accessor))
        tail = doc.split(claim, 1)[1].strip()
        if not tail:
            return
        # A new Args entry must name a REAL parameter of this tool. Matching
        # any lowercase word followed by a colon is not enough: "note:" and
        # "warning:" satisfy that and were each able to carry a retraction
        # (verified -- both survived before this was tightened). An example
        # block is also a legitimate terminator: it stops describing the claim
        # and starts showing usage.
        import inspect

        params = set(inspect.signature(_resolve(accessor)).parameters)
        next_token = re.match(r"^([a-z_]+):\s", tail)
        starts_new_arg = bool(next_token) and next_token.group(1) in params
        assert (
            starts_new_arg
            or tail.startswith("Args:")
            or tail.startswith("Example")
        ), (
            f"text follows the pinned claim for {accessor} without starting a "
            f"new Args entry, which is how a claim gets scoped away:\n\n"
            f"  ...{claim[-60:]}\n  >>> {tail[:200]}\n\n"
            f"Verified against: {go_ref}"
        )


GOLDEN_PATH = pathlib.Path(__file__).parent / "golden_docstrings.json"


class TestGoldenDocstrings:
    """Every tool description is pinned WHOLE, by exact equality.

    Three narrower designs were each defeated by a reviewer, and each failed the
    same way: they asked whether some approved text was PRESENT.

      1. keyword assertions -- "500" in doc plus "do not retry it unchanged"
         both held while the docstring said 500 is transient, retry it.
      2. position-independent phrase checks -- defeated by moving a term into a
         list where it no longer applied, and by a claim occurring twice so
         reversing one site left the other to satisfy the check.
      3. says=/never= tuples -- defeated by APPENDING a scope ("that applied to
         the 0.1.x gateway only"), and after the terminator was tightened, by
         PREPENDING one ("ignore the sentence below, retained for history").

    Containment cannot pin direction, because text is not closed under
    insertion: whatever is required to be present stays present when a reversal
    is written around it. Exact equality is closed under insertion anywhere, so
    it is the only check that cannot be worked around by adding words.

    The cost is deliberate: any docstring edit, even an honest typo fix, fails
    here until the golden file is regenerated. That is the point. Ten docstring
    claims have been found FALSE against the Go source, so regenerating is
    exactly the moment to re-read the Go code named in PINNED_CLAIMS and confirm
    the behaviour still holds.

    Regenerate with:
        python scripts/update_golden_docstrings.py
    """

    @staticmethod
    def _golden() -> dict[str, str]:
        return json.loads(GOLDEN_PATH.read_text())

    @pytest.mark.asyncio
    async def test_no_tool_description_has_drifted(self):
        golden = self._golden()
        live = {t.name: " ".join((t.description or "").split()) for t in await mcp.list_tools()}
        drifted = {
            name: (golden[name], live[name])
            for name in sorted(set(golden) & set(live))
            if golden[name] != live[name]
        }
        assert not drifted, (
            "Tool description(s) changed:\n"
            + "\n\n".join(
                f"--- {name}\nGOLDEN: {g}\nLIVE:   {live_text}"
                for name, (g, live_text) in drifted.items()
            )
            + "\n\nIf the new text is correct, re-read the Go source named beside "
            "this claim in PINNED_CLAIMS, confirm it still says what the docstring "
            "says, then run scripts/update_golden_docstrings.py."
        )

    @pytest.mark.asyncio
    async def test_golden_covers_exactly_the_registered_tools(self):
        """A pin nobody notices is missing protects nothing.

        A reviewer retargeted one pin to a second tool, leaving the first with
        zero coverage and the table with a duplicate, and nothing failed.
        """
        golden = set(self._golden())
        live = {t.name for t in await mcp.list_tools()}
        assert golden == live, (
            f"missing from golden: {sorted(live - golden)}; "
            f"stale in golden: {sorted(golden - live)}"
        )

    def test_every_pinned_claim_names_a_registered_tool(self):
        """Guards against a pin drifting onto a tool that no longer exists."""
        for accessor, _ref, _claim in PINNED_CLAIMS:
            assert _resolve(accessor) is not None, accessor

    def test_pinned_claims_have_no_duplicate_targets_for_one_claim(self):
        seen: dict[tuple[str, str], int] = {}
        for accessor, _ref, claim in PINNED_CLAIMS:
            key = (accessor, claim)
            seen[key] = seen.get(key, 0) + 1
        dupes = [k for k, n in seen.items() if n > 1]
        assert not dupes, f"duplicate pins: {dupes}"

    def test_the_set_of_pinned_tools_is_itself_pinned(self):
        """Retargeting a pin must not silently un-cover a tool.

        A reviewer pointed one pin at a sibling tool, which left the original
        with zero coverage while the table still looked full. Keying the
        duplicate check on (accessor, claim) does not catch that, because the
        retargeted entry is a new accessor with a new claim. The set of covered
        tools is therefore pinned too.
        """
        # Tool -> how many distinct claims are pinned on it. Counting matters:
        # several tools legitimately carry two pins, so a set alone would let a
        # pin move from a two-pin tool onto a one-pin tool unnoticed.
        expected = {
            "ais.create_ai": 2,
            "campaigns.create_campaign": 2,
            "conferences.create_conference": 2,
            "contacts.create_contact": 1,
            "emails.send_email": 1,
            "flows.create_flow": 1,
            "routes.list_routes": 1,
        }
        actual: dict[str, int] = {}
        for accessor, _ref, _claim in PINNED_CLAIMS:
            actual[accessor] = actual.get(accessor, 0) + 1
        assert actual == expected, (
            f"pin coverage changed.\nexpected: {expected}\nactual:   {actual}\n"
            "Adding a pin is good, but update this mapping deliberately so a "
            "RETARGETED pin cannot pass as a new one."
        )

    def test_every_go_reference_looks_like_a_real_source_location(self):
        """A reference nobody reads is decoration.

        A reviewer swapped a pin's reference for `nonexistent.go:9999` and
        nothing failed. The monorepo is not available in CI, so its contents
        cannot be checked here; what is enforced is the shape, plus the service
        directory being one that actually exists in the platform.
        """
        known_services = {
            "bin-ai-manager",
            "bin-api-manager",
            "bin-campaign-manager",
            "bin-common-handler",
            "bin-conference-manager",
            "bin-contact-manager",
            "bin-email-manager",
            "bin-flow-manager",
        }
        for accessor, ref, _claim in PINNED_CLAIMS:
            assert re.search(r"\.go:\d+", ref), (
                f"{accessor}: reference must name a Go file and line, got {ref!r}"
            )
            for service in re.findall(r"(bin-[a-z-]+)/", ref):
                assert service in known_services, (
                    f"{accessor}: unknown service {service!r} in reference {ref!r}"
                )

    @pytest.mark.skipif(
        not os.environ.get("VOIPBIN_MONOREPO"),
        reason="set VOIPBIN_MONOREPO=/path/to/monorepo to verify pin references resolve",
    )
    def test_go_references_resolve_in_the_monorepo(self):
        """Opt-in: prove each pin's Go file actually exists.

        CI has no monorepo checkout, and the shape check above cannot tell a
        real filename from a plausible one inside a real service directory (a
        reviewer swapped in `nonexistent.go:9999` and the shape check passed).
        Run this locally before regenerating the golden file:

            VOIPBIN_MONOREPO=~/gitvoipbin/monorepo pytest tests/ -k resolve
        """
        root = pathlib.Path(os.environ["VOIPBIN_MONOREPO"])
        missing = []
        for accessor, ref, _claim in PINNED_CLAIMS:
            for rel in re.findall(r"([\w./-]+\.go)", ref):
                name = rel.split("/")[-1]
                if (root / rel).exists():
                    continue
                if any(True for _ in root.rglob(name)):
                    continue
                missing.append(f"{accessor}: {rel}")
        assert not missing, f"pin references that resolve to nothing: {missing}"



class TestPageSizeIsAlwaysClamped:
    """Every paginated tool must route page_size through validate_page_size.

    A reviewer removed the clamp from one list tool and the whole suite stayed
    green, because no test tied a list tool to the clamp. Rather than add one
    test per tool and leave the next one uncovered, this walks the source: any
    tool taking a page_size argument must pass it through the validator.
    """

    def test_no_list_tool_sends_page_size_unclamped(self):
        import inspect

        import voipbin_mcp.tools as tools_pkg

        # Walk the package's real modules. Reading __all__ would have made this
        # test vacuous: the package does not define one, so the loop body never
        # ran and the test passed while examining nothing.
        module_names = [m.name for m in pkgutil.iter_modules(tools_pkg.__path__)]
        assert len(module_names) >= 9, (
            f"expected the tool modules to be discoverable, found {module_names}"
        )

        checked = 0
        offenders = []
        for module_name in module_names:
            module = importlib.import_module(f"voipbin_mcp.tools.{module_name}")
            for name, fn in vars(module).items():
                target = getattr(fn, "__wrapped__", fn)
                if not callable(target) or not hasattr(target, "__code__"):
                    continue
                if target.__module__ != module.__name__:
                    continue
                try:
                    params = inspect.signature(target).parameters
                except (TypeError, ValueError):  # pragma: no cover
                    continue
                if "page_size" not in params:
                    continue
                checked += 1
                src = inspect.getsource(target)
                if "validate_page_size(page_size)" not in src:
                    offenders.append(f"{module_name}.{name}")
        # Without this the test would pass if the signature walk silently
        # matched nothing, which is exactly how the first version of it broke.
        assert checked >= 15, f"only inspected {checked} paginated tools"
        assert not offenders, (
            "these tools accept page_size but do not clamp it: "
            f"{sorted(offenders)}"
        )
