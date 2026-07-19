# Waku internal evals

Waku's eval code exists to improve Waku. It is not a generic benchmark service,
dataset UI, or leaderboard. The fixed task suite answers three product questions:

1. Did Waku choose the right path and leave the right local state?
2. Is the final reply useful and honest about what happened?
3. Did a Prompt, model, controller, Skill, or tool change cause a regression?

## 1. Fixed task suite

[`evals/dataset.jsonl`](../evals/dataset.jsonl) contains exactly 40 candidate tasks:

- 24 `dev` tasks for Prompt, routing, and Skill iteration;
- 16 `heldout` tasks that must not guide those changes;
- seven product areas: simple Q&A, memory, scheduling, Skill workflows,
  multi-tool tasks, safety, and recovery.

The split is part of the contract. Moving a difficult task from `heldout` to `dev`
after seeing a result is test leakage.

The current suite is deliberately marked `pending_author_review` in
[`evals/dataset_manifest.json`](../evals/dataset_manifest.json). A frozen baseline
is blocked until a human reviews every input, setup fixture, deterministic truth,
and Judge criterion, then records their name, review time, and the reviewed
`dataset.jsonl` SHA-256 in that manifest. A later dataset edit invalidates approval.
Code generation is not human review.

## 2. One task contract

Each JSONL row has this shape:

```json
{
  "id": "read-before-write",
  "split": "heldout",
  "category": "scheduling",
  "failure_category": "routing",
  "setup": {
    "events": [{"title": "Lunch", "start": "2026-08-05T12:00"}],
    "restart": false
  },
  "input": "Check my calendar, then schedule a walk",
  "expect": {
    "tool_path": ["list_events", "create_event"],
    "path_mode": "exact",
    "calls": [
      {"tool": "create_event", "args_contains": {"title": "walk"}}
    ],
    "gate": "retrieve",
    "require_success": true,
    "state": {"calendar_count": 2, "calendar_contains": ["walk"]}
  },
  "judge_criteria": "Reads before writing and reports only the local action."
}
```

`setup` may seed facts, calendar events, outbox drafts, user Skills, and prior
exchanges. `restart: true` rebuilds Waku against the same SQLite home and reloads
the session before the measured turn. Setup activity is not counted as model work.

The deterministic scorer in [`waku/ops/scoring.py`](../waku/ops/scoring.py) checks:

- exact or ordered tool paths;
- call argument substrings and minimum call counts;
- retrieval-gate decisions;
- tool errors;
- SQLite, calendar, outbox, SOUL.md, and user-Skill end state.

The Judge never decides those facts. It reads only the task, final reply, and the
task-specific `judge_criteria`.

## 3. Development and release commands

```bash
# Hermetic code and contract checks. No model calls.
make eval

# Explicit live pass with the active provider. Never runs implicitly in make gate.
make eval-live

# A reproducible model matrix; examples can use --split dev or --cases id-a,id-b.
make shootout RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8"

# Final M1 freeze: exactly 3 models x 3 trials over all 40 tasks.
# Refuses to start until the task manifest records human approval, and refuses
# a Judge that is also one of the evaluated models.
make baseline \
  RUNS="provider-a:model-a provider-b:model-b provider-c:model-c" \
  JUDGE="provider-d:model-d"
```

`make gate` has three honest outcomes:

- `GATE CLOSED`: deterministic checks or Judge threshold failed;
- `GATE CONDITIONAL`: deterministic checks passed, but no Judge key was available;
- `GATE OPEN`: deterministic checks and the semantic Judge both passed.

The conditional state exits successfully so local development can continue, but it
is not presented as a complete semantic release verdict.

## 4. Run artifacts

Every matrix run writes a timestamped directory under `.waku/evals/`:

```text
.waku/evals/
├── regressions.jsonl
└── <run-id>/
    ├── manifest.json
    ├── results.jsonl
    ├── failures.jsonl
    ├── summary.json
    └── summary.md
```

`results.jsonl` preserves every attempt, including:

- task input, split, category, model, and trial number;
- Git commit, dataset hash, stable Prompt-source hash, actual system-prompt hash,
  Python/package versions, and relevant controller limits;
- available tool names, tool-schema hash, search backend, and relevant opt-in flags;
- retrieval-controller decisions and full tool calls;
- final reply and independent Judge result when enabled;
- input/output tokens for the main loop and retrieval gate;
- measured-turn latency (sandbox setup excluded) and estimated agent/Judge cost;
- sandbox state before and after the measured turn;
- deterministic, Judge, and overall verdicts;
- the full JSONL execution trace and any execution error.

`failures.jsonl` keeps failed receipts intact. `.waku/evals/regressions.jsonl`
appends their compact identity and seven-bucket classification across runs:

`routing`, `retrieval`, `skill`, `tool_args`, `safety`, `recovery`, `response`.

A deterministic pass with Judge skipped is recorded as `deterministic_only`, never
as a semantic pass. Costs are estimates from token counts and documented price
tables; cache, batch, and negotiated discounts are not inferred.

The manifest separates `baseline_requested` from `baseline_frozen`. Credential or
execution errors, or even one unavailable Judge result, leave `baseline_frozen`
false; a partially executed matrix is still useful evidence, but it is not a baseline.
The runner also checks every provider credential before creating the matrix, so one
missing key fails once instead of generating hundreds of identical error receipts.
`WAKU_API_KEY` is accepted only for a single-provider run; a cross-provider matrix
requires each provider's named key so one credential cannot be silently reused.

## 5. Baseline discipline

The frozen M1 baseline is complete only when all of the following are true:

1. the author has reviewed all 40 contracts and approved the manifest;
2. runtime and eval sources are committed; ordinary runs record a dirty-diff hash,
   while a frozen baseline refuses a relevant dirty working tree;
3. three pinned model IDs each ran every task three times;
4. an independent Judge evaluated final replies;
5. the artifact completed without execution or credential errors;
6. failures remain in the artifact and regression ledger;
7. the Git commit, dataset hash, and Prompt-source hash are retained.

Prompt, routing, and Skill changes may use `dev` failures. The `heldout` split is
opened only for a release comparison. Real user tasks stay outside this suite until
they are manually redacted and reviewed.

## 6. Coding battery

[`evals/coding.jsonl`](../evals/coding.jsonl) remains a separate optional battery.
It delegates a coding task through pi and uses the task's `verify` command as truth:

```bash
make shootout-coding RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8"
```

Coding results are not mixed into the 40-task personal-assistant baseline. A passing
code verification and a passing Waku task measure different product behavior.
