# Phase 6 — Project migration

**Goal:** bring an **entire existing project** under the pipeline in one run — discover what is
there, plan where it goes, clean it into KMPilot shape, wire it up.

**Working names, decided 2026-08-04:** `/kmp-to-kmpilot` and `/android-to-kmpilot`. Deliberately
literal and deliberately temporary — they say exactly what goes in and what comes out while the
mechanics are being built. **Final naming is an open item** and must be settled before the Phase 3
plugin publishes, because the plugin fixes the names publicly. Note that `migrate-feature` is
already a *shipped string* — install.sh:655, install.sh:715 and ADOPTING.md:81 tell refused users
to go looking for it — so whatever the name becomes, those three places change with it.

**Branch:** `phase-6-kmp-to-kmpilot`, then `phase-6-android-to-kmpilot` — **two branches, two
PRs**. The second is not cut until the first has landed *and* been confirmed against real repos.
This bends the one-phase-one-PR convention on purpose: the stages share a design, a discovery pass
and a rewrite core, and splitting them across two phase files would duplicate that design in both.

> **Prerequisites:** [Phase 5](PHASE-5-adopt-hardening.md) merged. Migration rewrites source
> against `.kmpilot.json`, so detection has to be right about the repo before a translator is
> layered on it.

---

## ▶ Resume here (2026-08-06) — cold-start contract

Steps 1–4 are **done, verified, staged, not committed**. This block is written to be the only
thing a fresh session has to read to keep going. Start it with exactly:

```
read .claude/docs/_roadmap/PHASE-6-project-migration.md and continue at step 5
```

### 1. Where the work lives

Branch `phase-6-kmp-to-kmpilot`. **11 files staged, nothing committed — do not commit or push
unless the user says so.** `git status --short` should show:

| Staged path | What it is |
|---|---|
| `.claude/skills/_shared/kmpilot_discover.py` | step 2 — read-only inventory. Schema 2 |
| `.claude/skills/_shared/kmpilot_plan.py` | steps 3–4 — plan, confirmation gate, resume ledger |
| `.claude/skills/kmp-to-kmpilot/SKILL.md` | the skill: Preflight → Discover → Present → Plan → Confirm → STOP |
| `.claude/skills/kmp-to-kmpilot/phases/phase-1-discover.md` | how to read the inventory |
| `.claude/skills/kmp-to-kmpilot/phases/phase-2-plan.md` | steps, statuses, order, work list, the gate |
| `scripts/kmpilot_discover_test.py` | discovery self-test |
| `scripts/kmpilot_plan_test.py` | plan self-test (imports the discovery fixture) |
| `scripts/make-nonconforming-project.sh` | the Stage B fixture generator |
| `scripts/migrate-matrix.sh` | 37 variants, 12 negative controls |
| `.claude/docs/_roadmap/{PHASE-6-project-migration,README}.md` | this file + the status table |

**`.claude/skills` is generated.** Authored source is the gitignored `pipeline/src`; edit there,
run `python3 scripts/gen-surfaces.py`, stage only `.claude/`. See `.claude/rules/pipeline-source.md`.

### 2. Prove the baseline before changing anything

```bash
python3 scripts/kmpilot_discover_test.py        # PASS, ~1s
python3 scripts/kmpilot_plan_test.py            # PASS, ~2s
bash scripts/migrate-matrix.sh                  # 37 passed · 0 failed (~2 min, builds the fixture)
python3 scripts/gen-surfaces.py --check         # .claude/ and pipeline/dist match the source
python3 .claude/skills/_shared/kmpilot_check.py --all   # 0 errors 0 warnings
claude plugin validate ./pipeline/dist --strict         # passed
```

All six were green at the end of the step-3 session. **`gen-surfaces.py --check` only compares
git-*tracked* files** — a newly generated file reads as `missing` until it is `git add`ed. That is
the expected first surprise, not a bug.

### 3. What already exists, so it is not rebuilt

```
/kmp-to-kmpilot
  discover  →  kmpilot_discover.py   reads everything, writes NOTHING
  plan      →  kmpilot_plan.py       writes ONE file: .claude/docs/_project/migration-plan.json
  clean     →  NOT BUILT (steps 5-7)
  integrate →  NOT BUILT (step 8)
```

