# Parked

Deliberately unscheduled. Recorded so the reasoning and the numbers do not have to be re-derived. Nothing here is rejected outright unless marked ❌.

---

## ⏸ Drift benchmark (and all budget/cost work)

**Parked 2026-07-30 by explicit decision: do not plan, cost, or start this until reopened.**

**What it was.** A published, reproducible measurement of architecture drift: two arms in the *same* repo with the *same* `core/` modules — skills+hooks enabled vs. removed. Comparing against raw Claude Code in an empty repo would be rigged and reviewers would say so. N features × K sequential modification steps, measuring rule violations per feature after each step, compile success, test pass rate, tokens, wall-clock, cost. Commit the raw CSV plus the harness.

Depends entirely on **Phase 1** — the checker's JSON output *is* the dataset.

**Purpose.** Turn the drift thesis from an opinion into a citable number, and feed Article 3 plus conference CFPs. Stars decay; a benchmark other people reference does not.

**Cost model** (2026-07-30 pricing: Opus 5 $5/$25 per MTok, cache write $6.25, cache read $0.50; Sonnet 5 $3/$15). Anchored on the measured `/design-ui` baseline of **10.5–14.7M cache reads per session**. One Opus 5 `/create-feature` run ≈ **$12** ($5 cache reads + $1.88 cache writes + $5 output). A `/modify-feature` step ≈ half that.

| Scale | Config | Estimate |
|---|---|---|
| Pilot | 1 feature, both arms, 1 step | ~$30 |
| Minimum credible | 3 features × 3 steps × 1 seed | ~$180 |
| Recommended | 4 features × 4 steps × 2 seeds | ~$600 |
| Full | 6 features × 5 steps × 3 seeds | ~$1,500 |

Plan was: spend $30 on a pilot, read the real `usage` fields, then price the matrix off measurements rather than estimates.

**Three cost facts worth keeping:**
- **Prompt caching is doing a ~10× lift, and a careless harness destroys it.** Injecting a run ID, timestamp, or seed *into the prompt prefix* invalidates the cached skill prefix and multiplies the bill ~10× ($600 → $6,000). Keep skill files byte-identical across runs; put per-run variation after the last cache breakpoint.
- **The Batch API's 50% discount does not apply** — agentic loops need tool-use round trips.
- **Running it on the Claude Code subscription costs ~$0 cash but is not reproducible by others.** Develop the harness on the subscription; do the *recorded* runs on the API so token counts and cost land in the writeup.

**Downstream of this, also parked:** Article 3 (drift-measurement content, in-pool distribution only) and the conference/podcast CFP push.

---

## ➡️ `migrate-feature` — Android → KMP — **unparked 2026-08-04**

Scheduled as [Phase 6](PHASE-6-project-migration.md), split into two stages: `/kmp-to-kmpilot`
(shape only, no Android) then `/android-to-kmpilot` (platform translation). Gated on
[Phase 5](PHASE-5-adopt-hardening.md). Stage B landing unparks the Phase 3 plugin.

**The bounded input below was reversed the same day** — migration is **project-scoped**, not one
feature package. The reasoning that produced the bound is still correct and still governs the
phase; it is now honoured by *staging* rather than by narrow input. The command takes a whole
project, and the **rewrite unit stays one feature at a time**, in a dependency order the run plans
and a human confirms. See the phase file → *Project-scoped, not feature-scoped*. The original entry
is kept below for the rest of its reasoning.



The largest reach idea available, and the loudest headline: *"port your Android app to iOS one feature at a time."* Android developers outnumber KMP developers by 10–50×, and the Kotlin/Google community actively amplifies KMP-adoption content, so it is in-pool by construction.

**Shape:** input is one Android feature package (Activity/Fragment, ViewModel, Retrofit service, Room DAO). Output is `feature/{name}/` as a KMP module in KMPilot shape, plus an honest report of what could not move and why. Acceptance = compiles for android + ios + desktop and passes `archTest`. It **refuses** features whose dependency surface is too Android-locked rather than emitting a broken module.

**Why bounded input is the whole point:** a full-app migrator meets ten years of legacy it cannot reason about, produces an impressive demo and a product that shatters on contact, and that reputation does not come back.

**Blocked on Phase 2** — migrate-mode is adopt-mode plus a translation step; both need "operate inside a repo I did not create."

---

## ⏸ Groovy DSL support in adopt mode

`install.sh --adopt` requires `settings.gradle.kts`. A project on the Groovy DSL is refused, cleanly and with an explanation.

