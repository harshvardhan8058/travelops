"""The planner asks the provider for a SHAPE, not just for JSON — Phase 5.

The live Phase 3 planner failed with the provider behaving perfectly: HTTP 200,
`finish_reason=stop`, `openai/gpt-oss-120b`, and a complete plan nested under a `final` key.
`response_format={"type": "json_object"}` had promised valid JSON and delivered exactly that.
Nothing had ever told the provider what the object should look like.

These tests pin the two halves of the fix, which must both hold:

    the request     carries a strict JSON Schema, so a cooperating endpoint cannot emit the
                    wrapper in the first place
    the response    is still validated against the unchanged `PlannerResponse`, so an endpoint
                    that ignores the schema is refused exactly as it is today

The second half is why the first is not a weakening. `PLANNER_WIRE_SCHEMA` narrows what a provider
may send; it has no vote on what this system accepts. `test_planner_live_contract.py` owns the
rejection side, including the wrapper regression.

Owner: Stream A (`backend/app/llm/**`).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agents.contract import ExplanationResponse, PlannerResponse
from app.agents.planner import PlannerAgent
from app.config import get_settings
from app.llm.client import PLANNER_WIRE_SCHEMA, LLMClient
from app.models.enums import ActionStatus, ActionType
from tests.llm_transport_stub import EXPLANATION_JSON, RecordingTransport

REFS = ["incident:INC-2026-0820-VOBL-01", "flight:1"]

#: A plan in exactly the shape the wire schema admits: no `inputs` key, every declared root
#: property present. This is what a compliant provider returns.
WIRE_SHAPED_PLAN = {
    "status": "success",
    "reason": "Weather at VOBL; protect time-sensitive connections before accommodation.",
    "evidence_refs": REFS,
    "payload_type": "planner.v1",
    "tasks": [
        {"action": "check_connections", "target_refs": REFS, "depends_on": []},
        {
            "action": "reserve_hotel_block",
            "target_refs": REFS,
            "depends_on": ["find_hotel_options"],
        },
    ],
}


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def groq_provider(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _propose(monkeypatch, *, content: str | None = None) -> RecordingTransport:
    """Drive the REAL `PlannerAgent` over a stubbed transport and return what went on the wire."""
    body = content or json.dumps(WIRE_SHAPED_PLAN)
    stub = RecordingTransport().returns(body).install(monkeypatch)
    await PlannerAgent().propose(incident_reference="INC-2026-0820-VOBL-01", flight_id=1)
    return stub


def _json_schema(sent: dict) -> dict:
    return sent["response_format"]["json_schema"]


def _objects(node: object) -> list[dict]:
    """Every object schema in the tree, so an invariant can be asserted over all of them."""
    found: list[dict] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_objects(item))
    return found


# ---------------------------------------------------------------- the request


class TestThePlannerRequestAsksForTheShape:
    async def test_the_response_format_is_json_schema_not_json_object(
        self, openrouter, monkeypatch
    ):
        """The defect in one assertion. `json_object` is what allowed `{"final": {...}}`."""
        stub = await _propose(monkeypatch)

        assert stub.last["json"]["response_format"]["type"] == "json_schema"

    async def test_strict_is_true(self, openrouter, monkeypatch):
        """`strict` is what asks an endpoint to CONSTRAIN decoding rather than be advised by it."""
        stub = await _propose(monkeypatch)

        assert _json_schema(stub.last["json"])["strict"] is True

    async def test_the_planner_wire_schema_is_the_one_sent(self, openrouter, monkeypatch):
        stub = await _propose(monkeypatch)

        block = _json_schema(stub.last["json"])
        assert block["name"] == PLANNER_WIRE_SCHEMA.name
        assert block["schema"] == PLANNER_WIRE_SCHEMA.schema

    async def test_openrouter_is_told_to_require_the_parameters(self, openrouter, monkeypatch):
        """Support is per ENDPOINT, not per model.

        `openai/gpt-oss-120b` is served by endpoints that advertise `structured_outputs` and by
        endpoints that do not. Without this, OpenRouter's load balancer can route to one that
        ignores the schema — reproducing the original defect intermittently, which is worse than
        reproducing it every time.
        """
        stub = await _propose(monkeypatch)

        assert stub.last["json"]["provider"] == {"require_parameters": True}

    async def test_the_rest_of_the_request_is_unchanged(self, openrouter, monkeypatch):
        """Model, messages, temperature and the sized budget are all as they were."""
        stub = await _propose(monkeypatch)

        sent = stub.last["json"]
        assert sent["model"] == "openai/gpt-oss-120b"
        assert [message["role"] for message in sent["messages"]] == ["system", "user"]
        assert isinstance(sent["max_tokens"], int)
        assert "temperature" in sent

    async def test_groq_gets_the_schema_but_not_openrouters_routing_field(
        self, groq_provider, monkeypatch
    ):
        """`provider` is an OpenRouter routing directive; Groq has no such field.

        Groq documents strict structured output for this same model, so the schema itself travels;
        only the routing hint is provider-specific.
        """
        stub = await _propose(monkeypatch)

        sent = stub.last["json"]
        assert sent["response_format"]["type"] == "json_schema"
        assert "provider" not in sent


class TestTheProseAgentsAreUntouched:
    """The blast radius. Both prose agents tolerate decoration on purpose and must keep doing so."""

    async def test_an_explanation_still_uses_json_object_mode(self, openrouter, monkeypatch):
        stub = RecordingTransport().returns(EXPLANATION_JSON).install(monkeypatch)

        await LLMClient().call(
            prompt="explain",
            system="You are the Recovery Explainer. Reply with JSON.",
            response_schema=ExplanationResponse,
            agent_name="explainer",
            prompt_version="explainer.v1",
        )

        sent = stub.last["json"]
        assert sent["response_format"] == {"type": "json_object"}
        assert "provider" not in sent, "no routing restriction for a call sending no schema"

    async def test_a_call_without_a_wire_schema_still_succeeds(self, openrouter, monkeypatch):
        RecordingTransport().returns(EXPLANATION_JSON).install(monkeypatch)

        parsed, audit = await LLMClient().call(
            prompt="explain",
            system="You are the Recovery Explainer. Reply with JSON.",
            response_schema=ExplanationResponse,
            agent_name="explainer",
            prompt_version="explainer.v1",
        )

        assert parsed.explanation
        assert audit.generator == "openrouter:openai/gpt-oss-120b"


# ---------------------------------------------------------------- the schema itself


class TestTheWireSchemaSatisfiesTheStrictSubset:
    """Requirements strict providers impose that `model_json_schema()` does not satisfy."""

    def test_every_object_forbids_additional_properties(self):
        objects = _objects(PLANNER_WIRE_SCHEMA.schema)
        assert objects, "no object schemas found; the walk is not reaching the tree"
        for schema in objects:
            assert schema.get("additionalProperties") is False

    def test_every_declared_property_is_required(self):
        for schema in _objects(PLANNER_WIRE_SCHEMA.schema):
            declared = sorted(schema.get("properties", {}))
            assert sorted(schema.get("required", [])) == declared

    def test_no_unsupported_cardinality_or_length_keywords_are_sent(self):
        """`minItems`/`minLength`/`maxLength` are outside the portable strict subset.

        They are not merely unsupported — an endpoint may reject the whole schema for carrying
        one, which would fail every planner call. The guarantees they would express live in
        `PlannerResponse` instead, and the tests below prove they are still enforced.
        """
        serialised = json.dumps(PLANNER_WIRE_SCHEMA.schema)
        for keyword in ("minItems", "maxItems", "minLength", "maxLength", "pattern", "format"):
            assert keyword not in serialised

    def test_the_root_is_a_plain_object_rather_than_a_union(self):
        assert PLANNER_WIRE_SCHEMA.schema["type"] == "object"
        assert "anyOf" not in PLANNER_WIRE_SCHEMA.schema
        assert "$ref" not in json.dumps(PLANNER_WIRE_SCHEMA.schema), "inlined on purpose"

    def test_no_object_declares_zero_properties(self):
        """The construct that decided how `inputs` is represented.

        An object with no declared properties is rejected outright by at least one major strict
        implementation, so `inputs` is omitted from the schema rather than declared as an empty
        closed object. Both express "must be `{}`"; only one is portable.
        """
        for schema in _objects(PLANNER_WIRE_SCHEMA.schema):
            assert schema.get("properties"), "an empty closed object is not portable"


class TestTheVocabularyMatchesTheContract:
    def test_the_task_action_enum_is_exactly_action_type(self):
        """Read from the enum, so a new action cannot appear in one place and not the other."""
        tasks = PLANNER_WIRE_SCHEMA.schema["properties"]["tasks"]
        sent = tasks["items"]["properties"]["action"]["enum"]

        assert sent == [member.value for member in ActionType]

    def test_the_status_enum_is_exactly_action_status(self):
        sent = PLANNER_WIRE_SCHEMA.schema["properties"]["status"]["enum"]

        assert sent == [member.value for member in ActionStatus]

    def test_dependencies_are_restricted_to_action_names(self):
        """Reflection resolves each dependency against the surviving plan's actions."""
        tasks = PLANNER_WIRE_SCHEMA.schema["properties"]["tasks"]
        depends_on = tasks["items"]["properties"]["depends_on"]

        assert depends_on["items"]["enum"] == [member.value for member in ActionType]

    def test_the_payload_type_is_the_contracts_own_literal(self):
        """Not retyped here: it is the discriminator, so a drifted copy would be rejected."""
        generated = PlannerResponse.model_json_schema()
        assert PLANNER_WIRE_SCHEMA.schema["properties"]["payload_type"]["enum"] == [
            generated["properties"]["payload_type"]["const"]
        ]

    def test_inputs_is_not_offered_to_the_model(self):
        """`PlanTask.inputs` flows into `GateInputs.payload`, which authorship rules police.

        Nothing needs a model-authored value: no playbook step sets one, the committed fixture is
        `{}` throughout, and services load their own inputs from recorded data. Not offering the
        key is narrower than refusing its contents afterwards — and the refusal still stands.
        """
        tasks = PLANNER_WIRE_SCHEMA.schema["properties"]["tasks"]
        assert "inputs" not in tasks["items"]["properties"]


