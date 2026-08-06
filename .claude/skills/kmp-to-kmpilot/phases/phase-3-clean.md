# Phase 3 — Clean

Rewrite the project into KMPilot shape, **one step at a time, in the planned order**, each one independently verifiable and independently undoable.

This is the phase the whole design exists to make safe. Project *scope*, feature-at-a-time *execution*: if this ever becomes one sweeping pass over the whole repo, every protection built in phases 1 and 2 is gone. The scale of the change is contained by the plan gate and the per-step checkpoint, never by narrowing what the transform is allowed to touch.

## Nothing starts without a confirmed plan

`kmpilot_migrate.py` refuses every command — not only `begin` — while the plan is `draft`. That is the gate from phase 2 doing its job: a large diff the user did not agree to in advance is not acceptable, and a gate that only guarded the first step would be walked around by starting at the second.

## The envelope is a script; the rewriting is the agents

```bash
python3 .claude/skills/_shared/kmpilot_migrate.py --root {repo} <command> [step]
```

| Command | What it does |
|---|---|
| `begin` | cut `kmpilot/migrate-{project}`, commit the tree as a checkpoint, record the way back |
| `next` | the resume pointer: the step to pick up, with its rewrite passes and `file:line` findings |
| `status` | every step, its status, and which ones have a checkpoint |
| `checkpoint {id}` | open a step: commit the current state and mark it in progress |
| `verify {id}` | is this step actually finished? Exit 0 only when it is |
| `complete {id}` | verify, commit, mark done (`--force` records an unverified sign-off as such) |
| `refuse {id} --reason "…"` | restore the subject to its checkpoint **and** record the refusal |

**Everything mechanical is in the script; nothing about Kotlin is.** The rewriting is delegated to the layer agents that already exist — the same reason discovery and the plan are scripts applies to the undo: it has to work identically every time rather than however a model felt like typing `git` that day.

## Dirty trees are supported, not refused

`begin` never refuses a dirty working tree — it absorbs it:

```
git switch -c kmpilot/migrate-{project}
git commit -am "checkpoint before migration"
```

Adopt mode's clean-tree refusal is deliberately **not** carried over: adopt only ever adds files, so a bad run is `rm -rf` of known paths, while migration rewrites working source and needs a real undo — but the undo does not have to be *"refuse until you clean up"*.

Undo the whole run with **`git switch -`**. That restores the pre-migration *committed* state; work that was never committed lives inside the checkpoint commit and is asked for by name — `begin` prints the exact `git restore --source={ref} -- .` when the tree was dirty. Say this to the user rather than letting them discover it.

## The loop, per step

```
next  →  checkpoint  →  [rewrite passes]  →  verify  →  complete
                                    └── blocked? ──→  refuse (restores, then records)
```

1. **`next`** — never pick a step by eye. The order is the safety property: nothing that consumes shared code is rewritten before that code reaches `:core:*`. `checkpoint` refuses to open a step whose dependencies are unfinished.
2. **`checkpoint {id}`** — opens the step. This is what a refusal restores to; skipping it means a blocker found later cannot be undone.
3. **Run the rewrite passes**, one cluster at a time, in the order the plan lists them. Each cluster is a work order for an agent that **already exists** — migration adds none:

   | Cluster | Rules | Agent |
   |---|---|---|
   | `data` | R9, R11c | `data-layer` |
   | `state` | R3, R11a, R11b | `ui-layer` |
   | `designsystem` | R5, R13, S4 | `ui-layer` |
   | `strings` | R12, R12res | `ui-layer` |
   | `structure` | R7, S1, S2 | `ui-layer` |
   | `di` | R8 | `integrator` |
   | `integration` | I1–I4 | `integrator` |

   Data shape first: the UI state slots are typed by the DTOs it settles. Give the agent the cluster's `file:line` findings and its goal — not the whole feature and not a free hand.
4. **`verify {id}`** — for a `migrate` step this re-runs `kmpilot_check.py` for that feature; a step is finished at **zero** findings. For `relocate` it checks the module actually moved and `settings.gradle.kts` followed; for `hoist`/`extract` it re-reads the project and checks the code is no longer shared from where it was.
5. **`complete {id}`** — verifies again and refuses to record a step the checker still finds work in. `--force` exists for the cases the script cannot prove, and writes the sign-off down as forced: an unverified tick that reads as verified is worse than no tick.

Never mark progress by hand with `kmpilot_plan.py --mark` during a run. `checkpoint` / `complete` / `refuse` keep the checkpoints and the statuses consistent with each other; `--mark` knows nothing about git.

## Refusing mid-rewrite

Discovery classifies the refusals it can see by reading the repo. Some blockers only surface once a pass is inside the feature — a construct with no KMPilot equivalent, an Android API reached indirectly, a "feature" that turns out to be three.

```bash
python3 .claude/skills/_shared/kmpilot_migrate.py --root {repo} \
    refuse migrate-legacy --reason "custom DI graph with no Koin equivalent" \
    --evidence Wiring.kt:44
```

**`refuse` restores first, then records.** It is one command on purpose: a refusal recorded without the restore leaves exactly the half-rewritten feature the refusal exists to prevent. The restore is additive — the work in progress is committed, then reverted in a **new** commit, so a refusal taken by mistake is still recoverable from the branch rather than only from the reflog. Nothing is ever `reset --hard`.

A refusal is a **pass**: the run moves to the next step. It does not lapse the plan's confirmation. Refusing a `hoist`, `extract` or `relocate` blocks the steps that depend on it and the plan says so; refusing a feature blocks nothing, because features never depend on features. Withdraw one with `kmpilot_plan.py --unrefuse {id}`.

Full semantics: `@phase-2-plan.md`

## Resuming

A whole-project run spans sessions and survives a failed build in the middle. Re-invoking resumes; it never restarts, and it never re-migrates a feature already promoted.

```bash
python3 .claude/skills/_shared/kmpilot_migrate.py --root {repo} status
python3 .claude/skills/_shared/kmpilot_migrate.py --root {repo} next
```

**The clean phase never re-runs discovery.** It reads the ledger as written. Rewriting source changes what discovery would report — a relocated feature stops needing its `relocate` step — so a regeneration mid-run would produce a different step list and drop the plan back to `draft` on the run's own progress, lapsing the confirmation everything here leans on. **The plan the user approved is the plan that executes.** Regenerate with `kmpilot_plan.py` between runs, not during one.

## What this phase does not do

- **It does not compile anything.** Verification is static, and deliberately the same checker `/review-feature` and CI consume — deriving conformance a second way is how a migration and a CI run come to disagree. Run `./gradlew assembleDebug` and `./gradlew archTest` yourself before calling the migration finished.
- **It does not touch tests.** Pre-existing tests are out of scope; `/test-feature` afterwards is the user's call.
- **It does not write the report or promote to `managedFeatures`.** That is phase 4 — `@phase-4-integrate.md`. A `complete` here is a claim; promotion re-runs the checker before believing it.

## Verify the script itself

```bash
python3 scripts/kmpilot_migrate_test.py   # the gate, the restore, the order, resumption
bash scripts/migrate-matrix.sh clean      # the clean variants and their negative controls
```

Both are upstream-only (`scripts/` is stripped on install).
