"""DETERMINISTIC EVAL — "did the meeting trigger?" This is a unit test.

No LLM judges anything here. Each case asserts a binary, checkable outcome:
the right tool fired (or didn't), with the right arguments, and the artifact
(DB row / outbox file) exists. 0 or 1. This is the half of eval that most
teams skip and shouldn't.

Two tiers:
  offline  — scripted model, always runs, tests OUR code (loop, tools, wiring)
  live     — real model, runs when ANTHROPIC_API_KEY is set, tests the
             MODEL+PROMPT behavior on evals/dataset.jsonl (the real eval)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.helpers import HAS_KEY, ScriptedClient, make_jarvis, response, text_block, tool_block

DATASET = [
    json.loads(line)
    for line in (Path(__file__).resolve().parents[1] / "dataset.jsonl").read_text().splitlines()
    if line.strip()
]

# ---------- offline tier: our plumbing is deterministic-testable without any model


def test_create_event_writes_db_and_ics(tmp_path):
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    turn = [
        response([tool_block("create_event", {"title": "Coffee with Alex", "start": "2026-07-14T09:00"})], "tool_use"),
        response([text_block("Booked!")]),
    ]
    app = make_jarvis(tmp_path / "home", client=ScriptedClient([gate] + turn))
    result = app.respond("coffee with alex tuesday 9am")

    assert [c["tool"] for c in result.tool_calls] == ["create_event"]
    row = app.conn.execute("SELECT title, start FROM calendar_events").fetchone()
    assert row["title"] == "Coffee with Alex"
    assert row["start"] == "2026-07-14T09:00"
    assert "SUMMARY:Coffee with Alex" in (tmp_path / "home" / "calendar.ics").read_text()


def test_no_tool_turn_ends_loop_in_one_iteration(tmp_path):
    script = [
        response([text_block('{"retrieve": false, "query": "", "reason": "test"}')]),
        response([text_block("Paris.")]),
    ]
    app = make_jarvis(tmp_path / "home", client=ScriptedClient(script))
    result = app.respond("capital of france?")
    assert result.reply == "Paris." and result.iterations == 1 and result.tool_calls == []


def test_iteration_guardrail_stops_runaway_loop(tmp_path):
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
    runaway = [
        response([tool_block("save_note", {"subject": "x", "content": "y"}, f"tu_{i}")], "tool_use")
        for i in range(99)
    ]
    app = make_jarvis(tmp_path / "home", client=ScriptedClient([gate] + runaway), max_iterations=3)
    result = app.respond("loop forever")
    assert result.iterations == 3 and "iteration limit" in result.reply


# ---------- live tier: the actual model eval over the dataset


@pytest.mark.skipif(not HAS_KEY, reason="live eval needs ANTHROPIC_API_KEY")
@pytest.mark.parametrize("case", DATASET, ids=[c["id"] for c in DATASET])
def test_dataset_case(case, tmp_path):
    app = make_jarvis(tmp_path / "home")
    if "setup_fact" in case:
        app.memory.facts.add(case["setup_fact"]["subject"], case["setup_fact"]["content"])

    result = app.respond(case["input"])
    fired = [c["tool"] for c in result.tool_calls]

    if case["expect_tool"] is None:
        assert fired == [], f"expected no tools, model called {fired}"
    else:
        assert case["expect_tool"] in fired, f"expected {case['expect_tool']}, model called {fired}"
        args = next(c["args"] for c in result.tool_calls if c["tool"] == case["expect_tool"])
        for key, needle in case.get("expect_in_args", {}).items():
            assert needle.lower() in str(args.get(key, "")).lower(), (
                f"expected '{needle}' in args[{key}], got: {args.get(key)}"
            )
