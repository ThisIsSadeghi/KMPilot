# KMP Architecture Patterns (Single Source of Truth)

All skills and agents import this file. Do not duplicate these rules elsewhere.

**The mechanized half of these rules is enforced by a failing build, not a prompt:** `./gradlew archTest` (thin wrapper over [`_shared/kmpilot_check.py`](kmpilot_check.py)) runs 19 deterministic checks — R3, R5, R7, R8, R9, R11a/b/c, R12, R13, the `Screen.kt` allowlist, `components/` placement, the `.app`-tier boundary, the preview import, and the 4 integration points — writes `.claude/docs/_project/check-report.json`, and exits non-zero on any `error`. `/review-feature` consumes that report instead of re-deriving it, so a review and a CI run cannot disagree. Rules 1, 2, 4, 10 and 14 stay judgment calls. Scanning a codebase that has **not** adopted KMPilot yet: add `--baseline` (same checks, errors reported as warnings, exit 0).

## 14 Critical Rules

1. **Interface + Impl** - DataSource and Repository always have interface + implementation pair
2. **Either<T>** - Return `Either<T>` for fallible operations, never throw exceptions
3. **setState** - Use `_uiModel.setState { copy() }`, NEVER `_uiModel.value =`
4. **4 UI States** - Handle all: Uninitialized / Loading / Success / Failed
5. **X-components** - Use `:core:designsystem` components, NO Material3
6. **ImmutableList** - Use `.toImmutableList()` for state collections
7. **Lowercase packages** - `{PKG_PREFIX}.featurename` (no hyphens/camelCase/underscores)
8. **DI Pattern** - feature exposes a top-level `val {featurename}Module = module { singleOf(::Impl).bind<Interface>() … }`; list it in `initKoin`'s `modules(...)`. No base class, no registry, no `initialize()`.
9. **No UseCases** - ViewModels invoke repositories directly
10. **Callback params** - Screens take callbacks (`onBackClick`), not `navController`
11. **Single UiModel + DTO-wrapped UiState** - `*UiModel` is the only presentation state container (no `*UiState.kt`). It holds plain UI fields + one `UiState<DTO>` slot per async operation, where DTO is the data-layer model (use `UiState<Unit>` for void ops). Repository returns `Either<DTO>`; data layer never imports from `presentation`. UI-derived display values live as sibling fields on `*UiModel`, never as mirror DTO types.
12. **No hardcoded user-facing strings** - Every display string (text, labels, content descriptions, placeholders) comes from a string resource via `stringResource(Res.string.*)` (feature-local) or `DesignSystemResources` (shared). Feature strings live in `composeResources/values/strings.xml`; translations in `values-{lang}/strings.xml`. `*UiModel` carries `UiText`/`StringResource`, never English literals — ViewModels build `UiText`, composables resolve with `.asString()`. **Not strings**: control sentinels parsed in logic, single-glyph symbols (`$`, `₿`, `✓`, `%`), and repository-supplied data (merchant names, dates, tickers). See "Strings & Localization" below.
13. **Single app-shell Scaffold** - The one `Scaffold` lives in the app shell (`App.kt`); `contentWindowInsets = WindowInsets(0, 0, 0, 0)` (consumes nothing). The shell pads the NavHost with the **top + horizontal** safe area (`WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal)`) plus `imePadding()` — **not** the bottom. The bottom (nav-bar) inset is owned by whatever sits at the bottom of each screen: a **bottom action bar** bleeds its background to the screen edge and pads its own content with `Modifier.windowInsetsPadding(WindowInsets.navigationBars.exclude(WindowInsets.ime))` (= `max(0, navBar − ime)`: clears the nav bar when the keyboard is closed, collapses to 0 when the shell's `imePadding()` lifts the screen — plain `navigationBarsPadding()` would stack on that lift and float the CTA a nav-bar height above the keyboard); a **full-bleed scroll screen** with no bar pads its own content (e.g. `Modifier.navigationBarsPadding()` on the list). Feature screens **NEVER** nest a `Scaffold`/`XScaffold` — they use `XScreen(topBar = …, bottomBar = …) { … }` with a plain `XTopAppBar`. `XScreen` and `XTopAppBar` add **no** insets of their own. Sticky CTAs go in `XScreen`'s `bottomBar` slot. Nesting a second Scaffold reintroduces double safe-area/nav-bar padding. See "Single App-Shell Scaffold" below.
14. **Platform capability = a DataSource; native view = expect/actual composable** - A device/native API (GPS, camera, BLE, biometrics, sensors) is hidden behind a `commonMain` interface returning `Either<T>` and implemented per-platform; ViewModel/Repository/`*UiModel` never see platform types (Rule 11 still holds). A native view (map, camera preview, WebView) embeds via an `expect @Composable` whose actuals use `AndroidView` (androidMain) / `UIKitView` (iosMain) / a graceful fallback (desktopMain). Every `expect` needs an `actual` for **all** targets — android, ios **and desktop** — or the build breaks. Sourcing precedence: multiplatform lib > expect/actual > iOS-Swift bridge (`/bridge-swift`). See "Platform Capabilities & Native Views" below and [create-feature/architecture/platform.md](../create-feature/architecture/platform.md).

## Design-Aware Implementation

Implementation skills (`/modify-feature`, `/create-feature`) auto-detect Stitch design blueprints:

1. Check for `.claude/docs/{featurename}/designs/{featurename}_blueprint.md`
2. Check `blueprintConsumed == false` in `.claude/docs/_project/stitch-project.json` under `features[featurename]`
3. If both conditions met → **design-aware mode**: blueprint drives UI implementation (XTheme updates, component tree, post-implementation checklist)
4. After implementation → set `blueprintConsumed: true` in `stitch-project.json.features[featurename]`

`/using-design-system` auto-activates for UI work and does not need explicit invocation.

## 4 Integration Points

Every feature requires exactly these 4 integrations. `{APP_MODULE}` is `composeApp` in a
template-mode project and whatever the host named its app module in an **adopted** one —
read it from `.kmpilot.json` (`appModule`), never assume. Same for `{CATALOG}`
(`libs` vs `kmpilotLibs`). See
[create-feature/phases/phase-0-context.md → Step 0.0](../create-feature/phases/phase-0-context.md).

| # | Point | File | Pattern |
|---|-------|------|---------|
| 1 | Gradle Include | `settings.gradle.kts` | `include(":feature:{featurename}")` |
| 2 | Gradle Dependency | `{APP_MODULE}/build.gradle.kts` | `implementation(project(":feature:{featurename}"))` |
| 3 | DI Init | `{INIT_KOIN_PATH}` | add `{featurename}Module` to `startKoin { modules(...) }` |
| 4 | Navigation | `{NAV_HOST_PATH}` | `{featurename}(onBackClick = {...})` |

**Optional 5th point — Bottom-Bar Tab**: only for features that are top-level (bottom-bar) destinations, not pushed screens. It registers the feature as a tab via a `TopLevelDestination` enum entry in the app module + an `XNavigationBar` in `App.kt`. This is **not** a rule and not required — most features skip it. **Adding the nav bar changes the shell's bottom-inset wiring** (see "Single App-Shell Scaffold" below): the Scaffold content lambda switches from `{ _ ->` to `{ innerPadding ->` and pads the NavHost with `.padding(bottom = innerPadding.calculateBottomPadding())` so scroll content doesn't bleed under the bar; and `XNavigationBar` takes `windowInsets = NavigationBarDefaults.windowInsets` (never `WindowInsets(0)` + a manual `windowInsetsPadding(navigationBars…)` on its modifier) so its `containerColor` fills behind the system nav-bar strip. Full playbook + canonical code: [create-feature/architecture/integration.md → "5. Bottom-Bar Tab (Optional)"](../create-feature/architecture/integration.md).

## Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Package | `{PKG_PREFIX}.{featurename}` | `com.example.productdetail` |
| ViewModel | `{Feature}ViewModel` | `ProductDetailViewModel` |
| Repository | `{Entity}Repository` / `{Entity}RepositoryImpl` | `ProductRepository` |
| DataSource | `{Entity}RemoteDataSource` / `...Impl` | `ProductRemoteDataSource` |
| Screen | `{Feature}Screen` + `{Feature}ScreenRoot` | `ProductDetailScreen` |
| Route | `{Feature}Route` | `ProductDetailRoute` |
| Nav Extension | `{featurename}` (lowercase) | `fun NavGraphBuilder.productdetail()` |
| DI Module val | `{featurename}Module` | `productdetailModule` |

## Key Patterns

### setState (Rule 3)
```kotlin
// CORRECT
_uiModel.setState { copy(isLoading = true) }

// WRONG - never do this
_uiModel.value = _uiModel.value.copy(isLoading = true)
```

### Either (Rule 2) — repository returns Either<DTO> directly (Rule 11)
```kotlin
when (val result = repository.getData()) {
    is Either.Success -> _uiModel.setState { copy(dataState = UiState.Success(result.data)) }
    is Either.Failure -> _uiModel.setState { copy(dataState = UiState.Failed(result.error)) }
}
// result.data is the data-layer DTO. Do NOT map to a presentation-layer mirror type.
```

### UiModel (Rule 11) — single state container, DTOs inside UiState
```kotlin
data class FeatureUiModel(
    val searchQuery: String = "",                                    // plain UI field
    val selectedTab: Int = 0,                                         // plain UI field
    val dataState: UiState<FeatureResponse> = UiState.Uninitialized,  // UiState<DTO>
    val submitState: UiState<Unit> = UiState.Uninitialized,           // UiState<Unit> for void ops
)
```

### ScreenRoot (Rule 10 + Rule 11) — takes the UiModel + callbacks only
```kotlin
// Screen: ViewModel wrapper (NOT tested directly)
@Composable
fun FeatureScreen(viewModel: FeatureViewModel, onBackClick: () -> Unit) {
    val uiModel by viewModel.uiModel.collectAsStateWithLifecycle()
    FeatureScreenRoot(uiModel = uiModel, onBackClick = onBackClick, onRetry = viewModel::retry)
}

// ScreenRoot: ViewModel-independent (TESTABLE) — uses XScreen, never a Scaffold (Rule 13)
@Composable
fun FeatureScreenRoot(uiModel: FeatureUiModel, onBackClick: () -> Unit, onRetry: () -> Unit) {
    XScreen(
        topBar = { XTopAppBar(/* title, back */) },
        bottomBar = { /* optional sticky CTA, e.g. only on Success */ },
    ) {
        // route on uiModel.dataState for the async slot; content fills XScreen's weight box
    }
}
```

### Single App-Shell Scaffold (Rule 13)

There is exactly **one** `Scaffold` in the whole app, in `App.kt`. Feature screens use `XScreen` — a plain `Column { topBar(); Box(weight 1f){ content() }; bottomBar() }` that **touches no window insets**.

The shell's bottom-inset wiring has **two cases**, gated on whether the app has a bottom nav bar (an `XNavigationBar` in the Scaffold's `bottomBar` — the Optional 5th integration point).

