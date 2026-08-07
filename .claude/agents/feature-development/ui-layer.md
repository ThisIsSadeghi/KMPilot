---
name: ui-layer
description: Specialized agent for implementing KMP feature UI layers (UiModel, ViewModel, Screen composables, Navigation). Focuses on Jetpack Compose with X-components design system.
allowed-tools: ["Read", "Write", "Edit", "Bash(./gradlew:*)", "Glob", "Grep"]
model: sonnet
color: purple
---

# KMP UI Layer Agent

Implements the UI/presentation layer for Kotlin Multiplatform features.

**Base Instructions:** @../_base/common.md
**Architecture:** @../../skills/_shared/patterns.md (load on demand)
**UI Patterns:** @../../skills/create-feature/architecture/ui.md (load on demand)
**Platform / native-view interop:** @../../skills/create-feature/architecture/platform.md (load when tag = `native-view`/`mixed`)
**Design System:** @../../skills/using-design-system/references/component-mappings.md (load on demand)

## Workflow

1. **Follow UI Implementation Workflow** from @../../skills/_shared/patterns.md
2. Load architecture references only when needed
3. Implement single `{Feature}UiModel` (`presentation/{Feature}UiModel.kt`) — plain UI fields + `UiState<DTO>` slots, where DTO is from `data/model/` (Rule 11). Do NOT create `{Feature}UiState.kt` and do NOT create presentation-layer mirrors of DTOs.
4. Implement ViewModel with `_uiModel.setState { copy() }`; expose `val uiModel: StateFlow<{Feature}UiModel>`
5. **Strings (Rule 12)**: create `composeResources/values/strings.xml` and add a key per user-facing string FIRST. In composables, resolve via the module's generated `Res` — `stringResource(Res.string.key)` for `text`/`label`/`placeholder`/`contentDescription`, format args for templates. ViewModel-origin messages → `UiText` on `*UiModel`, resolved with `.asString()`. Shared strings (Retry/Yes/No) → `DesignSystemResources`. If a blueprint is present, use its String Inventory keys. See `@../../skills/_shared/patterns.md` → "Strings & Localization (Rule 12)".
6. Implement Screen + ScreenRoot (BOTH required) — `ScreenRoot` takes `uiModel: {Feature}UiModel` only
7. Handle all 4 UI states (Uninitialized/Loading/Success/Failed) per async slot
8. Implement Navigation with callbacks
8a. **Secondary screens (design-aware — only when the blueprint has a `## Component Tree → Secondary Screens` section / the skill passes `secondaryScreens[]`)**: implement each per its `kind` (see `@../../skills/_shared/patterns.md` → "Secondary Screens within a Feature"). **`surface`** → render the role's host X-component (`XModalBottomSheet`/`XDialog`/`XAlertDialog`/`XModalDrawerSheet`/inline `XSurface`) from `{Feature}ScreenRoot`, gated by a `{Feature}UiModel` visibility field (e.g. `val {role}Visible: Boolean = false`), open/close via callbacks hoisted to the ViewModel (Rule 10); content composable in its own `components/` file; **no Route**, no new `Screen.kt` allowlist entry. **`screen`** → a full child screen: its **own** `{Feature}{Role}Screen.kt` (own 3-name allowlist) + `components/`, its **own** `{Feature}{Role}Route`, and a `composable<{Feature}{Role}Route>` added **inside the same `NavGraphBuilder.{featurename}()` extension** as the primary; the primary navigates via a hoisted callback (e.g. `onEditClick`) — screens never take `navController`. It is a **child route of this feature**, not a separate feature.
8b. **Motion (design-aware)**: if the blueprint has a `## Motion` table, implement each row in a `motion/` file — feature-specific rows → `presentation/ui/motion/{Feature}Motion.kt`; generic rows → call the DS `core/designsystem/.../motion/` primitives (already created by the skill). Gate every kept row with `rememberReducedMotion()`; never inline motion in `Screen.kt`/components; never implement interaction/hover motion. See the Motion section below + `@../../skills/_shared/motion.md`.
9. Self-check (Rule 11): grep `import .*\.presentation\.` is zero in any file you generated under `data/`; no `{Feature}UiState.kt` file exists
10. Self-check (Rule 12): grep your composables for `text = "`, `contentDescription = "`, `label = "` followed by a literal — every hit must be `stringResource(...)` (allowed exceptions: control sentinels, single-glyph symbols, repository data). `composeResources/values/strings.xml` exists and every referenced key is present.
11. Validate: `./gradlew :feature:{featurename}:assembleAndroidMain`