**Why it is genuinely tractable** (this is not a hard limit): every edit adopt mode makes to the target is **append-only** — `include(":core:…")`, a `versionCatalogs` block, three `implementation(project(…))` lines, and the `archTest` task — and each has a direct Groovy equivalent. Gradle also allows mixed DSL across projects, so the vendored `core/*` build files stay `.kts` untouched. Estimated ~1 day, mostly a second set of append templates plus matrix variants.

**Why it is parked:** KMP projects skew modern — Kotlin DSL is the default for anything scaffolded in the last several years, so a KMP team on Groovy settings is a minority of a minority. Building it now would repeat the mistake the vendor-vs-capability-map decision already avoided: shipping for an imagined adopter.

**Unpark trigger:** one real adopter reports being refused for Groovy DSL. The refusal message says "not supported **yet**" and points at the issue tracker precisely so that signal gets collected instead of lost — a parked item with no way to hear demand is just a silent no.

**Covered by:** the `groovy-dsl` variant in `scripts/adopt-matrix.sh` asserts the refusal stays clean and explanatory.

---

## ⏸ Maven Central publishing of `core/*`

Publish `kmpilot-core-common`, `-core-data`, `-core-designsystem` so adoption becomes three dependencies instead of three vendored modules.

**Unlocks:** `terrakok/kmp-awesome`, whose stated bar is Maven Central + ~50 stars + a third-party project using it (not the author's own sample). Also gives non-AI adopters a reason to show up.

**Deferred until Phase 2 has real users.** Publishing carries ongoing burden — versioning, binary compatibility, a release process — and it still imposes the KMPilot design system on adopters, which is the same objection vendoring has. Do it when someone asks.

---

## ⏸ Nightly golden-pipeline CI

A scheduled job runs `claude -p` headless against a fixture prompt and asserts the output compiles, passes `archTest`, and reviews clean. *"The pipeline is regenerated and verified nightly"* is a claim nobody else in this space can make, and it catches skill regressions before users do.

Needs Phase 1. Also needs an API budget, which is parked with the benchmark.

---

## ⏸ Issue → PR GitHub Action

Label a GitHub issue `feature`, CI opens a PR containing the module, spec, and tests. One GIF sells it. Run it live in `Loupe` and `Kickoff26` so it is demonstrably real rather than a concept. Same API-budget dependency.

---

## ⏸ Downstream architecture-health badge

`/health-report` emits a shields badge (`KMPilot 14/14 ✓`). Every project that adopts KMPilot then advertises KMPilot in its own README — a distribution surface that compounds with Phase 2. Needs Phase 1's JSON report.

---

## ⏸ Tier-3 hygiene (each ≤1 day; good `good-first-issue` candidates)

| Item | Note |
|---|---|
| Tests for the other example features | Only `feature/dashboard` has tests (7 files); the README sells "tests built in" |
| `AGENTS.md` shim → `patterns.md` | Free search surface for Cursor / Codex users |
| Gallery of generated output | Lets someone judge the value without installing |
| `?ref=` on the install curl URL | Release-asset downloads are currently the only adoption signal |
| `update.sh` has **0 downloads** across all four releases | The upgrade path has never been exercised by anyone. Investigate whether the migration note is reaching users |

---

## ❌ Rejected outright

| Idea | Why |
|---|---|
| **Screenshot → design input** | `/verify-ui` audits code ↔ HTML. HTML inferred from a PNG makes that audit circular and quietly falsifies the "verified against the design" claim — it would undermine the feature that makes KMPilot worth using. `--from-html` (Phase 4) is the correct abstraction |
| **Stack-agnostic core extraction** | Makes KMPilot the 500th generic AI-coding pipeline, and the measured data says general audiences do not convert for this project (HN: largest reach of the launch, +0 stars). Generalize in the writing, not the code |
| **Android-only / Compose-only variant** | Splits maintenance, dilutes the name, adds no story that Phase 2 + migrate-feature do not already tell |
| ~~**Full-app Android→KMP migrator**~~ **— reversed 2026-08-04** | Was: unbounded legacy is where coding agents fail hardest. Now scheduled as [Phase 6](PHASE-6-project-migration.md), which takes the **whole project** as input. The objection is answered structurally instead of by scope: discovery writes nothing, a human confirms the plan before any write, and features are rewritten one at a time in dependency order. A single-pass whole-app rewrite stays rejected |
| **Re-running the Article-2 channel mix** | Both converting pools (Kotlin/KMP, Claude Code) were touched once each and returned +10 total. HN and LinkedIn returned +0 between them. Reddit is gated. dev.to and Hashnode were cut on measured data |