The ledger is the contract every later step consumes — do not invent a second progress file:

- **steps**: `hoist` · `extract` · `relocate` · `migrate` · `report`, stable ids
  (`hoist-core-network`, `migrate-search`, `report`).
- **statuses**: `pending` · `in-progress` · `done` · `refused` · `blocked` · `skipped`.
- **work list**: each `migrate` step carries rewrite passes clustered by rule and routed to
  `data-layer` / `ui-layer` / `integrator`, each with its `file:line` findings.
- **API for the rewrite phases**: `--status` (read, no discovery, no write), `next` (resume
  pointer), `--mark {id}={status} --note "…"`, `--set-tier {id}={tier}`, `--confirm`, `--dry-run`.

### 4. Invariants — the things most likely to erode under pressure

1. **Project scope, feature-at-a-time execution.** Step 3 must never become one sweeping pass.
2. **Nothing is written before the user confirms the plan.** Discovery writes nothing; the plan
   writes one file; the rewrite phases start only from a `confirmed` plan.
3. **A `migrate` step never depends on another `migrate` step** — the `extract` step is what
   removes the cross-feature edge. Feature-on-feature ordering manufactures wrong refusals.
4. **Derived facts beat the ledger.** `refused` / `blocked` / `done`-from-`managedFeatures` are
   re-derived every run and are not overridable from disk.
5. **A refusal is a pass**, and a refused step carries no work list.
6. **Rule findings come from `kmpilot_check.py`**, never re-derived by reading files.
7. **Every new classifier gets a negative control in the matrix.** The failure that costs a user's
   trust is a wrong refusal, not a missing one.

### 5. Step 5 — what is actually left

Discovery already classifies all three refusal kinds (Android-locked, no entry point, unhoistable
shared code) and the plan already turns each into a `refused` step. What is missing is the
**rewrite-time half**:

- a blocker that only shows up once a pass opens the feature → refuse *then*, and leave the feature
  exactly as it was found (the per-feature checkpoint commit from step 6 is the mechanism);
- the transition itself: `in-progress` → `refused` has no path today, because `--mark` deliberately
  rejects `refused` (it is a derived fact). Decide whether refusal-at-rewrite-time is a manifest
  fact discovery can re-derive, or the one status the ledger may set. **Do not just relax `--mark`
  without deciding that.**

Then step 6 (checkpoint branch + per-feature checkpoint commits), step 7 (the rewrite passes, one
cluster at a time, each delegating to the existing layer agent), step 8 (report + specs +
`managedFeatures` promotion), step 9 (run it against the real repos).

### 6. Test beds and review artifacts

`~/KMPProjects/`: `bookshelf` (features at repo root → `relocate` steps), `bookshelf-featuredir`
(12 checker findings, the fixture that motivated `managedFeatures`), `Kickoff26` (template →
refused, correctly), `nonconforming-target` (generated fixture). Dry-run reports for review sit in
`~/KMPProjects/kmpilot-discovery-reports/` (`*.txt` + `*.json`, discovery and plan per repo) —
regenerated output, delete freely. **All four repos must stay byte-identical after a read-only
run**; the matrix and both self-tests assert it, but check `git status` in them anyway.

### 7. Open review items — none blocks step 5

