"""DETERMINISTIC EVAL — the ONE Completion scorer (waku.ops.scoring).

The CLI shootout and the live arena both score a run through this module, so the
terminal number and the on-screen number can't drift. These tests pin the
contract: the checklist logic, and matching a free-text prompt to its case."""

from __future__ import annotations

from collections import Counter

import pytest

from evals.helpers import ScriptedClient, make_waku
from waku.ops import scoring


def _tc(*names):
    return [{"tool": n, "args": {}} for n in names]


def test_expect_tool_none_wants_no_call():
    case = {"expect_tool": None}
    assert scoring.check_case(case, []) == (True, "no tool expected")
    ok, why = scoring.check_case(case, _tc("create_event"))
    assert not ok and "create_event" in why          # acted on a non-command


def test_expect_tool_must_fire():
    case = {"expect_tool": "create_event"}
    assert scoring.check_case(case, _tc("create_event"))[0]
    assert not scoring.check_case(case, _tc("save_note"))[0]


def test_expect_in_args_substring():
    case = {"expect_tool": "create_event", "expect_in_args": {"title": "sam"}}
    assert scoring.check_case(case, [{"tool": "create_event", "args": {"title": "Dinner w/ Sam"}}])[0]
    ok, why = scoring.check_case(case, [{"tool": "create_event", "args": {"title": "lunch"}}])
    assert not ok and "sam" in why


def test_min_tool_calls_floor():
    case = {"expect_tool": "create_event", "expect_min_tool_calls": 3}
    ok, why = scoring.check_case(case, _tc("create_event"))
    assert not ok and ">= 3" in why
    assert scoring.check_case(case, _tc("create_event", "create_event", "create_event"))[0]


def test_case_for_message_matches_trimmed_input():
    cases = [{"id": "a", "input": "Block three 25-minute focus sessions tomorrow morning"}]
    assert scoring.case_for_message("  Block three 25-minute focus sessions tomorrow morning ",
                                    cases)["id"] == "a"
    assert scoring.case_for_message("something else entirely", cases) is None


def test_real_dataset_loads_and_every_case_is_scoreable():
    cases = scoring.load_cases()
    scoring.validate_suite(cases)
    assert len(cases) == 40
    assert Counter(c["split"] for c in cases) == {"dev": 24, "heldout": 16}
    assert {c["category"] for c in cases} == {
        "simple_qa", "memory", "scheduling", "skill", "multi_tool", "safety", "recovery",
    }
    for case in cases:
        assert case["failure_category"] in scoring.FAILURE_CATEGORIES
        assert case["judge_criteria"].strip()
        assert "tool_path" in case["expect"]


def test_structured_contract_checks_path_args_gate_and_state():
    case = {
        "expect": {
            "tool_path": ["list_events", "create_event"],
            "path_mode": "exact",
            "calls": [
                {"tool": "create_event", "args_contains": {"title": "walk"}},
            ],
            "gate": "retrieve",
            "state": {"calendar_count": 1, "calendar_contains": ["walk"]},
        },
    }
    calls = [
        {"tool": "list_events", "args": {}, "output": "No events found."},
        {
            "tool": "create_event",
            "args": {"title": "Short walk"},
            "output": "Event created.",
        },
    ]
    state = {"calendar_count": 1, "calendar_text": "Short walk"}

    assert scoring.check_case(case, calls, state=state, controller={"decision": "retrieve"}) == (
        True,
        "ok",
    )

    ok, why = scoring.check_case(case, calls, state=state, controller={"decision": "skip"})
    assert not ok and "gate" in why


def test_failure_classification_separates_argument_and_response_failures():
    case = {"failure_category": "routing"}
    assert scoring.classify_failure(case, "'alex' not in args[title]") == "tool_args"
    assert scoring.classify_failure(case, "expected create_event, called nothing") == "routing"
    assert scoring.classify_failure(case, "judge score 4 below 7", judge_failed=True) == "response"


def test_real_dataset_only_names_registered_default_tools(tmp_path):
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    available = {schema["name"] for schema in app.tools.schemas()}

    referenced = {
        tool
        for case in scoring.load_cases()
        for tool in case["expect"]["tool_path"]
    }

    assert referenced <= available


def test_suite_validation_rejects_a_malformed_state_contract():
    cases = scoring.load_cases()
    cases[0]["expect"]["state"] = []

    with pytest.raises(ValueError, match="expect.state must be an object"):
        scoring.validate_suite(cases)