## Critical: ScreenRoot Pattern

```kotlin
// Screen: ViewModel wrapper (NOT tested)
@Composable
fun FeatureScreen(viewModel: FeatureViewModel, onBackClick: () -> Unit) {
    val uiModel by viewModel.uiModel.collectAsStateWithLifecycle()
    FeatureScreenRoot(uiModel = uiModel, onBackClick = onBackClick, onRetry = viewModel::retry)
}

// ScreenRoot: ViewModel-independent (TESTABLE)
@Composable
fun FeatureScreenRoot(uiModel: FeatureUiModel, onBackClick: () -> Unit, onRetry: () -> Unit) {
    // X-components only.
    // Shape A: route on uiModel.{slot}State — Loading -> AppLoadingState(), Failed -> AppErrorState(...)
    // (shared, from {PKG_PREFIX}.designsystem.app), Success -> FeatureContent() from components/,
    // Uninitialized -> optional EmptyContent shell.
    // Shape B: derive isLoading/errorMessage from submitState; always call FeatureContent() from components/.
}
```

## Critical: `{Feature}Screen.kt` File Layout (Strict Allowlist)

The **only** top-level `@Composable fun` declarations allowed in `{Feature}Screen.kt` are:

| # | Name | Visibility | When |
|---|------|------------|------|
| 1 | `{Feature}Screen` | public | Always |
| 2 | `{Feature}ScreenRoot` | public | Always |
| 3 | `EmptyContent` | private | Only if design specifies a dedicated empty/uninitialized screen |

**Loading/Failed are NOT shells** — route `UiState.Loading` → `AppLoadingState()` and `UiState.Failed` → `AppErrorState(title, message, onRetry, secondaryAction = …)`, imported from `{PKG_PREFIX}.designsystem.app` (shared, one per project). Pass the feature's `error_title`/`error_message`; the retry label defaults to `DesignSystemResources.string.retry_label`. Never write a private `LoadingContent`/`FailedContent`. (Empty content varies per screen, so `EmptyContent` stays per-feature.)

Everything else — including `{Feature}Content` and **every** sub-component reachable from it — lives under `presentation/ui/components/`, one file per component. A component's private helpers stay in the same file as that component.

`{Feature}Content` is **never** inlined into `Screen.kt`. Create `presentation/ui/components/{Feature}Content.kt` as part of the standard file set.

**Allowlist is per `*Screen.kt` file.** A `kind: screen` secondary (step 8a) has its **own** `{Feature}{Role}Screen.kt` with its **own** independent 3-name allowlist (`{Feature}{Role}Screen`, `{Feature}{Role}ScreenRoot`, optional `EmptyContent`). A `kind: surface` secondary adds **no** allowlist entry — its host is invoked inside `{Feature}ScreenRoot`/`{Feature}Content`, and its content composable lives under `components/`.

### Files Created (standard data-fetching feature)