**Case A — no bottom nav bar (`bottomBar = {}`), the default.** Keep `{ _ ->`; the bottom inset is owned per-screen.

```kotlin
// App.kt — the ONE Scaffold (shell), NO bottom nav bar
Scaffold(
    snackbarHost = {
        SnackbarHost(snackbarHostState, modifier = Modifier.windowInsetsPadding(WindowInsets.safeDrawing))
    },
    bottomBar = { /* empty */ },
    contentWindowInsets = WindowInsets(0, 0, 0, 0),   // consume nothing
) { _ ->                                              // innerPadding ignored — bottom owned per-screen
    BaseAppNavHost(
        modifier = Modifier
            .fillMaxSize()
            // TOP + HORIZONTAL only (status bar + cutout); bottom is owned per-screen so
            // bottom action bars can bleed to the edge. imePadding lifts the screen for the keyboard.
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
            .imePadding(),
    )
}
```

**Case B — with a bottom nav bar (`XNavigationBar` as `bottomBar`).** The shell owns the bottom (nav-bar) inset so scroll content can't slide under the bar. Switch to `{ innerPadding ->`, pad the NavHost bottom by `innerPadding.calculateBottomPadding()`, and let `XNavigationBar` draw its own system-nav-bar inset so its background reaches the screen edge.

