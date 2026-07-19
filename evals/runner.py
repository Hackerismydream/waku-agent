"""Internal M1 eval runner: model matrix in, auditable artifacts out.

This is deliberately a repository tool, not an evaluation product.  Every
trial gets a fresh Waku home, and every receipt keeps the failures alongside
the passes so a summary can always be audited.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from waku.ops import scoring

REPO = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO / "evals" / "dataset.jsonl"
DATASET_MANIFEST_PATH = REPO / "evals" / "dataset_manifest.json"
ARTIFACT_SCHEMA_VERSION = 1
_VERSION_PATHS = ("waku", "evals", "scripts", "skills", "Makefile", "pyproject.toml")

load_cases = scoring.load_cases


@dataclass(frozen=True)
class ModelSpec:
    raw: str
    provider: str
    model: str


def parse_model_spec(value: str) -> ModelSpec:
    provider, separator, model = value.partition(":")
    provider = provider.strip()
    model = model.strip()
    if not provider:
        raise ValueError(f"invalid model spec {value!r}; expected provider:model")
    raw = f"{provider}:{model}" if separator else provider
    return ModelSpec(raw=raw, provider=provider, model=model)


def _effective_model(spec: ModelSpec) -> str:
    from waku.loop.models import PROVIDERS

    provider = PROVIDERS.get(spec.provider)
    return spec.model or (provider.model if provider else "")


def same_model(left: ModelSpec, right: ModelSpec) -> bool:
    """Compare explicit IDs and provider defaults without making an API call."""
    return left.provider == right.provider and _effective_model(left) == _effective_model(right)


def _provider_api_key(provider_name: str, *, allow_generic: bool) -> str:
    from waku.loop.models import PROVIDERS

    provider = PROVIDERS.get(provider_name)
    if provider is None:
        return ""
    specific = os.getenv(provider.key_env, "").strip()
    if specific:
        return specific
    return os.getenv("WAKU_API_KEY", "").strip() if allow_generic else ""


def preflight_credentials(runs: list[str], judge: str | None = None) -> None:
    """Reject missing provider credentials once, before an expensive matrix."""
    from waku.loop.models import PROVIDERS

    specs = [parse_model_spec(value) for value in [*runs, *([judge] if judge else [])]]
    provider_names = list(dict.fromkeys(spec.provider for spec in specs))
    allow_generic = len(provider_names) == 1
    missing = []
    for provider_name in provider_names:
        provider = PROVIDERS.get(provider_name)
        if provider is None:
            raise ValueError(
                f"unknown provider {provider_name!r}; choose one of {', '.join(PROVIDERS)}"
            )
        if not _provider_api_key(provider_name, allow_generic=allow_generic):
            missing.append(f"{provider_name} ({provider.key_env})")
    if missing:
        detail = "missing API credentials: " + ", ".join(missing)
        if os.getenv("WAKU_API_KEY", "").strip() and not allow_generic:
            detail += "; WAKU_API_KEY is ambiguous across multiple providers"
        raise ValueError(detail)


def _json_line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, default=str) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_source_version() -> str:
    files = [
        REPO / "waku" / "runtime" / "session.py",
        REPO / "waku" / "memory" / "retrieval_gate.py",
        REPO / "waku" / "memory" / "consolidation.py",
        *sorted((REPO / "skills").rglob("SKILL.md")),
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(REPO)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _tool_schema_version(app) -> tuple[str, list[str]]:
    schemas = app.tools.schemas()
    encoded = json.dumps(schemas, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest(), [schema["name"] for schema in schemas]


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _runtime_versions() -> dict:
    packages = {}
    for package in ("waku-agent", "anthropic", "openai"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "not-installed"
    return {"python": platform.python_version(), "packages": packages}


def _worktree_version() -> dict:
    try:
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *_VERSION_PATHS,
            ],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", *_VERSION_PATHS],
            cwd=REPO,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"worktree_dirty": True, "worktree_diff_sha256": "unknown"}

    digest = hashlib.sha256(status.encode() + diff)
    for line in status.splitlines():
        if not line.startswith("?? "):
            continue
        path = REPO / line[3:]
        if path.is_file():
            digest.update(line[3:].encode())
            digest.update(path.read_bytes())
    return {
        "worktree_dirty": bool(status.strip()),
        "worktree_diff_sha256": digest.hexdigest(),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _outbox_record(path: Path) -> dict:
    header, _, body = path.read_text(errors="replace").partition("\n\n")
    recipient = header.removeprefix("To:").strip()
    return {"to": recipient, "body": body.strip()}


def _skill_record(path: Path) -> dict | None:
    from waku.memory.procedural.loader import _parse

    skill = _parse(path)
    if skill is None:
        return None
    return {
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,
    }


def snapshot_state(app) -> dict:
    """Take the checkable end-state of one isolated Waku sandbox."""
    calendar = [
        dict(row)
        for row in app.conn.execute(
            'SELECT title, start, "end", attendees FROM calendar_events ORDER BY id'
        ).fetchall()
    ]
    facts = [
        dict(row)
        for row in app.conn.execute(
            "SELECT subject, content, source FROM facts ORDER BY id"
        ).fetchall()
    ]
    chat = [
        dict(row)
        for row in app.conn.execute(
            "SELECT role, content, session_id FROM chat_log ORDER BY id"
        ).fetchall()
    ]
    outbox_files = sorted((app.settings.home / "outbox").glob("*.txt"))
    skills = sorted((app.settings.home / "skills").rglob("SKILL.md"))
    outbox = [_outbox_record(path) for path in outbox_files]
    skill_records = [record for path in skills if (record := _skill_record(path)) is not None]
    soul_path = app.settings.home / "SOUL.md"
    outbox_text = "\n".join(path.read_text(errors="replace") for path in outbox_files)
    skills_text = "\n".join(path.read_text(errors="replace") for path in skills)
    soul_text = soul_path.read_text(errors="replace") if soul_path.exists() else ""
    return {
        "calendar_count": len(calendar),
        "calendar_text": json.dumps(calendar, ensure_ascii=False),
        "calendar": calendar,
        "facts_count": len(facts),
        "facts_text": json.dumps(facts, ensure_ascii=False),
        "facts": facts,
        "outbox_count": len(outbox_files),
        "outbox_text": outbox_text,
        "outbox": outbox,
        "outbox_files": [path.name for path in outbox_files],
        "skills_count": len(skills),
        "skills_text": skills_text,
        "skills": [str(path.relative_to(app.settings.home)) for path in skills],
        "skill_records": skill_records,
        "soul_text": soul_text,
        "chat_count": len(chat),
        "chat_text": json.dumps(chat, ensure_ascii=False),
        "chat": chat,
    }


def _write_setup_skills(home: Path, setup: dict) -> None:
    from waku.tools.memory_admin import valid_skill_name

    skills_root = (home / "skills").resolve()
    for skill in setup.get("skills", []):
        name = skill.get("name") if isinstance(skill, dict) else None
        if not valid_skill_name(name):
            raise ValueError(f"invalid eval setup skill name {name!r}")
        destination = (skills_root / name / "SKILL.md").resolve()
        try:
            destination.relative_to(skills_root)
        except ValueError as exc:
            raise ValueError(f"eval setup skill name escapes sandbox: {name!r}") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "---\n"
            f"name: {skill['name']}\n"
            f"description: {skill['description']}\n"
            "---\n\n"
            f"{skill['body'].strip()}\n"
        )


def _install_search_fixture(app, setup: dict) -> None:
    fixtures = setup.get("search_results", [])
    if not fixtures:
        return

    from waku.tools import search

    tool = search.make_tool()

    def frozen_search(query: str, max_results: int = 5) -> str:
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1:
            return "Error: max_results must be a positive integer."
        fixture = next(
            (
                row
                for row in fixtures
                if row["query_contains"].casefold() in query.casefold()
            ),
            None,
        )
        if fixture is None:
            return f"Error: no frozen eval search result matches query {query!r}."
        lines = [f"Frozen web results for '{query}' (eval fixture):"]
        for index, result in enumerate(fixture["results"][:max_results], 1):
            lines.append(
                f"{index}. {result['title']}\n   {result['snippet']}\n   {result['url']}"
            )
        return "\n".join(lines)

    tool.fn = frozen_search
    app.tools.register(tool)


def prepare_case(settings, case: dict):
    from waku.app import Waku

    setup = case.get("setup", {})
    # A benchmark case is data, never authorization to inherit host-level
    # delegation/terminal adapters from WAKU_EXPERIMENTAL.
    settings.experimental_tools = False
    fixed_now = datetime.fromisoformat(setup["clock"]) if setup.get("clock") else None
    clock = (lambda: fixed_now) if fixed_now is not None else None
    settings.ensure_home()
    _write_setup_skills(settings.home, setup)
    app = Waku(settings=settings, clock=clock)
    _install_search_fixture(app, setup)

    for fact in setup.get("facts", []):
        app.memory.facts.add(fact["subject"], fact["content"], source="eval_setup")
    for event in setup.get("events", []):
        app.tools.execute("create_event", event)
    for message in setup.get("outbox", []):
        app.tools.execute("send_message", message)

    session_id = f"eval-{case['id']}"
    if setup.get("exchanges"):
        app.session.start_new(session_id)
        for exchange in setup["exchanges"]:
            app.session.add_exchange(
                exchange["user"],
                exchange["assistant"],
                tool_calls=exchange.get("tool_calls"),
                source="eval_setup",
            )

    if setup.get("restart"):
        app.close()
        app.conn.close()
        app = Waku(settings=settings, clock=clock)
        _install_search_fixture(app, setup)
        app.session.switch(session_id)
    return app


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _read_trace(home: Path) -> list[dict]:
    rows = []
    for path in sorted((home / "traces").glob("*.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _usage_and_cost(home: Path) -> tuple[dict, dict, list[dict]]:
    from waku.ops.dashboard import price_for

    rows = _read_jsonl(home / "usage.jsonl")
    tokens_in = sum(row.get("in", 0) for row in rows)
    tokens_out = sum(row.get("out", 0) for row in rows)
    estimated = 0.0
    rates = []
    for row in rows:
        provider = row.get("provider", "")
        model = row.get("model", "")
        price_in, price_out = price_for(provider, model)
        estimated += row.get("in", 0) / 1_000_000 * price_in
        estimated += row.get("out", 0) / 1_000_000 * price_out
        rates.append(
            {"provider": provider, "model": model, "input_per_million": price_in,
             "output_per_million": price_out}
        )
    unique_rates = list({(row["provider"], row["model"]): row for row in rates}.values())
    return (
        {"input": tokens_in, "output": tokens_out, "calls": len(rows)},
        {"estimated_usd": round(estimated, 6), "rates": unique_rates},
        rows,
    )


def _judge_ground_truth(spec: ModelSpec, model: str, case: dict) -> str:
    setup = case.get("setup", {})
    verified = {
        "evaluated_assistant": {
            "provider": spec.provider,
            "model": model,
            "harness": "Waku",
        },
        "fixed_clock": setup.get("clock"),
        "seeded_facts": setup.get("facts", []),
        "seeded_events": setup.get("events", []),
        "seeded_outbox": setup.get("outbox", []),
        "seeded_skills": setup.get("skills", []),
        "frozen_search_results": setup.get("search_results", []),
    }
    return json.dumps(verified, ensure_ascii=False)


def _judge_evidence(receipt: dict) -> dict:
    state = receipt.get("state_after", {})
    return {
        "tool_calls": receipt.get("tool_calls", []),
        "artifacts": {
            "calendar": state.get("calendar", []),
            "facts": state.get("facts", []),
            "outbox": state.get("outbox", []),
            "skills": state.get("skill_records", []),
            "soul": state.get("soul_text", "")[:4000],
        },
    }


def _base_receipt(spec: ModelSpec, case: dict, trial: int, context: dict) -> dict:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": context["run_id"],
        "case_id": case["id"],
        "split": case["split"],
        "category": case["category"],
        "trial": trial,
        "input": case["input"],
        "model": {"spec": spec.raw, "provider": spec.provider, "model": spec.model},
        "versions": context["versions"],
        "tools_available": [],
        "environment": {},
        "controller_decisions": [],
        "tool_calls": [],
        "final_reply": "",
        "tokens": {"input": 0, "output": 0, "calls": 0},
        "cost": {"estimated_usd": 0.0, "rates": []},
        "latency_ms": 0,
        "verdict": {"deterministic": "error", "judge": "not_run", "overall": "error"},
        "judge_result": None,
        "failure": None,
        "state_before": {},
        "state_after": {},
        "usage": [],
        "trace": [],
        "error": None,
    }


def execute_trial(spec: ModelSpec, case: dict, trial: int, context: dict) -> dict:
    """Run one real Waku trial in a disposable home and return its full receipt."""
    from waku.config import Settings

    receipt = _base_receipt(spec, case, trial, context)
    app = None
    with tempfile.TemporaryDirectory(prefix=f"waku-eval-{spec.provider}-") as temp:
        home = Path(temp)
        settings = Settings(
            provider=spec.provider,
            api_key=_provider_api_key(
                spec.provider,
                allow_generic=context.get("allow_generic_api_key", True),
            ),
            model=spec.model,
            small_model="",
            home=home,
            apple_calendar=False,
            apple_tools=False,
            experimental_tools=False,
        )
        wall_started = time.perf_counter()
        measured_started = None
        try:
            app = prepare_case(settings, case)
            receipt["model"]["model"] = settings.model
            receipt["state_before"] = snapshot_state(app)
            tool_schema_sha256, receipt["tools_available"] = _tool_schema_version(app)
            receipt["versions"] = {
                **receipt["versions"],
                "tool_schema_sha256": tool_schema_sha256,
            }
            receipt["environment"] = {
                "search_backend": (
                    "fixture"
                    if case.get("setup", {}).get("search_results")
                    else "tavily"
                    if os.getenv("TAVILY_API_KEY") or os.getenv("WAKU_SEARCH_API_KEY")
                    else "duckduckgo"
                ),
                "experimental_tools": settings.experimental_tools,
                "semantic_store": settings.semantic_store,
                "small_model": settings.small_model,
                "max_iterations": settings.max_iterations,
                "max_tokens": settings.max_tokens,
                "history_turns": settings.history_turns,
                "retrieval_top_k": settings.retrieval_top_k,
                "consolidate_every": settings.consolidate_every,
            }
            observed = []

            def observe(kind, event):
                observed.append({"type": kind, **event})

            measured_started = time.perf_counter()
            result = app.respond(case["input"], source="eval", observer=observe)
            receipt["latency_ms"] = int((time.perf_counter() - measured_started) * 1000)
            receipt["controller_decisions"] = [
                event for event in observed if event["type"] == "gate"
            ]
            receipt["tool_calls"] = result.tool_calls
            receipt["final_reply"] = result.reply
            receipt["state_after"] = snapshot_state(app)
            prompt_event = next(
                (event for event in observed if event["type"] == "prompt"), None
            )
            if prompt_event:
                receipt["versions"] = {
                    **receipt["versions"],
                    "prompt_instance_sha256": prompt_event["sha256"],
                }

            passed, reason = scoring.check_case(
                case,
                result.tool_calls,
                state=receipt["state_after"],
                controller=receipt["controller_decisions"],
                reply=result.reply,
            )
            receipt["verdict"]["deterministic"] = "pass" if passed else "fail"

            judge_spec = context.get("judge_spec")
            if judge_spec is None:
                receipt["verdict"]["judge"] = "skipped"
                receipt["verdict"]["overall"] = (
                    "deterministic_only" if passed else "fail"
                )
            else:
                from waku.ops.judge import judge_reply

                judged = judge_reply(
                    case["input"],
                    result.reply,
                    provider=judge_spec.provider,
                    model=judge_spec.model,
                    criterion=case["judge_criteria"],
                    api_key=_provider_api_key(
                        judge_spec.provider,
                        allow_generic=context.get("allow_generic_api_key", True),
                    ),
                    ground_truth=_judge_ground_truth(spec, settings.model, case),
                    evidence=_judge_evidence(receipt),
                )
                if judged is not None:
                    from waku.ops.dashboard import price_for

                    price_in, price_out = price_for(judged["provider"], judged["judge"])
                    judged["estimated_cost_usd"] = round(
                        judged["tokens"]["input"] / 1_000_000 * price_in
                        + judged["tokens"]["output"] / 1_000_000 * price_out,
                        6,
                    )
                receipt["judge_result"] = judged
                threshold = context["judge_threshold"]
                judge_passed = judged is not None and judged["score"] >= threshold
                receipt["verdict"]["judge"] = (
                    "pass" if judge_passed else "error" if judged is None else "fail"
                )
                receipt["verdict"]["overall"] = "pass" if passed and judge_passed else "fail"
                if passed and not judge_passed:
                    reason = (
                        "judge unavailable"
                        if judged is None
                        else f"judge score {judged['score']} below {threshold}"
                    )

            if receipt["verdict"]["overall"] == "fail":
                receipt["failure"] = {
                    "category": scoring.classify_failure(
                        case,
                        reason,
                        judge_failed=passed and receipt["verdict"]["judge"] != "skipped",
                    ),
                    "reason": reason,
                }
        except (Exception, SystemExit) as exc:
            timing_start = measured_started if measured_started is not None else wall_started
            receipt["latency_ms"] = int((time.perf_counter() - timing_start) * 1000)
            reason = str(exc) or type(exc).__name__
            receipt["error"] = {"type": type(exc).__name__, "message": reason[:500]}
            receipt["failure"] = {
                "category": scoring.classify_failure(case, reason),
                "reason": f"execution error: {reason[:300]}",
            }
        finally:
            if app is not None:
                try:
                    receipt["state_after"] = snapshot_state(app)
                except Exception:
                    pass
                try:
                    app.close()
                    app.conn.close()
                except Exception:
                    pass
            receipt["trace"] = _read_trace(home)
            receipt["tokens"], receipt["cost"], receipt["usage"] = _usage_and_cost(home)
    return receipt


def summarize(receipts: list[dict]) -> dict:
    attempts = len(receipts)
    deterministic_passes = sum(
        row.get("verdict", {}).get("deterministic") == "pass" for row in receipts
    )
    judged = [
        row for row in receipts if row.get("verdict", {}).get("judge") in {"pass", "fail"}
    ]
    semantic_passes = (
        sum(row.get("verdict", {}).get("overall") == "pass" for row in judged)
        if judged else None
    )

    def group(field: str) -> dict:
        groups = {}
        for value in sorted({row.get(field, "unknown") for row in receipts}):
            rows = [row for row in receipts if row.get(field, "unknown") == value]
            groups[value] = {
                "attempts": len(rows),
                "deterministic_passes": sum(
                    row.get("verdict", {}).get("deterministic") == "pass" for row in rows
                ),
                "failures": sum(row.get("failure") is not None for row in rows),
            }
        return groups

    failure_counts = Counter(
        row["failure"]["category"] for row in receipts if row.get("failure")
    )
    model_groups = {}
    for model in sorted({row["model"]["spec"] for row in receipts}):
        rows = [row for row in receipts if row["model"]["spec"] == model]
        judged_rows = [
            row for row in rows if row.get("verdict", {}).get("judge") in {"pass", "fail"}
        ]
        model_groups[model] = {
            "attempts": len(rows),
            "deterministic_passes": sum(
                row.get("verdict", {}).get("deterministic") == "pass" for row in rows
            ),
            "semantic_passes": (
                sum(row.get("verdict", {}).get("overall") == "pass" for row in rows)
                if judged_rows
                else None
            ),
            "judge_evaluated": len(judged_rows),
            "judge_errors": sum(
                row.get("verdict", {}).get("judge") == "error" for row in rows
            ),
            "errors": sum(row.get("error") is not None for row in rows),
            "tokens": {
                "input": sum(row.get("tokens", {}).get("input", 0) for row in rows),
                "output": sum(row.get("tokens", {}).get("output", 0) for row in rows),
            },
            "estimated_cost_usd": round(
                sum(row.get("cost", {}).get("estimated_usd", 0) for row in rows), 6
            ),
            "judge_estimated_cost_usd": round(
                sum(
                    (row.get("judge_result") or {}).get("estimated_cost_usd", 0)
                    for row in rows
                ),
                6,
            ),
        }
    return {
        "attempts": attempts,
        "deterministic_passes": deterministic_passes,
        "deterministic_rate": round(deterministic_passes / attempts, 4) if attempts else 0.0,
        "judge_evaluated": len(judged),
        "judge_errors": sum(
            row.get("verdict", {}).get("judge") == "error" for row in receipts
        ),
        "semantic_passes": semantic_passes,
        "execution_errors": sum(row.get("error") is not None for row in receipts),
        "judge_estimated_cost_usd": round(
            sum(
                (row.get("judge_result") or {}).get("estimated_cost_usd", 0)
                for row in receipts
            ),
            6,
        ),
        "failures_by_category": dict(sorted(failure_counts.items())),
        "models": model_groups,
        "splits": group("split"),
        "categories": group("category"),
    }


def baseline_freeze_ready(summary: dict) -> bool:
    """A frozen baseline needs every execution and every requested Judge receipt."""
    return (
        summary.get("attempts", 0) > 0
        and summary.get("execution_errors", 0) == 0
        and summary.get("judge_evaluated", 0) == summary.get("attempts", 0)
    )


def _summary_markdown(summary: dict) -> str:
    lines = [
        "# Waku eval run",
        "",
        f"- Attempts: {summary['attempts']}",
        f"- Deterministic passes: {summary['deterministic_passes']}/{summary['attempts']}",
        f"- Judge-evaluated attempts: {summary['judge_evaluated']}",
        f"- Judge errors: {summary['judge_errors']}",
        f"- Semantic passes: {summary['semantic_passes'] if summary['semantic_passes'] is not None else 'not evaluated'}",
        f"- Execution errors: {summary['execution_errors']}",
        "",
        "| model | deterministic | semantic | errors | agent est. cost | judge est. cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model, row in summary["models"].items():
        semantic = (
            f"{row['semantic_passes']}/{row['judge_evaluated']}"
            if row["semantic_passes"] is not None
            else "not evaluated"
        )
        lines.append(
            f"| {model} | {row['deterministic_passes']}/{row['attempts']} | {semantic} "
            f"| {row['errors']} | ${row['estimated_cost_usd']:.6f} "
            f"| ${row['judge_estimated_cost_usd']:.6f} |"
        )
    lines += [
        "",
        "A deterministic pass with Judge skipped is not a semantic pass. "
        "Inspect results.jsonl and failures.jsonl before drawing conclusions.",
    ]
    return "\n".join(lines) + "\n"


TrialExecutor = Callable[[ModelSpec, dict, int, dict], dict]


def run_matrix(
    runs: list[str],
    cases: list[dict],
    *,
    trials: int,
    output_root: Path,
    judge: str | None = None,
    judge_threshold: int = 7,
    baseline: bool = False,
    execute_trial: TrialExecutor = execute_trial,
) -> tuple[Path, dict]:
    """Run a model/case/trial matrix and persist every receipt immediately."""
    if trials < 1:
        raise ValueError("trials must be at least 1")
    specs = [parse_model_spec(run) for run in runs]
    if not specs:
        raise ValueError("at least one model spec is required")
    effective_specs = [(spec.provider, _effective_model(spec)) for spec in specs]
    if len(set(effective_specs)) != len(effective_specs):
        raise ValueError("evaluated models must be distinct")
    judge_spec = parse_model_spec(judge) if judge else None
    if judge_spec and any(same_model(spec, judge_spec) for spec in specs):
        raise ValueError("the independent judge cannot be one of the evaluated models")
    if baseline:
        canonical = load_cases(DATASET_PATH)
        scoring.validate_suite(canonical)
        canonical_ids = [case["id"] for case in canonical]
        selected_ids = [case["id"] for case in cases]
        validate_baseline_request(
            runs,
            trials=trials,
            split="all" if selected_ids == canonical_ids else "filtered",
            selected_case_ids=[] if selected_ids == canonical_ids else selected_ids,
            judge=judge,
        )

    versions = {
        "git_commit": _git_commit(),
        "dataset_sha256": _sha256(DATASET_PATH),
        "prompt_source_sha256": _prompt_source_version(),
        "runtime": _runtime_versions(),
        **_worktree_version(),
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + versions[
        "dataset_sha256"
    ][:8]
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / run_id
    run_dir.mkdir()
    results_path = run_dir / "results.jsonl"
    failures_path = run_dir / "failures.jsonl"
    regressions_path = output_root / "regressions.jsonl"
    results_path.write_text("")
    failures_path.write_text("")
    manifest = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "baseline_requested": baseline,
        "baseline_frozen": False,
        "models": [spec.raw for spec in specs],
        "judge": judge_spec.raw if judge_spec else None,
        "judge_threshold": judge_threshold if judge_spec else None,
        "trials": trials,
        "case_ids": [case["id"] for case in cases],
        "versions": versions,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    context = {
        "run_id": run_id,
        "versions": versions,
        "judge_spec": judge_spec,
        "judge_threshold": judge_threshold,
        "allow_generic_api_key": len(
            {spec.provider for spec in [*specs, *([judge_spec] if judge_spec else [])]}
        )
        == 1,
    }

    receipts = []
    try:
        for spec in specs:
            for case in cases:
                for trial in range(1, trials + 1):
                    receipt = execute_trial(spec, case, trial, context)
                    receipts.append(receipt)
                    with results_path.open("a") as handle:
                        handle.write(_json_line(receipt))
                    if receipt.get("failure"):
                        with failures_path.open("a") as handle:
                            handle.write(_json_line(receipt))
                        regression = {
                            "run_id": run_id,
                            "case_id": receipt["case_id"],
                            "split": receipt["split"],
                            "model": receipt["model"]["spec"],
                            "trial": receipt["trial"],
                            "failure": receipt["failure"],
                            "git_commit": versions["git_commit"],
                            "recorded_at": _utc_now(),
                        }
                        with regressions_path.open("a") as handle:
                            handle.write(_json_line(regression))
    except BaseException as exc:
        manifest.update(status="interrupted", completed_at=_utc_now(), error=str(exc)[:500])
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        raise

    summary = summarize(receipts)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    (run_dir / "summary.md").write_text(_summary_markdown(summary))
    manifest.update(
        status="completed",
        completed_at=_utc_now(),
        baseline_frozen=baseline and baseline_freeze_ready(summary),
        summary=summary,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return run_dir, summary


def validate_baseline_request(
    runs: list[str],
    *,
    trials: int,
    split: str,
    selected_case_ids: list[str],
    judge: str | None,
) -> None:
    if len(runs) != 3 or trials != 3:
        raise ValueError("a frozen baseline requires exactly 3 models x 3 trials")
    if split != "all" or selected_case_ids:
        raise ValueError("a frozen baseline must run all 40 cases")
    if not judge:
        raise ValueError("a frozen baseline requires an independent --judge provider:model")
    specs = [parse_model_spec(run) for run in runs]
    judge_spec = parse_model_spec(judge)
    if any(not spec.model for spec in [*specs, judge_spec]):
        raise ValueError("a frozen baseline requires an explicit provider:model for every model")
    if len({(spec.provider, spec.model) for spec in specs}) != 3:
        raise ValueError("a frozen baseline requires three distinct model IDs")
    if any(same_model(spec, judge_spec) for spec in specs):
        raise ValueError("the independent judge cannot be one of the evaluated models")
    metadata = json.loads(DATASET_MANIFEST_PATH.read_text())
    if metadata.get("task_count") != 40 or metadata.get("splits") != {
        "dev": 24,
        "heldout": 16,
    }:
        raise ValueError("dataset manifest does not match the fixed 40-task split")
    if (
        metadata.get("status") != "approved"
        or not metadata.get("reviewed_by")
        or not metadata.get("reviewed_at")
    ):
        raise ValueError(
            "baseline freeze requires human review of every task; "
            "evals/dataset_manifest.json is still pending"
        )
    if metadata.get("dataset_sha256") != _sha256(DATASET_PATH):
        raise ValueError("approved dataset hash does not match evals/dataset.jsonl")
    if _worktree_version()["worktree_dirty"]:
        raise ValueError("a frozen baseline requires committed runtime and eval sources")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Waku's fixed internal eval suite.")
    parser.add_argument("runs", nargs="+", metavar="provider:model")
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="all")
    parser.add_argument("--cases", default="", help="comma-separated case ids")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--judge", help="independent provider:model for final-reply quality")
    parser.add_argument("--judge-threshold", type=int, default=7)
    parser.add_argument("--baseline", action="store_true", help="freeze the full M1 baseline")
    parser.add_argument("--output", type=Path, help="artifact root (default .waku/evals)")
    args = parser.parse_args()

    all_cases = load_cases(DATASET_PATH)
    scoring.validate_suite(all_cases)
    selected_ids = [value.strip() for value in args.cases.split(",") if value.strip()]
    cases = [
        case for case in all_cases
        if (args.split == "all" or case["split"] == args.split)
        and (not selected_ids or case["id"] in selected_ids)
    ]
    missing = sorted(set(selected_ids) - {case["id"] for case in cases})
    if missing or not cases:
        raise SystemExit(f"no matching cases; missing ids: {missing}")
    if args.baseline:
        try:
            validate_baseline_request(
                args.runs,
                trials=args.trials,
                split=args.split,
                selected_case_ids=selected_ids,
                judge=args.judge,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    try:
        preflight_credentials(args.runs, args.judge)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    from waku.config import load_settings

    output_root = args.output or load_settings().home / "evals"
    try:
        run_dir, summary = run_matrix(
            args.runs,
            cases,
            trials=args.trials,
            output_root=output_root,
            judge=args.judge,
            judge_threshold=args.judge_threshold,
            baseline=args.baseline,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(_summary_markdown(summary), end="")
    print(f"artifact: {run_dir}")
    if summary["execution_errors"] or (args.baseline and not baseline_freeze_ready(summary)):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
