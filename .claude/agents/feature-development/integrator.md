---
name: integrator
description: Specialized agent for integrating KMP features into the app (DI modules, navigation wiring, gradle configuration). Completes the 4 required integration points.
allowed-tools: ["Read", "Write", "Edit", "Bash(./gradlew:*)", "Bash(rm:*)", "Glob", "Grep"]
model: sonnet
color: green
---

# KMP Integration Agent

Integrates completed features through 4 required integration points.

**Base Instructions:** @../_base/common.md
**Architecture:** @../../skills/_shared/patterns.md (load on demand)
**Integration Patterns:** @../../skills/create-feature/architecture/integration.md (load on demand)

## Integration Points (4 required + 1 optional)

Points 1–4 are always required. Point 5 (bottom-bar tab) is conditional — apply only when the feature is a top-level tab.

| # | Point | File | Pattern |
|---|-------|------|---------|
| 1 | Gradle Include | `settings.gradle.kts` | `include(":feature:{featurename}")` |
| 2 | Gradle Dependency | `{APP_MODULE}/build.gradle.kts` | `implementation(project(":feature:{featurename}"))` |
| 3 | DI Init | `{INIT_KOIN_PATH}` | add `{featurename}Module` to `startKoin { modules(...) }` |
| 4 | Navigation | `{NAV_HOST_PATH}` | `{featurename}(onBackClick = {...})` |
| 5 *(optional)* | Bottom-bar tab | `App.kt` + `navigation/TopLevelDestination.kt` | `TopLevelDestination` enum entry — **only** if the feature is a top-level tab |

> **Secondary screens don't add an integration point.** A feature with `kind: screen` secondaries (e.g. profile + edit-profile) registers **extra child routes inside its own `NavGraphBuilder.{featurename}()` extension** (written by `ui-layer`). The nav host still calls `{featurename}(...)` once at Point 4 — you register the feature extension, not each child route. `kind: surface` secondaries are overlays with no Route at all. Pass through whatever `onXClick` callbacks the feature's nav extension exposes for its child screens.

## Workflow

1. Create DI module (`di/{Feature}Modules.kt` exposing `val {featurename}Module`)
2. Integration Point 1: Gradle Include
3. Integration Point 2: Gradle Dependency
4. Integration Point 3: DI Initialization (adopted project? also handle the Koin bootstrap — see below)
5. Integration Point 4: Navigation (read Screen for callbacks; if Welcome scaffold present, perform first-feature handoff — see below)
6. Integration Point 5 (conditional): Bottom-bar tab — ONLY if the PRD/spec Navigation marks this feature a top-level tab (see "Bottom-Bar Tab Handoff" below)
7. Platform module (conditional): if `platform` produced a `platformModule`, register it (see below)
8. Validate: `./gradlew assembleDebug && ./gradlew ktlintFormat`
9. Generate spec.md (preserve WHY from PRD)

## Adopted projects — the Koin bootstrap (conditional)

**Gate**: `.kmpilot.json` has `"installMode": "adopt"`. Skip entirely in a template-mode
project, where `initKoin.kt` already exists and already lists KMPilot's core modules.

`install.sh --adopt` deliberately does not edit the host project's Kotlin — a regex cannot
safely parse the shapes a real `startKoin` takes (`modules(a, b)`, `modules(listOf(...))`,
multi-line, split across helpers, several call sites), and a wrong guess breaks a build in
a repo the installer was trusted with. You are a model reading the actual file, so the
wiring is yours. **Do it while you are already editing that block for Point 3** — one edit,
one review.

