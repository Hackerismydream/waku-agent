"""DETERMINISTIC EVAL — M1 matrix runner and auditable run artifacts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evals import runner
from evals.helpers import ScriptedClient, make_waku
from waku.config import Settings


def _receipt(spec, case, trial, context):
    failed = case["id"] == "remember-preference" and trial == 2
    return {
        "schema_version": runner.ARTIFACT_SCHEMA_VERSION,
        "run_id": context["run_id"],
        "case_id": case["id"],
        "split": case["split"],
        "category": case["category"],
        "trial": trial,
        "input": case["input"],
        "model": {"spec": spec.raw, "provider": spec.provider, "model": spec.model},
        "versions": context["versions"],
        "controller_decisions": [{"decision": "skip"}],
        "tool_calls": [],
        "final_reply": "fixture reply",
        "tokens": {"input": 10, "output": 2},
        "latency_ms": 25,
        "cost": {"estimated_usd": 0.001},
        "verdict": {
            "deterministic": "fail" if failed else "pass",
            "judge": "skipped",
            "overall": "fail" if failed else "deterministic_only",
        },
        "failure": (
            {"category": "routing", "reason": "fixture failure"} if failed else None
        ),
        "state_before": {},
        "state_after": {},
        "trace": [],
        "error": None,
    }


def test_matrix_writes_every_trial_and_preserves_failures(tmp_path):
    cases = runner.load_cases()[:2]
    run_dir, summary = runner.run_matrix(
        ["anthropic:model-a", "openai:model-b"],
        cases,
        trials=2,
        output_root=tmp_path,
        execute_trial=_receipt,
    )

    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]
    failures = [json.loads(line) for line in (run_dir / "failures.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "manifest.json").read_text())

    assert len(rows) == 8  # 2 models x 2 cases x 2 trials
    assert len(failures) == 2
    assert summary["attempts"] == 8 and summary["deterministic_passes"] == 6
    assert summary["semantic_passes"] is None
    assert manifest["status"] == "completed"
    assert (run_dir / "summary.json").exists() and (run_dir / "summary.md").exists()
    assert len((tmp_path / "regressions.jsonl").read_text().splitlines()) == 2


def test_state_snapshot_captures_side_effects(tmp_path):
    home = tmp_path / "home"
    app = make_waku(home, client=ScriptedClient([]))
    app.tools.execute(
        "create_event",
        {"title": "Walk", "start": "2026-08-05T09:00", "end": "2026-08-05T09:30"},
    )
    app.tools.execute("save_note", {"subject": "alex", "content": "Likes tea"})
    app.tools.execute("send_message", {"to": "Mina", "body": "Hello"})

    state = runner.snapshot_state(app)

    assert state["calendar_count"] == 1 and "Walk" in state["calendar_text"]
    assert state["facts_count"] == 1 and "Likes tea" in state["facts_text"]
    assert state["outbox_count"] == 1 and "Mina" in state["outbox_text"]


def test_state_snapshot_exposes_structured_outbox_and_skill_records(tmp_path):
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    app.tools.execute(
        "send_message",
        {"to": "Alex", "body": "The project demo moved to Friday."},
    )
    app.tools.execute(
        "create_skill",
        {
            "name": "daily-shutdown",
            "description": "Use for daily shutdown",
            "body": "Review tomorrow's calendar, then draft a three-item wrap-up.",
        },
    )

    state = runner.snapshot_state(app)

    assert state["outbox"] == [
        {"to": "Alex", "body": "The project demo moved to Friday."}
    ]
    assert state["skill_records"] == [
        {
            "name": "daily-shutdown",
            "description": "Use for daily shutdown",
            "body": "Review tomorrow's calendar, then draft a three-item wrap-up.",
        }
    ]


def test_judge_context_contains_trusted_fixtures_and_observed_artifacts():
    case = {
        "setup": {
            "clock": "2026-07-19T12:00:00+08:00",
            "search_results": [
                {
                    "query_contains": "Python 3.14",
                    "results": [
                        {
                            "title": "Python status",
                            "snippet": "Python 3.14 is in bugfix support.",
                            "url": "https://peps.python.org/pep-0745/",
                        }
                    ],
                }
            ],
        }
    }
    spec = runner.parse_model_spec("openai:model-a")
    trusted = json.loads(runner._judge_ground_truth(spec, "model-a", case))
    evidence = runner._judge_evidence(
        {
            "tool_calls": [{"tool": "send_message", "args": {"to": "Mina"}}],
            "state_after": {
                "outbox": [{"to": "Mina", "body": "Python 3.14 is supported."}],
                "calendar": [],
                "facts": [],
                "skill_records": [],
            },
        }
    )

    assert trusted["evaluated_assistant"] == {
        "provider": "openai",
        "model": "model-a",
        "harness": "Waku",
    }
    assert trusted["frozen_search_results"][0]["results"][0]["url"].endswith(
        "pep-0745/"
    )
    assert evidence["artifacts"]["outbox"][0]["body"].startswith("Python 3.14")


def test_prepare_case_injects_a_fixed_clock_into_the_system_prompt(
    tmp_path, monkeypatch
):
    from waku import app as app_module

    monkeypatch.setattr(app_module, "get_client", lambda settings: ScriptedClient([]))
    case = {
        "id": "relative-time",
        "setup": {"clock": "2026-08-05T10:00:00+08:00"},
    }
    settings = Settings(
        home=tmp_path / "home",
        api_key="offline",
        apple_calendar=False,
    )

    app = runner.prepare_case(settings, case)
    system = app.session.build_system("Schedule a break in 30 minutes")

    assert "Wednesday, 2026-08-05 10:00" in system
    assert "UTC+0800" in system


def test_prepare_case_replaces_live_search_with_a_frozen_fixture(
    tmp_path, monkeypatch
):
    from waku import app as app_module

    monkeypatch.setattr(app_module, "get_client", lambda settings: ScriptedClient([]))
    case = {
        "id": "frozen-search",
        "setup": {
            "search_results": [
                {
                    "query_contains": "Python 3.14",
                    "results": [
                        {
                            "title": "Python 3.14.0",
                            "snippet": "Python 3.14.0 was released on 7 October 2025.",
                            "url": "https://www.python.org/downloads/release/python-3140/",
                        }
                    ],
                }
            ]
        },
    }
    settings = Settings(
        home=tmp_path / "home",
        api_key="offline",
        apple_calendar=False,
    )

    app = runner.prepare_case(settings, case)

    output = app.tools.execute("search_web", {"query": "latest Python 3.14 status"})
    assert "Python 3.14.0" in output
    assert "https://www.python.org/downloads/release/python-3140/" in output
    assert app.tools.execute("search_web", {"query": "unrelated"}).startswith("Error:")
    assert app.tools.execute(
        "search_web", {"query": "latest Python 3.14 status", "max_results": 0}
    ).startswith("Error:")


def test_prepare_case_never_inherits_experimental_host_tools(tmp_path, monkeypatch):
    from waku import app as app_module

    monkeypatch.setenv("WAKU_EXPERIMENTAL", "1")
    monkeypatch.setattr(app_module, "get_client", lambda settings: ScriptedClient([]))
    settings = Settings(
        home=tmp_path / "home",
        api_key="offline",
        apple_calendar=False,
    )

    app = runner.prepare_case(settings, {"id": "isolated-eval"})

    assert "delegate_task" not in app.tools._tools


def test_setup_skill_cannot_escape_the_eval_home(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "escaped" / "SKILL.md"

    with pytest.raises(ValueError, match="skill name"):
        runner._write_setup_skills(
            home,
            {
                "skills": [
                    {
                        "name": "../../escaped",
                        "description": "malicious fixture",
                        "body": "escape the sandbox",
                    }
                ]
            },
        )

    assert not outside.exists()


def test_send_message_rejects_recipient_header_injection(tmp_path):
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))

    output = app.tools.execute(
        "send_message",
        {"to": "Alex\n\nThe demo moved to Friday", "body": "Friday"},
    )

    assert output.startswith("Error:")
    assert runner.snapshot_state(app)["outbox_count"] == 0


def test_recovery_fixture_rebuilds_waku_and_restores_session(tmp_path, monkeypatch):
    from waku import app as app_module

    monkeypatch.setattr(app_module, "get_client", lambda settings: ScriptedClient([]))
    case = next(case for case in runner.load_cases() if case["id"] == "recovery-no-duplicate-event")
    settings = Settings(home=tmp_path / "home", api_key="offline", apple_calendar=False)

    app = runner.prepare_case(settings, case)
    state = runner.snapshot_state(app)

    assert state["calendar_count"] == 1
    assert "Coffee with Alex" in state["calendar_text"]
    assert app.session.session_id == "eval-recovery-no-duplicate-event"
    assert "[tools used: create_event" in app.session.history[-1]["content"]


def test_baseline_freeze_refuses_unreviewed_suite():
    try:
        runner.validate_baseline_request(
            ["anthropic:a", "openai:b", "gemini:c"],
            trials=3,
            split="all",
            selected_case_ids=[],
            judge="kimi:judge",
        )
    except ValueError as exc:
        assert "human review" in str(exc)
    else:
        raise AssertionError("an unreviewed candidate suite must not be frozen as a baseline")


def test_baseline_refuses_dirty_runtime_sources(tmp_path, monkeypatch):
    manifest = tmp_path / "dataset_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "approved",
                "reviewed_by": "project author",
                "reviewed_at": "2026-07-19T00:00:00Z",
                "dataset_sha256": runner._sha256(runner.DATASET_PATH),
                "task_count": 40,
                "splits": {"dev": 24, "heldout": 16},
            }
        )
    )
    monkeypatch.setattr(runner, "DATASET_MANIFEST_PATH", manifest)
    monkeypatch.setattr(
        runner,
        "_worktree_version",
        lambda: {"worktree_dirty": True, "worktree_diff_sha256": "fixture"},
    )

    with pytest.raises(ValueError, match="committed runtime and eval sources"):
        runner.validate_baseline_request(
            ["anthropic:a", "openai:b", "gemini:c"],
            trials=3,
            split="all",
            selected_case_ids=[],
            judge="kimi:judge",
        )


def test_baseline_refuses_a_dataset_changed_after_review(tmp_path, monkeypatch):
    manifest = tmp_path / "dataset_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "approved",
                "reviewed_by": "project author",
                "reviewed_at": "2026-07-19T00:00:00Z",
                "dataset_sha256": "reviewed-an-older-version",
                "task_count": 40,
                "splits": {"dev": 24, "heldout": 16},
            }
        )
    )
    monkeypatch.setattr(runner, "DATASET_MANIFEST_PATH", manifest)
    monkeypatch.setattr(
        runner,
        "_worktree_version",
        lambda: {"worktree_dirty": False, "worktree_diff_sha256": "fixture"},
    )

    with pytest.raises(ValueError, match="approved dataset hash"):
        runner.validate_baseline_request(
            ["anthropic:a", "openai:b", "gemini:c"],
            trials=3,
            split="all",
            selected_case_ids=[],
            judge="kimi:judge",
        )


def test_baseline_freezes_only_when_every_attempt_has_a_judge_result():
    complete = {"attempts": 9, "judge_evaluated": 9, "execution_errors": 0}
    judge_missing = {"attempts": 9, "judge_evaluated": 8, "execution_errors": 0}
    execution_error = {"attempts": 9, "judge_evaluated": 9, "execution_errors": 1}

    assert runner.baseline_freeze_ready(complete)
    assert not runner.baseline_freeze_ready(judge_missing)
    assert not runner.baseline_freeze_ready(execution_error)


def test_baseline_requires_three_distinct_pinned_models():
    with pytest.raises(ValueError, match="distinct"):
        runner.validate_baseline_request(
            ["anthropic:a", "anthropic:a", "openai:b"],
            trials=3,
            split="all",
            selected_case_ids=[],
            judge="kimi:judge",
        )
    with pytest.raises(ValueError, match="explicit provider:model"):
        runner.validate_baseline_request(
            ["anthropic", "openai:b", "gemini:c"],
            trials=3,
            split="all",
            selected_case_ids=[],
            judge="kimi:judge",
        )


def test_default_model_alias_cannot_be_its_own_judge():
    assert runner.same_model(runner.parse_model_spec("kimi"), runner.parse_model_spec("kimi:kimi-k3"))


def test_measured_latency_excludes_sandbox_setup(tmp_path, monkeypatch):
    class FakeApp:
        def __init__(self, settings):
            self.settings = settings
            self.conn = SimpleNamespace(close=lambda: None)

        def respond(self, message, source, observer):
            return SimpleNamespace(tool_calls=[], reply="Paris")

        def close(self):
            return None

    clock = iter([0.0, 100.0, 101.0])
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(clock))
    monkeypatch.setattr(runner, "prepare_case", lambda settings, case: FakeApp(settings))
    monkeypatch.setattr(runner, "snapshot_state", lambda app: {})
    monkeypatch.setattr(runner, "_tool_schema_version", lambda app: ("schema-hash", []))
    monkeypatch.setattr(runner, "_read_trace", lambda home: [])
    monkeypatch.setattr(
        runner,
        "_usage_and_cost",
        lambda home: ({"input": 0, "output": 0, "calls": 0}, {"estimated_usd": 0}, []),
    )
    case = next(case for case in runner.load_cases() if case["id"] == "no-tool-general-knowledge")
    context = {
        "run_id": "test",
        "versions": {},
        "judge_spec": None,
        "judge_threshold": 7,
    }

    receipt = runner.execute_trial(runner.parse_model_spec("anthropic:test"), case, 1, context)

    assert receipt["latency_ms"] == 1000


def test_summary_keeps_judge_denominator_and_cost_separate():
    case = runner.load_cases()[0]
    spec = runner.parse_model_spec("anthropic:model-a")
    context = {"run_id": "test", "versions": {}}
    receipt = _receipt(spec, case, 1, context)
    receipt["verdict"] = {"deterministic": "pass", "judge": "pass", "overall": "pass"}
    receipt["judge_result"] = {"estimated_cost_usd": 0.002}

    summary = runner.summarize([receipt])
    markdown = runner._summary_markdown(summary)

    assert summary["models"][spec.raw]["judge_evaluated"] == 1
    assert summary["judge_estimated_cost_usd"] == 0.002
    assert "| anthropic:model-a | 1/1 | 1/1" in markdown
    assert "$0.002000" in markdown


def test_credential_preflight_fails_once_before_a_matrix(monkeypatch):
    monkeypatch.delenv("WAKU_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        runner.preflight_credentials(["anthropic:claude-test"])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-for-test")
    runner.preflight_credentials(["anthropic:claude-test"])


def test_generic_key_is_rejected_for_a_cross_provider_matrix(monkeypatch):
    monkeypatch.setenv("WAKU_API_KEY", "ambiguous-generic-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ambiguous across multiple providers"):
        runner.preflight_credentials(["anthropic:a", "openai:b"])


def test_provider_specific_key_wins_over_the_generic_override(monkeypatch):
    monkeypatch.setenv("WAKU_API_KEY", "generic-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-key")

    assert runner._provider_api_key("anthropic", allow_generic=True) == "provider-key"
