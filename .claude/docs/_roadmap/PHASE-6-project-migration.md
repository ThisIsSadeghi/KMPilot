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
| `.claude/skills/_shared/kmpilot_check.py` | per-feature machine-readable work list; `managedFeatures` append helper | OVERRIDE |
| `.claude/skills/_shared/patterns.md` | migration entry alongside create/modify | OVERRIDE |
| `CLAUDE.md` | mandatory-skill table gains both commands | TIER1 (merged) |
| `install.sh` | `migrate-feature` → final name in both refusal messages (:655, :715) | not delivered |
| `ADOPTING.md` | same rename (:81); compatibility note | stripped on install |
| `scripts/make-nonconforming-project.sh` | **new** — Stage B fixture: several features + shared code | stripped on install |
| `scripts/make-android-target.sh` | **new** — Stage C Android fixture (Compose + Hilt + Retrofit) | stripped on install |
| `scripts/migrate-matrix.sh` | **new** — variant matrix, refusal quality under test | stripped on install |
| `.claude/docs/_roadmap/PARKED.md` | migrate entry resolved; full-app rejection recorded as reversed | not delivered |

Skills are OVERRIDE tier, so both commands reach every existing install on `./update.sh` with no
conflicts. **Authored source is `pipeline/src`** — regenerate with `scripts/gen-surfaces.py` and
commit only `.claude/`.

---

## Steps

**Stage B**

1. Fixture generator for a non-conforming *project* — several features plus shared networking, DI
   and utils — and `migrate-matrix.sh` with the refusal variants. Refusals get tested before
   rewrites do.
2. **Discovery** pass: feature inventory, shared-code inventory, dependency graph. Writes nothing.
   Land this alone and check its output against the real repos before building anything on it.
3. **Plan** generation, user confirmation, and the on-disk plan/progress artifact.
4. Machine-readable per-feature work list out of `kmpilot_check.py`.
5. Refusal detection (Android APIs, no screen entry point, unhoistable shared code).
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
- [ ] The plan is written, shown, and confirmed before a single file is written.
- [ ] A whole real project migrates: every migratable feature at **0 checker findings** and in
      `managedFeatures`, shared code in core, refusals listed with reasons.
- [ ] Compiles for android + ios + desktop in the target repo; strict `archTest` green.
- [ ] Pre-existing tests either still pass, or the report names each one that broke and why.
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
# Stage B, inside an adopted repo — a dirty tree is fine
#   /kmp-to-kmpilot
python3 .claude/skills/_shared/kmpilot_check.py --all      # strict, expect 0 errors
./gradlew assembleDebug
./gradlew archTest
git switch -                                               # the undo must be this cheap

bash scripts/migrate-matrix.sh          # refusal quality, seconds
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
