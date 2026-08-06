# Phase 1 — Discover

Inventory the whole project, **writing nothing**. This phase answers three questions the rewriting phases are entirely built on: what is in this repo, where would each piece go, and in what order.

Because everything downstream depends on it, discovery is a deterministic script rather than model prose — the same reason `kmpilot_check.py` is. A migration and a CI run must not disagree about what a project contains.

## Run it

```bash
python3 .claude/skills/_shared/kmpilot_discover.py --root {repo}
```

| Flag | Use |
|---|---|
| `--root PATH` | the repo to inventory (default: cwd) |
| `--report PATH` | also write the JSON report there. **Omitted by default on purpose** — discovery writes nothing unless asked, and never inside the target |
| `--json-only` | print the JSON report to stdout |
| `--compact` | one greppable line per row (automatic when piped) |

Exit code is always 0 for a readable repo. **A refusal is a finding, not an error** — only an unusable target (no `settings.gradle.kts`) exits non-zero.

## Read the report, do not re-derive it

Rule findings come from `.claude/skills/_shared/kmpilot_check.py`, imported and run in-process at `--baseline` grading. Do not grep for Material3 imports or missing `di/` packages yourself — that is how two answers to the same question come into existence.

One scope limit is worth stating to the user when it applies: **the checker only grades `feature/*`.** A project keeping features at the repo root gets them *found* by discovery, reported with `location: root` and a `feature-outside-featuredir` note, and carrying **no** rule findings until they move. That is a fact about the plan, not a gap.

## What the script decides vs what it proposes

**Decides** (mechanical, checkable against the files): the module graph, module kinds, source layout, targets, catalog usage, entry points, Android-API evidence with file:line, topological order, cycles.

**Proposes** (judgment, for the user to confirm in the plan phase): which `:core:*` tier each shared package belongs in.

Phase 6's central safety property is that a human confirms the plan before anything is written. Present proposals **as proposals, with their evidence**, and invite correction. A script that quietly made these calls would absorb that guarantee.

## Report shape

