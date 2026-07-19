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


def test_args_equals_rejects_a_wrong_date_with_the_right_clock_time():
    case = {
        "expect": {
            "tool_path": ["create_event"],
            "calls": [
                {
                    "tool": "create_event",
                    "args_equals": {"start": "2026-08-05T09:00"},
                }
            ],
        }
    }

    correct = [
        {
            "tool": "create_event",
            "args": {"start": "2026-08-05T09:00"},
        }
    ]
    wrong_date = [
        {
            "tool": "create_event",
            "args": {"start": "2027-08-05T09:00"},
        }
    ]

    assert scoring.check_case(case, correct) == (True, "ok")
    equivalent_iso = [
        {
            "tool": "create_event",
            "args": {"start": "2026-08-05T09:00:00+08:00"},
        }
    ]
    assert scoring.check_case(case, equivalent_iso) == (True, "ok")
    ok, why = scoring.check_case(case, wrong_date)
    assert not ok and "2026-08-05T09:00" in why


def test_structured_state_match_rejects_a_negated_fact():
    case = {
        "expect": {
            "tool_path": ["save_note"],
            "state": {
                "facts_matches": [
                    {
                        "subject_equals": "orchid 项目",
                        "content_equals": "Orchid 项目的发布负责人是林岚。",
                    }
                ]
            },
        }
    }
    calls = [{"tool": "save_note", "args": {}}]
    correct = {
        "facts": [
            {
                "subject": "orchid 项目",
                "content": "Orchid 项目的发布负责人是林岚。",
            }
        ]
    }
    negated = {
        "facts": [
            {
                "subject": "orchid 项目",
                "content": "林岚不是 Orchid 项目的发布负责人。",
            }
        ]
    }

    assert scoring.check_case(case, calls, state=correct) == (True, "ok")
    ok, why = scoring.check_case(case, calls, state=negated)
    assert not ok and "facts" in why


def test_structured_state_not_contains_rejects_any_forbidden_phrase():
    case = {
        "expect": {
            "tool_path": ["save_note"],
            "state": {
                "facts_matches": [
                    {
                        "content_contains": ["alex", "morning"],
                        "content_not_contains": ["not", "dislikes"],
                    }
                ]
            },
        }
    }
    calls = [{"tool": "save_note", "args": {}}]
    state = {"facts": [{"content": "Alex does not prefer morning meetings."}]}

    ok, why = scoring.check_case(case, calls, state=state)
    assert not ok and "facts" in why


def test_structured_state_matches_require_distinct_records():
    case = {
        "expect": {
            "tool_path": ["create_event", "create_event"],
            "state": {
                "calendar_matches": [
                    {"title_contains": "focus"},
                    {"title_contains": "focus"},
                ]
            },
        }
    }
    calls = [
        {"tool": "create_event", "args": {}},
        {"tool": "create_event", "args": {}},
    ]

    ok, why = scoring.check_case(
        case,
        calls,
        state={"calendar": [{"title": "Focus session"}]},
    )
    assert not ok and "calendar" in why


def test_structured_state_one_of_accepts_only_an_explicit_alternative():
    case = {
        "expect": {
            "tool_path": ["save_note"],
            "state": {
                "facts_matches": [
                    {
                        "subject_one_of": ["user", "me", "self"],
                        "content_contains": "vegetarian",
                    }
                ]
            },
        }
    }
    calls = [{"tool": "save_note", "args": {}}]

    assert scoring.check_case(
        case,
        calls,
        state={"facts": [{"subject": "user", "content": "I am vegetarian."}]},
    ) == (True, "ok")
    ok, why = scoring.check_case(
        case,
        calls,
        state={"facts": [{"subject": "alex", "content": "Alex is vegetarian."}]},
    )
    assert not ok and "facts" in why


def test_contains_any_accepts_equivalent_argument_and_state_phrasings():
    case = {
        "expect": {
            "tool_path": ["send_message"],
            "calls": [
                {
                    "tool": "send_message",
                    "args_contains_any": {"body": ["16:00", "4:00 PM"]},
                }
            ],
            "state": {
                "outbox_matches": [
                    {"body_contains_any": ["16:00", "4:00 PM"]}
                ]
            },
        }
    }
    calls = [{"tool": "send_message", "args": {"body": "Demo at 4:00 PM"}}]

    assert scoring.check_case(
        case,
        calls,
        state={"outbox": [{"body": "Demo at 4:00 PM"}]},
    ) == (True, "ok")
    ok, why = scoring.check_case(
        case,
        [{"tool": "send_message", "args": {"body": "Demo tomorrow"}}],
        state={"outbox": [{"body": "Demo tomorrow"}]},
    )
    assert not ok and "args[body]" in why


def test_reply_max_chars_is_checked_deterministically():
    case = {
        "expect": {
            "tool_path": [],
            "reply": {"max_chars": 4},
        }
    }

    assert scoring.check_case(case, [], reply="真实失败") == (True, "ok")
    ok, why = scoring.check_case(case, [], reply="发现真实失败")
    assert not ok and "4" in why


