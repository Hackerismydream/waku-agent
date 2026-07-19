"""One deterministic scorer for Waku's internal task battery.

The same JSONL suite drives live pytest cases, the CLI shootout, and the local
Compare arena.  A task can assert the tool path, call arguments, retrieval-gate
decision, and isolated sandbox state.  LLM-as-judge quality remains separate.

The structured contract lives under ``expect``.  The original flat
``expect_tool`` fields are still accepted so old reports and small third-party
examples do not break.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

_DATASET = Path(__file__).resolve().parents[2] / "evals" / "dataset.jsonl"

FAILURE_CATEGORIES = (
    "routing",
    "retrieval",
    "skill",
    "tool_args",
    "safety",
    "recovery",
    "response",
)
TASK_CATEGORIES = (
    "simple_qa",
    "memory",
    "scheduling",
    "messaging",
    "skill",
    "multi_tool",
    "safety",
    "recovery",
)
_SETUP_FIELDS = {
    "facts",
    "events",
    "outbox",
    "skills",
    "exchanges",
    "restart",
    "clock",
    "search_results",
}
_STATE_NAMESPACES = {"calendar", "facts", "outbox", "skills", "soul", "chat"}
_STATE_RECORDS = {
    "calendar": "calendar",
    "facts": "facts",
    "outbox": "outbox",
    "skills": "skill_records",
    "chat": "chat",
}
_STATE_RECORD_FIELDS = {
    "calendar": {"title", "start", "end", "attendees"},
    "facts": {"subject", "content", "source"},
    "outbox": {"to", "body"},
    "skills": {"name", "description", "body"},
    "chat": {"role", "content", "session_id"},
}
_RECORD_SUFFIXES = (
    "_not_contains",
    "_contains_any",
    "_contains",
    "_one_of",
    "_equals",
)


def load_cases(path: Path | None = None) -> list[dict]:
    """Load battery cases in file order; an absent file is an empty suite."""
    dataset = path or _DATASET
    if not dataset.exists():
        return []
    return [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]


def validate_suite(cases: list[dict], *, expected_count: int = 40) -> None:
    """Fail fast when the frozen benchmark contract drifts accidentally."""
    if len(cases) != expected_count:
        raise ValueError(f"expected {expected_count} eval cases, found {len(cases)}")
    ids = [case.get("id") for case in cases]
    duplicates = [case_id for case_id, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate eval case ids: {duplicates}")
    splits = Counter(case.get("split") for case in cases)
    if splits != {"dev": 24, "heldout": 16}:
        raise ValueError(f"expected split dev=24/heldout=16, found {dict(splits)}")
    for case in cases:
        missing = [key for key in ("id", "input", "category", "failure_category", "expect")
                   if key not in case]
        if missing:
            raise ValueError(f"{case.get('id', '<unknown>')}: missing {missing}")
        if not isinstance(case["id"], str) or not case["id"].strip():
            raise ValueError("eval case id must be a non-empty string")
        if not isinstance(case["input"], str) or not case["input"].strip():
            raise ValueError(f"{case['id']}: input must be a non-empty string")
        if case["failure_category"] not in FAILURE_CATEGORIES:
            raise ValueError(
                f"{case['id']}: unknown failure category {case['failure_category']!r}"
            )
        if case["category"] not in TASK_CATEGORIES:
            raise ValueError(f"{case['id']}: unknown task category {case['category']!r}")
        expect = case["expect"]
        if not isinstance(expect, dict) or "tool_path" not in expect:
            raise ValueError(f"{case['id']}: expect.tool_path is required")
        tool_path = expect["tool_path"]
        if not isinstance(tool_path, list) or not all(isinstance(tool, str) for tool in tool_path):
            raise ValueError(f"{case['id']}: expect.tool_path must be a list of tool names")
        if expect.get("path_mode", "exact") not in {"exact", "subsequence", "contains"}:
            raise ValueError(f"{case['id']}: invalid expect.path_mode")
        if expect.get("gate") not in {None, "retrieve", "skip"}:
            raise ValueError(f"{case['id']}: invalid expect.gate")
        minimum = expect.get("min_tool_calls", 0)
        if not isinstance(minimum, int) or minimum < 0:
            raise ValueError(f"{case['id']}: expect.min_tool_calls must be >= 0")
        if not isinstance(expect.get("require_success", False), bool):
            raise ValueError(f"{case['id']}: expect.require_success must be boolean")
        expected_calls = expect.get("calls", [])
        if not isinstance(expected_calls, list):
            raise ValueError(f"{case['id']}: expect.calls must be a list")
        for call in expected_calls:
            if not isinstance(call, dict) or call.get("tool") not in tool_path:
                raise ValueError(f"{case['id']}: every expect.calls tool must be in tool_path")
            if not isinstance(call.get("args_contains", {}), dict):
                raise ValueError(f"{case['id']}: expect.calls.args_contains must be an object")
            if not isinstance(call.get("args_contains_any", {}), dict):
                raise ValueError(
                    f"{case['id']}: expect.calls.args_contains_any must be an object"
                )
            for field, alternatives in call.get("args_contains_any", {}).items():
                if not isinstance(alternatives, list) or not alternatives:
                    raise ValueError(
                        f"{case['id']}: expect.calls.args_contains_any[{field}] "
                        "must be a non-empty list"
                    )
            if not isinstance(call.get("args_equals", {}), dict):
                raise ValueError(f"{case['id']}: expect.calls.args_equals must be an object")
        expected_state = expect.get("state", {})
        if not isinstance(expected_state, dict):
            raise ValueError(f"{case['id']}: expect.state must be an object")
        for assertion, wanted in expected_state.items():
            if assertion.endswith("_matches"):
                namespace = assertion.removesuffix("_matches")
                if namespace not in _STATE_RECORDS:
                    raise ValueError(f"{case['id']}: unknown structured state assertion {assertion!r}")
                if not isinstance(wanted, list) or not all(isinstance(row, dict) for row in wanted):
                    raise ValueError(f"{case['id']}: {assertion} must be a list of objects")
                for record in wanted:
                    if not record:
                        raise ValueError(f"{case['id']}: structured record assertion cannot be empty")
                    for record_assertion, value in record.items():
                        suffix = next(
                            (item for item in _RECORD_SUFFIXES
                             if record_assertion.endswith(item)),
                            None,
                        )
                        if suffix is None or not record_assertion.removesuffix(suffix):
                            raise ValueError(
                                f"{case['id']}: unknown structured record assertion "
                                f"{record_assertion!r}"
                            )
                        field = record_assertion.removesuffix(suffix)
                        if field not in _STATE_RECORD_FIELDS[namespace]:
                            raise ValueError(
                                f"{case['id']}: unknown {namespace} record field {field!r}"
                            )
                        if suffix == "_one_of" and (
                            not isinstance(value, list) or not value
                        ):
                            raise ValueError(
                                f"{case['id']}: {record_assertion} must be a non-empty list"
                            )
                        if suffix in {"_contains", "_not_contains"} and not isinstance(
                            value, (str, list)
                        ):
                            raise ValueError(
                                f"{case['id']}: {record_assertion} must be text or a list"
                            )
                        if suffix == "_contains_any" and not isinstance(value, list):
                            raise ValueError(
                                f"{case['id']}: {record_assertion} must be a list"
                            )
                        if isinstance(value, list) and not value:
                            raise ValueError(
                                f"{case['id']}: {record_assertion} cannot be an empty list"
                            )
                continue
            namespace = assertion
            for suffix in ("_not_contains", "_contains", "_count", "_min"):
                if namespace.endswith(suffix):
                    namespace = namespace.removesuffix(suffix)
                    break
            if namespace not in _STATE_NAMESPACES:
                raise ValueError(f"{case['id']}: unknown state assertion {assertion!r}")
        expected_reply = expect.get("reply", {})
        if not isinstance(expected_reply, dict):
            raise ValueError(f"{case['id']}: expect.reply must be an object")
        if set(expected_reply) - {"max_chars", "contains", "not_contains"}:
            raise ValueError(f"{case['id']}: unknown reply assertion")
        if "max_chars" in expected_reply and (
            not isinstance(expected_reply["max_chars"], int)
            or expected_reply["max_chars"] < 0
        ):
            raise ValueError(f"{case['id']}: expect.reply.max_chars must be >= 0")
        for assertion in ("contains", "not_contains"):
            if assertion not in expected_reply:
                continue
            value = expected_reply[assertion]
            if not isinstance(value, (str, list)) or (isinstance(value, list) and not value):
                raise ValueError(
                    f"{case['id']}: expect.reply.{assertion} must be text or a non-empty list"
                )
        setup = case.get("setup", {})
        if not isinstance(setup, dict):
            raise ValueError(f"{case['id']}: setup must be an object")
        unknown_setup = set(setup) - _SETUP_FIELDS
        if unknown_setup:
            raise ValueError(f"{case['id']}: unknown setup fields {sorted(unknown_setup)}")
        for field in _SETUP_FIELDS - {"restart", "clock"}:
            if field in setup and not isinstance(setup[field], list):
                raise ValueError(f"{case['id']}: setup.{field} must be a list")
        if "restart" in setup and not isinstance(setup["restart"], bool):
            raise ValueError(f"{case['id']}: setup.restart must be boolean")
        if "clock" in setup:
            try:
                fixed_clock = datetime.fromisoformat(setup["clock"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{case['id']}: setup.clock must be an ISO timestamp with timezone"
                ) from exc
            if fixed_clock.utcoffset() is None:
                raise ValueError(
                    f"{case['id']}: setup.clock must be an ISO timestamp with timezone"
                )
        from waku.tools.memory_admin import valid_skill_name

        for skill in setup.get("skills", []):
            if not isinstance(skill, dict) or not valid_skill_name(skill.get("name")):
                raise ValueError(
                    f"{case['id']}: setup.skills entries need a safe skill name slug"
                )
            if not all(
                isinstance(skill.get(field), str) and skill[field].strip()
                for field in ("description", "body")
            ):
                raise ValueError(
                    f"{case['id']}: setup.skills entries need description and body"
                )
        for fixture in setup.get("search_results", []):
            if not isinstance(fixture, dict) or not isinstance(fixture.get("query_contains"), str):
                raise ValueError(f"{case['id']}: every search fixture needs query_contains")
            results = fixture.get("results")
            if not isinstance(results, list) or not results:
                raise ValueError(f"{case['id']}: every search fixture needs results")
            for result in results:
                if not isinstance(result, dict) or not all(
                    isinstance(result.get(field), str) for field in ("title", "snippet", "url")
                ):
                    raise ValueError(f"{case['id']}: malformed frozen search result")
        criterion = case.get("judge_criteria", "")
        if not isinstance(criterion, str) or not criterion.strip():
            raise ValueError(f"{case['id']}: judge_criteria is required")


def _contract(case: dict) -> dict:
    if "expect" in case:
        return case["expect"]

    # Backward compatibility with the 11-case v0 battery and callers that use
    # tiny inline dictionaries in tests.
    expected_tool = case.get("expect_tool")
    contract = {
        "tool_path": [] if expected_tool is None else [expected_tool],
        "path_mode": "exact" if expected_tool is None else "contains",
        "min_tool_calls": case.get("expect_min_tool_calls", 0),
    }
    if expected_tool and case.get("expect_in_args"):
        contract["calls"] = [
            {"tool": expected_tool, "args_contains": case["expect_in_args"]}
        ]
    return contract


def _is_subsequence(wanted: list[str], actual: list[str]) -> bool:
    cursor = iter(actual)
    return all(any(item == want for item in cursor) for want in wanted)


def _path_error(expected: list[str], actual: list[str], mode: str) -> str | None:
    if mode == "exact" and actual != expected:
        if not expected:
            return f"expected no tools, called {actual}"
        return f"expected exact tool path {expected}, called {actual or 'nothing'}"
    if mode == "subsequence" and not _is_subsequence(expected, actual):
        return f"expected ordered tool path {expected}, called {actual or 'nothing'}"
    if mode == "contains":
        missing = Counter(expected) - Counter(actual)
        if missing:
            if len(expected) == 1:
                return f"expected {expected[0]}, called {actual or 'nothing'}"
            return f"missing tools {list(missing.elements())}, called {actual or 'nothing'}"
    if mode not in {"exact", "subsequence", "contains"}:
        return f"unknown path mode {mode!r}"
    return None


def _same_value(actual, wanted) -> bool:
    if isinstance(actual, str) and isinstance(wanted, str):
        if "T" in actual and "T" in wanted:
            try:
                actual_dt = datetime.fromisoformat(actual)
                wanted_dt = datetime.fromisoformat(wanted)
            except ValueError:
                pass
            else:
                # Calendar stores local wall time to minute precision. Match the
                # same semantics even when a provider includes seconds/offset.
                return actual_dt.replace(
                    second=0, microsecond=0, tzinfo=None
                ) == wanted_dt.replace(second=0, microsecond=0, tzinfo=None)
        return actual.strip().casefold() == wanted.strip().casefold()
    return actual == wanted


def _args_error(expected_calls: list[dict], tool_calls: list[dict]) -> str | None:
    cursor = 0
    for expected in expected_calls:
        tool = expected["tool"]
        match_at = next(
            (index for index in range(cursor, len(tool_calls))
             if tool_calls[index].get("tool") == tool),
            None,
        )
        if match_at is None:
            return f"expected arguments for {tool}, but that call was missing"
        args = tool_calls[match_at].get("args", {})
        for key, needle in expected.get("args_contains", {}).items():
            if str(needle).lower() not in str(args.get(key, "")).lower():
                return f"'{needle}' not in args[{key}] for {tool}"
        for key, alternatives in expected.get("args_contains_any", {}).items():
            actual = str(args.get(key, "")).casefold()
            if not any(str(candidate).casefold() in actual for candidate in alternatives):
                return (
                    f"none of {alternatives!r} found in args[{key}] for {tool}"
                )
        for key, wanted in expected.get("args_equals", {}).items():
            actual = args.get(key)
            if not _same_value(actual, wanted):
                return f"args[{key}] for {tool} expected {wanted!r}, got {actual!r}"
        cursor = match_at + 1
    return None


_STATE_TEXT = {
    "calendar": "calendar_text",
    "facts": "facts_text",
    "outbox": "outbox_text",
    "skills": "skills_text",
    "soul": "soul_text",
    "chat": "chat_text",
}


def _record_matches(record: dict, expected: dict) -> bool:
    for assertion, wanted in expected.items():
        suffix = next(
            (item for item in _RECORD_SUFFIXES
             if assertion.endswith(item)),
            None,
        )
        if suffix is None:
            return False
        field = assertion.removesuffix(suffix)
        if field not in record:
            return False
        actual = record.get(field)
        if suffix == "_equals":
            if not _same_value(actual, wanted):
                return False
            continue
        if suffix == "_one_of":
            if not any(_same_value(actual, candidate) for candidate in wanted):
                return False
            continue
        text = str(actual or "").casefold()
        needles = wanted if isinstance(wanted, list) else [wanted]
        if suffix == "_contains_any" and not any(
            str(needle).casefold() in text for needle in needles
        ):
            return False
        if suffix == "_contains" and not all(
            str(needle).casefold() in text for needle in needles
        ):
            return False
        if suffix == "_not_contains" and any(
            str(needle).casefold() in text for needle in needles
        ):
            return False
    return True


def _state_error(expected: dict, state: dict) -> str | None:
    for key, wanted in expected.items():
        if key.endswith("_matches"):
            namespace = key.removesuffix("_matches")
            records = list(state.get(_STATE_RECORDS.get(namespace, namespace), []))
            for record_spec in wanted:
                match_at = next(
                    (index for index, record in enumerate(records)
                     if _record_matches(record, record_spec)),
                    None,
                )
                if match_at is None:
                    return f"state {namespace} has no record matching {record_spec!r}"
                records.pop(match_at)
            continue
        if key.endswith("_count"):
            actual = state.get(key)
            if actual != wanted:
                return f"state {key} expected {wanted}, got {actual}"
            continue
        if key.endswith("_min"):
            actual = state.get(key[:-4] + "_count", 0)
            if actual < wanted:
                return f"state {key} expected >= {wanted}, got {actual}"
            continue
        suffix = "_not_contains" if key.endswith("_not_contains") else "_contains"
        if key.endswith(suffix):
            namespace = key.removesuffix(suffix)
            text = str(state.get(_STATE_TEXT.get(namespace, namespace + "_text"), "")).lower()
            needles = wanted if isinstance(wanted, list) else [wanted]
            for needle in needles:
                present = str(needle).lower() in text
                if suffix == "_contains" and not present:
                    return f"state {namespace} missing {needle!r}"
                if suffix == "_not_contains" and present:
                    return f"state {namespace} unexpectedly contains {needle!r}"
            continue
        return f"unknown state assertion {key!r}"
    return None


def _controller_decision(controller: dict | list[dict] | None) -> str | None:
    if isinstance(controller, dict):
        return controller.get("decision")
    if controller:
        return controller[-1].get("decision")
    return None


def _reply_error(expected: dict, reply: str | None) -> str | None:
    if not expected:
        return None
    if reply is None:
        return "reply receipt required for deterministic assertions"
    if "max_chars" in expected and len(reply.strip()) > expected["max_chars"]:
        return f"reply exceeds {expected['max_chars']} characters"
    text = reply.casefold()
    for assertion, should_be_present in (("contains", True), ("not_contains", False)):
        if assertion not in expected:
            continue
        needles = expected[assertion] if isinstance(expected[assertion], list) else [expected[assertion]]
        for needle in needles:
            present = str(needle).casefold() in text
            if should_be_present and not present:
                return f"reply missing {needle!r}"
            if not should_be_present and present:
                return f"reply unexpectedly contains {needle!r}"
    return None


def check_case(
    case: dict,
    tool_calls: list[dict],
    *,
    state: dict | None = None,
    controller: dict | list[dict] | None = None,
    reply: str | None = None,
) -> tuple[bool, str]:
    """Evaluate deterministic task truth and return ``(passed, reason)``.

    State and controller checks run when the caller supplies those receipts.
    This keeps the scorer usable by the free-form Compare UI while the baseline
    runner, which owns an isolated sandbox, exercises the full contract.
    """
    expected = _contract(case)
    fired = [call.get("tool") for call in tool_calls]

    if error := _path_error(
        expected.get("tool_path", []), fired, expected.get("path_mode", "exact")
    ):
        return False, error

    minimum = expected.get("min_tool_calls", 0)
    if len(fired) < minimum:
        return False, f"only {len(fired)} tool calls, wanted >= {minimum}"

    if error := _args_error(expected.get("calls", []), tool_calls):
        return False, error

    if expected.get("require_success"):
        for call in tool_calls:
            output = str(call.get("output", "")).lower()
            search_empty = call.get("tool") == "search_web" and output.startswith("no results")
            if (
                output.startswith("error")
                or " failed" in output
                or "timed out" in output
                or search_empty
            ):
                return False, f"tool {call.get('tool')} returned an error: {output[:100]}"

    if controller is not None and expected.get("gate"):
        decision = _controller_decision(controller)
        if decision != expected["gate"]:
            return False, f"gate expected {expected['gate']}, got {decision or 'nothing'}"

    if state is not None and expected.get("state"):
        if error := _state_error(expected["state"], state):
            return False, error

    if error := _reply_error(expected.get("reply", {}), reply):
        return False, error

    if not fired and not expected.get("tool_path"):
        return True, "no tool expected" if "expect" not in case else "ok"
    return True, "ok"


def classify_failure(case: dict, reason: str, *, judge_failed: bool = False) -> str:
    """Map a failed receipt to the fixed seven-bucket regression taxonomy."""
    if judge_failed:
        return "response"
    lowered = reason.lower()
    if "args[" in lowered or "arguments for" in lowered:
        return "tool_args"
    declared = case.get("failure_category", "response")
    return declared if declared in FAILURE_CATEGORIES else "response"


def case_for_message(message: str, cases: list[dict] | None = None) -> dict | None:
    """Return the battery case whose input is a trimmed exact match."""
    candidate = (message or "").strip()
    for case in cases if cases is not None else load_cases():
        if (case.get("input") or "").strip() == candidate:
            return case
    return None
