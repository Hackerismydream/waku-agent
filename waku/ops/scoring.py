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
    "skill",
    "multi_tool",
    "safety",
    "recovery",
)
_SETUP_FIELDS = {"facts", "events", "outbox", "skills", "exchanges", "restart"}
_STATE_NAMESPACES = {"calendar", "facts", "outbox", "skills", "soul", "chat"}


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
        expected_state = expect.get("state", {})
        if not isinstance(expected_state, dict):
            raise ValueError(f"{case['id']}: expect.state must be an object")
        for assertion in expected_state:
            namespace = assertion
            for suffix in ("_not_contains", "_contains", "_count", "_min"):
                if namespace.endswith(suffix):
                    namespace = namespace.removesuffix(suffix)
                    break
            if namespace not in _STATE_NAMESPACES:
                raise ValueError(f"{case['id']}: unknown state assertion {assertion!r}")
        setup = case.get("setup", {})
        if not isinstance(setup, dict):
            raise ValueError(f"{case['id']}: setup must be an object")
        unknown_setup = set(setup) - _SETUP_FIELDS
        if unknown_setup:
            raise ValueError(f"{case['id']}: unknown setup fields {sorted(unknown_setup)}")
        for field in _SETUP_FIELDS - {"restart"}:
            if field in setup and not isinstance(setup[field], list):
                raise ValueError(f"{case['id']}: setup.{field} must be a list")
        if "restart" in setup and not isinstance(setup["restart"], bool):
            raise ValueError(f"{case['id']}: setup.restart must be boolean")
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


def _state_error(expected: dict, state: dict) -> str | None:
    for key, wanted in expected.items():
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


def check_case(
    case: dict,
    tool_calls: list[dict],
    *,
    state: dict | None = None,
    controller: dict | list[dict] | None = None,
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
            if output.startswith("error") or " failed" in output or "timed out" in output:
                return False, f"tool {call.get('tool')} returned an error: {output[:100]}"

    if controller is not None and expected.get("gate"):
        decision = _controller_decision(controller)
        if decision != expected["gate"]:
            return False, f"gate expected {expected['gate']}, got {decision or 'nothing'}"

    if state is not None and expected.get("state"):
        if error := _state_error(expected["state"], state):
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
