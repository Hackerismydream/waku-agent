"""DETERMINISTIC EVAL — release language must match what actually ran."""

from __future__ import annotations

import json

from waku.ops import release_gate


def test_gate_status_is_conditional_when_judge_was_skipped():
    assert release_gate.gate_status("pass", "skipped") == "conditional"
    assert release_gate.gate_status("pass", "pass") == "open"
    assert release_gate.gate_status("fail", "not run") == "closed"
    assert release_gate.gate_status("pass", "fail") == "closed"


def test_report_persists_release_status(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_HOME", str(tmp_path / "home"))

    release_gate.report("pass", "skipped", {"deterministic": {"passed": 10}})

    record = json.loads((tmp_path / "home" / "eval_report.json").read_text())
    assert record["release"] == "conditional"
