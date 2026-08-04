#!/bin/bash
# reinject-on-compact.sh
# Re-injects critical architecture rules after context compaction.
# Used as a SessionStart hook with "compact" matcher.
#
# The heading is overridable via KMPILOT_RULES_HEADING so another hook can reuse
# this digest without the text claiming a compaction that did not happen. The
# rules themselves are defined once, here.

# Clean up stale skill marker on compaction
rm -f /tmp/.claude-kmpilot-skill-active

printf '## %s\n' "${KMPILOT_RULES_HEADING:-Critical Architecture Rules (Re-injected After Compaction)}"

cat <<'RULES'

**14 Rules:**
1. Interface + Impl pairs for DataSource/Repository
2. Either<T> for errors - NEVER throw exceptions
3. setState { copy() } - NEVER _uiModel.value =
4. 4 UI states: Uninitialized / Loading / Success / Failed
5. X-components from :core:designsystem - NO Material3
6. ImmutableList with .toImmutableList()
7. Lowercase packages only
8. DI: top-level val {featurename}Module = module { singleOf(::Impl).bind<Interface>() } - no BaseFeature/registry
9. No UseCases - ViewModels call repositories directly
10. Callback params (onBackClick) - not navController
11. Single *UiModel + DTO-wrapped UiState<T> - NO *UiState.kt; data/ never imports presentation/
12. No hardcoded user-facing strings - stringResource(Res.string.*)/DesignSystemResources; ViewModel strings via UiText
13. Single app-shell Scaffold - feature screens use XScreen, never Scaffold/XScaffold; shell owns window insets
14. Platform capability = commonMain DataSource -> Either<T> (actuals for android/ios/desktop); native view = expect @Composable; DI via includes(platformModule)

**4 Integration Points (all required):**
1. settings.gradle.kts - include(":feature:{name}")
2. composeApp/build.gradle.kts - implementation(project(":feature:{name}"))
3. initKoin.kt - add {featurename}Module to modules(...)
4. BaseAppNavHost.kt - {featurename}(onBackClick = {...})

**Mandatory Workflow:** NEVER edit feature/ files directly. Use /create-feature or /modify-feature.
RULES

# Emitted outside the quoted heredoc, which must stay quoted — the digest above
# is full of backticks that an unquoted heredoc would treat as command
# substitution. The `@` prefix asks Claude Code to inline the file.
printf '\nFull patterns: @%s\n' ".claude/skills/_shared/patterns.md"