1. `Book` (bookshelf's `core/model`) proposes **`data.app`** because it is `@Serializable`;
   `common.app` is equally defensible since it is the domain type. Settled at plan time now:
   `--set-tier hoist-core-model=common`.
2. Whether either bookshelf inventory is missing anything the user knows is in those repos.
3. Whether the migration orders are ones the user agrees with.

**Applied 2026-08-06:** an unhoistable shared package gets no tier proposal
(`proposedTier: "blocked"`), and the plan does not list it as a decision the user must make —
proposing a destination for something refused is noise, and the Rule-14 fix that unblocks it
usually changes the tier anyway.

---

## Decisions locked 2026-08-05

Settled at Stage B kickoff, before any code was written.

| Decision | Choice | Why |
|---|---|---|
| **Command name** | Keep the working name `/kmp-to-kmpilot` for now | The published name is one rename commit, and it sweeps `install.sh:655`, `install.sh:715` and `ADOPTING.md:81` in the same pass. Naming the thing before the mechanics exist would fix a name to a shape that is still moving |
| **Shared-code hoisting** | **Full hoist.** Everything migrates to KMPilot; a host's own `core/*` modules are not preserved | Lands on machinery that already exists — `common.app` / `data.app` / `designsystem.app` — so it adds no new concepts. The blast radius is contained by the plan gate, not by narrowing the transform |
| **Pre-existing tests** | **Out of scope.** Migration migrates features; `/test-feature` afterwards is the user's call | Confirmed empirically: `bookshelf`, `bookshelf-featuredir` and `Kickoff26` have **zero** test source sets between them, so there was nothing for a port-vs-quarantine policy to act on. Amends the exit criterion below |
| **Increment** | Steps 1–2 (fixture + discovery) land and are verified before any rewrite code exists | The phase's own instruction. Everything in the rewriting half is built on what discovery reports; a misreading found at step 7 invalidates steps 3–6 |

### Landed 2026-08-05 — steps 1–2

Discovery is a **deterministic script**, `.claude/skills/_shared/kmpilot_discover.py`, not skill prose —
same rationale as the checker: a migration and a CI run must not disagree about what a project
contains. It decides mechanical facts (module graph, kinds, targets, catalogs, entry points,
Android-API evidence with file:line, topological order, cycles) and **proposes** the one judgment
call (which `:core:*` tier shared code belongs in) with the trigger that fired, for the plan phase
to confirm. Rule findings are not re-derived: it imports `kmpilot_check.py` and runs it in-process
at `--baseline` grading.

Two classifier traps were found and carved out by running against the real repos — both are
multiplatform, both are in KMPilot's own `commonMain`, and flagging either would have refused
**every feature in every real repo**:

- `androidx.navigation.*` (CMP navigation) — only `.fragment` / `.ui` are Android-locked;
- `androidx.lifecycle.ViewModel` / `viewModelScope` (the KMP lifecycle artifact).

An `android.*` import inside `androidMain` is likewise Rule 14 working as designed — recorded as
evidence with `expected: true`, never blocking. All three are pinned by negative-control variants
in `migrate-matrix.sh`, because the failure that costs a user's trust is a **wrong refusal**, not a
missing one.

Verified: `kmpilot_discover_test.py` (every classifier fires, traps silent, nothing written),
`migrate-matrix.sh` (27 variants, 11 of them negative controls, exit code = failing count), and
discovery run against the generated fixture plus `bookshelf-featuredir`, `bookshelf` and
`Kickoff26` with all four repos left byte-identical. Findings match the checker exactly (12 on
`bookshelf-featuredir`). `bookshelf`'s root-level features and the real `search → favorites`
cross-feature edge are both reported correctly.

### Landed 2026-08-06 — steps 3–4

The plan is a second deterministic script, `.claude/skills/_shared/kmpilot_plan.py`, consuming the
discovery report and writing **one** file: `.claude/docs/_project/migration-plan.json`. No source
file is touched in this phase. It is simultaneously the reviewable plan and the resume ledger,
because two artifacts that can disagree about what is done is the same mistake as two checkers.

Design calls made while building it, each with the failure it avoids:

| Call | Why |
|---|---|
| **Every unit of work is a step with a stable id** (`hoist-core-network`, `migrate-search`) | ids carry progress across sessions and are what the user types to overrule a tier. Regeneration must land on the same ids or resume is fiction |
| **A `migrate` step never depends on another `migrate` step** | the cross-feature edge is what the `extract` step removes. Ordering feature-on-feature would let one refused feature block every feature that imports it today — a **wrong refusal**, the failure that costs trust |
| **Derived facts beat remembered ones** | `refused` / `blocked` / a `done` that came from `managedFeatures` are re-derived every run and cannot be overridden from the ledger. `in-progress` / `done` / `skipped` are preserved. That is what stops a stale entry half-migrating a refused feature, or re-migrating a promoted one |
| **A refused step carries no rewrite passes** | a work list on a refused feature reads as a job somebody is meant to work through. A refusal is a pass, not a smaller job |
| **Confirmation lapses when the step list changes** | otherwise a confirmed plan quietly grows work the user never saw — precisely what the gate exists to prevent. Progress survives the lapse; only the approval does not |
| **An unhoistable package is asked no tier question** | already applied to discovery on 2026-08-06 (`proposedTier: "blocked"`); the plan does not resurrect the question in its *needs a decision* list |

Step 4 turned out to be half-built already: discovery gained `findingRows` (schema 2 — the
checker's own rows with `file:line`, not just per-rule counts) and the plan clusters them into
**rewrite passes** routed to the agents that already exist — `data-layer`, `ui-layer`,
`integrator`. Every finding lands in exactly one pass; a rule nobody has routed surfaces as an
`other` cluster rather than vanishing.

Verified: `kmpilot_plan_test.py` (every step kind and status fires; the gate holds; the ledger
resumes; only the plan file is written), `migrate-matrix.sh` now 37 variants / 12 negative
controls, and dry-runs against `bookshelf` (9 steps, both root-level features get a `relocate`
before their `migrate`), `bookshelf-featuredir` (6 steps) and `Kickoff26` (refused — template, no
plan written), all three repos left untouched.

---

## Project-scoped, not feature-scoped

**Reversed 2026-08-04.** The first draft of this phase, and the `migrate-feature` entry it came
from, took **one feature package** as input; `PARKED.md` additionally rejected a full-app migrator
outright. That is no longer the design: *"the migration must contain the whole project, not feature
by feature — the skill is responsible for gathering data about the project and the features and
also cleaning them based on KMPilot. This is the whole point."*

**What the reversal does not throw away.** The original objection — an agent meeting ten years of
legacy in a single unbounded transformation produces an impressive demo and a product that shatters
on contact — is answered by **staging**, not by narrowing the input. The *command unit* is the
project; the **rewrite unit stays one feature at a time**, in a planned order, each independently
verifiable. Step 3 below must never become a whole-app rewrite in one pass. That is the line, and
it is the thing in this phase most likely to erode under pressure.

**What the reversal buys** is the question per-feature migration structurally could not answer:
**where shared code goes**. An app's `network/`, `di/`, `base/`, `utils/` and its own design system
have to be mapped onto `:core:common` / `:core:data` / `:core:designsystem` *before* any feature
that depends on them can be cleaned. Feature-at-a-time had no vantage point from which to decide
that, and no way to order the work.

---

## The four steps

Both commands run the same pipeline; they differ only in whether step 3 also translates platform
APIs.

### 1. Discover

Inventory the whole project, writing nothing:

- every feature, and the source layout it currently uses;
- shared / cross-cutting code — networking, DI, base classes, utils, their design system, their
  `Result`/`Either` equivalent;
- the dependency graph between all of the above;
- per feature: Android-locked, portable-but-non-conforming, or already conforming;
- what the existing tests cover, since that is the only evidence a rewrite preserved behaviour.

### 2. Plan

Turn the inventory into a written, reviewable plan **before touching anything**:

- which packages become `feature/{name}/` modules;
- which shared code lands in `:core:common` / `:core:data` / `:core:designsystem`, and what stays
  put because it is genuinely app-specific;
- the migration **order** — dependencies first; nothing that consumes shared code moves before that
  code reaches core;
- what is refused, with reasons, up front rather than discovered at file 40.

The user confirms the plan. Same shape as adopt mode's compatibility report, and for the same
reason: a large diff the user did not agree to in advance is not acceptable.

**Landed** as `kmpilot_plan.py` → `.claude/docs/_project/migration-plan.json`: one step per unit of
work (`hoist` · `extract` · `relocate` · `migrate` · `report`), one status per step (`pending` ·
`in-progress` · `done` · `refused` · `blocked` · `skipped`), and the same file doubles as the
resume ledger. Confirmation records the exact step list approved and lapses if that list changes.

### 3. Clean

Rewrite, **one feature at a time, in the planned order**, driving each to zero checker findings.
`kmpilot_check.py` already reports per feature which of the 19 checks fail and where; under
`managedFeatures` a pre-existing feature is graded at `--baseline` — every violation reported in
full, as a warning, never failing the build. That report **is** the work list per feature, and a
feature is done when it reaches zero and can be promoted to strict grading.

```
/kmp-to-kmpilot
  discover → 7 features, 4 shared packages
  plan     → core: network + result;  order: core → auth → profile → …
  clean    → auth       12 findings → 0   → managedFeatures += auth
             profile      9 findings → 0   → managedFeatures += profile
             legacyweb   REFUSED (WebView + Android Service)
  integrate→ 4 integration points, specs, report
```

Same contract `/review-feature` uses — consume the report rather than re-deriving it — so a
migration and a CI run cannot disagree about whether the result conforms.

**Rewrite passes** per feature, each routed to the agent that already writes that layer
(`data-layer`, `ui-layer`, `platform`, `integrator`) — no new agents:

| Cluster | Typical inbound shape | Target |
|---|---|---|
| R1, R2, R9 | repository throwing, or a UseCase layer | interface+impl pairs, `Either<T>`, ViewModel → repository directly |
| R3, R4, R11 | `_state.value =`, several state classes, presentation mirror types | `setState { copy() }`, one `*UiModel`, `UiState<DTO>` slots |
| R5, R13 | Material3 imports, nested `Scaffold` | `X*` components, `XScreen(topBar, bottomBar)` |
| R12 | English literals in composables | `composeResources/values/strings.xml` + `UiText` on the UiModel |
| R7, R8, structure | hyphenated/camelCase packages, no `di/`, everything in one `Screen.kt` | lowercase package, `{featurename}Module`, `components/` one file per composable |

### 4. Integrate

The 4 integration points, `managedFeatures` entries, a spec per feature via the existing
`/audit-spec` generation path, and `MIGRATION-REPORT.md` — what changed per rule, what was refused
and why, and which features had no tests and therefore carry the most behavioural risk.

---

## Resumable state

A whole-project run is long and **will** span sessions, interruptions and a failed build in the
middle. It keeps its plan and progress on disk — which feature is done, which is next, what was
refused — in the spirit of `stitch-project.json`'s `blueprintConsumed` flag. Re-invoking resumes;
it does not restart, and it never re-migrates a feature already promoted to `managedFeatures`.

**Landed 2026-08-06** as `.claude/docs/_project/migration-plan.json`, written by
`kmpilot_plan.py`. The plan *is* the ledger — a separate progress file could disagree with the plan
it tracks. `--status` prints it without running discovery; `next` is the resume pointer;
`--mark {step}={status}` is how the rewrite phases record progress.

## Dirty trees are supported, not refused

Migration **never refuses a dirty working tree**. Before touching anything it cuts its own branch
and commits the working tree as a checkpoint:

```
git switch -c kmpilot/migrate-{project}
git commit -am "checkpoint before migration"
```

Undo is `git switch -`, with the user's uncommitted work preserved inside the checkpoint commit.
Adopt mode's whole-repo clean-tree refusal is **not** carried over: adopt only ever adds files, so
a bad run is `rm -rf` of known paths, while migration rewrites working source and needs a real
undo — but the undo does not have to be *"refuse until you clean up"*. Per-feature checkpoint
commits during step 3 also keep the migration bisectable.

---

## Stage B — `/kmp-to-kmpilot`

**Input:** an adopted KMP project whose features do not follow KMPilot's rules.
**Output:** every migratable feature in KMPilot shape and in `managedFeatures`, shared code in
core, the rest refused with reasons.

Ships and is confirmed first. It needs no Android translation, and it is testable **today**:
`bookshelf-featuredir` produced 10 genuine `archTest` errors (6× R5 Material3, 2× R8 missing `di/`,
2× R11b missing UiModel) and is the fixture that motivated `managedFeatures`.

**Refusals** — a refusal is a pass. Stage B reports and moves to the next feature rather than
half-rewriting when it finds Android-only APIs (`Context`, `LiveData`, Hilt/Dagger, Retrofit, the
Room Android artifact, XML layouts, Fragments, `androidx.navigation`), or a feature with no
identifiable screen entry point. **A cross-feature dependency is no longer an automatic refusal** —
at project scope that is precisely what step 2 exists to resolve, by hoisting the shared code into
core. It stays a refusal only when the shared code cannot be hoisted.

## Stage C — `/android-to-kmpilot`

Gated on Stage B landing and being confirmed. Adds platform translation to step 3. Decisions
already locked:

- **Jetpack Compose only.** XML layouts and Fragments are refused with a reason, not attempted.
- **Generate + report only.** The original Android sources are left in place; KMPilot does not
  rewire their app module or delete their code.

**The two commands did not collapse into one** even at project scope, because the discriminator —
*is this repo already KMP?* — is a property of the **project**, not of a feature. That is also what
keeps the staging honest: Stage B is a complete, shippable product for KMP projects on its own.

### Translation table

| Android | Multiplatform target |
|---|---|
| Retrofit + OkHttp | Ktor `Resources` + `ApiClient` (`:core:data`) |
| Hilt / Dagger | Koin `{featurename}Module` (R8) |
| `LiveData` / RxJava | `StateFlow` + `setState` (R3) |
| `androidx.lifecycle.ViewModel` | the KMP `ViewModel` the pipeline already targets |
| Android resources | `composeResources/values/strings.xml` (R12) |
| Navigation Component | CMP nav + callback params (R10) |
| `Context` and friends | a `commonMain` DataSource with per-platform actuals (R14), or refuse |
| Room (Android artifact) | **open** — Room-KMP vs refuse, decided at Stage C kickoff |

### Open decision — repo topology

`install.sh --adopt` refuses non-KMP repos, and a pure Android app is exactly that. So the audience
Stage C exists for cannot install the prerequisite. Four candidate answers, recorded so the
reasoning is not re-derived; **decided at Stage C kickoff, not now**:

1. **User bootstraps.** Stage C requires an already-adopted repo and refuses otherwise with an
   actionable message. Zero installer work; excludes pure-Android repos.
2. **Adopt learns Android-only.** `install.sh --adopt` bootstraps KMP into a pure-Android repo.
   Full headline, and a second installer mode as large as Phase 2.
3. **Cross-repo.** `--from <path>` reads an Android project from a different checkout and writes
   into the current KMP repo. Their Android build is never opened for writing.
4. **Stage C owns the bootstrap.** Adopt stays KMP-only; the migration command handles the
   Android-only path itself.

---

## Out of scope

- **A single-pass whole-app rewrite.** Project *scope*, feature-at-a-time *execution*. See above.
- **XML → Compose.** Refused in Stage C. Revisit behind an experimental flag only if refusals
  generate real demand — the same unpark-trigger pattern as Groovy DSL.
- **Cutover.** No rewiring of the Android app, no deletion of the original sources.
- **The capability-map model.** Migration targets `Either` / `UiState` / `X*` as vendored.

---

## Files touched

| Path | Change | `update.sh` tier |
|---|---|---|
| `pipeline/src/skills/kmp-to-kmpilot/**` | **new** skill (Stage B) → generates `.claude/skills/` | OVERRIDE |
| `pipeline/src/skills/android-to-kmpilot/**` | **new** skill (Stage C) | OVERRIDE |
| `pipeline/src/skills/_shared/kmpilot_discover.py` | ✅ **new** — the discovery pass (step 2); schema 2 adds `findingRows` (step 4) | OVERRIDE |
| `pipeline/src/skills/_shared/kmpilot_plan.py` | ✅ **new** — plan generation, confirmation and the resume ledger (steps 3–4) | OVERRIDE |
| `scripts/kmpilot_discover_test.py` | ✅ **new** — discovery self-test | stripped on install |
| `scripts/kmpilot_plan_test.py` | ✅ **new** — plan self-test | stripped on install |
| `.claude/skills/_shared/kmpilot_check.py` | `managedFeatures` append helper. *Per-feature machine-readable work list turned out to exist already: the report carries `feature`/`rule`/`file`/`line`/`severity` + `preExistingFeatures`, and discovery consumes it in-process — no checker change needed* | OVERRIDE |
| `.claude/skills/_shared/patterns.md` | migration entry alongside create/modify | OVERRIDE |
| `CLAUDE.md` | mandatory-skill table gains both commands | TIER1 (merged) |
| `install.sh` | `migrate-feature` → final name in both refusal messages (:655, :715) | not delivered |
| `ADOPTING.md` | same rename (:81); compatibility note | stripped on install |
| `scripts/make-nonconforming-project.sh` | **new** — Stage B fixture: several features + shared code | stripped on install |
| `scripts/make-android-target.sh` | **new** — Stage C Android fixture (Compose + Hilt + Retrofit) | stripped on install |
| `scripts/migrate-matrix.sh` | ✅ **new** — variant matrix, refusal + plan quality under test (37 variants) | stripped on install |
| `.claude/docs/_roadmap/PARKED.md` | migrate entry resolved; full-app rejection recorded as reversed | not delivered |

Skills are OVERRIDE tier, so both commands reach every existing install on `./update.sh` with no
conflicts. **Authored source is `pipeline/src`** — regenerate with `scripts/gen-surfaces.py` and
commit only `.claude/`.

---

## Steps

**Stage B**

1. ✅ **Done 2026-08-05.** Fixture generator for a non-conforming *project* — several features plus
   shared networking, DI and utils — and `migrate-matrix.sh` with the refusal variants. Refusals get
   tested before rewrites do. *(`make-nonconforming-project.sh` delegates the buildable baseline to
   `make-adopt-target.sh`, then adopts in place via `KMPILOT_SOURCE_DIR` — offline, no release tag.
   The matrix asserts discovery + refusal classification only; the rewrite outcomes join it at step
   7.)*
2. ✅ **Done 2026-08-05.** **Discovery** pass: feature inventory, shared-code inventory, dependency
   graph. Writes nothing. Land this alone and check its output against the real repos before
   building anything on it. *(Verified against all four repos — see "Landed" above.)*
3. ✅ **Done 2026-08-06.** **Plan** generation, user confirmation, and the on-disk plan/progress
   artifact. *(`kmpilot_plan.py` → `.claude/docs/_project/migration-plan.json`; writes no source.)*
4. ✅ **Done 2026-08-06.** Machine-readable per-feature work list out of `kmpilot_check.py`.
   *(Discovery schema 2 carries `findingRows` — the checker's own rows with `file:line` — and the
   plan clusters them into rewrite passes routed to the existing layer agents.)*
5. Refusal detection (Android APIs, no screen entry point, unhoistable shared code). ← **next**
   *(Discovery + plan already classify all three; what remains is refusing **mid-rewrite** without
   leaving a feature half-migrated.)*
6. Checkpoint branch + per-feature checkpoint commits.
7. Rewrite passes, one rule cluster at a time, each delegating to the existing layer agent.
8. `MIGRATION-REPORT.md`, spec generation, `managedFeatures` promotion, resume support.
9. Run against `bookshelf-featuredir`, `bookshelf`, `Kickoff26`, and the projects you build by
   hand. Transcript of each goes in the PR.

**Stage C** — only after Stage B is confirmed

10. Settle the topology decision above.
11. Android fixture generator + Android variants in the matrix.
12. Translation passes per the table; every unmapped API is a refusal, never a guess.
13. Run against the scripted fixture, an open-source Android app (publishable transcript), and one
    of `~/AndroidStudioProjects` (private, informs the work only).

---

## Exit criteria

**Stage B**

- [ ] Discovery reports every feature and shared package in the real repos, with a dependency order
      a human agrees with — verified **before** any rewrite code exists.
- [ ] The plan is written, shown, and confirmed before a single file is written. *(Machinery
      landed 2026-08-06 and is under test — plan, confirmation gate, lapse-on-change, resume
      ledger. Ticked once a real project has been migrated through it.)*
- [ ] A whole real project migrates: every migratable feature at **0 checker findings** and in
      `managedFeatures`, shared code in core, refusals listed with reasons.
- [ ] Compiles for android + ios + desktop in the target repo; strict `archTest` green.
- [ ] ~~Pre-existing tests either still pass, or the report names each one that broke and why.~~
      **Amended 2026-08-05 — tests are out of scope** (see Decisions above). Instead:
      `MIGRATION-REPORT.md` names any test source set referencing types the rewrite replaced, so
      the user can regenerate with `/test-feature`. No real test repo had a single test source set,
      so there was nothing this criterion could have been verified against.
- [ ] Runs on a **dirty** repo: checkpoint branch cut, uncommitted work recoverable, `git switch -`
      restores the pre-migration state exactly.
- [ ] Interrupt mid-run and re-invoke: resumes at the next unmigrated feature, re-migrates nothing.
- [ ] Confirmed on `bookshelf-featuredir`, `bookshelf`, `Kickoff26`, plus hand-built projects.
- [ ] `migrate-matrix.sh` green, every variant proven able to fail.
- [ ] Phase 3 unpark decision recorded in `PARKED.md` and the README status table.

**Stage C**

- [ ] Topology decision recorded here before any code is written.
- [ ] An Android Compose project becomes a KMPilot project that compiles on all three targets and
      passes strict `archTest`.
- [ ] The original Android sources are **byte-identical** after the run.
- [ ] XML/Fragment input refused with a reason; every unmapped Android API refused, never guessed.
- [ ] Publishable transcript against an open-source Android app.
- [ ] `migrate-feature` no longer appears in install.sh or ADOPTING.md.

---

## Verification

```bash
# ── steps 1-4, landed: discovery writes nothing; the plan writes one file ───
python3 scripts/kmpilot_discover_test.py                   # every classifier fires, ~1s
python3 scripts/kmpilot_plan_test.py                       # every step kind, the gate, the ledger
bash scripts/migrate-matrix.sh                             # 37 variants, 12 negative controls
scripts/make-nonconforming-project.sh --force              # regenerate the fixture (offline)

python3 .claude/skills/_shared/kmpilot_discover.py --root ~/KMPProjects/bookshelf-featuredir
python3 .claude/skills/_shared/kmpilot_plan.py --root ~/KMPProjects/bookshelf-featuredir --dry-run
git -C ~/KMPProjects/bookshelf-featuredir status --short   # must be unchanged

python3 scripts/gen-surfaces.py --check                    # .claude/ matches pipeline/src
python3 .claude/skills/_shared/kmpilot_check.py --all       # KMPilot itself still strict-green

# ── steps 5-9, not yet: inside an adopted repo, a dirty tree is fine ────────
#   /kmp-to-kmpilot
python3 .claude/skills/_shared/kmpilot_plan.py --root . --status   # resume pointer
python3 .claude/skills/_shared/kmpilot_check.py --all      # strict, expect 0 errors
./gradlew assembleDebug
./gradlew archTest
git switch -                                               # the undo must be this cheap
```

---

## Risks

- **Whole-project scope is where this fails if it fails.** The mitigation is structural, not
  hopeful: discovery writes nothing, the plan is confirmed before any write, features are rewritten
  one at a time in dependency order, each is verified by the checker, and each gets its own
  checkpoint commit. If step 3 ever becomes a single sweeping pass, that protection is gone.
- **A rewrite that compiles but changes behaviour is the worst possible failure** — it is silent.
  Hence pre-existing tests passing as an exit criterion, and untested features flagged as
  higher-risk in `MIGRATION-REPORT.md`.
- **Shared-code placement is a judgment call at scale.** Getting `:core:*` wrong misplaces code for
  every feature at once. The main reason the plan is confirmed by a human first.
- **A long run invites a mid-run failure.** Resumability is an exit criterion, not a nicety.
- **Working names leaking into published material.** They are literal and unlovely on purpose;
  settle the final names before the plugin publishes, not after.

---

## Downstream delivery

Both skills land in `.claude/skills` (OVERRIDE) and auto-deliver on `./update.sh`. `CLAUDE.md` is
TIER1 and merges. `.kmpilot.json` gains no new field — `managedFeatures` already exists and is only
appended to, so the release back-compat contract holds.