def test_reply_contains_checks_visible_business_result():
    case = {
        "expect": {
            "tool_path": [],
            "reply": {"contains": ["Pikachu", "Venusaur", "Blastoise"]},
        }
    }

    assert scoring.check_case(
        case, [], reply="Pikachu, Venusaur, and Blastoise are on the team."
    ) == (True, "ok")
    ok, why = scoring.check_case(case, [], reply="Pikachu is on the team.")
    assert not ok and "Venusaur" in why


def test_require_success_rejects_an_empty_web_search_receipt():
    case = {
        "expect": {
            "tool_path": ["search_web"],
            "require_success": True,
        }
    }
    calls = [
        {
            "tool": "search_web",
            "args": {"query": "Python 3.14"},
            "output": "No results found. Try a more specific query.",
        }
    ]

    ok, why = scoring.check_case(case, calls)
    assert not ok and "error" in why


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
        "simple_qa", "memory", "scheduling", "messaging", "skill", "multi_tool", "safety",
        "recovery",
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


def test_every_web_task_uses_a_frozen_search_fixture():
    web_cases = [
        case
        for case in scoring.load_cases()
        if "search_web" in case["expect"]["tool_path"]
    ]

    assert {case["id"] for case in web_cases} == {
        "multi-search-and-message",
        "pokemon-team",
        "worldcup-final",
        "multi-remember-search-schedule",
    }
    assert all(case.get("setup", {}).get("search_results") for case in web_cases)


def test_web_business_results_are_pinned_beyond_a_source_url():
    cases = {case["id"]: case for case in scoring.load_cases()}
    python_record = cases["multi-search-and-message"]["expect"]["state"][
        "outbox_matches"
    ][0]
    pokemon_reply = cases["pokemon-team"]["expect"]["reply"]["contains"]

    assert {"bugfix", "3.14.6", "3.14.7"} <= set(python_record["body_contains"])
    assert "discontinued" in python_record["body_not_contains"]
    assert set(pokemon_reply) == {
        "Pikachu",
        "Dugtrio",
        "Charizard",
        "Blastoise",
        "Venusaur",
        "Snorlax",
    }


def test_first_person_preferences_accept_user_or_topic_owned_fact_subjects():
    cases = {case["id"]: case for case in scoring.load_cases()}
    direct = cases["remember-and-book"]["expect"]["state"]["facts_matches"][0]
    topical = cases["multi-remember-search-schedule"]["expect"]["state"][
        "facts_matches"
    ][0]

    assert {"user", "me", "self", "用户", "我"} <= set(direct["subject_one_of"])
    assert {"user", "preference", "用户", "我"} <= set(
        topical["subject_contains_any"]
    )


def test_skill_drafts_accept_equivalent_user_and_checklist_labels():
    cases = {case["id"]: case for case in scoring.load_cases()}
    weekly = cases["skill-use-weekly-reset"]["expect"]["state"][
        "outbox_matches"
    ][0]
    trip = cases["skill-use-trip-prep"]["expect"]

    assert {"plan", "weekly reset", "checklist"} <= set(
        weekly["body_contains_any"]
    )
    assert set(trip["calls"][1]["args_contains_any"]["to"]) == {
        "me",
        "you",
        "self",
    }
    assert set(trip["state"]["outbox_matches"][0]["to_one_of"]) == {
        "me",
        "you",
        "self",
    }


def test_relative_time_case_has_a_fixed_clock_and_exact_event_state():
    case = next(
        case for case in scoring.load_cases() if case["id"] == "schedule-relative-time"
    )

    assert case["setup"]["clock"] == "2026-08-05T10:00:00+08:00"
    assert case["expect"]["state"]["calendar_matches"] == [
        {
            "title_contains": "stretch",
            "start_equals": "2026-08-05T10:30",
            "end_equals": "2026-08-05T10:50",
        }
    ]


def test_suite_validation_rejects_a_malformed_state_contract():
    cases = scoring.load_cases()
    cases[0]["expect"]["state"] = []

    with pytest.raises(ValueError, match="expect.state must be an object"):
        scoring.validate_suite(cases)


def test_suite_validation_rejects_an_unknown_structured_record_assertion():
    cases = scoring.load_cases()
    cases[0]["expect"]["state"]["calendar_matches"] = [
        {"start_is_about": "nine-ish"}
    ]

    with pytest.raises(ValueError, match="structured record assertion"):
        scoring.validate_suite(cases)


def test_suite_validation_rejects_a_misspelled_structured_record_field():
    cases = scoring.load_cases()
    cases[0]["expect"]["state"]["calendar_matches"] = [
        {"strat_not_contains": "wrong"}
    ]

    with pytest.raises(ValueError, match="calendar record field"):
        scoring.validate_suite(cases)


def test_suite_validation_rejects_a_setup_skill_path_escape():
    cases = scoring.load_cases()
    cases[0]["setup"] = {
        "skills": [
            {
                "name": "../../escape",
                "description": "malicious fixture",
                "body": "escape the sandbox",
            }
        ]
    }

    with pytest.raises(ValueError, match="setup.skills.*name"):
        scoring.validate_suite(cases)


def test_suite_validation_rejects_an_invalid_fixed_clock():
    cases = scoring.load_cases()
    cases[0]["setup"] = {"clock": "next Wednesday-ish"}

    with pytest.raises(ValueError, match="setup.clock"):
        scoring.validate_suite(cases)