class TestTheSchemaAdmitsOnlyWhatTheContractAdmits:
    """The direction that matters: wire-valid must imply contract-valid, never the reverse."""

    def test_a_wire_shaped_plan_passes_the_unchanged_contract(self):
        parsed = PlannerResponse.model_validate(WIRE_SHAPED_PLAN)

        assert [task.action.value for task in parsed.tasks] == [
            "check_connections",
            "reserve_hotel_block",
        ]

    def test_an_omitted_inputs_key_becomes_the_empty_dict(self):
        """The wire contract can only ever produce `{}` here, and that is what arrives."""
        parsed = PlannerResponse.model_validate(WIRE_SHAPED_PLAN)

        assert all(task.inputs == {} for task in parsed.tasks)

    def test_the_contract_still_refuses_an_undeclared_key(self):
        assert PLANNER_WIRE_SCHEMA.schema["additionalProperties"] is False
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate({**WIRE_SHAPED_PLAN, "confidence": 91})

    def test_the_contract_still_refuses_an_empty_task_list(self):
        """`minItems` cannot be sent, so this is the assertion that keeps the guarantee real."""
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate({**WIRE_SHAPED_PLAN, "tasks": []})

    def test_the_contract_still_refuses_an_over_long_reason(self):
        """`maxLength` cannot be sent either. Enforced after receipt, exactly as before."""
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate({**WIRE_SHAPED_PLAN, "reason": "R" * 2500})

    def test_the_contract_still_refuses_a_blank_target_ref(self):
        payload = json.loads(json.dumps(WIRE_SHAPED_PLAN))
        payload["tasks"][0]["target_refs"] = ["  "]

        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(payload)

    def test_the_contract_still_refuses_an_action_outside_the_enum(self):
        payload = json.loads(json.dumps(WIRE_SHAPED_PLAN))
        payload["tasks"][0]["action"] = "wire_money"

        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(payload)


class TestThePromptAndTheSchemaAgree:
    """They are two statements of one contract, and a model reads both."""

    def test_the_prompt_no_longer_asks_for_an_inputs_key(self):
        from app.agents.planner import PROMPT_PATH

        assert '"inputs"' not in PROMPT_PATH.read_text(encoding="utf-8")

    def test_the_prompt_forbids_wrapping_the_object(self):
        from app.agents.planner import PROMPT_PATH

        text = PROMPT_PATH.read_text(encoding="utf-8").lower()
        assert "final" in text
        assert "top level" in text

    def test_every_action_the_prompt_lists_is_in_the_schema(self):
        from app.agents.planner import PROMPT_PATH

        text = PROMPT_PATH.read_text(encoding="utf-8")
        tasks = PLANNER_WIRE_SCHEMA.schema["properties"]["tasks"]
        for action in tasks["items"]["properties"]["action"]["enum"]:
            assert action in text, f"the prompt does not offer {action}"
