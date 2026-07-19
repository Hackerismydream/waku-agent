"""Model shootout — same agent body, different brains, real receipts.

    python scripts/shootout.py kimi:kimi-k3 anthropic:claude-opus-4-8
    make shootout RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8"

For each provider:model pair this runs every case in evals/dataset.jsonl through
a REAL Waku (fresh isolated home per run, your keys from .env) and scores it the
same deterministic way the live eval tier does: did the right tool fire, with
the right arguments — 0 or 1, no judge involved. Alongside correctness it
collects what benchmarks usually hide: tokens, estimated dollars (per-model
pricing, same table the dashboard uses), latency, and loop iterations.

Output: a complete run directory under .waku/evals/ (manifest, every trial,
failures, summary, and regression ledger). Publish a redacted artifact or rerun
it with your own keys; never trust a summary without its receipts.

Honesty notes baked in: cost is an ESTIMATE from tokens x list price (cache
discounts not modeled); pass-rate is deterministic tool-behavior, not vibes —
for judged answer quality run `make eval-judge` per provider (use a third-party
judge model, never one of the contestants).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from waku.config import load_settings  # noqa: E402  (loads .env keys)
from waku.ops.scoring import check_case as check_case, load_cases  # noqa: E402  (re-export)

DATASET = load_cases()


def run_one(provider: str, model: str, cases: list[dict], trials: int = 1) -> dict:
    """Compatibility summary over the canonical receipt-producing runner."""
    from evals.runner import preflight_credentials, run_matrix

    spec = f"{provider}:{model}" if model else provider
    preflight_credentials([spec])
    with tempfile.TemporaryDirectory(prefix="waku-shootout-summary-") as temp:
        run_dir, _ = run_matrix(
            [spec], cases, trials=trials, output_root=Path(temp)
        )
        receipts = [
            json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()
        ]

    rows = []
    for case in cases:
        attempts = [receipt for receipt in receipts if receipt["case_id"] == case["id"]]
        hits = sum(row["verdict"]["deterministic"] == "pass" for row in attempts)
        failures = [row["failure"]["reason"] for row in attempts if row.get("failure")]
        rows.append({
            "case": case["id"],
            "hits": hits,
            "trials": trials,
            "passed": hits == trials,
            "why": failures[-1] if failures else "ok",
            "avg_latency_s": round(
                sum(row["latency_ms"] for row in attempts) / max(len(attempts), 1) / 1000, 1
            ),
            "iterations": max(
                (event.get("iterations", 0) for row in attempts for event in row["trace"]
                 if event.get("type") == "turn_end"),
                default=0,
            ),
            "tokens_in": sum(row["tokens"]["input"] for row in attempts),
            "tokens_out": sum(row["tokens"]["output"] for row in attempts),
            "cost_usd": round(
                sum(row["cost"]["estimated_usd"] for row in attempts), 4
            ),
        })
    n = max(len(rows), 1)
    resolved_model = receipts[0]["model"]["model"] if receipts else model
    return {"provider": provider, "model": resolved_model,
            "trials": trials, "cases": rows,
            "hit_rate": round(sum(r["hits"] for r in rows) / (n * trials), 3),
            "passed": sum(r["hits"] for r in rows), "total": len(rows) * trials,
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "avg_latency_s": round(sum(r["avg_latency_s"] for r in rows) / n, 1)}


def markdown(results: list[dict]) -> str:
    lines = ["| brain | pass rate | est cost | avg latency | tokens in/out |",
             "|---|---|---|---|---|"]
    for r in results:
        tin = sum(c["tokens_in"] for c in r["cases"])
        tout = sum(c["tokens_out"] for c in r["cases"])
        lines.append(f"| {r['provider']}:{r['model']} | {r['passed']}/{r['total']} "
                     f"| ${r['cost_usd']:.4f} | {r['avg_latency_s']}s | {tin}/{tout} |")
    trials = results[0].get("trials", 1) if results else 1
    lines.append("")
    lines.append(f"Every case runs {trials} trial(s) — tool-calling is nondeterministic, "
                 "so a rate beats a coin flip. Same agent, same tasks, keys from your own "
                 ".env — re-run with: `make shootout RUNS=\"...\"`. Costs are estimates "
                 "(tokens x list price; cache discounts not modeled). Pass = deterministic "
                 "tool-behavior checks from evals/dataset.jsonl.")
    return "\n".join(lines)


def coding_shootout(runs: list[str], cases: list[dict], trials: int) -> str:
    """Cross-model CODING round: pi runs each contestant's model on each coding
    task, scored by the task's `verify` command (tests pass = 1). Prints a table."""
    from waku.ops.coding_eval import run_coding_case

    lines = ["| brain | pass rate | avg latency | detail |", "|---|---|---|---|"]
    for spec in runs:
        provider, _, model = spec.partition(":")
        print(f"\n=== coding · {provider}:{model or '(default)'} — {len(cases)} cases x {trials} ===")
        hits = total = 0
        lats, notes = [], []
        for case in cases:
            for _ in range(trials):
                passed, why, secs = run_coding_case(provider, model, case)
                hits += 1 if passed else 0
                total += 1
                lats.append(secs)
                print(f"  [{'PASS' if passed else 'fail'}] {case['id']:16} {secs:6.1f}s  {why}")
            notes.append(f"{case['id']}:{'ok' if passed else why[:24]}")
        avg = round(sum(lats) / len(lats), 1) if lats else 0
        lines.append(f"| {spec} | {hits}/{total} | {avg}s | {'; '.join(notes)} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the same tasks on different brains.")
    parser.add_argument("runs", nargs="+", metavar="provider:model",
                        help="e.g. kimi:kimi-k3 anthropic:claude-opus-4-8 "
                             "(omit :model for the provider default)")
    parser.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    parser.add_argument("--trials", type=int, default=3,
                        help="attempts per case (default 3 — rates, not coin flips)")
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="all",
                        help="fixed dataset split (default all)")
    parser.add_argument("--judge", default="",
                        help="independent provider:model for final-reply quality")
    parser.add_argument("--baseline", action="store_true",
                        help="freeze the reviewed 3-model x 3-trial M1 baseline")
    parser.add_argument("--output", type=Path,
                        help="artifact root (default .waku/evals)")
    parser.add_argument("--coding", action="store_true",
                        help="run the CODING battery (evals/coding.jsonl) via pi per model, "
                             "scored by each task's verify command")
    args = parser.parse_args()

    if args.coding:
        from waku.ops.coding_eval import load_coding_cases, pi_available
        if not pi_available():
            raise SystemExit("pi isn't installed — the coding battery needs it. "
                             "Install: npm install -g --ignore-scripts @earendil-works/pi-coding-agent")
        wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
        cases = [c for c in load_coding_cases() if not wanted or c["id"] in wanted]
        if not cases:
            raise SystemExit(f"no coding cases match {wanted!r} — ids: "
                             f"{[c['id'] for c in load_coding_cases()]}")
        table = coding_shootout(args.runs, cases, args.trials)
        print("\n" + table)
        out_dir = load_settings().home / "shootout"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out_dir / f"coding-{stamp}.md").write_text(table + "\n")
        print(f"\nreport: {out_dir}/coding-{stamp}.md")
        return

    from evals.runner import (
        baseline_freeze_ready,
        preflight_credentials,
        run_matrix,
        validate_baseline_request,
    )
    from waku.ops.scoring import validate_suite

    validate_suite(DATASET)
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
    cases = [
        case for case in DATASET
        if (args.split == "all" or case["split"] == args.split)
        and (not wanted or case["id"] in wanted)
    ]
    missing = sorted(set(wanted) - {case["id"] for case in cases})
    if missing or not cases:
        raise SystemExit(
            f"no complete case selection; missing ids: {missing} — "
            f"available: {[case['id'] for case in DATASET]}"
        )
    if args.baseline:
        try:
            validate_baseline_request(
                args.runs,
                trials=args.trials,
                split=args.split,
                selected_case_ids=wanted,
                judge=args.judge or None,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    try:
        preflight_credentials(args.runs, args.judge or None)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    out_root = args.output or load_settings().home / "evals"
    try:
        run_dir, summary = run_matrix(
            args.runs,
            cases,
            trials=args.trials,
            output_root=out_root,
            judge=args.judge or None,
            baseline=args.baseline,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("\n" + (run_dir / "summary.md").read_text())
    print(f"artifact: {run_dir}")
    if summary["execution_errors"] or (args.baseline and not baseline_freeze_ready(summary)):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
