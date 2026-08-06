---
description: Review a KMP feature against architecture patterns and spec
allowed-tools: ["Task", "Read", "Glob", "Grep", "Write", "Bash(python3 .claude/skills/_shared/kmpilot_check.py:*)", "Bash(./gradlew archTest:*)"]
---

# Review Feature Implementation

Review a KMP feature against Clean Architecture, 14 critical rules, and 4 integration points.

**Architecture Reference:** @../skills/_shared/patterns.md

## Usage

```bash
/review-feature {featurename}
```

## Process

1. **Validate**: `ls feature/{featurename}/src/commonMain/kotlin/`
2. **Run the deterministic checker first** — the mechanized rules are settled by a script,
   not by a model:
   ```bash
   python3 .claude/skills/_shared/kmpilot_check.py {featurename}
   ```
   Read `.claude/docs/_project/check-report.json` and report its violations **verbatim**
   (`severity: error` → Critical P1, `severity: warning` → Warning P2). Do **not** re-derive
   them by grepping.
3. **Spawn Agent**: Delegate the remaining judgment rules to the `code-reviewer` agent
4. **Generate Reports**: `.claude/docs/{featurename}/review.md` and `fixes.md`

The same checks run in CI as `./gradlew archTest`, which fails the build on any
`error`-severity violation. A review and a CI run cannot disagree.

## What Gets Checked

### Mechanized — from `check-report.json` (20 checks, reproducible)

| ID | Rule |
|---|---|
| `R3` | setState — no `_uiModel.value =` |
| `R5` | X-components — no Material3 *component* imports (`MaterialTheme`/`Shapes`/`darkColorScheme`/`lightColorScheme` are allowed; `XTheme` wraps MaterialTheme) |
| `R7` | Lowercase packages |
| `R8` | DI — a top-level `val {featurename}Module` + `.bind<Interface>()` |
| `R9` | No UseCases |
| `R11a` `R11b` `R11c` | No `*UiState.kt`; exactly one `presentation/*UiModel.kt`; no `presentation` import under `data/` |
| `R12` | No hardcoded user-facing strings in `presentation/ui`, and `composeResources/values/strings.xml` exists. Allowlisted: `@Preview` fixtures, control sentinels, single-glyph symbols (`$`/`₿`/`%`/`✓`), interpolated repository data, Compose animation `label =` debug tags |
| `R13` | Single app-shell Scaffold — no `Scaffold`/`XScaffold` and no `contentWindowInsets`/`safeDrawing`/`statusBarsPadding`/`imePadding` in feature UI; plain `navigationBarsPadding()` on a sticky bottom bar warns (use the `exclude(ime)` form) |
| `S1` | `Screen.kt` `@Composable` allowlist, enforced per `*Screen.kt` file (`@Preview` exempt) |
| `S2` | No non-composable file under `components/` |
| `S3` | `.app`-tier boundary in generic core code (`core/data/**/DataModules.kt` exempt) |
| `S4` | Preview import is `androidx.compose.ui.tooling.preview.Preview` |
| `I1`–`I4` | The 4 integration points: `settings.gradle.kts`, `composeApp/build.gradle.kts`, `initKoin.kt`, `BaseAppNavHost.kt` |

### Judgment — reviewed by the agent (what no grep can settle)

1. **Rule 1** Interface + Impl pairs — *exception:* a feature using a **shared `data.app` datasource** (cross-feature remote) owns no per-feature datasource; an empty/absent `datasource/` is correct (its repo injects `{PKG_PREFIX}.data.app.*`)
2. **Rule 2** `Either<T>` — which operations are genuinely fallible
4. **Rule 4** All four UI states *genuinely* handled (an empty `when` branch passes any grep)
6. **Rule 6** ImmutableList in the UiModel
10. **Rule 10** Callback parameters, no `navController` in a screen
11. **Rule 11 (semantic)** `UiState<T>`'s `T` is a `data/model/` DTO or `Unit`; `RepositoryImpl` returns `Either<DTO>`; the ViewModel exposes `StateFlow<{Feature}UiModel>`
12. **Rule 12 (semantic)** `*UiModel` carries `UiText`/`StringResource`, never English literals
14. **Rule 14** Platform capability / native view — only when Platform Profile is `platform-capability`/`native-view`/`mixed` (N/A if `network` or field absent): capability behind a `commonMain` DataSource → `Either<DTO>` with actuals for **all** targets incl. desktop; native view via `expect @Composable` (`AndroidView`/`UIKitView`) under `components/`; `platformModule` (expect/actual) pulled into `{featurename}Module` via `includes(platformModule)`; no platform types in ViewModel/Repository

### Spec Compliance (if spec exists)
- Data Models, Interfaces, State, Navigation

### Design-Aware Compliance (if blueprint exists)
- Blueprint present at `.claude/docs/{featurename}/designs/{featurename}_blueprint.md`
- `blueprintConsumed: true` in `.claude/docs/_project/stitch-project.json` under `features.{featurename}`
- A `false` flag with a blueprint present means implementation skipped the design pipeline

### UI File Organization
- `{Feature}Screen.kt` allowlist (nothing else): `{Feature}Screen`, `{Feature}ScreenRoot`, and optionally `EmptyContent` — Loading/Failed must route to the shared `AppLoadingState`/`AppErrorState` (`{PKG_PREFIX}.designsystem.app`), never private shells; `EmptyContent` appears only when the design specifies a dedicated empty screen
- Every other composable, **including `{Feature}Content`** and its sub-components, lives in `presentation/ui/components/{Name}.kt` — one file per component
- **Utilities** (non-`@Composable` helpers: formatters, validators) live in `presentation/ui/{Feature}Utils.kt`, never under `components/`
- **`@Preview` composables** live in the **same file** as the composable they preview (marked `private`), and are exempt from the allowlist
- Preview import must be `androidx.compose.ui.tooling.preview.Preview` (CMP 1.11.0+); the older `org.jetbrains.compose...` is deprecated
- Reference: `patterns.md` "UI File Organization" section

## Output

| Status | Meaning |
|--------|---------|
| **PASS** | All rules and integrations pass |
| **PASS WITH WARNINGS** | Minor issues, non-blocking |
| **FAIL** | Critical violations found |

## After Review

Reports are saved to:
- `.claude/docs/{featurename}/review.md` — full review
- `.claude/docs/{featurename}/fixes.md` — actionable fixes

Optional: run `/audit-spec {featurename} --compare` to check spec drift.

Pick the matching literal footer based on the review status and emit it as the very last line of output.

**If status is PASS:**

```
---

> **Next step —** run `/clear` to free the context window, then `/test-feature {featurename}` to generate comprehensive tests for the feature.
```

**If status is PASS WITH WARNINGS or FAIL:**

```
---

> **Next step —** run `/clear` to free the context window, then `/modify-feature {featurename} apply fixes from @.claude/docs/{featurename}/fixes.md` to address the review findings.
```