```kotlin
// App.kt — shell WITH a bottom nav bar (Optional 5th point)
Scaffold(
    snackbarHost = {
        SnackbarHost(snackbarHostState, modifier = Modifier.windowInsetsPadding(WindowInsets.safeDrawing))
    },
    bottomBar = {
        XNavigationBar(
            modifier = Modifier.fillMaxWidth(),       // NO manual windowInsetsPadding(navigationBars…) here
            containerColor = MaterialTheme.colorScheme.surface,
            // Let XNavigationBar handle the system nav-bar inset internally so its containerColor
            // fills behind the gesture / button strip (edge-to-edge). NOT WindowInsets(0).
            windowInsets = NavigationBarDefaults.windowInsets,
        ) { /* XNavigationBarItem per TopLevelDestination */ }
    },
    contentWindowInsets = WindowInsets(0, 0, 0, 0),
) { innerPadding ->                                   // capture it — nav-bar height lives here
    BaseAppNavHost(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
            .imePadding()
            // Push content above the bottom nav bar so it isn't covered.
            .padding(bottom = innerPadding.calculateBottomPadding()),
    )
}
```

- **Case A: bottom inset is NOT applied at the shell.** A bottom action bar (`XScreen`'s `bottomBar`) draws its background full-bleed to the screen edge and pads its own content with `Modifier.windowInsetsPadding(WindowInsets.navigationBars.exclude(WindowInsets.ime))` — so the bg sits behind the nav bar while the buttons clear it, and the nav-bar pad collapses to 0 when the shell's `imePadding()` lifts the screen for the keyboard. (Plain `navigationBarsPadding()` here would stack on top of that lift and float the CTA a nav-bar height above the keyboard.) A scroll screen with no bottom bar pads its own content (`Modifier.navigationBarsPadding()` on the list). This is the standard edge-to-edge pattern; centralising the bottom inset at the shell pushes bottom bars up off the edge and looks wrong.
- **Case B: the shell DOES pad the NavHost bottom.** Two bugs this prevents: **(1) scroll content bleeding under the `XNavigationBar`** — fixed by `{ innerPadding ->` + `.padding(bottom = innerPadding.calculateBottomPadding())` on the NavHost; using `{ _ ->` here leaves the bar floating over content. **(2) the system nav-bar strip showing a different color than the bar** — fixed by `windowInsets = NavigationBarDefaults.windowInsets` so `XNavigationBar` extends its `containerColor` behind the strip natively. Do **not** pass `WindowInsets(0)` + a manual `windowInsetsPadding(WindowInsets.navigationBars.exclude(WindowInsets.ime))` on the bar's modifier — that clips the background short of the screen edge.
- **Why not `contentWindowInsets = systemBars` + `padding(innerPadding)`?** With an empty `bottomBar` (Case A), Scaffold does not reliably push the bottom (nav-bar) inset into `innerPadding`. Applying the top/horizontal safe area directly on the NavHost is version-independent.
- Nesting a second `Scaffold`/`XScaffold` inside a feature reintroduces double safe-area / nav-bar padding. Don't.
- `XScaffold` still exists but is **app-shell only**; feature screens never call it.

### DI Module (Rule 8)

A feature exposes one top-level `val {featurename}Module: Module` (idiomatic Koin — no base class, no registry, no `initialize()`). `initKoin` lists it in `modules(...)`.

```kotlin
// feature/{featurename}/di/{Feature}Modules.kt
val featureModule: Module =
    module {
        singleOf(::RemoteDataSourceImpl).bind<RemoteDataSource>()
        singleOf(::RepositoryImpl).bind<Repository>()
        viewModelOf(::FeatureViewModel)
    }
```

```kotlin
// composeApp initKoin.kt — single integration point
startKoin {
    appDeclaration()
    modules(
        appModule,
        commonModule,
        dataModule,
        featureModule,   // ← add the new feature module here
    )
}
```

A module that aggregates several sub-modules (or a `platformModule`, Rule 14) composes them with Koin's `includes()`:

```kotlin
val featureModule = module {
    includes(platformModule)               // internal leaf (Rule 14) — pulled in here
    singleOf(::RepositoryImpl).bind<Repository>()
    viewModelOf(::FeatureViewModel)
}
```

**Visibility convention:** the aggregate (`{featurename}Module`, `commonModule`, `dataModule`) is **public** — it's the only module that crosses the module boundary. Leaf/sub-modules composed in via `includes()` (a `platformModule`, or `:core` leaves like `localeModule`/`binder`) are **`internal`** (incl. `internal expect`/`internal actual`). Encapsulation is the documented benefit of `includes()`; only expose the root.

### Strings & Localization (Rule 12)

Every user-facing string is a string resource. No English literals in composables or on `*UiModel`.

**Where strings live** (per-module):

```
feature/{featurename}/src/commonMain/composeResources/
├── values/strings.xml          # default (English) — the source of truth for keys
└── values-{lang}/strings.xml   # one per translation (e.g. values-fa, values-es) — same keys
```

Shared strings (Yes/No/Retry/Cancel, common errors) already live in `:core:designsystem` and are consumed via `DesignSystemResources` — do not duplicate them per feature.

**Key naming**: `{area}_{purpose}` snake_case. Suffix `_template` for format strings, `cd_` for content descriptions, `section_` for headers, `status_` for badges. Examples: `send_title`, `recipient_placeholder`, `balance_amount_template`, `cd_back`, `section_portfolio`, `status_overdue`.

**In composables** — feature-local via the module's generated `Res`:
```kotlin
import {PROJECT_NAMESPACE}.feature.{featurename}.generated.resources.Res
import {PROJECT_NAMESPACE}.feature.{featurename}.generated.resources.send_title
import org.jetbrains.compose.resources.stringResource

XText(text = stringResource(Res.string.send_title))
XText(text = stringResource(Res.string.balance_amount_template, balanceBtc))  // format args
XIcon(contentDescription = stringResource(Res.string.cd_back))                // a11y text too
```

`{PROJECT_NAMESPACE}` is the root segment of the generated-resources package (`kmpilot` in this repo, derived from the app module) — distinct from `{PKG_PREFIX}`, the Kotlin source package. Both are project-specific; substitute your own.

**Strings that originate in a ViewModel** (validation messages, computed labels): ViewModels cannot call the `@Composable stringResource`. Carry them as `UiText` (in `:core:common`) on `*UiModel`, resolve in the composable:
```kotlin
// *UiModel:        val emailError: UiText? = null
// ViewModel:       copy(emailError = UiText.Resource(Res.string.email_required))
// Composable:      uiModel.emailError?.let { XText(it.asString()) }
// Coroutine/suspend context: getString(Res.string.x) or uiText.resolve()
```
`ErrorModel.Resource(...) + ErrorModel.asString()` is the same pattern for errors and already exists.

**Not strings** (leave as literals): control sentinels parsed in logic (e.g. `label == "MAX"`), single-glyph symbols (`$`, `₿`, `✓`, `%`), and repository-supplied data (merchant names, dates, tickers, coin names). Currency-symbol formatting is a data concern, not UI i18n.

**Adding a language**: drop a `values-{lang}/strings.xml` into each module that owns strings (same keys, translated values). The locale mechanism spans three modules: the `LocalAppLocale` CompositionLocal (+ android/ios/desktop actuals) lives in **`:core:designsystem`** (`{PKG_PREFIX}.designsystem.locale`) next to `XTheme`; the selected tag is persisted by **`LanguageRepository`** in **`:core:data`** (see [local-data.md](../create-feature/architecture/local-data.md)); and the app shell glue **`ProvideAppLocale`** (in `composeApp`) feeds that tag into `LocalAppLocale` and recomposes. Drive selection at runtime via `LanguageRepository`; `null` tag = follow system locale. The picker UI is app-specific — each app builds its own.

**Gradle**: feature modules already depend on `libs.compose.components.resources` and have a `composeResources/` dir — no extra config. The generated `Res` is `internal` per module (default); keep it.

### Typography (app-global type scale)

Typography is **app-global, exactly like color roles** — never per-feature. One `FontFamily` + one M3 `Typography` (all 15 roles) live in `XTheme.kt` (`:core:designsystem`), built the canonical CMP way: a `@Composable` `FontFamily(Font(Res.font.x, FontWeight.Y), …)` from the design system's `composeResources/font/`, applied per-role via `MaterialTheme.typography.copy(role = role.copy(fontFamily = …))` and passed to `MaterialTheme(typography = XTypography())`. M3 has **no** `defaultFontFamily` — the family must be set per role.

- **In features, text picks a type-scale role, not a raw size.** Use `style = MaterialTheme.typography.{role}` (or an `XTextDefaults` preset) — e.g. `XText(text = …, style = MaterialTheme.typography.titleLarge)`. Do **not** pass raw `fontSize`/`fontWeight`/`fontFamily` on `XText` unless a design divergence is recorded as an override (mirrors the color-override rule). This is how each Stitch text node maps to an M3 role just as each fill maps to an M3 color role.
- **The font family is one project-global value.** When a Stitch design uses a different typeface than the theme currently ships, that triggers a **one-time global font swap** (download the new `.ttf` set into the design system's `composeResources/font/`, rewire `XFontFamily()` in `XTheme.kt`) — never a per-feature font. The swap is driven by the design pipeline: `/design-ui` detects+records the typeface (`fonts.json` + the blueprint's *Typography Updates Required*), and `/create-feature` / `/modify-feature` materialize it via `.claude/skills/_shared/download_font.py`. Static per-weight `.ttf` → `Font(Res.font.x, FontWeight.Y)`; a variable `[wght].ttf` → `Font(Res.font.x, FontWeight.Y, variationSettings = FontVariation.Settings(FontVariation.weight(n)))`. `/verify-ui` audits both the theme font family and each node's type-scale role.
- **No web target** → no `preloadFont`/`ExperimentalResourceApi`. `Font(Res.font.x)` works as-is on android/ios/desktop.

### Platform Capabilities & Native Views (Rule 14)

When a feature uses a **device capability** (GPS, camera, BLE, biometrics, sensors) or embeds a **native view** (map, camera preview, WebView), the data still flows through Clean Architecture — the capability is just a DataSource, and the native view is an `expect/actual` composable. Full patterns, the decision tree, Gradle deltas, and the iOS-Swift handoff: [create-feature/architecture/platform.md](../create-feature/architecture/platform.md).

**Three patterns:**

1. **Capability as a DataSource** — `commonMain` interface returning `Either<DTO>`, one `actual` class per target (android/ios/**desktop**). Repository delegates and returns `Either<DTO>` unchanged (Rule 11). ViewModel can't tell GPS from HTTP.
   ```kotlin
   interface LocationDataSource { suspend fun current(): Either<LatLng> }   // commonMain
   ```
2. **DI via `internal expect/actual val platformModule`** — platform `actual` classes can't be bound in the common Koin module; bind them in a per-target `internal actual val platformModule` and pull it into the feature's module with `includes(platformModule)` inside `{featurename}Module` (the `platformModule` stays `internal` — only `{featurename}Module` is public).
3. **Native view via `expect @Composable`** (Shape C) — `PlatformX()` in `commonMain` with `AndroidView` / `UIKitView` / desktop-fallback actuals under `components/`. `{Feature}Content` stays pure Compose and passes only DTOs + callbacks.

**Sourcing precedence** (the one judgment call — gate with `AskUserQuestion`): multiplatform lib (single `commonMain` dep) > `expect/actual` in the feature > iOS-Swift bridge (`/bridge-swift`, the iOS leg only). Permissions are their own capability, not buried in the map/camera one.

**Hard constraint**: targets are android + ios + `jvm("desktop")` — provide an `actual` for **all three** (desktop = graceful fallback) or the build breaks.

**Who writes what** (create-feature Phase 4): `platform` writes the DataSource interface + per-platform actuals + `platformModule` (provider-only, no composables); `ui-layer` writes the `expect @Composable` native-view interop; `integrator` adds `platformModule` to the feature's module list.

### Motion (animation)

Animation is **captured from the Stitch design**, never injected. Press/hover feedback (touch `active:*`, `ripple`, and pointer `hover:*`/`group-hover:*`) is **dropped** — primary targets are android + ios. The 4 non-interaction families (Ambient bg, Loading/Attention loop, Entrance, Value-driven) plus `prefers-reduced-motion` are kept and implemented in **dedicated motion files** — generic primitives in `core/designsystem/.../motion/`, feature-specific wiring in `feature/{name}/.../presentation/ui/motion/{Feature}Motion.kt` — never inline in `Screen.kt`/components. Three discipline rules mirror the color-role rule: **durations/easings** flow through `XMotion` tokens (no ad-hoc `tween(<literal>)`); **magnitudes** (scale/translate/opacity ranges) are copied from the design's captured keyframes, never invented; **`rememberReducedMotion()`** is an `expect/actual` reading the OS setting (not a stub). Animation imports (`androidx.compose.animation.*`, `animation.core.*`, `foundation.interaction.*`) are **not** Material3 (not a Rule-5 violation). Full policy, family→Compose mapping, easing map, reduced-motion gate, and file layout: [`_shared/motion.md`](motion.md).

## Module Dependencies

| Feature depends on | When |
|--------------------|------|
| `:core:common` | Always (Either, UiState, setState, ErrorModel) |
| `:core:designsystem` | Always (X-components) |
| `:core:data` | Only if using ApiClient |

**Features NEVER depend on other features.**

### Module Roles (app-global state)

Persisted app-state and the app-global UI runtime context are split across modules by role. Keep
each module to its role — do not re-introduce state holders into `:core:common`.

| Module | Role |
|--------|------|
| `:core:common` | **Primitives only** — Either, UiState, UiText, ext, util, LinkHandler. **No** state holders or platform stores. Project/example value types live in the `common.app` tier (empty in a baseline project). |
| `:core:designsystem` | UI primitives + app-global UI runtime context: `XTheme(darkTheme: Boolean)` and the `LocalAppLocale` CompositionLocal (+ per-platform actuals). Project composites/state screens live in the `designsystem.app` tier. |
| `composeApp` | App-shell glue: maps `ThemeRepository.themeMode` → `darkTheme`; `ProvideAppLocale` feeds `LanguageRepository`'s tag into `LocalAppLocale`. |
| `:core:data` | Owns all persisted state — per-concern `Repository` + `LocalDataSource` over a shared backend. Universal app-state (theme/locale/token) is generic; project/domain state lives in the `data.app` tier. See [local-data.md](../create-feature/architecture/local-data.md). |

### Core Module Tiers (generic vs `app/`)

The same **generic vs `app/`** two-tier split applies to **every core library module** that holds
project-specific content — `:core:designsystem`, `:core:data`, and `:core:common`. One package
convention, one boundary rule (generic never imports `.app`), one strip seam per module:

| Module | Generic tier | `app/` tier (stripped) |
|--------|--------------|------------------------|
| `:core:designsystem` | `X*` primitives, `XTheme` | `designsystem.app` — `App*` state screens (content-free, kept), project composites + brand drawables (stripped) |
| `:core:data` | backends/network infra + theme/locale/token | `data.app` — **all** project/domain data: persisted state **and shared remote** (cross-feature endpoints/wire models + `{Shared}RemoteDataSource`); strip seam = `appDataModule` include in `DataModules.kt` |
| `:core:common` | Either/UiState/UiText/ext/util primitives | `common.app` — project/example value types; empty in a baseline project |

**DRY corollary (`:core:data`):** a remote endpoint or wire model used by **≥2 features** is shared
app data → it lives **once** in `data.app` (canonical superset DTO + one `{Shared}RemoteDataSource`),
never duplicated per feature. Features inject the shared datasource and keep only their own
wire-DTO→presentation-DTO mapping. Single-feature remote stays in the feature. See
[data.md → "Shared remote data"](../create-feature/architecture/data.md).

`:core:designsystem` is the reference implementation:

- **Generic primitives** — `{PKG_PREFIX}.designsystem.*` (`XButton`, `XText`, `XScreen`, `Placeholder`, `XTheme`, …). Publish-clean; ship to every downstream project.
- **Project `app` tier** — `{PKG_PREFIX}.designsystem.app`. The project's own composed UI: the shared **`AppLoadingState`/`AppErrorState`** state screens (content-free — copy + navigation are caller params) plus the project's example/domain composites and brand drawables. `install.sh` strips the example/domain content + brand drawables for downstream but **keeps** the content-free `App*` state screens (each project redesigns them via the design pipeline). All shared strings (including project-specific, cross-feature) live in the single DS `strings.xml` via `DesignSystemResources` — there is no separate string tier.

**Boundary rule (all three modules):** dependencies flow **`app/` → generic only**. Generic (root) code must **never** import its module's `.app` tier — `{PKG_PREFIX}.designsystem.app`, `{PKG_PREFIX}.data.app`, or `{PKG_PREFIX}.common.app` — because a generic file that imports the `app` tier breaks the build once that tier is stripped/neutralized. (Features may import any `app` tier — they depend on the whole module.) It's a package convention; enforce it with a boundary check (grep for `{PKG_PREFIX}.{designsystem,data,common}.app` in generic files), a git hook, or your reviewer. **One sanctioned exception:** the `:core:data` aggregate `DataModules.kt` references `appDataModule` (the strip seam) — the boundary grep excludes that single file.

**Shared state UI:** features render `UiState.Loading` → `AppLoadingState()` and `UiState.Failed` → `AppErrorState(title, message, onRetry, secondaryAction = …)` from `{PKG_PREFIX}.designsystem.app`. They do **not** define per-feature `LoadingContent`/`FailedContent`. Feature copy (`error_title`/`error_message`) is passed as params; the retry label defaults to `DesignSystemResources.string.retry_label`. **Empty/Uninitialized stays per-feature** (empty content varies screen to screen).

## Feature Module Structure

```
{PKG_PREFIX}.{featurename}/
├── data/
│   ├── model/           # @Serializable DTOs
│   ├── remote/          # Ktor Resources
│   ├── datasource/      # Interface + Impl
│   └── repository/      # Interface + Impl
├── presentation/
│   ├── {Feature}ViewModel.kt
│   ├── {Feature}UiModel.kt      # Single state container: plain fields + UiState<DTO> slots
│   ├── ui/
│   │   ├── {Feature}Screen.kt   # allowlist only — see "UI File Organization"
│   │   ├── {Feature}Utils.kt    # Optional — formatters, validators (non-@Composable)
│   │   └── components/          # One file per @Composable component (incl. {Feature}Content.kt)
│   └── navigation/      # Routes + NavGraphBuilder
└── di/
    └── {Feature}Modules.kt
```

Resources are a sibling of `kotlin/` under the same source set (not in the package tree):

```
feature/{featurename}/src/commonMain/
├── kotlin/...                       # the tree above
└── composeResources/
    ├── values/strings.xml           # Rule 12 — feature strings (default/English)
    ├── values-{lang}/strings.xml    # translations (same keys), e.g. values-fa
    └── drawable/                     # feature-local icons/images
```

### UI File Organization

`{Feature}Screen.kt` has a **fixed allowlist of composables**. Nothing else is allowed at file scope. This is a structural rule, not a judgment call.

**`{Feature}Screen.kt` allowlist (top-level `@Composable fun`):**

| # | Name | Visibility | Required? |
|---|------|------------|-----------|
| 1 | `{Feature}Screen` | public | **Always** — ViewModel wrapper |
| 2 | `{Feature}ScreenRoot` | public | **Always** — owns state routing; renders `XScreen(topBar = …, bottomBar = …)`, never a `Scaffold`/`XScaffold` (Rule 13) |
| 3 | `EmptyContent` | private | **Optional** — only if the design specifies a dedicated empty/uninitialized screen |

**Loading and Failed are NOT per-feature shells.** `{Feature}ScreenRoot` routes `UiState.Loading` → `AppLoadingState()` and `UiState.Failed` → `AppErrorState(title, message, onRetry, secondaryAction = …)`, both from `{PKG_PREFIX}.designsystem.app` (the shared, one-per-project state UI — see "Design System Tiers"). Feature copy (`error_title`/`error_message`) is passed as params; the retry label defaults to `DesignSystemResources.string.retry_label`. Never define a private `LoadingContent`/`FailedContent`.

The optional `EmptyContent` shell (3) is present only when the design calls for a dedicated empty/uninitialized screen — empty content is feature-specific, so it is **not** unified. A screen that renders empty inline inside `{Feature}Content` does not introduce it. Never add a state shell the design does not require.

**Everything else lives under `presentation/ui/components/`, one file per component:**

- `{Feature}Content.kt` — the success-state composable (Shape A) or the always-mounted form composable (Shape B). **Always its own file; never inlined into `Screen.kt`.**
- One file per sub-component reachable from `{Feature}Content`, no matter how small.
- One file per component reachable from `EmptyContent` (rare — it usually contains only X-components).
- A component's private helpers and private sub-composables stay in the **same file** as that component — they are not promoted to new files.
- **Native-view interop (Shape C, Rule 14)**: an `expect @Composable PlatformX` plus its `.android`/`.ios`/`.desktop` actuals each live one-concept-per-file under `components/` (in their respective source sets). They are exempt from the "commonMain only" reading of this rule — by design they have per-platform siblings. `{Feature}Content` calls `PlatformX()` and otherwise stays pure Compose. See [architecture/platform.md](../create-feature/architecture/platform.md) → "Pattern C".

**Enforcement**: any top-level `@Composable fun` defined in `{Feature}Screen.kt` outside the 3-name allowlist (`{Feature}Screen`, `{Feature}ScreenRoot`, optional `EmptyContent`) is a violation — **except** for `@Preview`-annotated composables (see "Previews" below). A private `LoadingContent`/`FailedContent` is itself a violation: route to the shared `AppLoadingState`/`AppErrorState` instead. The reviewer / lint check is a simple grep for `@Composable fun` at file scope in `Screen.kt` against the allowlist; `@Preview`-annotated entries are exempt.

### Secondary Screens within a Feature

A feature may own — besides its primary screen — one or more **secondary screens**, designed together in a single `/design-ui` invocation (one feature per invocation) and recorded in `stitch-project.json.features[{featurename}].secondaryScreens[]`, each tagged with a **`kind`**. They are part of the **same feature module** — features still never depend on other features, and the **4 Integration Points are unchanged** (a secondary screen is internal to the feature, not a new module or nav-host registration).

**`kind: surface`** — an **overlay** (bottom sheet, dialog, modal, drawer, panel):
- Rendered from `{Feature}ScreenRoot` via the role's host X-component (`bottomsheet` → `XModalBottomSheet`, `dialog`/`modal` → `XDialog`/`XAlertDialog`, `drawer` → `XModalDrawerSheet`, `panel` → inline `XSurface`).
- Visibility is a plain field on `{Feature}UiModel` (e.g. `val {role}Visible: Boolean = false`); open/close via **callbacks** hoisted to the ViewModel (Rule 10). **Not** a navigation destination — no Route.
- Its content composable is its own file under `presentation/ui/components/`. It adds **no** entry to the `Screen.kt` allowlist (the host is invoked inside `ScreenRoot`/`Content`, not declared at `Screen.kt` file scope).

**`kind: screen`** — a **full sibling/child screen** of the same feature (e.g. profile → edit-profile):
- A complete screen mirroring the primary's structure: its **own** `{Feature}{Role}Screen.kt` (with its **own** 3-name allowlist: `{Feature}{Role}Screen` + `{Feature}{Role}ScreenRoot` [+ optional `EmptyContent`]) and its own `components/` files. Uses its **own `XScreen`** (Rule 13 — never a nested Scaffold).
- Gets its **own `{Feature}{Role}Route`** and a `composable<{Feature}{Role}Route> { … }` registered **inside the same `NavGraphBuilder.{featurename}()` extension** as the primary (one feature nav extension, multiple child routes). The primary navigates to it via a **hoisted callback** (Rule 10 — e.g. `onEditClick`), wired in the feature's nav graph; screens never receive a `navController`.
- It is a **child route of this feature**, not a separate feature and not Integration Point 5 (that's only for top-level bottom-bar tabs).

**Allowlist scope**: the `Screen.kt` allowlist is enforced **per `*Screen.kt` file** — each child screen's `{Feature}{Role}Screen.kt` carries its own independent 3-name allowlist. `Naming`: child screen types follow the primary's conventions with the role inserted — `{Feature}{Role}Screen`, `{Feature}{Role}ScreenRoot`, `{Feature}{Role}Route`, `NavGraphBuilder.{featurename}{role}()` (or extend the single `{featurename}()` extension with the extra `composable<…>`).

**Picking the screen shape**: see [architecture/ui.md → "Screen Shapes"](../create-feature/architecture/ui.md) — Shape A (data-fetch), Shape B (form), Shape C (native-view host, Rule 14). Shape choice affects which **optional** slots are present in `Screen.kt`, but never changes the file layout under `components/`. Deviation from Shape A must be recorded in the feature's spec under Design Decisions.

**Fragment-composable rule**: a component that emits ≥2 top-level siblings MUST own its enclosing `Column`/`Row` — never emit bare siblings and rely on the caller's layout. A `Box` caller z-stacks all children; a fragment composable passed to a `Box` merges all rows into one. Apply `modifier` to the wrapping `Column`, not to the first child. Full rule + grep hint: [architecture/ui.md → "Fragment-composable rule"](../create-feature/architecture/ui.md). A composable emitting ≥2 top-level siblings must own its enclosing `Column`/`Row` (Box z-stacks — never use Box as a multi-child container wrapper).

### Utility Functions (non-`@Composable`)

Pure helpers like formatters, validators, and mappers are **not composables** and do not go under `components/`. They live at the same level as `Screen.kt`:

```
presentation/ui/
├── {Feature}Screen.kt
├── {Feature}Utils.kt          ← formatters, validators, computed-display helpers
└── components/                ← composables only
```

`components/` contains only `@Composable` declarations. A `fun formatBalance(amount: Long): String` does not belong there.

### Previews (`@Preview` composables)

**Import**: `androidx.compose.ui.tooling.preview.Preview` — available from `commonMain` as of Compose Multiplatform 1.11.0. Do **not** use the deprecated `org.jetbrains.compose.ui.tooling.preview.Preview`.

**Placement**: `@Preview`-annotated composables live in the **same file** as the composable they preview, marked `private`. They are exempt from the `Screen.kt` allowlist and from the "one file per `@Composable`" rule.

```kotlin
// In components/BalanceCard.kt
@Composable
fun BalanceCard(balance: String, currency: String) { /* ... */ }

@Preview
@Composable
private fun BalanceCardPreview() {
    XTheme { BalanceCard(balance = "1,250.00", currency = "USD") }
}
```

**`@PreviewParameter`**: supported in `commonMain` as of CMP 1.11.0. Use a `PreviewParameterProvider` for multi-variant previews (light/dark, edge cases, long strings).

**Dependencies** (per feature module): add to `commonMain` and Android runtime classpath:

```kotlin
// feature/{featurename}/build.gradle.kts
sourceSets {
    commonMain {
        dependencies {
            implementation(libs.compose.ui.tooling.preview)
            // ...
        }
    }
}

dependencies {
    androidRuntimeClasspath(libs.compose.ui.tooling)  // for AS preview renderer
}
```

Both aliases already exist in `libs.versions.toml` (`compose-ui-tooling-preview`, `compose-ui-tooling`).

## Build Commands

```bash
./gradlew :feature:{featurename}:assembleAndroidMain  # Incremental (fast)
./gradlew assembleDebug                         # Full build
./gradlew :feature:{featurename}:ktlintFormat          # Format
./gradlew :feature:{featurename}:desktopTest           # Tests
./gradlew archTest                              # Architecture rules (fails on any error)
```

`archTest` wraps the checker; run it directly for one feature or in the pre-adoption tier:

```bash
python3 .claude/skills/_shared/kmpilot_check.py {featurename}
python3 .claude/skills/_shared/kmpilot_check.py --all --baseline   # errors → warnings, exit 0
```

## Hook Marker Contract

A PreToolUse hook (`.claude/hooks/protect-feature-files.sh`, registered in `.claude/settings.json`) blocks direct `Edit`/`Write` on files under `feature/` unless a skill is active.

| Aspect | Value |
|--------|-------|
| Marker file | `/tmp/.claude-kmpilot-skill-active` |
| Activation | `touch /tmp/.claude-kmpilot-skill-active` before editing feature files |
| Cleanup | `rm -f /tmp/.claude-kmpilot-skill-active` after completion or early exit |
| Staleness | Marker auto-expires after 2 hours; hook removes stale markers |
| Bypassed paths | `*/commonTest/*`, `*/desktopTest/*`, `*/androidTest/*`, `*/test/*`, any `build.gradle.kts` |

**Initialization gate (checked before the marker).** If the project has neither
`.kmpilot.json` nor `core/common`, the hook refuses every `feature/` write regardless of
the marker — an active skill cannot bypass it. A repo can end up with the skills but
without the `core/` modules they generate against, and a feature written there would
import `Either`/`UiState`/`X*` from modules that do not exist. Fix: run
`install.sh --adopt` from the repo root (see `ADOPTING.md`). A project created by
`install.sh` through either door already satisfies this.

**Skills that activate the marker:** `/create-feature`, `/modify-feature`. Both skills' allowlists include `Bash(touch:*)` and `Bash(rm -f /tmp/.claude-kmpilot-skill-active)`. Test agents write test files directly (bypassed by path rule), so they do NOT need the marker.

If the hook blocks an edit, message shown: *"Blocked: Cannot edit feature source files directly. Use /create-feature or /modify-feature skill first."*