| Key | What it holds |
|---|---|
| `project` | name, package prefix, `role`, `migrationTarget`, app module, catalogs, `managedFeatures` |
| `modules[]` | `gradlePath`, `kind`, targets, source sets, catalogs, project deps, notable deps |
| `features[]` | verdict, `location`, `entryPoint`, `androidEvidence[]`, `findings{}` (counts) + `findingRows[]` (the checker's own rows with `file:line`), `consumes[]`, `consumedBy[]` |
| `shared[]` | `proposedTier` + `proposedTarget`, `reason`, `consumers[]`, `hoistable`, `blockers[]`, `filesByTier{}` |
| `inFeatureShared[]` | shared code living **inside** a feature: owner, consumer, symbols, `declaredIn[]`, proposal |
| `graph` | `gradleEdges`, `sourceEdges`, `order[]`, `cycles[]` |
| `refusals[]` | `subject`, `kind`, `reason`, `evidence[]`, and for shared code the features it `blocks[]` |
| `notes[]` | everything that is not a refusal but changes the plan |
| `summary` | counts: features, migratable, conforming, refused, shared, hoistable, findings, notes |

### Module kinds

| Kind | Meaning |
|---|---|
| `app` | the app module named by `.kmpilot.json` (`appModule`) |
| `app-android` | the AGP *application* module — the Android launcher, whatever it is called |
| `core-kmpilot` | `:core:{common,data,designsystem}` that adopt vendored — already home |
| `core-host` | the project's **own** `core/*` module — needs a tier |
| `feature` | under `feature/`, **or** any module with a top-level `@Composable` (a root-level feature) |
| `shared` | other non-feature code with sources — needs a tier |

### Feature verdicts

| Verdict | Meaning |
|---|---|
| `conforming` | in `managedFeatures`, or zero findings under `feature/` — nothing to do |
| `portable` | migratable: findings to clear, no Android blocker |
| `android-locked` | Android-only APIs in non-Android source sets → refused |
| `no-entry-point` | no top-level `@Composable fun` → refused |
| `owned` | KMPilot wrote it (template / pipeline-source role) and it has findings → `/modify-feature` work, not migration |

### Tier proposals

| Trigger in the file | Proposal |
|---|---|
| `@Composable`, `androidx.compose.*`, `XTheme`, `ColorScheme`, `Typography` | `core:designsystem (designsystem.app)` |
| `@Serializable`, Ktor, `HttpClient`, `Dao`, `RoomDatabase`, `DataStore`, `SqlDriver`, or a filename ending `Dto`/`Response`/`Api`/`Client`/`DataSource`/`Repository`/`Entity`/`Database` | `core:data (data.app)` |
| none of the above | `core:common (common.app)` |
| files disagreeing | `split across tiers`, with `filesByTier` — the plan phase splits it, the script does not guess a majority |

A shared **data** package consumed by two or more features cites the **DRY corollary** in its reason: it lives once in `data.app` as a canonical DTO plus one `{Shared}RemoteDataSource`, never duplicated per feature.

`hoistable: false` means the package holds Android-only APIs in a common source set. It is refused, and its `blocks[]` names every feature that cannot migrate until it is resolved.

## Refusals

A refusal is a **pass**: report it and move on. Three kinds:

| Refusal | Trigger |
|---|---|
| Android-locked feature | Android-only imports from a non-Android source set |
| No screen entry point | no top-level `@Composable fun` anywhere in the module |
| Unhoistable shared code | Android-only APIs in shared code, blocking its consumers |

**A cross-feature dependency is not a refusal.** At project scope that is precisely what hoisting resolves — it is reported as a `cross-feature-dependency` note plus an `inFeatureShared` row with a tier proposal.

### The two APIs that look Android-only and are not

Do not "correct" the classifier on these. Both are multiplatform, both are used in KMPilot's own `commonMain`, and flagging either would refuse essentially every real feature:

- **`androidx.navigation.*`** — `NavController`, `NavGraphBuilder`, `NavHostController`, `NavBackStackEntry`, `toRoute` are Compose Multiplatform navigation. Only `androidx.navigation.fragment` / `.ui` are Android-locked.
- **`androidx.lifecycle.ViewModel`** / `viewModelScope` — the KMP lifecycle artifact, and the base class the pipeline itself targets. Only `LiveData` and friends are Android-locked.

Likewise, an `android.content.Context` import inside **`androidMain`** is Rule 14 working as designed. Evidence there is recorded with `expected: true` and never blocks.

## Notes

| Note | Meaning |
|---|---|
| `feature-outside-featuredir` | a feature not under `feature/` — ungradable until it moves |
| `missing-desktop-target` | no desktop/jvm target; every `expect` needs a desktop `actual` or the build breaks |
| `cross-feature-dependency` | features never depend on features — hoist first |
| `dependency-cycle` | not orderable as-is; hoisting breaks it. Cycle members are held **out** of `order[]` rather than linearised |
| `catalog-split` | two version catalogs; migrated features must read the one `.kmpilot.json` names |
| `template-mode` / `pipeline-source` / `not-adopted` | the repo is not a migration target |

## Present it

Lead with the **role**. If `migrationTarget` is false, say so first and stop — do not walk the user through an inventory of a project that has nothing to migrate.

Otherwise present, in order: features → shared code with proposals and their triggers → migration order → refusals with evidence → notes. Group repeated notes rather than listing the same fact five times.

Then state plainly: discovery is complete and **nothing was written**. Go on to `@phase-2-plan.md`, which turns this inventory into the plan the user confirms.

## Verify the script itself

```bash
python3 scripts/kmpilot_discover_test.py    # every classifier fires; traps stay silent; nothing written
bash scripts/migrate-matrix.sh              # 37 variants incl. 12 negative controls
scripts/make-nonconforming-project.sh       # regenerate the fixture (offline; runs install.sh --adopt)
```

Those three are upstream-only (`scripts/` is stripped on install), so they are available when developing the pipeline, not in a downstream project.