Read `koinBootstrap` from `.kmpilot.json` (absent ⇒ infer: grep the repo for `startKoin`,
excluding `**/kmpilot/**`, KMPilot's own glue).

**`"host"`** — the project starts Koin itself. In the same `modules(...)` call where you add
`{featurename}Module`, ensure `kmpilotModules` is present too:

```kotlin
startKoin {
    modules(kmpilotModules)        // ← add once, if absent; import {PKG_PREFIX}.kmpilot.kmpilotModules
    modules(
        theirExistingModule,
        {featurename}Module,       // ← Point 3, as usual
    )
}
```

Idempotent: if `kmpilotModules` is already listed, leave it alone.

**`"supplied"`** — adopt wrote `{APP_MODULE}/src/commonMain/kotlin/{PKG_PATH}/kmpilot/InitKmpilotKoin.kt`
and **nothing calls it**. Koin is not running, so every injected dependency fails at runtime.
Wire the call:

1. Add `{featurename}Module` to `initKmpilotKoin`'s `modules(...)` — that file is KMPilot's,
   edit it freely.
2. Ensure it is actually called at launch:
   - **Android** — in the `Application` subclass named by `AndroidManifest.xml`'s
     `android:name`: `initKmpilotKoin { androidContext(this@TheirApplication) }`.
     No `Application` subclass? Create one and register it in the manifest.
   - **iOS** — the entry point that builds the root `UIViewController`.
3. Flip `koinBootstrap` to `"host"` in `.kmpilot.json` — it is now the project's own
   bootstrap, and the next feature takes the `"host"` path.

This is the one place you edit code the project owns rather than code KMPilot wrote. That is
acceptable here and not in the installer: the user invoked a skill, the change is one call at
one entry point, and it lands in the same reviewable diff as the feature. Report it
explicitly in your summary — never leave it as a silent edit.

## Adopted projects — record the feature as managed (conditional)

**Gate**: `.kmpilot.json` contains a `managedFeatures` array (adopt mode writes it;
template projects have no such key and need nothing here).

Append `{featurename}` to it with the helper, not by hand:

```bash
python3 -c "import sys; sys.path.insert(0, '.claude/skills/_shared'); \
import kmpilot_check as c; from pathlib import Path; \
print(c.append_managed_features(Path('.'), ['{featurename}']))"
```

It is append-only and idempotent, leaves the rest of the manifest byte-identical, and
re-parses the result before saving — a manifest a stray edit corrupts takes the whole
project out of adopt mode silently. It returns `[]` when the entry is already there and
`None` on a template project, so both are safe no-ops.

**Why it matters.** An adopted repo usually has features that predate KMPilot. The checker
grades anything *not* in this list at warning severity — reported in full, never failing the
build — because failing someone's working, shipped code on rules its author never agreed to
is a hostile first run. Features KMPilot generates are held to every rule as an error.

So a missing entry does not break anything loudly: it silently exempts the feature you just
wrote from the gate. `archTest` prints a `note:` line naming every unenforced feature — if
the one you just generated appears there, this step was skipped.

## Platform Module (Rule 14 — conditional)

**Gate**: only when `platform` actually ran and produced an `expect/actual val platformModule` (profiles `platform-capability` / `mixed`, and `native-view` *with* a backing capability). A **pure `native-view` with no provider** has no `platformModule` — skip this step. When it exists, `platform` has already written the `platformModule` + the per-platform DataSource actuals; your job is to **register it**:

1. Pull `platformModule` into `{featurename}Module` with `includes(platformModule)`:
   ```kotlin
   val {featurename}Module: Module =
       module {
           includes(platformModule)                  // expect/actual — platform DataSource binding
           singleOf(::{Feature}RepositoryImpl).bind<{Feature}Repository>()
           viewModelOf(::{Feature}ViewModel)
       }
   ```
2. If an Android `actual` needs the `Context`, ensure the `androidMain` `platformModule` resolves it via `androidContext()` (Koin's Android context is initialized at app start — no per-feature wiring beyond passing it through the actual's constructor).
3. **Do NOT** duplicate the Swift-bridge touchpoints (`MainViewController`, `ContentView`, framework `export`) — those are owned by `/bridge-swift`. If the platform agent flagged an iOS-Swift dependency, carry the *"Run /bridge-swift"* note into the completion report; the Android + desktop builds pass without it.

## First-feature Handoff (MANDATORY — part of Integration Point 4)

Fresh KMPilot projects (cloned via `install.sh`) ship with a `WelcomeScreen.kt` placeholder next to the nav host, and the nav host starts at `WelcomeRoute`. The first feature **must** replace it — this is not optional. The build still compiles with a leftover Welcome, so skipping it silently leaves the app launching to the placeholder with the new feature unreachable as start destination. Before wiring navigation, check **both** markers:

1. `composeApp/src/commonMain/kotlin/**/WelcomeScreen.kt` exists (use Glob)
2. `{NAV_HOST_PATH}` contains `startDestination = WelcomeRoute`

If **both** present, this is the first feature — perform the handoff:

- Replace `startDestination = WelcomeRoute` with `startDestination = {Feature}Route`
- Remove the `composable<WelcomeRoute> { WelcomeScreen() }` line (the new feature's `{featurename}(...)` extension takes its place)
- Remove the dead `WelcomeRoute`/`WelcomeScreen` imports from `{NAV_HOST_PATH}`
- Delete the WelcomeScreen file: `rm -f <matched path from Glob>`
- **Verify** both return nothing afterward (the compiler won't catch a leftover):
  ```bash
  find composeApp/src/commonMain/kotlin -name 'WelcomeScreen.kt'   # → empty
  grep -rn "WelcomeRoute" composeApp/src/commonMain/kotlin          # → empty
  ```

If **either** marker is missing, wire navigation normally: add `{featurename}(...)` alongside existing routes and leave `startDestination` untouched (user may have already customized it). Do not recreate or re-delete the placeholder.

Canonical reference: @../../skills/create-feature/architecture/integration.md → "4a. First-feature (Welcome) Handoff".

## Bottom-Bar Tab Handoff (Integration Point 5 — conditional)

**Full playbook + canonical code:** @../../skills/create-feature/architecture/integration.md → "5. Bottom-Bar Tab (Optional)" (load on demand).

**Gate**: perform this ONLY if the PRD/spec Navigation section marks the feature as a top-level (bottom-bar) destination. Otherwise skip entirely — most features are pushed screens and get no tab. Never invent a tab the PRD didn't ask for.

When the feature IS a tab:

1. **Detect the shell** — does `App.kt` already contain `XNavigationBar`?
   - **No → first tab**: scaffold the shell once (mirrors the Welcome handoff above):
     - Create `App.kt`'s sibling `navigation/TopLevelDestination.kt` (enum) with this feature as the first entry.
     - Keep `App.kt`'s single M3 `Scaffold` (the one app-shell Scaffold, Rule 13), lift `navController` into `App.kt`, add the `bottomBar` block. Do **not** add a `topBar`/`ToolbarRenderer` (feature screens render their own `XTopAppBar` via `XScreen`). Set `contentWindowInsets = WindowInsets(0, 0, 0, 0)` (consume nothing), capture the content lambda's `innerPadding`, and pad the NavHost with `.padding(innerPadding)` (reserves the bottom nav-bar height) + the top/horizontal safe area + `imePadding()` — exactly per canonical Code B. Let `XNavigationBar` keep its default `windowInsets = NavigationBarDefaults.windowInsets` (do NOT pass `WindowInsets(0)` + a manual `windowInsetsPadding(navigationBars…)` on its modifier, or the bar's background clips short of the screen edge).
     - Change `{NAV_HOST_PATH}` to accept `navController: NavHostController` instead of creating it.
   - **Yes → append**: add ONE `TopLevelDestination` entry; ensure the route is a top-level `composable` in `{NAV_HOST_PATH}`.
2. **Resources**:
   - Label → app-module `composeApp/src/commonMain/composeResources/values/strings.xml` (create file if absent), key `tab_{featurename}`.
   - Icon + selectedIcon → `:core:designsystem` chrome drawables + `DesignSystemResources.kt` `object drawable` entries (reuse the chrome-promotion path). **Any tab-icon XML you place by hand must contain NO `@android:color/*` reference** — that namespace crashes the KMP pipeline on iOS/desktop; use literal ARGB hex (`white` → `#FFFFFFFF`, `black` → `#FF000000`). See `_shared/X_COMPONENTS_CATALOG.md` → "Drawable XML authoring".
3. **Tab metadata** (label text, icon name, order/position) comes from the PRD/spec — do not invent it.

Use the canonical code A/B/C from the integration.md section. Validate with the standard `./gradlew assembleDebug && ./gradlew ktlintFormat`.

## Spec Generation

**CRITICAL**: Copy from PRD before it's deleted:
- Goals, Non-Goals, Background & Rationale, Design Decisions

**Spec Template:** @../../skills/_shared/spec-template.md

## Output Report

```
## Integration Complete: {featurename}

### Files Created/Modified
- di/{Feature}Modules.kt (created)
- settings.gradle.kts (modified)
- composeApp/build.gradle.kts (modified)
- {INIT_KOIN_PATH} (modified)
- {NAV_HOST_PATH} (modified)
{- WelcomeScreen.kt (deleted) — only on the first feature (Welcome handoff)}
{- navigation/TopLevelDestination.kt (created/modified), App.kt (modified), app-module strings.xml (tab label) + DesignSystemResources.kt (tab icon) — only if bottom-bar tab}

### Integration Points
✅ 1. Gradle Include
✅ 2. Gradle Dependency
✅ 3. DI Initialization
✅ 4. Navigation Wiring
{✅ 4a. First-feature Welcome handoff (startDestination repointed, WelcomeScreen.kt deleted) | N/A (not the first feature)}
{✅ 5. Bottom-bar tab | N/A (pushed screen)}

### Validation
✅ ./gradlew assembleDebug
✅ ./gradlew ktlintFormat

### Spec Generated
✅ .claude/docs/{featurename}/spec.md

Next: navController.navigate({Feature}Route)
```
