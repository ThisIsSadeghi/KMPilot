# Phase 2 — Plan

Turn the inventory into a **written, confirmable plan**, and keep it on disk as the resume ledger. Discovery answers *what is in this repo*; this answers *what will be done to it, in what order, and what will not be touched*.

Nothing downstream may write a source file until the user has confirmed this plan. That gate is the whole safety property of project-scoped migration — a large diff the user did not agree to in advance is not acceptable — so the plan is a deterministic script, not model prose. A plan re-improvised on the next invocation is not a plan and cannot be resumed.

## Run it

```bash
python3 .claude/skills/_shared/kmpilot_plan.py --root {repo} --discovery /tmp/kmpilot-discovery.json
```

Pass `--discovery` with the report from phase 1 so the plan is built from **the inventory the user was just shown**. Without it the script runs discovery itself, which is correct but re-reads the repo.

| Flag | Use |
|---|---|
| `--root PATH` | the repo to plan (default: cwd) |
| `--discovery PATH` | consume an existing discovery report instead of running the pass again |
| `--dry-run` | generate and print, **write nothing** |
| `--status` | print the plan on disk; runs no discovery, writes nothing |
| `--confirm` | mark the plan confirmed |
| `--set-tier ID=TIER` | overrule a tier proposal (`common`\|`data`\|`designsystem`\|`split`); repeatable |
| `--mark ID=STATUS` | record progress (`pending`\|`in-progress`\|`done`\|`skipped`); repeatable |
| `--note TEXT` | note attached to the `--mark` entries written in this run |
| `--refuse ID` | record a blocker a rewrite pass hit; needs `--reason`, takes `--evidence`; repeatable |
| `--unrefuse ID` | withdraw a recorded mid-rewrite refusal; repeatable |
| `--json-only` / `--compact` | machine-readable output |

Exit codes: **0** normal, **1** the repo is not a migration target (or there is no plan to confirm), **2** an unusable target or a bad argument. A refused subject is a finding, not an error.

## What it writes

Exactly one file: **`.claude/docs/_project/migration-plan.json`** inside the target repo, beside `stitch-project.json` and `check-report.json`. **No source file is touched in this phase.** Say that to the user in as many words — it is what makes the plan safe to review rather than something to be rushed through.

## The steps

Every unit of work is one step with a stable id (`hoist-core-network`, `migrate-search`, `report`). Ids are what carry progress from one session to the next and what the user types to overrule a decision, so they do not change between runs.

| Kind | What it does |
|---|---|
| `hoist` | a shared module → `:core:common` / `:core:data` / `:core:designsystem` |
| `extract` | shared code living **inside** a feature → the same tiers; this is what removes a cross-feature edge |
| `relocate` | a feature outside `feature/` → `feature/{name}/`, so the checker can grade it at all |
| `migrate` | rewrite one feature to zero checker findings, then promote it to `managedFeatures` |
| `report` | `MIGRATION-REPORT.md`, a spec per migrated feature, the closing step |

| Status | Meaning |
|---|---|
| `pending` | not started |
| `in-progress` | a rewrite phase is working on it — where a resumed run picks up |
| `done` | finished, **or** already conforming / in `managedFeatures`. Never re-migrated |
| `refused` | Android-locked, no screen entry point, unhoistable shared code — or a blocker a rewrite pass hit, recorded with `--refuse` |
| `blocked` | its dependency is refused, or it sits in a dependency cycle |
| `skipped` | the user took it out of scope |

## Order

A step never precedes the code it consumes: hoists and extracts before the features that use them, `relocate` before that feature's `migrate`, `report` last. Within that constraint the order is discovery's own topological module order.

A `migrate` step **never depends on another `migrate` step.** Features do not depend on features; the cross-feature edge is exactly what the `extract` step removes. Hanging one feature's rewrite on another's would make a single refused feature block every feature that happens to import it today — a wrong refusal, which is the failure that costs a user's trust.

## The work list

Each `migrate` step carries **rewrite passes** — the checker's own findings, clustered by rule and routed to the agent that already writes that layer. Migration adds no agents.

| Cluster | Rules | Agent |
|---|---|---|
| `data` | R9, R11c | `data-layer` |
| `state` | R3, R11a, R11b | `ui-layer` |
| `designsystem` | R5, R13, S4 | `ui-layer` |
| `strings` | R12, R12res | `ui-layer` |
| `structure` | R7, S1, S2 | `ui-layer` |
| `di` | R8 | `integrator` |
| `integration` | I1–I4 | `integrator` |

Every finding lands in exactly one pass, with its `file:line`. A **refused** step carries **no** passes at all: a refusal is a pass, not a smaller job, and a work list on a refused feature reads as something somebody is expected to work through.