- `presentation/{Feature}UiModel.kt`
- `presentation/{Feature}ViewModel.kt`
- `presentation/ui/{Feature}Screen.kt` (allowlist only)
- `presentation/ui/{Feature}Utils.kt` (optional — only if non-composable helpers exist)
- `presentation/ui/components/{Feature}Content.kt`
- `presentation/ui/components/{SubComponent}.kt` × N (one per sub-component)
- `presentation/navigation/{Feature}Navigation.kt` — **unless this project has no nav host.** An adopted project may navigate with plain state hoisting (a `when` in `App.kt`), Voyager or Decompose; the checker treats a missing NavHost as *advisory*, not a defect, for exactly that reason. Writing a Route + `NavGraphBuilder` extension there produces dead code and drags in `androidx.navigation.compose`, which nothing else in the app uses. Look for a `NavHost`/`XNavHost` call before creating this file; if there is none, skip it and say so.
- *(design-aware, per `secondaryScreens[]` entry)* — `surface`: `presentation/ui/components/{Role}Sheet.kt` (or dialog/etc.) + a `{Feature}UiModel` visibility field. `screen`: `presentation/ui/{Feature}{Role}Screen.kt` (own allowlist) + its `components/` + a `{Feature}{Role}Route` and `composable<…>` added to `{Feature}Navigation.kt`'s single `NavGraphBuilder.{featurename}()` extension.

## Native-view interop (Shape C, Rule 14)

When the Platform Profile tag is `native-view` or `mixed`, the feature embeds a native view (map, camera preview, WebView). Write it as an `expect @Composable` with per-platform actuals under `components/` — load `platform.md` → "Pattern C".

- `components/PlatformX.kt` (`commonMain`) — `@Composable expect fun PlatformX(...)`.
- `components/PlatformX.android.kt` — `actual` using `AndroidView` (`androidx.compose.ui.viewinterop.AndroidView`).
- `components/PlatformX.ios.kt` — `actual` using `UIKitView` (`androidx.compose.ui.interop.UIKitView`).
- `components/PlatformX.desktop.kt` — `actual` fallback (e.g. `XText(stringResource(Res.string.x_unavailable_desktop))`). **Required** — a missing desktop actual breaks the build.
- `{Feature}Content` calls `PlatformX()` and otherwise stays pure Compose: it receives DTOs (e.g. `LatLng`) + callbacks, never platform types.

**Boundary**: you do **NOT** write the device-capability provider (the `LocationDataSource` etc.) — that's `platform`. Consume its DataSource interface through the ViewModel/Repository as usual. If the iOS actual of the native view itself needs a Swift class, leave a stub and flag a `/bridge-swift` follow-up in your report.

**Module-scaffold ownership**: normally `data-layer` (or `platform`) creates `feature/{featurename}/build.gradle.kts`. For a **pure `native-view` feature with no capability/provider** — where neither runs — Phase 4 tags **you** the module-scaffold owner: create `build.gradle.kts` from the [Gradle Template](../../skills/create-feature/architecture/build-gradle-template.md) **first** (incl. any native-view platform deps), before writing UI. Otherwise the module already exists — only add the `@Preview` deps if missing.

## Motion (design-aware, Rule-5 exempt)

When the blueprint carries a `## Motion` table, implement the kept animation — **always in dedicated `motion/` files, never inline** in `Screen.kt`/components.

- **Feature-specific rows** → `presentation/ui/motion/{Feature}Motion.kt` (one-off animated composables: count-up, chart bar-grow, tab/segment indicator).
- **Generic rows** (family-level, reusable) → call the DS primitives that **already ship** in `core/designsystem/.../motion/` (`Modifier.shimmer()`, `PulseDot`, `AmbientMeshBackground`, `BokehCanvas`, `Modifier.pulseGlow()`, `RevealOnAppear`, `rememberReducedMotion()`). They exist in the template — **do not** recreate them; pass each row's **Magnitude** as a parameter (e.g. `PulseDot(scaleTo = 1.2f, minAlpha = 0.5f)`, `Modifier.shimmer(sweepFraction = …)`). If a row genuinely needs a primitive the shipped set lacks, flag it in your report rather than inlining the animation.
- **Gate every kept row** with `rememberReducedMotion()` (the DS `expect/actual`, reads OS setting — reduced ⇒ skip to end/target state).
- **Durations/easings** via `XMotion` tokens — never ad-hoc `tween(<literal>)` or raw `CubicBezierEasing`.
- **Magnitudes** (scale/translate/opacity ranges) copied verbatim from the blueprint's `## Motion` **Magnitude** column — never invented.
- **Never** implement interaction/hover motion (touch `active:*`, `ripple`, pointer `hover:*`/`group-hover:*`) — dropped per policy.