A feature still outside `feature/` has **no** findings yet — the checker only grades `feature/*`. Its `migrate` step says so (`gradable: false`); the work list appears once the `relocate` step has run and the feature is re-graded.

## Confirmation, and how it lapses

A plan is `draft` until `--confirm`. Confirmation records the exact list of step ids the user approved.

- Regeneration that produces a **different step list** — a feature appeared, a refusal cleared — drops the plan back to `draft` with a `plan-reconfirm-required` note. Progress is kept; only the approval lapses.
- `--set-tier` also lapses it: a tier the user just changed is not the plan they confirmed.
- `--confirm` refuses when there is no plan on disk. Confirming a plan nobody has seen is not a gate.

## Present it, then ask

Show the user, in this order:

1. **The plan** — every step, in order, with its status and its one-line reason.
2. **Decisions needed** — steps with `needsDecision`, which today means a shared module whose files disagree about a tier (`split`). Present each with the file breakdown and the trigger that fired.
3. **Refusals and blocked steps** — with evidence, and for blocked steps what unblocks them.
4. **What the plan does not cover** — pre-existing tests are out of scope; `/test-feature` afterwards is the user's call.

Then ask for confirmation with `AskUserQuestion`, offering: confirm as-is · change a tier · take a feature out of scope. Apply corrections with `--set-tier` / `--mark ID=skipped`, re-present, and only then `--confirm`.

**Tier proposals stay proposals until the user settles them.** Discovery decides mechanical facts; where shared code lands is the judgment call this gate exists for. Never present a proposal as a settled verdict, and never confirm on the user's behalf.

## Refusing once a pass has opened the feature

Discovery classifies the three refusals it can see by reading the repo — Android-locked APIs, no screen entry point, unhoistable shared code. Some blockers only surface once a rewrite pass is inside the feature. That refusal is recorded with `--refuse`, and it is **not** a `--mark` status:

```bash
python3 .claude/skills/_shared/kmpilot_plan.py --root {repo} \
    --refuse migrate-legacy --reason "custom DI graph with no Koin equivalent" \
    --evidence Wiring.kt:44 --evidence Wiring.kt:91
```

**Revert first, refuse second.** A refused feature is left exactly as it was found — the per-feature checkpoint commit is the mechanism, and the recorded refusal keeps the status the step held (`priorStatus`) so a refusal taken with work already under way is called out for exactly that reason. Half a rewrite is worse than none: it is the failure the refusal exists to prevent.

| Rule | Why |
|---|---|
| `--mark ID=refused` is **rejected** | a remembered `refused` would outlive the blocker being fixed — a permanent refusal nobody can lift. Remembered statuses only apply where the derived one is `pending`, so nothing could ever clear it |
| a refusal is a **declaration**, not a status | `--refuse` records a reason and evidence beside the tier decisions; `build_steps` derives `refused` from it exactly as it does from discovery's own refusals. The status stays derived |
| `--reason` is **required** | a refusal with no reason cannot go into `MIGRATION-REPORT.md`, and is indistinguishable from giving up |
| only `pending` / `in-progress` steps can be refused | refusing a `done` step claims a migration that happened did not; refusing a `blocked` one hides the dependency that is the actual problem |
| it does **not** lapse confirmation | a refusal is a pass — the run moves to the next feature rather than stopping for re-approval |
| `--unrefuse ID` withdraws it | the step returns to `pending` with its work list. A refusal the user cannot lift is the wrong-refusal failure in slow motion |

A refused `hoist`, `extract` or `relocate` blocks its dependents through the ordinary dependency machinery, and the plan says so with a `rewrite-refusal-blocks-work` note — work the user confirmed will now not run, which they get to hear about. Refusing a **feature** blocks nothing: features never depend on features.

Discovery cannot re-derive a rewrite refusal, so the record is the only memory of it — but **derived facts still win**. A record naming a step that discovery now refuses on its own, or that reached `managedFeatures`, is reported as `stale-rewrite-refusal` and **not** obeyed. It is kept rather than dropped: silently discarding it would lose the reason.

## Resuming

Re-invoking reads the ledger and continues:

```bash
python3 .claude/skills/_shared/kmpilot_plan.py --root {repo} --status
```

`next` names the step to pick up. Statuses the rewrite phases wrote (`in-progress`, `done`, `skipped`) survive regeneration; facts about the repo (`refused`, `blocked`, and a `done` that came from `managedFeatures`) are re-derived every run and are **not** overridable from the ledger — that is what stops a refused feature from being half-migrated by a stale entry, and a promoted feature from being migrated twice.

## Verify the script itself

```bash
python3 scripts/kmpilot_plan_test.py     # every step kind and status; the gate; the ledger
bash scripts/migrate-matrix.sh plan      # the plan variants, incl. their negative controls
```

Both are upstream-only (`scripts/` is stripped on install).