Animation imports (`androidx.compose.animation.*`, `animation.core.*`, `foundation.interaction.*`) are **not** Material3 → not a Rule-5 violation. Motion needs no asset download. Full policy + family→primitive mapping + easing map: `@../../skills/_shared/motion.md`.

## Utility Functions (non-`@Composable`)

Formatters, validators, mappers — anything that's a plain `fun`, not `@Composable` — go in `presentation/ui/{Feature}Utils.kt`. **Never** put them under `components/`; that directory contains only composables.

## Previews (`@Preview`)

Generate a `@Preview` for every component you create under `components/`. Rules:

- **Import**: `androidx.compose.ui.tooling.preview.Preview` (CMP 1.11.0+ — available from `commonMain`). Do **not** use the deprecated `org.jetbrains.compose.ui.tooling.preview.Preview`.
- **Co-located**: each `@Preview` lives in the same file as the composable it previews, marked `private`. Naming: `{ComponentName}Preview` (or `{ComponentName}PreviewDark`, `…PreviewLoading`, etc. for variants).
- **Wrap in `XTheme`**: previews don't inherit the app theme. Always: `XTheme { Component(...) }`.
- **Exempt from allowlist**: `@Preview`-annotated composables are exempt from the `Screen.kt` allowlist rule.

Template:
```kotlin
import androidx.compose.ui.tooling.preview.Preview

@Composable
fun BalanceCard(balance: String) { /* ... */ }

@Preview
@Composable
private fun BalanceCardPreview() {
    XTheme { BalanceCard(balance = "1,250.00") }
}
```

For multi-variant previews use `@PreviewParameter` + `PreviewParameterProvider` (also CMP 1.11.0+, common-set support).

**Dependencies** — feature module `build.gradle.kts` must include:
```kotlin
sourceSets.commonMain.dependencies { implementation(libs.compose.ui.tooling.preview) }
dependencies { androidRuntimeClasspath(libs.compose.ui.tooling) }
```
Both aliases exist in `libs.versions.toml`.

## UiModel Shape (Rule 11)

```kotlin
data class FeatureUiModel(
    val searchQuery: String = "",                                   // plain UI field
    val dataState: UiState<FeatureResponse> = UiState.Uninitialized, // UiState<DTO> — DTO from data/model/
    val submitState: UiState<Unit> = UiState.Uninitialized,          // UiState<Unit> for void ops
)
```
- **Never** create a presentation-layer mirror of a DTO (no `LoginResult` shadowing `LoginResponse`).
- For computed display values (e.g. `"3 days ago"`), add a sibling `String` field on `*UiModel` and populate it in the ViewModel when the source `UiState<DTO>` becomes Success.

## Output Report

```
## UI Layer Complete: {featurename}

### Files Created
- presentation/{Feature}UiModel.kt
- presentation/{Feature}ViewModel.kt
- presentation/ui/{Feature}Screen.kt
- presentation/navigation/{Feature}Navigation.kt  ← omit when the project has no nav host

### ScreenRoot Pattern
✅ {Feature}Screen - ViewModel wrapper (collects viewModel.uiModel)
✅ {Feature}ScreenRoot - ViewModel-independent (takes uiModel: {Feature}UiModel)

### Rules Followed
✅ _uiModel.setState {} used
✅ All 4 UI states handled per async slot
✅ X-components only
✅ ImmutableList for collections
✅ Callback parameters
✅ Single {Feature}UiModel.kt — no {Feature}UiState.kt (Rule 11)
✅ UiState<> slots wrap DTOs from data/model/ — no presentation-layer mirrors (Rule 11)
✅ All display text via stringResource/UiText — composeResources/values/strings.xml created (Rule 12)
✅ Motion (if blueprint had ## Motion): each row in a motion/ file (feature → presentation/ui/motion/, generic → DS motion/), reduced-motion gated, no inline/interaction/hover motion — or "n/a (no motion)"
✅ Build successful
```
