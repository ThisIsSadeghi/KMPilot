#!/usr/bin/env bash
# Bootstrap a new KMPilot project — interactive, colorized installer.
#
# Engine (clone → trim → rename → fresh docs → manifest → git init) with an
# interactive, colorized presentation layer: a banner, colored step log, a
# clone spinner, and prompts for the project name / package when you don't
# pass them as arguments.
#
# Usage (remote):
#   curl -fsSL https://raw.githubusercontent.com/ThisIsSadeghi/KMPilot/main/install.sh \
#     | bash -s <ProjectName> [package.prefix]
#   # or run with no args for the guided prompts:
#   curl -fsSL https://raw.githubusercontent.com/ThisIsSadeghi/KMPilot/main/install.sh | bash
#
# Usage (local):
#   ./install.sh <ProjectName> [package.prefix]
#   ./install.sh                 # guided prompts
#
# Adopt mode — install the pipeline INTO a KMP repo you already have, instead of
# generating a new project. Run it from that repo's root:
#   ./install.sh --adopt --dry-run   # print the compatibility report + file plan
#   ./install.sh --adopt             # vendor core/, wire it up, write .claude/
#   ./install.sh --adopt --force     # re-run over an existing adoption (idempotent)
#   ./install.sh --adopt --app-module=shared   # name the app module explicitly
#                                    # (detection asks when it cannot tell)
#
# Installs from the latest published vX.Y.Z release tag by default (reproducible).
# The new project keeps a ./update.sh you can run later to pull future releases
# without clobbering your code (see update.sh).
#
# Env vars:
#   KMPILOT_TEMPLATE_REPO   Git URL of the template (default: ThisIsSadeghi/KMPilot)
#   KMPILOT_TEMPLATE_BRANCH Branch or tag to install from (default: latest release
#                           tag; set to "main" for the bleeding edge)
#   NO_COLOR                Set to any value to disable colored output.
#   KMPILOT_ASSUME_YES      Set to 1 to skip the confirmation prompt.
#   KMPILOT_NONINTERACTIVE  Set to 1 to never prompt, even when a terminal exists.
#                           Anything that would be asked becomes a refusal instead.
#   KMPILOT_SOURCE_DIR      --adopt only: stage from a local KMPilot checkout instead
#                           of cloning a release (for testing an unreleased installer).

set -euo pipefail

TEMPLATE_REPO="${KMPILOT_TEMPLATE_REPO:-https://github.com/ThisIsSadeghi/KMPilot.git}"
# Resolved after the git check below: defaults to the latest vX.Y.Z release tag
# (reproducible installs); override with KMPILOT_TEMPLATE_BRANCH=main for bleeding edge.
TEMPLATE_BRANCH="${KMPILOT_TEMPLATE_BRANCH:-}"
# Stamped to a release tag (vX.Y.Z) by .github/workflows/release.yml when it uploads
# this file as a release asset — so a released installer clones the EXACT tag it shipped
# with (script and template tree are the same release; no drift). Left as the placeholder
# on main, so a raw-main run falls through to "resolve the latest published tag" below.
PINNED_TAG="__KMPILOT_PINNED_TAG__"

# ─────────────────────────────────────────────────────────────────────────────
# Presentation helpers (color, tty, logging)
# ─────────────────────────────────────────────────────────────────────────────

# Colors on only when stdout is a real terminal and NO_COLOR is unset. Under
# curl | bash, stdin is the script but stdout is still the terminal, so this
# stays true and the output is colored.
if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
    RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
    BLUE=$'\033[34m'; MAGENTA=$'\033[35m'; CYAN=$'\033[36m'
    BORDER=$'\033[38;5;39m'   # vivid azure for the banner frame
else
    BOLD=""; DIM=""; RESET=""
    RED=""; GREEN=""; YELLOW=""; BLUE=""; MAGENTA=""; CYAN=""; BORDER=""
fi

# Interactive prompts read from the controlling terminal, not stdin — so the
# guided flow works even when the script itself arrives on stdin (curl | bash).
# Readable is not enough: under CI, a nested subshell, or `bash <script` the
# device can open for read and still fail on write with "Device not configured",
# which under `set -e` aborts the run mid-prompt. Probe an actual write.
#
# KMPILOT_NONINTERACTIVE=1 forces the no-terminal path even when /dev/tty is
# openable. Capturing stdout is not enough to make a run non-interactive — a
# script launched from a terminal still has a controlling tty — so the adopt
# matrix, which asserts what happens when nobody can be asked, needs to say so
# explicitly or it blocks on the first prompt when run by hand.
if [[ "${KMPILOT_NONINTERACTIVE:-0}" == "1" ]]; then
    TTY=""
elif { true > /dev/tty; } 2>/dev/null; then
    TTY=/dev/tty
else
    TTY=""
fi

STEP_NO=0
step() {   # numbered top-level step
    STEP_NO=$((STEP_NO + 1))
    printf '%s%s[%s]%s %s%s\n' "$BOLD" "$CYAN" "$STEP_NO" "$RESET" "$BOLD" "$1$RESET"
}
substep() { printf '    %s%s›%s %s\n' "$DIM" "$BLUE" "$RESET" "$1"; }
ok()      { printf '    %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()    { printf '    %s⚠%s %s\n' "$YELLOW" "$RESET" "$1"; }
die()     { printf '\n%s✗ %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

banner() {
    printf '\n'
    printf '%s%s  ╭───────────────────────────────────────────────╮%s\n' "$BOLD" "$BORDER" "$RESET"
    printf '%s%s  │%s  %s%sKMPilot%s  %s·%s  Kotlin Multiplatform scaffolder  %s%s│%s\n' \
        "$BOLD" "$BORDER" "$RESET" "$BOLD" "$CYAN" "$RESET" "$DIM" "$RESET" "$BOLD$BORDER" "" "$RESET"
    printf '%s%s  ╰───────────────────────────────────────────────╯%s\n' "$BOLD" "$BORDER" "$RESET"
    printf '%s     an Android + iOS app from a handful of Claude Code commands%s\n\n' "$DIM" "$RESET"
}

# Run a command with a braille spinner. Captures the child exit code safely
# under `set -e` (a bare failing `wait` would abort before we could read $?).
spinner() {
    local msg="$1"; shift
    if [[ ! -t 1 ]]; then          # non-terminal: no animation, just run
        "$@"; return $?
    fi
    "$@" &
    local pid=$! frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0 rc=0
    tput civis 2>/dev/null || true # hide cursor
    while kill -0 "$pid" 2>/dev/null; do
        printf '\r    %s%s%s %s' "$CYAN" "${frames:i++%${#frames}:1}" "$RESET" "$msg"
        sleep 0.08
    done
    if wait "$pid"; then rc=0; else rc=$?; fi
    tput cnorm 2>/dev/null || true # restore cursor
    printf '\r\033[K'              # clear the spinner line
    return $rc
}

# ─────────────────────────────────────────────────────────────────────────────
# Input: arguments, then guided prompts for anything missing
# ─────────────────────────────────────────────────────────────────────────────

command -v git >/dev/null 2>&1 || die "git is required but was not found on PATH."

# Cross-platform sed -i (BSD/macOS vs GNU/Linux). Defined up here because both
# the template flow and adopt mode need it.
if [[ "$(uname -s)" == "Darwin" ]]; then
    sedi() { sed -i '' "$@"; }
else
    sedi() { sed -i "$@"; }
fi

ADOPT=no
DRY_RUN=no
FORCE=no
APP_MODULE_ARG=""
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --adopt)        ADOPT=yes ;;
        --dry-run)      DRY_RUN=yes ;;
        --force)        FORCE=yes ;;
        --app-module=*) APP_MODULE_ARG="${arg#*=}" ;;
        -h|--help) sed -n '2,35p' "$0"; exit 0 ;;
        -*)        die "unknown option '$arg' (try --help)" ;;
        *)         POSITIONAL+=("$arg") ;;
    esac
done
if [[ "$ADOPT" == "no" ]]; then
    [[ "$DRY_RUN" == "no" ]] || die "--dry-run is only meaningful with --adopt."
    [[ "$FORCE"   == "no" ]] || die "--force is only meaningful with --adopt."
fi

NAME="${POSITIONAL[0]:-}"
PKG_ARG="${POSITIONAL[1]:-}"

# Validators (kept permissive but enough to avoid a broken rename downstream).
valid_name() { [[ "$1" =~ ^[A-Za-z][A-Za-z0-9]*$ ]]; }
valid_pkg()  { [[ "$1" =~ ^[a-z][a-z0-9]*(\.[a-z][a-z0-9]+)+$ ]]; }

# Resolve the install ref, in priority order:
#   1. KMPILOT_TEMPLATE_BRANCH — explicit override (e.g. =main for bleeding edge)
#   2. PINNED_TAG              — stamped into this script by the release workflow
#   3. newest published vX.Y.Z — for raw-main runs (unstamped script)
#   4. main                    — last resort when the repo has no tags yet
resolve_template_ref() {
    [[ -z "$TEMPLATE_BRANCH" ]] || return 0
    if [[ "$PINNED_TAG" != '__KMPILOT_PINNED_TAG__' && -n "$PINNED_TAG" ]]; then
        TEMPLATE_BRANCH="$PINNED_TAG"
    else
        TEMPLATE_BRANCH="$(git ls-remote --tags --refs --sort=-v:refname "$TEMPLATE_REPO" 'v*' 2>/dev/null \
            | head -n1 | sed -E 's#.*refs/tags/##')"
        TEMPLATE_BRANCH="${TEMPLATE_BRANCH:-main}"
    fi
}

prompt_name() {
    [[ -n "$TTY" ]] || die "No project name given and no terminal to prompt on.
Pass one:  install.sh <ProjectName> [package.prefix]"
    local ans
    while :; do
        printf '%s?%s %sProject name%s %s(PascalCase, e.g. MyStore)%s: ' \
            "$CYAN" "$RESET" "$BOLD" "$RESET" "$DIM" "$RESET" > /dev/tty
        read -r ans < "$TTY" || die "Aborted."
        if valid_name "$ans"; then NAME="$ans"; return; fi
        warn "Use letters and digits only, starting with a letter."
    done
}

prompt_pkg() {
    local default="dev.kmpilot.$(echo "$NAME" | tr '[:upper:]' '[:lower:]')"
    if [[ -n "$PKG_ARG" ]]; then PKG="$PKG_ARG"; return; fi
    if [[ -z "$TTY" ]]; then PKG="$default"; return; fi
    local ans
    while :; do
        printf '%s?%s %sPackage prefix%s %s[%s]%s: ' \
            "$CYAN" "$RESET" "$BOLD" "$RESET" "$DIM" "$default" "$RESET" > /dev/tty
        read -r ans < "$TTY" || die "Aborted."
        ans="${ans:-$default}"
        if valid_pkg "$ans"; then PKG="$ans"; return; fi
        warn "Use a dotted lowercase package, e.g. com.acme.store"
    done
}

confirm() {
    [[ "${KMPILOT_ASSUME_YES:-0}" == "1" ]] && return 0
    [[ -n "$TTY" ]] || return 0     # non-interactive: proceed (matches install.sh)
    local ans
    printf '\n    %sProceed?%s %s[Y/n]%s ' "$BOLD" "$RESET" "$DIM" "$RESET" > /dev/tty
    read -r ans < "$TTY" || die "Aborted."
    case "${ans:-y}" in [Yy]*|"") return 0 ;; *) die "Cancelled." ;; esac
}

# Shared by template mode and adopt mode: both ship the generic tier of the core
# modules and neither ships KMPilot's own example content. Operates on the
# current directory, so adopt mode runs it inside its staging clone.
neutralize_core_app_tiers() {
    # 9. Neutralize the design system's `app/` tier. `:core:designsystem` is split into
    #    generic primitives (XButton, XText, Placeholder, ItemPickerModal, …) that ship to every
    #    project, and a `designsystem.app` package holding the project's own composed UI.
    #    AppLoadingState/AppErrorState (in `designsystem.app`) are intentionally content-free
    #    (copy + navigation are caller parameters), so they are KEPT as-is — downstream redesigns
    #    them via the design pipeline. Project composites in `designsystem.app` (e.g. MoneyText)
    #    are project/example content and are stripped in 9b.
    #    The generic `designsystem/motion/` package (XMotion + rememberReducedMotion expect/actual,
    #    Modifier.shimmer, PulseDot, AmbientMeshBackground, BokehCanvas, Modifier.pulseGlow,
    #    RevealOnAppear) is brand-neutral generic-tier and is KEPT (not stripped) — the motion
    #    pipeline reuses it; per-feature motion is generated on demand.
    local ds="core/designsystem/src/commonMain"
    # 9a. Replace KMPilot's logotype (referenced by the generic XTopLogo) with a neutral mark.
    cat > "$ds/composeResources/drawable/app_logo_type.xml" <<'LOGO_EOF'
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="44dp"
    android:height="44dp"
    android:viewportWidth="24"
    android:viewportHeight="24">
    <path
        android:fillColor="#9E9E9E"
        android:pathData="M12,2 A10,10 0 1 0 12,22 A10,10 0 1 0 12,2 Z" />
</vector>
LOGO_EOF

    # 9b. Strip the project example `app/` tiers across the core library modules. The `app/`
    #     package in each module is the project's own example/domain layer — generic code never
    #     imports it (the boundary rule), so removing it yields a clean, generic-only template:
    #       • common.app       — project/example value types (e.g. Money/Currency)
    #       • designsystem.app — project composites (e.g. MoneyText); the content-free App* state
    #                            screens are KEPT (see 9 above)
    #       • data.app         — project/domain persisted + shared-remote data (empty in the
    #                            baseline template; may be populated by a real project)
    #     Guards are no-ops when a tier is absent, so this is safe whether or not `data.app` exists.
    rm -rf core/common/src/commonMain/kotlin/thisissadeghi/common/app
    find core/designsystem/src/commonMain/kotlin/thisissadeghi/designsystem/app -name '*.kt' \
        ! -name 'AppErrorState.kt' ! -name 'AppLoadingState.kt' -delete 2>/dev/null || true
    rm -rf core/data/src/commonMain/kotlin/thisissadeghi/data/app
    # Drop the data.app DI strip seam (import + includes entry) — no-op if data.app never existed.
    [ -f core/data/src/commonMain/kotlin/thisissadeghi/data/di/DataModules.kt ] && \
        sedi '/appDataModule/d' core/data/src/commonMain/kotlin/thisissadeghi/data/di/DataModules.kt || true

    # 9c. Reset the KEPT `App*` state screens to NEUTRAL defaults. `designsystem.app` keeps the
    #     AppLoadingState/AppErrorState *contracts* (the signatures every feature calls), but their
    #     bodies in the template are KMPilot's own design — overwrite them with generic baselines so
    #     a fresh project ships a plain spinner / plain error layout (no branded illustration), then
    #     redesigns via the design pipeline. The branded drawables they no longer use
    #     (failed_background, warning) are left in place but orphaned — harmless, and available to
    #     the redesign.
    local app="$ds/kotlin/thisissadeghi/designsystem/app"
    cat > "$app/AppLoadingState.kt" <<'APPLOADING_EOF'
package thisissadeghi.designsystem.app

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import thisissadeghi.designsystem.XCircularProgressIndicator

/**
 * Shared, project-level **Loading** state — the neutral default written on install. Reused by every
 * feature for Rule 4's `UiState.Loading`. Redesign per project via the design pipeline. Built only
 * from generic primitives ([XCircularProgressIndicator]); never imported by generic design-system code.
 */
@Composable
fun AppLoadingState(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        XCircularProgressIndicator()
    }
}
APPLOADING_EOF
    cat > "$app/AppErrorState.kt" <<'APPERROR_EOF'
package thisissadeghi.designsystem.app

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.jetbrains.compose.resources.stringResource
import thisissadeghi.designsystem.DesignSystemResources
import thisissadeghi.designsystem.XButton
import thisissadeghi.designsystem.XText

/**
 * Shared, project-level **Failed** state — the neutral default written on install. Reused by every
 * feature for Rule 4's `UiState.Failed`. Copy and navigation are **parameters** ([title]/[message]
 * from the feature's own strings, [onRetry] the primary action, [retryLabel] defaulting to the
 * shared label, [secondaryAction] an optional nav slot), so nothing app-specific is baked in.
 * Redesign per project via the design pipeline. Built only from generic primitives ([XButton],
 * [XText]); never imported by generic design-system code.
 */
@Composable
fun AppErrorState(
    title: String,
    message: String,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    retryLabel: String = stringResource(DesignSystemResources.string.retry_label),
    secondaryAction: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier = modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        XText(
            text = title,
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.onSurface,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        XText(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(max = 320.dp).padding(bottom = 24.dp),
        )
        XButton(onClick = onRetry) {
            XText(text = retryLabel)
        }
        secondaryAction?.invoke()
    }
}
APPERROR_EOF
}



# ─────────────────────────────────────────────────────────────────────────────
# Adopt mode — install the pipeline into a KMP repo that already exists
#
# Template mode owns an empty directory and can do what it likes. Adopt mode
# writes into a repo somebody already cares about, so the rules are inverted:
# it only ever CREATES paths, only ever APPENDS to Gradle files, refuses on a
# dirty tree, and never touches the target's own Kotlin.
# ─────────────────────────────────────────────────────────────────────────────

ADOPT_CORE=(common data designsystem)
# Versions KMPilot's core is actually built and tested against. A target below a
# floor is warned about, not refused — the target's own versions always win, so
# there is never a second Kotlin/AGP plugin on the build classpath.
FLOOR_KOTLIN="2.2.0"
FLOOR_AGP="8.10.0"
FLOOR_COMPOSE="1.8.0"

# Detected facts about the target repo.
TGT_CATALOG="gradle/libs.versions.toml"
ROOT_NAME=""; APP_MODULE=""; PKG_PREFIX=""
VER_KOTLIN=""; VER_AGP=""; VER_COMPOSE=""
TGT_COMPILE_SDK=""; TGT_MIN_SDK=""
HAS_KMP=no; HAS_CMP=no; HAS_KOIN=no; HAS_KTOR=no; HAS_DATASTORE=no
HAS_EITHER=no; HAS_UISTATE=no; HAS_DS=no; HAS_CATALOG=no; HAS_DRM=no; HAS_ARROW=no
HAS_KOIN_START=no
WAS_ADOPTED=no
STAGE=""

# The plan: every path adopt mode would create or edit, collected during
# detection and printed before anything is written. --dry-run stops here.
PLAN_ACT=(); PLAN_PATH=(); PLAN_NOTE=()
plan_add() { PLAN_ACT+=("$1"); PLAN_PATH+=("$2"); PLAN_NOTE+=("${3:-}"); }

ver_lt() {  # ver_lt A B → true when A < B
    [[ "$1" == "$2" ]] && return 1
    [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$1" ]]
}

# Gradle module directories, parsed out of the target's settings file.
adopt_modules() {
    grep -o '":[A-Za-z0-9_.:-]*"' settings.gradle.kts 2>/dev/null \
        | tr -d '"' | sed 's|^:||' | tr ':' '/' | sort -u
}

# Every Gradle build file in the target, for inventory greps.
#
# Includes convention-plugin sources under buildSrc/, build-logic/ and any
# includeBuild(...) — a large project applies the Kotlin Multiplatform plugin as
# `id("myapp.kmp.library")` and the real plugin id appears ONLY inside that
# included build, which the include() graph never mentions. Missing them made
# adopt mode refuse perfectly good KMP repos as "not Kotlin Multiplatform".
adopt_build_files() {
    local m d
    {
        printf '%s\n' settings.gradle.kts
        [[ -f build.gradle.kts ]] && printf '%s\n' build.gradle.kts
        [[ -f "$TGT_CATALOG" ]] && printf '%s\n' "$TGT_CATALOG"
        while IFS= read -r m; do
            [[ -f "$m/build.gradle.kts" ]] && printf '%s\n' "$m/build.gradle.kts"
        done < <(adopt_modules)
        # Convention plugins + composite builds. Capped so a monorepo with a huge
        # buildSrc cannot make detection crawl.
        while IFS= read -r d; do
            [[ -d "$d" ]] || continue
            find "$d" \( -name '*.gradle.kts' -o -name '*.kt' \) -not -path '*/build/*' 2>/dev/null | head -n 60
        done < <(
            printf '%s\n' buildSrc build-logic gradle/build-logic
            sed -n 's/.*includeBuild("\([^"]*\)").*/\1/p' settings.gradle.kts 2>/dev/null
        )
    } 2>/dev/null | sort -u
    return 0
}

adopt_has() {  # adopt_has <extended-regex> — is it anywhere in the build config?
    local files=() f
    while IFS= read -r f; do files+=("$f"); done < <(adopt_build_files)
    [[ ${#files[@]} -gt 0 ]] || return 1
    grep -qsE "$1" "${files[@]}"
}

# Kotlin targets the host declares that KMPilot's core does NOT ship actuals for.
# core/{common,data,designsystem} carry androidMain / iosMain / desktopMain and
# nothing else, so a host target outside that set would compile against a core
# module with no matching variant. Detected here, refused in adopt_detect —
# breaking someone's wasm or macOS target silently is the worst outcome there is.
UNSUPPORTED_TARGETS=""
WANTS_IOS_X64=no
# Set in adopt_vendor_core from adopt_root_declares_agp: whether the target's root
# build file already puts AGP on the build classpath, which decides whether the
# vendored core may name a version for the KMP-Android-library plugin.
AGP_ON_ROOT_CLASSPATH=no
detect_targets() {
    local files=() f found
    while IFS= read -r f; do files+=("$f"); done < <(adopt_build_files)
    [[ ${#files[@]} -gt 0 ]] || return 0

    # iosX64 is the one addition that is free: the default hierarchy folds it
    # into the SAME iosMain source set the existing iOS targets use, so no new
    # actual is needed. It is added to the vendored modules rather than refused.
    grep -qsE '\biosX64[[:space:]]*[({]' "${files[@]}" && WANTS_IOS_X64=yes

    found="$(grep -hosE '\b(wasmJs|wasmWasi|js|macosX64|macosArm64|watchos[A-Za-z0-9]*|tvos[A-Za-z0-9]*|linux[A-Za-z0-9]*|mingw[A-Za-z0-9]*)[[:space:]]*[({]' \
        "${files[@]}" 2>/dev/null | sed 's/[[:space:]]*[({]$//' | sort -u | tr '\n' ' ' || true)"
    UNSUPPORTED_TARGETS="${found% }"
}

catalog_ver_ref() {  # value of a [versions] entry
    [[ -f "$TGT_CATALOG" ]] || return 0
    sed -n "s/^[[:space:]]*${1//./\\.}[[:space:]]*=[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$TGT_CATALOG" | head -n1
}

catalog_plugin_ver() {  # resolved version of a plugin id declared in [plugins]
    local line
    [[ -f "$TGT_CATALOG" ]] || return 0
    line="$(grep -E "id[[:space:]]*=[[:space:]]*\"${1//./\\.}\"" "$TGT_CATALOG" 2>/dev/null | head -n1 || true)"
    [[ -n "$line" ]] || return 0
    if [[ "$line" =~ version\.ref[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
        catalog_ver_ref "${BASH_REMATCH[1]}"
    elif [[ "$line" =~ version[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

# First [versions] entry matching any of the given alias names.
# The explicit `return 0` matters: without it a loop that matches nothing exits
# with the last test's status (1), which `set -e` turns into an abort at the
# assignment that called it. Finding no version is a normal outcome, not an error.
catalog_first_ver() {
    local a v
    for a in "$@"; do
        v="$(catalog_ver_ref "$a")"
        [[ -n "$v" ]] && { printf '%s' "$v"; return 0; }
    done
    return 0
}

# Modules that could plausibly be the app shell. Anything under core/ is a
# library by universal convention and is never the shell — without this exclusion
# an alphabetical fallback happily picks `core/model`, derives the package prefix
# from it, and renames the entire vendored core into a data module's package.
adopt_app_candidates() {
    local m
    while IFS= read -r m; do
        [[ -d "$m/src/commonMain" ]] || continue
        case "$m" in core|core/*|*/core|*/core/*) continue ;; esac
        printf '%s\n' "$m"
    done < <(adopt_modules)
    return 0
}

# Modules the Android application module depends on — the app shell is almost
# always one of them, and it is a far better signal than alphabetical order.
adopt_android_app_deps() {
    local m f
    while IFS= read -r m; do
        f="$m/build.gradle.kts"
        [[ -f "$f" ]] || continue
        grep -qs 'com\.android\.application\|androidApplication' "$f" || continue
        sed -n 's/.*project("\:\([A-Za-z0-9_.:-]*\)").*/\1/p' "$f" | tr ':' '/'
    done < <(adopt_modules)
    return 0
}

# How the app module was decided. A guess gets confirmed with the user; a real
# signal does not. detect_app_module prints its answer rather than setting these
# directly: it runs inside a command substitution, and a subshell's assignments
# never reach the caller.
APP_MODULE_CONFIDENCE=weak
APP_MODULE_CANDIDATES=""

# Every candidate module matching ONE app-shell signal — all of them, never just
# the first. Two modules both bootstrapping Koin is the shape this exists for:
# returning on the first match resolved it to whichever sorted first, silently,
# and every downstream value (the package prefix, which build file gets the core
# dependencies, where the Koin glue is written) hangs off that one answer.
app_signal_matches() {  # <koin|entry|androiddep>
    local m d
    case "$1" in
        koin)
            while IFS= read -r m; do
                grep -rqs "startKoin" "$m/src" 2>/dev/null && printf '%s\n' "$m"
            done < <(adopt_app_candidates)
            ;;
        entry)
            while IFS= read -r m; do
                grep -rqsE 'NavHost|setContent|@Composable[[:space:]]+fun[[:space:]]+App' \
                    "$m/src" 2>/dev/null && printf '%s\n' "$m"
            done < <(adopt_app_candidates)
            ;;
        androiddep)
            while IFS= read -r d; do
                while IFS= read -r m; do
                    [[ "$m" == "$d" ]] && printf '%s\n' "$m"
                done < <(adopt_app_candidates)
            done < <(adopt_android_app_deps)
            ;;
    esac
    # Matching nothing is a normal answer, not an error. Without this the last
    # failed test becomes the exit status and `set -e` aborts the run.
    return 0
}

# The app module: whichever module starts Koin, hosts the nav graph / Compose
# entry point, or is what the Android app depends on. Never assumed to be named
# composeApp — an adopted repo names its modules whatever it likes.
#
# Prints "<module>|<confidence>|<candidates>", empty when there is no candidate
# at all. Confidence is one of:
#   strong     exactly one match in the winning signal tier — used unconfirmed
#   ambiguous  more than one match in that tier — the caller asks, never picks
#   weak       no signal anywhere — first candidate, offered as a guess
# <candidates> is the list worth showing the user for that verdict.
detect_app_module() {
    local tier hits n all
    for tier in koin entry androiddep; do
        hits="$(app_signal_matches "$tier" | sort -u)"
        [[ -n "$hits" ]] || continue
        n="$(printf '%s\n' "$hits" | wc -l | tr -d '[:space:]')"
        if [[ "$n" -eq 1 ]]; then
            printf '%s|strong|%s' "$hits" "$hits"
        else
            printf '%s|ambiguous|%s' \
                "$(printf '%s\n' "$hits" | head -n1)" \
                "$(printf '%s\n' "$hits" | tr '\n' ' ' | sed 's/ *$//')"
        fi
        return 0
    done

    # No signal at all — take the first candidate but mark it a guess, so the
    # caller asks instead of silently building on it.
    all="$(adopt_app_candidates)"
    [[ -n "$all" ]] || return 0
    printf '%s|weak|%s' \
        "$(printf '%s\n' "$all" | head -n1)" \
        "$(printf '%s\n' "$all" | tr '\n' ' ' | sed 's/ *$//')"
    return 0
}

# Package prefix = the longest package prefix shared by every source file in the
# app module. `com.acme.notes` + `com.acme.notes.di` + `com.acme.notes.data`
# → `com.acme.notes`. (The template's strip-last-segment rule reads a core
# module's package, which an adopted repo does not have yet.)
detect_pkg_prefix() {
    local pkgs seg lcp="" i=1 count
    pkgs="$(grep -rhs --include='*.kt' -E '^package [a-z]' "$1/src" 2>/dev/null \
        | awk '{print $2}' | tr -d '\r' | sort -u || true)"
    [[ -n "$pkgs" ]] || return 0
    while :; do
        seg="$(printf '%s\n' "$pkgs" | cut -d. -f"$i" | sort -u)"
        # Count EVERY line, empties included. `grep -c .` skips empty lines, so a
        # package shorter than the others (`…bookshelf` beside `…bookshelf.kmpilot`)
        # contributed an invisible empty segment, the depth looked unanimous, and a
        # segment containing a newline got appended — yielding a prefix like
        # "com.example.bookshelf.\nkmpilot". A short package must END the prefix.
        count="$(printf '%s\n' "$seg" | wc -l | tr -d '[:space:]')"
        [[ "$count" -eq 1 && -n "$seg" ]] || break
        lcp="${lcp:+$lcp.}$seg"
        i=$((i + 1))
    done
    printf '%s' "$lcp"
}

# Package identities the target's Android build DECLARES — every `namespace` and
# `applicationId` in its build files. The prefix above is inferred by walking
# source files; these are stated outright, which makes them the one independent
# check available on that inference.
android_pkg_identities() {
    local files=() f
    while IFS= read -r f; do files+=("$f"); done < <(adopt_build_files)
    [[ ${#files[@]} -gt 0 ]] || return 0
    grep -hosE '(namespace|applicationId)[[:space:]]*=[[:space:]]*"[a-z][A-Za-z0-9_.]*"' \
        "${files[@]}" 2>/dev/null \
        | sed 's/.*"\(.*\)"/\1/' | sort -u
    return 0
}

# Cross-check the inferred prefix against those declared identities, and confirm
# when they disagree. Consistent means the prefix IS one of them, or one of them
# extends it (`com.acme.notes` beside a `com.acme.notes.android` namespace is the
# ordinary shape, and stays silent).
#
# The disagreement worth catching is the walk swallowing a sub-package: an app
# module whose sources all sit under `…notes.ui` yields `com.acme.notes.ui`, which
# is a perfectly well-formed package and completely wrong — every vendored core
# module would be renamed into a UI sub-package. Nothing else notices, because
# there is nothing else to compare against.
#
# A warning plus a default, never a refusal: this is a soft signal, and a repo
# that genuinely has no Android module has no identities to check at all.
confirm_pkg_prefix() {
    local ids id shortest=""
    ids="$(android_pkg_identities)"
    [[ -n "$ids" ]] || return 0
    while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        [[ "$id" == "$PKG_PREFIX" || "$id" == "$PKG_PREFIX."* ]] && return 0
        [[ -z "$shortest" || ${#id} -lt ${#shortest} ]] && shortest="$id"
    done <<< "$ids"

    warn "Package prefix '${BOLD}${PKG_PREFIX}${RESET}' does not match what your Android build declares:"
    substep "namespace / applicationId: $(printf '%s' "$ids" | tr '\n' ' ' | sed 's/ *$//')"
    substep "the prefix was read from the packages in ${APP_MODULE}/src; the vendored core"
    substep "modules are renamed into it, so a sub-package here follows them everywhere."
    if [[ -n "$TTY" && -n "$shortest" ]] && valid_pkg "$shortest"; then
        local ans
        printf '    %s?%s %sPackage prefix%s %s[%s]%s: ' \
            "$CYAN" "$RESET" "$BOLD" "$RESET" "$DIM" "$shortest" "$RESET" > /dev/tty
        read -r ans < "$TTY" || die "Aborted."
        ans="${ans:-$shortest}"
        valid_pkg "$ans" || die "'$ans' is not a dotted lowercase package."
        PKG_PREFIX="$ans"
    else
        substep "keeping '${PKG_PREFIX}' — pass it explicitly to override:"
        substep "    install.sh --adopt ${ROOT_NAME} ${shortest:-com.acme.myapp}"
    fi
    return 0
}

# Does a directory contain a Kotlin Multiplatform build? Deliberately shallow —
# this only ever runs to improve a refusal, so a `commonMain` source set or a
# build file naming the KMP plugin is enough. The depth covers `<root>/<module>/
# src/commonMain` and one extra level for `<root>/core/<module>/src/commonMain`.
looks_kmp_at() {  # <dir>
    find "$1" -maxdepth 5 -type d -name commonMain -not -path '*/build/*' 2>/dev/null \
        | grep -q . && return 0
    grep -rqsE 'org\.jetbrains\.kotlin\.multiplatform|kotlin\("multiplatform"\)|kotlinMultiplatform' \
        --include='*.gradle.kts' "$1" 2>/dev/null
}

# Kotlin Multiplatform builds sitting ONE level below here. `--adopt` installs
# into the Gradle project it is run from, and a monorepo with android/, ios/ and
# backend/ keeps that project in a subdirectory — so "you ran this a level too
# high" and "this is not a KMP repo" are the same message today, and only one of
# the two is something the reader can act on.
#
# A sibling Gradle root that is not KMP (a `backend/`) is deliberately not named:
# pointing someone at the wrong directory is worse than not pointing at all.
nested_kmp_roots() {
    local d
    for d in */; do
        d="${d%/}"
        case "$d" in .*|build|buildSrc|build-logic|gradle|node_modules) continue ;; esac
        [[ -f "$d/settings.gradle.kts" || -f "$d/settings.gradle" ]] || continue
        looks_kmp_at "$d" && printf '%s\n' "$d"
    done
    return 0
}

# Refuse naming the directory to run in, when there is one to name.
die_wrong_level() {  # <first line of the refusal>
    local nested
    nested="$(nested_kmp_roots)"
    [[ -n "$nested" ]] || return 0
    die "$1

A Kotlin Multiplatform build does live one level down:

$(printf '%s\n' "$nested" | sed 's|^|    |')

--adopt installs into the Gradle project it is run from, never into a parent of
one. Change into that directory and re-run the same command:

    cd $(printf '%s\n' "$nested" | head -n1)"
}

adopt_detect() {
    step "Inspecting this repository"

    if [[ ! -f settings.gradle.kts ]]; then
        die_wrong_level "No settings.gradle.kts here — this directory is not a Gradle project root."
        if [[ -f settings.gradle ]]; then
            die "This project uses the Groovy DSL (settings.gradle), which adopt mode does not support yet.

Nothing is wrong with your project — the edits adopt mode makes are all append-only
and have direct Groovy equivalents, so this is a gap in KMPilot, not in your build.
It is unscheduled rather than rejected: if you want it, please open an issue saying so.
That is the signal that decides whether it gets built.

    https://github.com/ThisIsSadeghi/KMPilot/issues"
        fi
        die "No settings.gradle.kts here.
Run --adopt from the root of the Gradle project you want to adopt into."
    fi

    git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || die "Not a git repository. Adopt mode writes several files into this repo;
git is what makes that reviewable and revertible. Run 'git init' first."

    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        if [[ "$DRY_RUN" == "yes" ]]; then
            warn "Working tree is dirty. A real --adopt run would refuse; --dry-run writes nothing, so continuing."
        else
            die "Working tree is not clean. Commit or stash first — adoption should land as one reviewable diff.
    git status --short"
        fi
    fi

    [[ -f .kmpilot.json ]] && WAS_ADOPTED=yes
    if [[ -f .kmpilot.json && "$FORCE" != "yes" ]]; then
        die "This repo already has a .kmpilot.json — it has been adopted (or installed) already.
Re-run with --force to re-apply. Re-applying is idempotent: it never duplicates Gradle lines."
    fi

    ROOT_NAME="$(sed -n 's/^[[:space:]]*rootProject\.name[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
        settings.gradle.kts | head -n1)"
    [[ -n "${POSITIONAL[0]:-}" ]] && ROOT_NAME="${POSITIONAL[0]}"
    ROOT_NAME="${ROOT_NAME:-$(basename "$(pwd)")}"
    # An existing project's rootProject.name is a given, not something adopt mode
    # gets to choose, so this is far laxer than template mode's valid_name:
    # `acme-notes` is ordinary. Compose Multiplatform sanitizes it to `acme_notes`
    # for the generated-resources package and scripts/rename.sh applies the
    # identical rule, so the vendored imports match what Compose generates. Only a
    # name that cannot survive that sanitization is worth stopping for — and even
    # then, ask rather than refuse.
    if [[ ! "$(lower "$ROOT_NAME" | sed 's/[^a-z0-9_]/_/g')" =~ ^[a-z_][a-z0-9_]*$ ]]; then
        warn "rootProject.name '$ROOT_NAME' does not sanitize to a usable package segment."
        if [[ -n "$TTY" ]]; then
            local ans
            printf '    %s?%s %sName to derive the resources package from%s: ' \
                "$CYAN" "$RESET" "$BOLD" "$RESET" > /dev/tty
            read -r ans < "$TTY" || die "Aborted."
            ROOT_NAME="${ans:-$ROOT_NAME}"
        fi
        [[ "$(lower "$ROOT_NAME" | sed 's/[^a-z0-9_]/_/g')" =~ ^[a-z_][a-z0-9_]*$ ]] \
            || die "Still unusable. Pass one explicitly:  install.sh --adopt <Name> <package.prefix>"
    fi

    if [[ -n "$APP_MODULE_ARG" ]]; then
        APP_MODULE="$APP_MODULE_ARG"
        APP_MODULE_CONFIDENCE=strong
        APP_MODULE_CANDIDATES="$APP_MODULE_ARG"
        [[ -d "$APP_MODULE/src/commonMain" ]] \
            || die "--app-module=$APP_MODULE has no src/commonMain."
    else
        local detected
        detected="$(detect_app_module)"
        IFS='|' read -r APP_MODULE APP_MODULE_CONFIDENCE APP_MODULE_CANDIDATES \
            <<< "$detected"
    fi
    # Before calling it a non-KMP repo, check whether the KMP build is simply one
    # level down — a Gradle root that includes only backend modules is a monorepo
    # top, not an Android-only app, and telling its owner to go use
    # 'migrate-feature' is the wrong answer to a fixable mistake.
    if [[ -z "$APP_MODULE" ]]; then
        die_wrong_level "No module in settings.gradle.kts has a src/commonMain source set."
    fi
    [[ -n "$APP_MODULE" ]] || die "This does not look like a Kotlin Multiplatform project.
No module in settings.gradle.kts has a src/commonMain source set.

Adopt mode installs a KMP pipeline; there is nothing safe to do in a non-KMP repo.
Bringing an Android-only app to KMP is a different job — see 'migrate-feature' upstream."

    # Everything downstream hangs off this one value — the package prefix read
    # below, which build file gets the core dependencies, where the Koin glue is
    # written. A wrong guess is silent and expensive, so a guess is never used
    # unconfirmed, and the confirmation happens HERE, before the package prefix is
    # derived from it — confirming afterwards left the prefix read off the module
    # the user had just rejected.
    #
    # Two shapes reach a human, for opposite reasons. `weak` is absence: no signal
    # anywhere, so the first candidate is offered as a default and Enter accepts it.
    # `ambiguous` is contradiction: several modules each look like the shell, so
    # there is nothing to default to — pressing Enter would be the silent coin flip
    # this exists to remove, and a non-interactive run refuses rather than picks.
    if [[ "$APP_MODULE_CONFIDENCE" == "ambiguous" ]]; then
        warn "More than one module looks like your app shell:"
        substep "candidates: ${BOLD}${APP_MODULE_CANDIDATES}${RESET}"
        substep "each shows the same signal (Koin bootstrap / Compose entry point / app dependency),"
        substep "so detection cannot choose between them."
        if [[ -n "$TTY" ]]; then
            local ans
            while :; do
                printf '    %s?%s %sWhich one is your app shell%s %s(no default)%s: ' \
                    "$CYAN" "$RESET" "$BOLD" "$RESET" "$DIM" "$RESET" > /dev/tty
                read -r ans < "$TTY" || die "Aborted."
                ans="${ans#:}"; ans="${ans//:/\/}"   # accept :shared as well as shared
                [[ -n "$ans" ]] || { warn "Name one of: ${APP_MODULE_CANDIDATES}"; continue; }
                [[ -d "$ans/src/commonMain" ]] || { warn "'$ans' has no src/commonMain."; continue; }
                APP_MODULE="$ans"
                APP_MODULE_CONFIDENCE=strong
                break
            done
        else
            die "Cannot tell which of these is your app shell:

    ${APP_MODULE_CANDIDATES}

Everything adoption writes hangs off that choice — the package prefix, which build
file gets the core dependencies, where the Koin glue lands — so picking one at
random here would be silent and expensive. Nothing has been written.

Name it explicitly, or re-run where a terminal can ask you:

    install.sh --adopt --app-module=${APP_MODULE}"
        fi
    elif [[ "$APP_MODULE_CONFIDENCE" == "weak" ]]; then
        warn "Could not tell which module is your app shell — no startKoin, no NavHost,"
        substep "and no Android application module pointing at one. Best guess: ${BOLD}${APP_MODULE}${RESET}"
        if [[ -n "$TTY" ]]; then
            local ans
            printf '    %scandidates:%s %s\n' "$DIM" "$RESET" "$APP_MODULE_CANDIDATES" > /dev/tty
            printf '    %s?%s %sApp module%s %s[%s]%s: ' \
                "$CYAN" "$RESET" "$BOLD" "$RESET" "$DIM" "$APP_MODULE" "$RESET" > /dev/tty
            read -r ans < "$TTY" || die "Aborted."
            ans="${ans:-$APP_MODULE}"
            [[ -d "$ans/src/commonMain" ]] || die "'$ans' has no src/commonMain."
            APP_MODULE="$ans"
        else
            substep "pass --app-module=<module> to be certain (non-interactive run)"
        fi
    fi

    PKG_PREFIX="$(detect_pkg_prefix "$APP_MODULE")"
    # An explicit argument always wins; otherwise ask before giving up. Detection
    # reads the longest package prefix shared by the app module's sources, which
    # fails only on unusual layouts — a question is a better answer than an exit.
    [[ -n "$PKG_ARG" ]] && PKG_PREFIX="$PKG_ARG"
    if [[ -z "$PKG_PREFIX" ]] || ! valid_pkg "$PKG_PREFIX"; then
        [[ -z "$PKG_PREFIX" ]] \
            && warn "Could not read a package prefix from $APP_MODULE/src." \
            || warn "Detected package prefix '$PKG_PREFIX' is not a valid lowercase dotted package."
        if [[ -n "$TTY" ]]; then
            local ans
            while :; do
                printf '    %s?%s %sPackage prefix%s %s(e.g. com.acme.myapp)%s: ' \
                    "$CYAN" "$RESET" "$BOLD" "$RESET" "$DIM" "$RESET" > /dev/tty
                read -r ans < "$TTY" || die "Aborted."
                if valid_pkg "$ans"; then PKG_PREFIX="$ans"; break; fi
                warn "Use a dotted lowercase package, e.g. com.acme.store"
            done
        else
            die "Pass one explicitly:  install.sh --adopt <Name> <package.prefix>"
        fi
    elif [[ -z "$PKG_ARG" ]]; then
        # Only an INFERRED prefix gets cross-checked. An explicit argument is the
        # user's own statement of intent and is never second-guessed.
        confirm_pkg_prefix
    fi

    # Two independent signals, either is sufficient. The structural one (a module
    # with src/commonMain) is what makes convention-plugin projects work: their
    # modules say `id("myapp.kmp.library")` and never name the KMP plugin at all.
    adopt_has 'org\.jetbrains\.kotlin\.multiplatform|kotlin\("multiplatform"\)|kotlinMultiplatform' && HAS_KMP=yes
    [[ -n "$APP_MODULE" ]] && HAS_KMP=yes
    adopt_has 'org\.jetbrains\.compose|compose\.material3|composeMultiplatform|jetbrainsCompose' && HAS_CMP=yes
    adopt_has 'io\.insert-koin|koin' && HAS_KOIN=yes
    adopt_has 'io\.ktor|ktor' && HAS_KTOR=yes
    adopt_has 'androidx\.datastore|datastore' && HAS_DATASTORE=yes
    [[ -f "$TGT_CATALOG" ]] && HAS_CATALOG=yes
    grep -qs 'dependencyResolutionManagement' settings.gradle.kts && HAS_DRM=yes

    grep -rqsE '^\s*(sealed (class|interface)) Either' --include='*.kt' . 2>/dev/null && HAS_EITHER=yes
    # Arrow ships its own `Either`. Vendoring ours puts two different types with
    # the same simple name in one codebase — harmless until a file imports both,
    # and confusing everywhere else. Worth saying out loud before anything lands.
    { adopt_has 'io\.arrow-kt' || grep -rqsE '^import arrow\.core\.' --include='*.kt' . 2>/dev/null; } && HAS_ARROW=yes
    # Does the project start Koin itself? Decides whether adopt supplies an
    # initKmpilotKoin(). Excludes KMPilot's own glue so a --force re-run does not
    # mistake its own output for the host's DI setup.
    #
    # `startKoin` is only ONE of Koin's entry points. Compose Multiplatform apps
    # commonly bootstrap with `KoinApplication { }` / `koinConfiguration { }` /
    # `KoinMultiplatformApplication` and never call startKoin at all — missing those
    # made adopt write a dead InitKmpilotKoin.kt into a project that already had DI.
    grep -rqsE "startKoin|KoinApplication|koinConfiguration|KoinMultiplatformApplication|koinApplication" \
        --include='*.kt' --exclude-dir=kmpilot . 2>/dev/null && HAS_KOIN_START=yes
    grep -rqsE '^\s*(sealed (class|interface)) UiState' --include='*.kt' . 2>/dev/null && HAS_UISTATE=yes
    adopt_modules | grep -qiE 'designsystem|design-system|uikit|ui-kit' && HAS_DS=yes

    [[ "$HAS_KMP" == "yes" ]] || die "This does not look like a Kotlin Multiplatform project.
No module has a src/commonMain, and no build file (including buildSrc / build-logic /
included builds) applies the Kotlin Multiplatform plugin.

Adopt mode installs a KMP pipeline; there is nothing safe to do in a non-KMP repo.
Bringing an Android-only app to KMP is a different job — see 'migrate-feature' upstream."

    # A pre-existing `core/common` of THEIR OWN is a name collision, not a
    # module of ours to skip: vendoring is skipped (never clobber) but the app
    # module still gets implementation(project(":core:common")), which then
    # resolves to their module — and every feature fails to find Either/UiState.
    # Silent and baffling, so stop here instead. On a --force re-run those paths
    # are KMPilot's own, which is why this is gated on WAS_ADOPTED.
    if [[ "$WAS_ADOPTED" == "no" ]]; then
        local clash=""
        for m in "${ADOPT_CORE[@]}"; do
            is_real_module "core/$m" && clash="${clash} core/$m"
        done
        # Every clashing path carrying KMPilot's own signature files means an
        # adoption was removed without deleting the directories — not a collision.
        local theirs=""
        for m in "${ADOPT_CORE[@]}"; do
            is_real_module "core/$m" && ! is_kmpilot_core "$m" && theirs="${theirs} core/$m"
        done
        # Everything KMPilot has already put here, reported as one inventory
        # before anything is written. Core modules are only one of the ways a
        # KMPilot can arrive without a manifest — a hand-vendored install from
        # before adopt mode existed leaves a kmpilotLibs catalog and namespaced
        # modules instead, and adopting over either would write a second copy of
        # what is already there. Re-adopting on top of a vendoring of unknown
        # vintage is not something to do by accident, so it takes --force.
        #
        # Gated on --force as well as on the manifest. WAS_ADOPTED reads
        # .kmpilot.json, which is precisely what is absent in this shape, so
        # without the FORCE test the refusal would tell the reader to pass
        # --force and then refuse again when they did.
        local artefacts
        artefacts="$(kmpilot_artefacts)"
        if [[ -n "$artefacts" && -z "$theirs" && "$FORCE" != "yes" ]]; then
            die "This project already carries KMPilot, but has no .kmpilot.json:

$(printf '%s\n' "$artefacts" | sed 's|^|    |')

That is either a hand-vendored install from before adopt mode existed, or an
adoption whose manifest was deleted. Nothing here is yours to lose, and nothing
has been written.

Re-run with --force to adopt over it — re-applying is idempotent and never
duplicates a Gradle line:

    …--adopt --force
${clash:+
Or finish removing what is there and adopt clean:

    rm -rf${clash}
}"
        fi
        # A real name collision, and --force does not make their core/common
        # ours — keyed on `theirs` rather than `clash`, because a forced run past
        # the inventory above leaves `clash` set to KMPilot's own modules.
        if [[ -n "$theirs" ]]; then
            die "This project already has a module at:${theirs}

KMPilot vendors its own modules at exactly those paths, so the names collide. It will not
overwrite yours, but it also cannot wire features to a \`core/common\` that is not the one
providing Either / UiState — so adoption would leave you with features that do not compile.

Rename your module(s), or open an issue if a configurable vendor path would help:
    https://github.com/ThisIsSadeghi/KMPilot/issues"
        fi
    fi

    detect_targets
    if [[ -n "$UNSUPPORTED_TARGETS" ]]; then
        die "This project declares Kotlin target(s) KMPilot's core modules cannot serve:
    ${UNSUPPORTED_TARGETS}

core/{common,data,designsystem} ship androidMain, iosMain and desktopMain actuals only.
Vendoring them into a project that also builds ${UNSUPPORTED_TARGETS%% *} would leave that target with
no matching variant, so adopt mode stops rather than break your build.

Adopting is possible once those targets have actuals upstream — please open an issue
saying which ones you need; that is the signal that decides whether they get built."
    fi

    VER_KOTLIN="$(catalog_plugin_ver org.jetbrains.kotlin.multiplatform)"
    [[ -n "$VER_KOTLIN" ]] || VER_KOTLIN="$(catalog_first_ver kotlin kotlinVersion kotlin-version)"
    VER_AGP="$(catalog_plugin_ver com.android.application)"
    [[ -n "$VER_AGP" ]] || VER_AGP="$(catalog_first_ver agp androidGradlePlugin android-gradle-plugin)"
    VER_COMPOSE="$(catalog_plugin_ver org.jetbrains.compose)"
    [[ -n "$VER_COMPOSE" ]] || VER_COMPOSE="$(catalog_first_ver compose-plugin composeMultiplatform compose composeVersion)"
    TGT_COMPILE_SDK="$(catalog_first_ver android-compileSdk compileSdk android-compile-sdk androidCompileSdk)"
    TGT_MIN_SDK="$(catalog_first_ver android-minSdk minSdk android-min-sdk androidMinSdk)"
    [[ -n "$TGT_COMPILE_SDK" ]] || TGT_COMPILE_SDK="$(grep -rhsE '^\s*compileSdk\s*=\s*[0-9]+' --include='build.gradle.kts' . 2>/dev/null | grep -oE '[0-9]+' | head -n1 || true)"
    [[ -n "$TGT_MIN_SDK" ]] || TGT_MIN_SDK="$(grep -rhsE '^\s*minSdk\s*=\s*[0-9]+' --include='build.gradle.kts' . 2>/dev/null | grep -oE '[0-9]+' | head -n1 || true)"

    ok "Root project ${BOLD}${ROOT_NAME}${RESET} · app module ${BOLD}${APP_MODULE}${RESET} · package ${BOLD}${PKG_PREFIX}${RESET}"
}

yn() { [[ "$1" == "yes" ]] && printf '%s✓%s' "$GREEN" "$RESET" || printf '%s✗%s' "$RED" "$RESET"; }

# Rules the checker can enforce against this repo as it stands today. Everything
# else needs the core types adopt mode is about to vendor.
adopt_rule_count() {
    local n=5  # R1, R7, R9, R10, R14 need nothing from core
    [[ "$HAS_KOIN" == "yes" ]] && n=$((n + 1))   # R8  DI module
    [[ "$HAS_CMP"  == "yes" ]] && n=$((n + 2))   # R12 strings, R13 single Scaffold
    printf '%s' "$n"
}

floor_check() {  # floor_check <label> <detected> <tested floor>
    if [[ -z "$2" ]]; then
        warn "Could not determine your $1 version — skipping that compatibility check."
        return 0
    fi
    if ver_lt "$2" "$3"; then
        warn "$1 $2 is below KMPilot's tested floor ($3). Your version is kept (never overridden),"
        substep "but the vendored core modules may not compile. Consider upgrading before adopting."
    fi
}

adopt_report() {
    printf '\n'
    printf '    %sDetected%s   Kotlin Multiplatform %s   Compose Multiplatform %s   Koin %s   Ktor %s   DataStore %s\n' \
        "$DIM" "$RESET" "$(yn "$HAS_KMP")" "$(yn "$HAS_CMP")" "$(yn "$HAS_KOIN")" "$(yn "$HAS_KTOR")" "$(yn "$HAS_DATASTORE")"
    printf '    %sMissing%s    Either %s   UiState %s   design system %s\n' \
        "$DIM" "$RESET" "$(yn "$HAS_EITHER")" "$(yn "$HAS_UISTATE")" "$(yn "$HAS_DS")"
    printf '    %sVersions%s   Kotlin %s   AGP %s   Compose %s\n' \
        "$DIM" "$RESET" "${VER_KOTLIN:-?}" "${VER_AGP:-?}" "${VER_COMPOSE:-?}"
    # Say whose targets these are. The vendored modules ship a superset of the
    # host's on purpose — a target the host does not declare simply never builds.
    printf '    %sVendored%s   core builds for android · ios · desktop%s\n' \
        "$DIM" "$RESET" "$([[ "$WANTS_IOS_X64" == "yes" ]] && printf ' · iosX64 (added to match yours)')"
    printf '\n'
    printf '    %sPlan%s       vendor %score/common%s, %score/data%s, %score/designsystem%s as new modules\n' \
        "$DIM" "$RESET" "$BOLD" "$RESET" "$BOLD" "$RESET" "$BOLD" "$RESET"
    printf '    %sRules%s      enforceable today: %s of 14   ·   after adoption: %s14 of 14%s\n' \
        "$DIM" "$RESET" "$(adopt_rule_count)" "$GREEN" "$RESET"
    printf '\n'

    floor_check kotlin  "$VER_KOTLIN"  "$FLOOR_KOTLIN"
    floor_check AGP     "$VER_AGP"     "$FLOOR_AGP"
    floor_check Compose "$VER_COMPOSE" "$FLOOR_COMPOSE"

    if [[ "$HAS_ARROW" == "yes" ]]; then
        warn "This project uses Arrow, which has its own 'Either'. KMPilot vendors ${PKG_PREFIX}.common.Either —"
        substep "two distinct types sharing one simple name. Any file importing both must alias one."
    fi
    [[ "$HAS_DRM" == "yes" ]] || warn "No dependencyResolutionManagement block in settings.gradle.kts — one will be appended."
    [[ "$HAS_DS" == "no" ]] || warn "This repo already has a design-system module. KMPilot vendors its own (X* components);
    the two will coexist and the rules will only be enforced against KMPilot's."
}

# The paths adopt mode touches. Computed once, printed by --dry-run, executed by
# adopt_apply — so the plan is never a description of something else.
adopt_plan() {
    local m target
    for m in "${ADOPT_CORE[@]}"; do
        target="core/$m"
        if is_real_module "$target"; then
            plan_add skip "$target" "exists — left untouched (update.sh --core merges it)"
        else
            plan_add create "$target" "vendored, renamed to $PKG_PREFIX"
        fi
    done

    if [[ -f gradle/kmpilot.versions.toml ]]; then
        plan_add overwrite gradle/kmpilot.versions.toml "regenerated (KMPilot-owned)"
    else
        plan_add create gradle/kmpilot.versions.toml "second catalog, exposed as kmpilotLibs"
    fi

    local m need=0
    for m in "${ADOPT_CORE[@]}"; do
        grep -qs "\":core:${m}\"" settings.gradle.kts || need=$((need + 1))
    done
    grep -qs 'kmpilotLibs' settings.gradle.kts || need=$((need + 1))
    if [[ $need -gt 0 ]]; then
        plan_add edit settings.gradle.kts "append ${need} line-group(s): core includes + the kmpilotLibs catalog"
    else
        plan_add skip settings.gradle.kts "core includes + catalog already registered"
    fi

    if grep -qs '"archTest"' build.gradle.kts 2>/dev/null; then
        plan_add skip build.gradle.kts "archTest task already registered"
    else
        plan_add edit build.gradle.kts "append the archTest task (the architecture gate)"
    fi

    need=0
    for m in "${ADOPT_CORE[@]}"; do
        grep -qs "\":core:${m}\"" "$APP_MODULE/build.gradle.kts" 2>/dev/null || need=$((need + 1))
    done
    if [[ $need -gt 0 ]]; then
        plan_add edit "$APP_MODULE/build.gradle.kts" "add ${need} implementation(project(\":core:…\")) line(s)"
    else
        plan_add skip "$APP_MODULE/build.gradle.kts" "core dependencies already present"
    fi

    local kdir="$APP_MODULE/src/commonMain/kotlin/${PKG_PREFIX//.//}/kmpilot"
    plan_glue_file "$kdir/BuildOptionProviderImpl.kt" "binds :core:data's BuildOptionProvider"
    plan_glue_file "$kdir/KmpilotModules.kt" "the Koin modules core needs"
    if [[ "$HAS_KOIN_START" == "no" ]]; then
        plan_glue_file "$kdir/InitKmpilotKoin.kt" "this project has no startKoin — one is supplied"
    fi

    local p
    for p in .claude/skills .claude/agents .claude/commands .claude/hooks; do
        if [[ -e "$p" ]]; then
            plan_add overwrite "$p" "KMPilot-owned layer (update.sh OVERRIDE tier)"
        else
            plan_add create "$p" ""
        fi
    done
    plan_place_or_sidecar "$STAGE/.claude/settings.json" .claude/settings.json \
        "registers the feature-file protection hook"
    plan_place_or_sidecar "$STAGE/CLAUDE.md" CLAUDE.md \
        "architecture rules the skills read"
    plan_add create .kmpilot.json "installMode: adopt — update.sh reads this"
    plan_glue_file .claude/docs/_project/.gitignore "keeps the generated check-report out of your diffs"
    plan_add create KMPILOT-NEXT-STEPS.md "the wiring adopt cannot do for you"
}

# Plan-side twin of adopt_place_or_sidecar — same three outcomes, so the printed
# plan never promises something the apply step would do differently.
plan_place_or_sidecar() {  # <staged> <destination> <note>
    local staged="$1" dest="$2" note="$3"
    if [[ ! -f "$dest" ]]; then
        plan_add create "$dest" "$note"
    elif cmp -s "$staged" "$dest"; then
        plan_add skip "$dest" "already identical to KMPilot's"
    else
        plan_add skip "$dest" "yours — left untouched"
        plan_add create "${dest%.*}.kmpilot.${dest##*.}" "KMPilot's version, for you to merge"
    fi
}

plan_glue_file() {  # <path> <note> — new-files-only, so an existing one is a skip
    if [[ -e "$1" ]]; then plan_add skip "$1" "already exists"; else plan_add create "$1" "$2"; fi
}

adopt_print_plan() {
    local i act color
    printf '  %sFile plan%s\n\n' "$BOLD" "$RESET"
    for i in "${!PLAN_ACT[@]}"; do
        act="${PLAN_ACT[$i]}"
        case "$act" in
            create)    color="$GREEN" ;;
            edit)      color="$CYAN" ;;
            overwrite) color="$YELLOW" ;;
            *)         color="$DIM" ;;
        esac
        printf '    %s%-9s%s %-52s %s%s%s\n' \
            "$color" "$act" "$RESET" "${PLAN_PATH[$i]}" "$DIM" "${PLAN_NOTE[$i]}" "$RESET"
    done
    printf '\n'
    printf '    %sNothing else is written, and no file outside this list is modified.%s\n' "$DIM" "$RESET"
    printf '    %sYour own Kotlin is never edited — Koin and nav wiring are printed as a snippet.%s\n\n' "$DIM" "$RESET"
}

# Existing violations in the target's own feature modules, if it has any.
# --baseline reports errors as warnings and always exits 0: this is a report on
# a codebase that has not adopted the rules yet, not a gate.
adopt_baseline_check() {
    [[ -d feature ]] || return 0
    command -v python3 >/dev/null 2>&1 || return 0
    local checker="$STAGE/.claude/skills/_shared/kmpilot_check.py"
    [[ -f "$checker" ]] || return 0
    step "Baseline check of your existing feature/ modules"
    # Report path lives OUTSIDE the target: --dry-run must not write here.
    python3 "$checker" --all --baseline --compact --root "$(pwd)" \
        --report "$STAGE/baseline-report.json" 2>/dev/null | head -n 20 || true
}

adopt_stage_clone() {
    STAGE="$(mktemp -d)/kmpilot"
    # The staging clone is a working file, not an artifact — never leave it behind.
    trap 'rm -rf "$(dirname "$STAGE")"' EXIT

    # KMPILOT_SOURCE_DIR stages from a local KMPilot checkout instead of cloning
    # a published release — how an unreleased installer is tested against a real
    # target repo. Tracked paths only (working-tree content, so uncommitted
    # changes are included; build output and untracked local files are not).
    if [[ -n "${KMPILOT_SOURCE_DIR:-}" ]]; then
        local src; src="$(cd "$KMPILOT_SOURCE_DIR" && pwd)"
        [[ -d "$src/core" && -d "$src/.claude" ]] \
            || die "KMPILOT_SOURCE_DIR=$src is not a KMPilot checkout (no core/ or .claude/)."
        step "Staging KMPilot from a local checkout"
        substep "source: ${BOLD}${src}${RESET}"
        mkdir -p "$STAGE"
        if git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            ( cd "$src" && git ls-files -z | tar --null -T - -cf - ) | tar -C "$STAGE" -xf -
        else
            ( cd "$src" && tar --exclude='./.git' --exclude='*/build' --exclude='./build' -cf - . ) \
                | tar -C "$STAGE" -xf -
        fi
        ok "Staged in a temp directory (nothing written here yet)"
        return 0
    fi

    step "Fetching KMPilot"
    substep "ref: ${BOLD}${TEMPLATE_BRANCH}${RESET}"
    spinner "Cloning ..." \
        git -c advice.detachedHead=false clone --depth=1 --branch "$TEMPLATE_BRANCH" --quiet "$TEMPLATE_REPO" "$STAGE" \
        || die "Clone failed. Check the repo URL / ref and your network."
    rm -rf "$STAGE/.git"
    ok "Staged in a temp directory (nothing written here yet)"
}

# ── the writes ───────────────────────────────────────────────────────────────

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# A file whose last line has no newline turns every `>>` append into a splice:
# `include(":core:network")include(":core:common")`, which is not valid Kotlin.
# Plenty of hand-edited Gradle files end that way, so never append without this.
ensure_trailing_newline() {
    [[ -s "$1" ]] || return 0
    [[ -z "$(tail -c 1 "$1")" ]] || printf '\n' >> "$1"
}

# KMPilot's core build files lean on KMPilot's own root build.gradle.kts for
# compileSdk / minSdk / JVM target, and on its `libs` catalog. An adopted repo
# has neither, and its root build is not ours to edit — so each vendored module
# is rewritten to configure itself and to read the kmpilotLibs catalog.
# Does the target's ROOT build file already put AGP on the build classpath?
#
# Every KMP project's root declares its Android plugins `apply false` so the
# subprojects share one classloader. Once any `com.android.*` plugin is declared
# there, the whole AGP artifact is on the classpath — including the plugins the
# root did NOT name. A vendored core module that then requests
# `com.android.kotlin.multiplatform.library` **with a version** is refused:
#
#   Error resolving plugin [id: 'com.android.kotlin.multiplatform.library', version: '9.0.1']
#   > already on the classpath with an unknown version, so compatibility cannot be checked.
#
# Which is why an app module that is BOTH `com.android.application` and KMP — the
# single-module shape — could not configure after adoption at all: its root names
# only `com.android.application`, so the KMP-Android-library plugin arrives
# untagged. Projects with a separate android app module happen to name both, so
# their versions are known and the versioned alias resolves. That is the whole
# difference, and it is why this went unnoticed through Phase 2.
#
# Requesting the plugin without a version binds it to whatever AGP the host
# already resolved — correct in both shapes, and the versions cannot drift because
# there is only ever one AGP on the classpath.
adopt_root_declares_agp() {
    local root_build="build.gradle.kts"
    [[ -f "$root_build" ]] || return 1
    # A literal id, the unambiguous case.
    grep -qE '\bid\s*\(\s*"com\.android\.' "$root_build" && return 0
    # Or a catalog alias whose entry resolves to a com.android.* plugin. Resolved
    # through the host's own catalog rather than matched on alias spelling, which
    # every project picks differently (androidKmpLibrary, androidMultiplatformLibrary…).
    local catalog="gradle/libs.versions.toml" alias_name
    [[ -f "$catalog" ]] || return 1
    while read -r alias_name; do
        [[ -n "$alias_name" ]] || continue
        # Catalog aliases normalise - and _ to . in the accessor; match either form.
        local pattern; pattern="$(printf '%s' "$alias_name" | sed 's/\./[-_.]/g')"
        grep -qE "^[[:space:]]*${pattern}[[:space:]]*=.*id[[:space:]]*=[[:space:]]*\"com\.android\." \
            "$catalog" && return 0
    done < <(sed -n 's/.*alias(\([A-Za-z][A-Za-z0-9_]*\)\.plugins\.\([A-Za-z0-9_.]*\)).*/\2/p' "$root_build")
    return 1
}

adopt_rewrite_core_builds() {
    local m f cs ms
    cs="${TGT_COMPILE_SDK:-$(sed -n 's/^android-compileSdk = "\([0-9]*\)".*/\1/p' "$STAGE/gradle/libs.versions.toml" | head -n1)}"
    ms="${TGT_MIN_SDK:-$(sed -n 's/^android-minSdk = "\([0-9]*\)".*/\1/p' "$STAGE/gradle/libs.versions.toml" | head -n1)}"
    for m in "${ADOPT_CORE[@]}"; do
        f="$STAGE/core/$m/build.gradle.kts"
        [[ -f "$f" ]] || continue
        # `libs.` → `kmpilotLibs.` (BSD sed has no \b; every occurrence is
        # preceded by `(` or whitespace)
        sedi 's|(libs\.|(kmpilotLibs.|g; s| libs\.| kmpilotLibs.|g' "$f"
        # See adopt_root_declares_agp: when the host root already put AGP on the
        # classpath, asking for it again *with a version* is an error, not a
        # duplicate. Bind to what is already there instead.
        if [[ "$AGP_ON_ROOT_CLASSPATH" == "yes" ]]; then
            sedi 's|alias(kmpilotLibs\.plugins\.androidKotlinMultiplatformLibrary)|id("com.android.kotlin.multiplatform.library")|' "$f"
        fi
        awk -v cs="$cs" -v ms="$ms" -v iosx64="$WANTS_IOS_X64" '
            BEGIN { injected = 0 }
            /^[[:space:]]*namespace[[:space:]]*=/ && injected == 0 {
                print
                print "        compileSdk = " cs
                print "        minSdk = " ms
                print "        androidResources.enable = true"
                injected = 1
                next
            }
            /^[[:space:]]*jvm\("desktop"\)[[:space:]]*$/ {
                print "    jvm(\"desktop\") {"
                print "        compilerOptions { jvmTarget.set(JvmTarget.JVM_21) }"
                print "    }"
                next
            }
            # The host builds the Intel simulator too. Adding it here is safe:
            # the default hierarchy folds iosX64 into the same iosMain source set
            # the other iOS targets already use, so no new actual is required.
            iosx64 == "yes" && /^[[:space:]]*val xcfName[[:space:]]*=/ {
                print
                print "    iosX64 { binaries.framework { baseName = xcfName } }"
                next
            }
            { print }
        ' iosx64="$WANTS_IOS_X64" "$f" > "$f.new" && mv "$f.new" "$f"
        { printf 'import org.jetbrains.kotlin.gradle.dsl.JvmTarget\n\n'; cat "$f"; } > "$f.new"
        mv "$f.new" "$f"
    done
}

# A directory is not a module. `core/common/` holding nothing but Gradle's
# `build/` output is leftover from an earlier build — git-ignored, so `git clean`
# leaves it behind, and a plain -e test then reports "already exists", skips
# vendoring, and still writes include(":core:common"): a project that cannot
# configure. Require an actual module.
is_real_module() {
    [[ -f "$1/build.gradle.kts" ]] && return 0
    # A src/ DIRECTORY is not a module — removing an adoption by hand deletes the
    # files but leaves the empty skeleton behind, and an empty husk must not read
    # as "already exists" (that skips vendoring while the include is still
    # written). Require at least one actual source file.
    [[ -d "$1/src" ]] && find "$1/src" -type f -name '*.kt' 2>/dev/null | grep -q .
}

# Does a module at core/<name> look like KMPilot's own vendored copy rather than
# something the host wrote? Signature files, one per module. Distinguishes a real
# name collision (refuse) from leftovers of an adoption that was removed by hand
# without deleting the directories (say so, and let --force reuse the paths).
is_kmpilot_core() {  # <module name>
    case "$1" in
        common)       find "core/common/src" -name 'Either.kt' -o -name 'UiState.kt' 2>/dev/null | grep -q . ;;
        data)         find "core/data/src" -name 'BuildOptionProvider.kt' 2>/dev/null | grep -q . ;;
        designsystem) find "core/designsystem/src" -name 'XScreen.kt' 2>/dev/null | grep -q . ;;
        *)            return 1 ;;
    esac
}

# KMPilot artefacts present in a repo with NO .kmpilot.json — a hand-vendored
# install from before adopt mode existed, or an adoption whose manifest was
# deleted. The manifest is the normal signal and is exactly what is missing here,
# so every one of these is detected independently of it.
kmpilot_artefacts() {
    local m d
    for m in "${ADOPT_CORE[@]}"; do
        is_real_module "core/$m" && is_kmpilot_core "$m" \
            && printf 'core/%s — a KMPilot core module\n' "$m"
    done
    # Someone who hand-vendored before adopt mode existed had no reason to use
    # KMPilot's own paths, and every reason to namespace them out of the way.
    for d in core/kmpilot*; do
        [[ -d "$d" ]] && printf '%s — a KMPilot-named module\n' "$d"
    done
    [[ -f gradle/kmpilot.versions.toml ]] \
        && printf 'gradle/kmpilot.versions.toml — the kmpilotLibs catalog\n'
    grep -qs 'kmpilotLibs' settings.gradle.kts \
        && printf 'settings.gradle.kts — already registers kmpilotLibs\n'
    [[ -f .claude/skills/_shared/kmpilot_check.py ]] \
        && printf '.claude/skills/_shared/kmpilot_check.py — the architecture checker\n'
    [[ -f KMPILOT-NEXT-STEPS.md ]] \
        && printf 'KMPILOT-NEXT-STEPS.md — written by a previous adoption\n'
    # Finding nothing is the normal answer; without this the last failed test
    # becomes the exit status and `set -e` aborts the run.
    return 0
}

adopt_vendor_core() {
    step "Vendoring core modules"
    ( cd "$STAGE" && neutralize_core_app_tiers )
    substep "example ${BOLD}app/${RESET} tiers stripped"

    # Rename ONLY core/ inside the staging clone. The target's own sources are
    # never rewritten, and --no-readme keeps rename.sh away from README.md.
    local rlog; rlog="$(mktemp)"
    if ! bash "$STAGE/scripts/rename.sh" --name="$ROOT_NAME" --pkg="$PKG_PREFIX" \
            --paths=core --no-readme >"$rlog" 2>&1; then
        cat "$rlog" >&2; rm -f "$rlog"; die "rename of the vendored core failed."
    fi
    rm -f "$rlog"
    substep "identifiers → ${BOLD}${PKG_PREFIX}${RESET}, resources → ${BOLD}$(lower "$ROOT_NAME").core.*${RESET}"

    if adopt_root_declares_agp; then AGP_ON_ROOT_CLASSPATH=yes; else AGP_ON_ROOT_CLASSPATH=no; fi
    adopt_rewrite_core_builds
    substep "build files made self-contained (compileSdk ${TGT_COMPILE_SDK:-default}, kmpilotLibs)"
    [[ "$AGP_ON_ROOT_CLASSPATH" == "yes" ]] && \
        substep "AGP comes from your root build file — core binds to it without a version"

    mkdir -p core
    local m
    for m in "${ADOPT_CORE[@]}"; do
        if is_real_module "core/$m"; then
            warn "core/$m already exists — left untouched (./update.sh --core merges upstream changes)"
        else
            # copy CONTENTS: the path may already exist as a stale build/ dir
            mkdir -p "core/$m"
            cp -R "$STAGE/core/$m/." "core/$m/"
            ok "core/$m"
        fi
    done
}

adopt_write_catalog() {
    step "Writing the kmpilotLibs catalog"
    mkdir -p gradle
    {
        printf '# KMPilot dependency catalog, read by the vendored core/ and feature/ modules.\n'
        printf '# Kept separate from your own gradle/libs.versions.toml so no alias can collide.\n'
        printf '# Versions shared with your build (Kotlin, AGP, Compose, the SDK levels) are\n'
        printf '# seeded from YOUR catalog — this file never overrides them.\n#\n'
        printf '# Regenerated by install.sh --adopt --force. Safe to edit; safe to delete once\n'
        printf '# you migrate these entries into your own catalog.\n\n'
        cat "$STAGE/gradle/libs.versions.toml"
    } > gradle/kmpilot.versions.toml

    local pair key val
    for pair in "kotlin:$VER_KOTLIN" "agp:$VER_AGP" "compose-plugin:$VER_COMPOSE" \
                "android-compileSdk:$TGT_COMPILE_SDK" "android-minSdk:$TGT_MIN_SDK" \
                "android-targetSdk:$TGT_COMPILE_SDK"; do
        key="${pair%%:*}"; val="${pair#*:}"
        [[ -n "$val" ]] || continue
        sedi "s|^${key} = \"[^\"]*\"|${key} = \"${val}\"|" gradle/kmpilot.versions.toml
    done
    ok "gradle/kmpilot.versions.toml"
}

adopt_wire_settings() {
    step "Wiring settings.gradle.kts"
    local m added=0
    for m in "${ADOPT_CORE[@]}"; do
        if grep -qs "\":core:${m}\"" settings.gradle.kts; then
            substep "include(\":core:${m}\") already present"
        else
            ensure_trailing_newline settings.gradle.kts
            printf 'include(":core:%s")\n' "$m" >> settings.gradle.kts
            added=$((added + 1))
        fi
    done
    [[ $added -gt 0 ]] && ok "added ${added} include line(s)"

    if grep -qs 'kmpilotLibs' settings.gradle.kts; then
        substep "kmpilotLibs catalog already registered"
        return 0
    fi
    if [[ "$HAS_DRM" == "yes" ]]; then
        awk '
            BEGIN { done = 0 }
            /^[[:space:]]*dependencyResolutionManagement[[:space:]]*\{/ && done == 0 {
                print
                print "    versionCatalogs {"
                print "        // KMPilot core/feature modules read this catalog; yours is untouched."
                print "        create(\"kmpilotLibs\") { from(files(\"gradle/kmpilot.versions.toml\")) }"
                print "    }"
                done = 1
                next
            }
            { print }
        ' settings.gradle.kts > settings.gradle.kts.new && mv settings.gradle.kts.new settings.gradle.kts
    else
        ensure_trailing_newline settings.gradle.kts
        cat >> settings.gradle.kts <<'CATALOG_EOF'

dependencyResolutionManagement {
    versionCatalogs {
        // KMPilot core/feature modules read this catalog; yours is untouched.
        create("kmpilotLibs") { from(files("gradle/kmpilot.versions.toml")) }
    }
}
CATALOG_EOF
    fi
    ok "kmpilotLibs catalog registered"
}

# The architecture gate. The checker itself is standalone (python3, no Gradle),
# but `./gradlew archTest` is the form CI and contributors actually run, so the
# task is appended to the target's root build — additive, idempotent, and the
# only edit adopt mode makes to that file.
adopt_wire_archtest() {
    step "Registering the archTest gate"
    if [[ ! -f build.gradle.kts ]]; then
        warn "no root build.gradle.kts — run the checker directly:"
        substep "python3 .claude/skills/_shared/kmpilot_check.py --all"
        return 0
    fi
    if grep -qs '"archTest"' build.gradle.kts; then
        substep "archTest already registered"
        return 0
    fi
    ensure_trailing_newline build.gradle.kts
    cat >> build.gradle.kts <<'ARCHTEST_EOF'

// Deterministic architecture checker (see .claude/skills/_shared/kmpilot_check.py).
// Thin wrapper on purpose — all logic lives in the script so it stays runnable
// standalone from CI, a pre-commit hook, or a project without this task.
tasks.register<Exec>("archTest") {
    group = "verification"
    description = "Checks feature modules against the KMPilot architecture rules."
    workingDir = rootDir
    commandLine("python3", ".claude/skills/_shared/kmpilot_check.py", "--all")
    doFirst {
        val onPath =
            System.getenv("PATH")
                ?.split(File.pathSeparator)
                ?.any { dir -> File(dir, "python3").canExecute() || File(dir, "python3.exe").canExecute() }
                ?: false
        if (!onPath) {
            throw GradleException(
                "archTest needs python3 on PATH (macOS: `brew install python`, " +
                    "Windows: python.org installer + Git Bash). Or run the checker directly: " +
                    "python3 .claude/skills/_shared/kmpilot_check.py --all",
            )
        }
    }
}
ARCHTEST_EOF
    ok "./gradlew archTest"
}

adopt_wire_app_module() {
    local f="$APP_MODULE/build.gradle.kts"
    step "Wiring ${f}"
    if [[ ! -f "$f" ]]; then
        warn "no build file at $f — add the three core dependencies by hand"
        return 0
    fi
    local m missing=()
    for m in "${ADOPT_CORE[@]}"; do
        grep -qs "\":core:${m}\"" "$f" || missing+=("$m")
    done
    if [[ ${#missing[@]} -eq 0 ]]; then
        substep "core dependencies already present"
        return 0
    fi
    # Insert into commonMain's dependencies block, whichever of the two shapes
    # it uses: `commonMain.dependencies {` or `commonMain { … dependencies {`.
    if awk -v mods="${missing[*]}" '
            function emit(   i, n, arr) {
                n = split(mods, arr, " ")
                for (i = 1; i <= n; i++) {
                    print "            implementation(project(\":core:" arr[i] "\"))"
                }
            }
            BEGIN { done = 0; incommon = 0 }
            done == 0 && /commonMain[[:space:]]*\.[[:space:]]*dependencies[[:space:]]*\{/ { print; emit(); done = 1; next }
            done == 0 && /commonMain[[:space:]]*\{/ { incommon = 1; print; next }
            done == 0 && incommon == 1 && /dependencies[[:space:]]*\{/ { print; emit(); done = 1; next }
            { print }
            END { if (done == 0) exit 3 }
        ' "$f" > "$f.new"; then
        mv "$f.new" "$f"
        ok "added ${#missing[@]} core dependency line(s)"
    else
        rm -f "$f.new"
        warn "could not find a commonMain dependencies block in $f — add these by hand:"
        for m in "${missing[@]}"; do substep "implementation(project(\":core:${m}\"))"; done
    fi
}

# New files only. Adopt mode never edits the target's Kotlin, so the last two
# lines of wiring (startKoin + the nav graph) are printed, not applied.
adopt_write_app_glue() {
    local dir="$APP_MODULE/src/commonMain/kotlin/${PKG_PREFIX//.//}/kmpilot"
    step "Writing the Koin glue"
    mkdir -p "$dir"

    if [[ -f "$dir/BuildOptionProviderImpl.kt" ]]; then
        substep "BuildOptionProviderImpl.kt already exists — left untouched"
    else
        cat > "$dir/BuildOptionProviderImpl.kt" <<'GLUE_EOF'
package __PKG__.kmpilot

import __PKG__.data.config.BuildOptionProvider

/**
 * Supplies :core:data with the two build-time values it needs. Point [apiBaseUrl]
 * at your own backend — the KMPilot template reads these from BuildKonfig, but an
 * adopted project should use whatever it already has.
 */
class BuildOptionProviderImpl : BuildOptionProvider {
    override val apiBaseUrl: String = "https://api.example.com/"

    override val appVersion: String = "1.0.0"
}
GLUE_EOF
        sedi "s|__PKG__|${PKG_PREFIX}|g" "$dir/BuildOptionProviderImpl.kt"
        ok "${dir#./}/BuildOptionProviderImpl.kt"
    fi

    if [[ -f "$dir/KmpilotModules.kt" ]]; then
        substep "KmpilotModules.kt already exists — left untouched"
    else
        cat > "$dir/KmpilotModules.kt" <<'GLUE_EOF'
package __PKG__.kmpilot

import __PKG__.common.di.commonModule
import __PKG__.data.config.BuildOptionProvider
import __PKG__.data.di.dataModule
import org.koin.core.module.Module
import org.koin.core.module.dsl.singleOf
import org.koin.dsl.bind
import org.koin.dsl.module

/**
 * The Koin modules KMPilot's core needs. Add this to your existing startKoin:
 *
 *     startKoin {
 *         modules(kmpilotModules)
 *         modules(yourOwnModules)
 *     }
 *
 * Feature modules generated by /create-feature register themselves the same way
 * — one `{featurename}Module` per feature, added to your startKoin call.
 */
val kmpilotModules: List<Module> =
    listOf(
        module { singleOf(::BuildOptionProviderImpl).bind<BuildOptionProvider>() },
        commonModule,
        dataModule,
    )
GLUE_EOF
        sedi "s|__PKG__|${PKG_PREFIX}|g" "$dir/KmpilotModules.kt"
        ok "${dir#./}/KmpilotModules.kt"
    fi

    # A host with no Koin at all has nothing to add `kmpilotModules` to, so the
    # printed instruction would point at a startKoin that does not exist. Give it
    # one. Still new-files-only: written beside the glue, never spliced into the
    # project's own entry points — calling it stays the developer's decision.
    if [[ "$HAS_KOIN_START" == "yes" ]]; then
        substep "your project starts Koin already — add modules(kmpilotModules) to it"
    elif [[ -f "$dir/InitKmpilotKoin.kt" ]]; then
        substep "InitKmpilotKoin.kt already exists — left untouched"
    else
        cat > "$dir/InitKmpilotKoin.kt" <<'GLUE_EOF'
package __PKG__.kmpilot

import org.koin.core.KoinApplication
import org.koin.core.context.startKoin
import org.koin.dsl.KoinAppDeclaration

/**
 * Starts Koin with the modules KMPilot's core needs. This project had no
 * `startKoin` when it was adopted, so one is provided here rather than leaving
 * you an instruction with nothing to attach it to.
 *
 * Call it once at launch, and add your own modules alongside:
 *
 *   Android — in your Application.onCreate():
 *       initKmpilotKoin { androidContext(this@MyApplication) }
 *   iOS — from your entry point:
 *       initKmpilotKoin()
 *
 * Already have your own DI setup? Delete this file and add `modules(kmpilotModules)`
 * to your existing startKoin instead.
 */
fun initKmpilotKoin(appDeclaration: KoinAppDeclaration = {}): KoinApplication =
    startKoin {
        appDeclaration()
        modules(kmpilotModules)
    }
GLUE_EOF
        sedi "s|__PKG__|${PKG_PREFIX}|g" "$dir/InitKmpilotKoin.kt"
        ok "${dir#./}/InitKmpilotKoin.kt"
        warn "No startKoin found in this project — wrote one. Call initKmpilotKoin() at launch."
    fi
}

adopt_write_tooling() {
    step "Installing the Claude Code pipeline"
    mkdir -p .claude
    local p
    for p in skills agents commands hooks; do
        [[ -d "$STAGE/.claude/$p" ]] || continue
        rm -rf ".claude/$p"
        cp -R "$STAGE/.claude/$p" ".claude/$p"
    done
    chmod +x .claude/hooks/*.sh 2>/dev/null || true
    ok ".claude/{skills,agents,commands,hooks}"

    # Every `archTest` run rewrites check-report.json. KMPilot ignores it in its
    # own .gitignore; an adopted repo has no such line, so the file would show up
    # as a modification on every single run. A .gitignore inside the directory
    # KMPilot owns fixes that without editing the project's own ignore file.
    mkdir -p .claude/docs/_project
    if [[ ! -f .claude/docs/_project/.gitignore ]]; then
        printf '# Regenerated by every archTest run — not worth versioning.\ncheck-report.json\n' \
            > .claude/docs/_project/.gitignore
        ok ".claude/docs/_project/.gitignore"
    fi

    adopt_place_or_sidecar "$STAGE/.claude/settings.json" .claude/settings.json \
        "merge its hooks block in, or the feature-file guard will not fire"
    adopt_place_or_sidecar "$STAGE/CLAUDE.md" CLAUDE.md \
        "merge it into yours — the skills read CLAUDE.md for the architecture rules"
}

# Files the target may legitimately own already. Never clobbered: written only
# when absent, left alone when already identical, and otherwise dropped beside
# the original as *.kmpilot.* for a manual merge. Comparing content (not mere
# existence) is what keeps a --force re-run from spawning sidecars of itself.
adopt_place_or_sidecar() {  # <staged> <destination> <merge hint>
    local staged="$1" dest="$2" hint="$3" side
    if [[ ! -f "$dest" ]]; then
        cp "$staged" "$dest"
        ok "$dest"
        return 0
    fi
    if cmp -s "$staged" "$dest"; then
        substep "$dest already matches KMPilot's — unchanged"
        return 0
    fi
    side="${dest%.*}.kmpilot.${dest##*.}"
    cp "$staged" "$side"
    warn "kept your $dest — KMPilot's version is at $side"
    substep "$hint"
}

adopt_write_manifest() {
    local version now
    # "host"     — the project starts Koin itself; the integrator adds
    #              kmpilotModules to that existing call.
    # "supplied" — adopt wrote InitKmpilotKoin.kt and nothing calls it yet; the
    #              integrator wires the call when it scaffolds the first feature.
    local KOIN_BOOTSTRAP="host"
    [[ "$HAS_KOIN_START" == "yes" ]] || KOIN_BOOTSTRAP="supplied"
    if [[ "$TEMPLATE_BRANCH" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
        version="${TEMPLATE_BRANCH#v}"
    elif [[ -f "$STAGE/VERSION" ]]; then
        version="$(tr -d '[:space:]' < "$STAGE/VERSION")"
    else
        version="unknown"
    fi
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > .kmpilot.json <<MANIFEST_EOF
{
  "kmpilotVersion": "${version}",
  "projectName": "${ROOT_NAME}",
  "packagePrefix": "${PKG_PREFIX}",
  "installMode": "adopt",
  "appModule": "${APP_MODULE}",
  "catalogAccessor": "kmpilotLibs",
  "koinBootstrap": "${KOIN_BOOTSTRAP}",
  "adoptedCoreModules": ["core:common", "core:data", "core:designsystem"],
  "managedFeatures": [],
  "templateRepo": "${TEMPLATE_REPO}",
  "upstreamPkg": "thisissadeghi",
  "upstreamName": "KMPilot",
  "installedAt": "${now}"
}
MANIFEST_EOF
    ok "Wrote .kmpilot.json (version ${version}, mode adopt)"
}

# Terminal output scrolls away; a file survives, gets reviewed in the diff, and
# can be deleted when the work is done. Everything adopt could not do for itself
# is written here, so nothing important lives only in scrollback.
adopt_write_next_steps() {
    local koin_section
    if [[ "$HAS_KOIN_START" == "yes" ]]; then
        koin_section="Your project already starts Koin. Add KMPilot's modules to that call:

    modules(kmpilotModules)   // import ${PKG_PREFIX}.kmpilot.kmpilotModules

\`/create-feature\` does this for you the first time you scaffold a feature — it edits the
same \`startKoin { }\` block to register the feature's own module. Doing it by hand now is
optional."
    else
        koin_section="This project had no \`startKoin\`, so adopt wrote one:
\`${APP_MODULE}/src/commonMain/kotlin/${PKG_PREFIX//.//}/kmpilot/InitKmpilotKoin.kt\`

Call it once at launch:

    // Android — Application.onCreate()
    initKmpilotKoin { androidContext(this@YourApplication) }

    // iOS — your entry point
    initKmpilotKoin()

\`/create-feature\` wires this call for you the first time you scaffold a feature. Doing it
by hand now is optional — but nothing Koin-injected will resolve until one or the other
happens."
    fi

    cat > KMPILOT-NEXT-STEPS.md <<NEXTSTEPS_EOF
# KMPilot — what is left to do

Adopted on $(date -u +%Y-%m-%d). Delete this file when you are done with it.

## What landed

- \`core/common\`, \`core/data\`, \`core/designsystem\` — vendored, renamed to \`${PKG_PREFIX}\`
- \`gradle/kmpilot.versions.toml\` — KMPilot's dependencies, exposed as \`kmpilotLibs\`.
  Your own \`libs\` catalog was not touched.
- \`.claude/\` + \`CLAUDE.md\` — the pipeline the skills read
- \`archTest\` task in the root build — the architecture gate

Your own code was not edited. Everything above is in \`git diff\`.

## 1. Koin

${koin_section}

## 2. Build

    ./gradlew assembleDebug
    ./gradlew archTest

## 3. Your first feature

    claude
    > /create-feature a profile screen with an avatar and an editable display name

It scaffolds the module, registers it in Koin and in your navigation graph, then
\`./gradlew archTest\` holds it to the 14 architecture rules.

## Notes

- Features are generated into \`feature/{name}/\`. Existing modules of yours are left alone —
  the checker only looks at \`feature/*\`.
- \`./update.sh\` pulls newer KMPilot releases without touching your code.
- Compatibility, and what is deliberately unsupported:
  https://github.com/ThisIsSadeghi/KMPilot/blob/main/ADOPTING.md
NEXTSTEPS_EOF
    ok "KMPILOT-NEXT-STEPS.md"
}

adopt_apply() {
    adopt_vendor_core
    adopt_write_catalog
    adopt_wire_settings
    adopt_wire_archtest
    adopt_wire_app_module
    adopt_write_app_glue
    adopt_write_tooling
    adopt_write_manifest
    adopt_write_next_steps

    printf '\n'
    printf '%s%s  ✓ %s adopted KMPilot%s\n\n' "$BOLD" "$GREEN" "$ROOT_NAME" "$RESET"
    printf '  %sYour own code was not edited.%s Everything above is in %sgit diff%s.\n' "$BOLD" "$RESET" "$CYAN" "$RESET"
    if [[ "$HAS_KOIN_START" == "yes" ]]; then
        printf '  Koin wiring (%smodules(kmpilotModules)%s) is added by %s/create-feature%s with your\n' \
            "$CYAN" "$RESET" "$CYAN" "$RESET"
        printf '  first feature — or add it yourself now; both are fine.\n\n'
    else
        printf '  This project starts no Koin, so one was written for you\n'
        printf '  (%sinitKmpilotKoin()%s). %s/create-feature%s wires the call with your first feature.\n\n' \
            "$CYAN" "$RESET" "$CYAN" "$RESET"
    fi
    printf '  %sNext%s\n' "$BOLD" "$RESET"
    printf '    %s$%s git diff                %s# review everything that landed%s\n' "$DIM" "$RESET" "$DIM" "$RESET"
    printf '    %s$%s ./gradlew assembleDebug\n' "$DIM" "$RESET"
    printf '    %s$%s ./gradlew archTest      %s# the architecture gate%s\n' "$DIM" "$RESET" "$DIM" "$RESET"
    printf '    %s$%s claude                  %s# then: /create-feature a profile screen%s\n\n' "$DIM" "$RESET" "$DIM" "$RESET"
    printf '  %sFull details, including anything left for you: %sKMPILOT-NEXT-STEPS.md%s\n\n' "$DIM" "$RESET" "$RESET"
}

adopt_main() {
    banner
    adopt_detect
    adopt_stage_clone
    adopt_report
    adopt_baseline_check
    adopt_plan
    adopt_print_plan

    if [[ "$DRY_RUN" == "yes" ]]; then
        printf '  %sDry run — nothing was written.%s Re-run without --dry-run to apply.\n\n' "$BOLD" "$RESET"
        return 0
    fi

    confirm
    printf '\n'
    adopt_apply
}

if [[ "$ADOPT" == "yes" ]]; then
    resolve_template_ref
    adopt_main
    exit 0
fi

banner

[[ -n "$NAME" ]] || prompt_name
valid_name "$NAME" || die "Invalid project name '$NAME' (letters and digits, start with a letter)."
prompt_pkg

if [[ -e "$NAME" ]]; then die "'$NAME' already exists in $(pwd)."; fi

resolve_template_ref

# Recap what we're about to build.
printf '\n'
printf '    %sProject%s   %s%s%s\n'  "$DIM" "$RESET" "$BOLD" "$NAME" "$RESET"
printf '    %sPackage%s   %s\n'      "$DIM" "$RESET" "$PKG"
printf '    %sTemplate%s  %s %s(%s)%s\n' "$DIM" "$RESET" "$TEMPLATE_REPO" "$DIM" "$TEMPLATE_BRANCH" "$RESET"
printf '    %sTarget%s    %s/%s\n'   "$DIM" "$RESET" "$(pwd)" "$NAME"
confirm
printf '\n'

# ─────────────────────────────────────────────────────────────────────────────
# Core template surgery — logic identical to install.sh, only the echoes restyled
# ─────────────────────────────────────────────────────────────────────────────

trim_template() {
    # Strip KMPilot's example features from the cloned target and write a
    # minimal Welcome screen so the empty shell still compiles and runs.
    # Runs BEFORE rename.sh so source files still use the template's
    # original identifiers (thisissadeghi.*) — rename.sh will rewrite the
    # new files along with everything else.
    #
    # NOTE: This trims the CLONE only. The KMPilot repo itself keeps
    # feature/dashboard/ (and any other sample features) as a working
    # reference implementation.

    # 1-3. Strip EVERY example feature module and its wiring. Generic on purpose:
    #       a hardcoded list silently drifts whenever a sample feature is added
    #       (it did — assetdetail/swap/profile shipped to fresh installs). Looping
    #       over feature/*/ keeps the clean-slate guarantee no matter what ships.
    local koin="composeApp/src/commonMain/kotlin/thisissadeghi/kmpilot/initKoin.kt"
    if [[ -d feature ]]; then
        for fdir in feature/*/; do
            # Only real modules (have a build.gradle.kts); skips gradle's build/ dir.
            [[ -f "${fdir}build.gradle.kts" ]] || continue
            local fname
            fname="$(basename "$fdir")"
            rm -rf "$fdir"
            sedi "/include(\":feature:${fname}\")/d"            settings.gradle.kts
            sedi "/project(\":feature:${fname}\")/d"            composeApp/build.gradle.kts
            sedi "/\"kover\"(project(\":feature:${fname}\"))/d" build.gradle.kts
            sedi "/import thisissadeghi\\.${fname}\\./d"        "$koin"
            sedi "/^[[:space:]]*${fname}Module,/d"              "$koin"
        done
    fi

    # 3a. Replace the mock-API BASE_URL with a neutral placeholder. The template
    #     ships KMPilot's own mock API URL, which rename.sh would otherwise
    #     mangle (it rewrites every occurrence of `thisissadeghi` and `KMPilot`
    #     across the tree, producing a malformed URL in the new project).
    sedi 's|https://thisissadeghi\.github\.io/KMPilot/mock-api/|https://api.example.com/|g' \
        composeApp/build.gradle.kts

    # 4. Write a Welcome screen so the empty shell compiles and runs.
    #    User deletes this file when adding their first feature.
    cat > composeApp/src/commonMain/kotlin/thisissadeghi/kmpilot/WelcomeScreen.kt <<'WELCOME_EOF'
package thisissadeghi.kmpilot

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.serialization.Serializable

@Serializable
object WelcomeRoute

@Composable
fun WelcomeScreen() {
    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 32.dp, vertical = 56.dp),
            contentAlignment = Alignment.TopCenter,
        ) {
            Column(modifier = Modifier.widthIn(max = 560.dp)) {
                EyebrowLabel("// STARTER")
                Spacer(Modifier.height(16.dp))
                Text(
                    text = "Welcome to your new app.",
                    style = MaterialTheme.typography.displaySmall,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onBackground,
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    text = "This is a starter screen. Replace it with your first feature — the scaffolding agent takes care of the wiring.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                Spacer(Modifier.height(56.dp))
                EyebrowLabel("CHOOSE A PATH  ·  NOT BOTH")
                Spacer(Modifier.height(20.dp))

                PathCard(
                    index = "01",
                    title = "Code-first",
                    description = "Skip the design step and scaffold straight into Kotlin.",
                    commands = listOf("/create-feature"),
                )
                Spacer(Modifier.height(12.dp))
                PathCard(
                    index = "02",
                    title = "Design-first",
                    description = "Design your screens in Stitch, then let the blueprint drive the scaffold.",
                    commands = listOf("/design-ui", "/create-feature"),
                )

                Spacer(Modifier.height(56.dp))
                EyebrowLabel("IN YOUR TOOLBOX")
                Spacer(Modifier.height(16.dp))

                CommandRow("/modify-feature", "Change an existing feature")
                CommandRow("/test-feature", "Generate the test suite")
                CommandRow("/review-feature", "Audit the architecture")
                CommandRow("/verify-ui", "Compare implementation against Stitch design")

                Spacer(Modifier.height(40.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Spacer(Modifier.height(16.dp))
                Text(
                    text = "Run any command inside Claude Code. The scaffolding agent removes this screen as soon as your first feature is wired in.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun EyebrowLabel(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        fontFamily = FontFamily.Monospace,
        letterSpacing = 2.sp,
        color = MaterialTheme.colorScheme.primary,
    )
}

@Composable
private fun PathCard(
    index: String,
    title: String,
    description: String,
    commands: List<String>,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 1.dp,
        shape = RoundedCornerShape(12.dp),
    ) {
        Row(
            modifier = Modifier.padding(20.dp),
            horizontalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Text(
                text = index,
                style = MaterialTheme.typography.titleMedium,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.primary,
            )
            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = description,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(14.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    commands.forEachIndexed { i, cmd ->
                        if (i > 0) {
                            Text(
                                text = "→",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        CodeChip(cmd)
                    }
                }
            }
        }
    }
}

@Composable
private fun CodeChip(text: String) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(6.dp),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
        )
    }
}

@Composable
private fun CommandRow(command: String, description: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(20.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(modifier = Modifier.widthIn(min = 220.dp)) {
            CodeChip(command)
        }
        Text(
            text = description,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}
WELCOME_EOF

    # 5. Replace nav host with a Welcome-only version. The integration
    #    agent in /create-feature swaps this for the first feature's
    #    route (and deletes WelcomeScreen.kt) — see integrator.md.
    cat > composeApp/src/commonMain/kotlin/thisissadeghi/kmpilot/BaseAppNavHost.kt <<'NAVHOST_EOF'
package thisissadeghi.kmpilot

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import thisissadeghi.designsystem.XNavHost

/**
 * Main app navigation host.
 * Routes to WelcomeScreen until the first feature is wired in.
 */
@Composable
fun BaseAppNavHost(modifier: Modifier) {
    val navController = rememberNavController()

    XNavHost(
        modifier = modifier,
        navController = navController,
        startDestination = WelcomeRoute,
    ) {
        composable<WelcomeRoute> { WelcomeScreen() }
    }
}
NAVHOST_EOF

    # 6. Strip KMPilot's own dev history. Skills recreate
    #    .claude/docs/{feature}/ as needed.
    rm -rf .claude/docs
    rm -f  .claude/settings.local.json

    # 7. Stray macOS metadata
    find . -name ".DS_Store" -type f -delete 2>/dev/null || true

    # 8. KMPilot-only project files (upstream meta + sample mock data, not
    #    wanted in a user project). LICENSE/CONTRIBUTING are KMPilot's own;
    #    mock-api/finance only fed the (now-removed) dashboard sample, and
    #    .github/workflows/pages.yml only existed to publish that mock data;
    #    assets/ held KMPilot README screenshots; the .pbxproj.backup is a
    #    stray Xcode backup that rename.sh's *.pbxproj glob does not rewrite.
    rm -f  CONTRIBUTING.md LICENSE ADOPTING.md
    rm -rf .github assets mock-api
    rm -f  iosApp/iosApp.xcodeproj/project.pbxproj.backup

    neutralize_core_app_tiers
}

write_fresh_docs() {
    # Replace KMPilot's own README + CHANGELOG (upstream marketing / release history) with
    # a minimal project README and an empty changelog, so the new project owns its docs
    # instead of inheriting the template's. Runs AFTER rename.sh so the upstream KMPilot
    # credit/links written below are NOT rewritten by the rename. Uses a quoted heredoc
    # (literal) + a placeholder sed so the code fences and links stay intact.
    cat > README.md <<'README_EOF'
# __PROJECT_NAME__

A Kotlin Multiplatform + Compose Multiplatform app, generated from
[KMPilot](https://github.com/ThisIsSadeghi/KMPilot).

## Build

```bash
./gradlew assembleDebug          # Android
# open iosApp/iosApp.xcodeproj in Xcode for iOS
```

## Build features with Claude Code

Run the scaffolding commands inside [Claude Code](https://claude.ai/code):

```
/create-feature            # scaffold a new feature
/modify-feature           # change an existing one
/test-feature                    # generate its test suite
/review-feature                  # audit the architecture
```

## Staying up to date

Pull newer KMPilot releases without touching your code:

```bash
./update.sh            # tooling only (.claude skills/agents/hooks, CLAUDE.md, gradle wrapper)
./update.sh --core     # also merge core/ modules (rename-aware; conflicts surfaced, never silent)
./update.sh --dry-run  # preview what would change; writes nothing
```

Release notes live in the upstream
[CHANGELOG](https://github.com/ThisIsSadeghi/KMPilot/blob/main/CHANGELOG.md).
README_EOF
    sedi "s/__PROJECT_NAME__/${NAME}/g" README.md

    cat > CHANGELOG.md <<'CHANGELOG_EOF'
# Changelog

All notable changes to this project are documented here.

## [Unreleased]
CHANGELOG_EOF
}

write_manifest() {
    # Records identity + installed version so update.sh can later diff against
    # upstream and re-apply the package rename. Written AFTER rename (so the
    # upstream identifiers below are NOT rewritten) and committed in the initial
    # commit. Keystone artifact — without it, update.sh has no baseline or pkg.
    # Prefer the resolved release tag — it is authoritative, and update.sh diffs from
    # v$version, so the manifest MUST match the tag actually cloned. Fall back to the
    # VERSION file only for bleeding-edge (main) installs that aren't on a tag.
    local version
    if [[ "$TEMPLATE_BRANCH" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
        version="${TEMPLATE_BRANCH#v}"
    elif [[ -f VERSION ]]; then
        version="$(tr -d '[:space:]' < VERSION)"
    else
        version="unknown"
    fi
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > .kmpilot.json <<MANIFEST_EOF
{
  "kmpilotVersion": "${version}",
  "projectName": "${NAME}",
  "packagePrefix": "${PKG}",
  "templateRepo": "${TEMPLATE_REPO}",
  "upstreamPkg": "thisissadeghi",
  "upstreamName": "KMPilot",
  "installedAt": "${now}"
}
MANIFEST_EOF
    ok "Wrote .kmpilot.json (version ${version}, package ${PKG})"
}

# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

step "Cloning KMPilot template"
substep "ref: ${BOLD}${TEMPLATE_BRANCH}${RESET}"
spinner "Cloning into ${NAME}/ ..." \
    git -c advice.detachedHead=false clone --depth=1 --branch "$TEMPLATE_BRANCH" --quiet "$TEMPLATE_REPO" "$NAME" \
    || die "Clone failed. Check the repo URL / ref and your network."
ok "Cloned into ${BOLD}${NAME}/${RESET}"

cd "$NAME"
rm -rf .git

step "Trimming to a fresh project shell"
trim_template
ok "Example features removed, Welcome screen written"

step "Renaming project to ${BOLD}${NAME}${RESET}"
substep "package ${PKG}"
# rename.sh is chatty; capture its output so it doesn't break the styled log,
# and surface it only if the rename fails.
RENAME_LOG="$(mktemp)"
if spinner "Rewriting identifiers ..." \
        bash -c 'bash scripts/rename.sh --name="$1" --pkg="$2" >"$3" 2>&1' _ "$NAME" "$PKG" "$RENAME_LOG"; then
    rm -f "$RENAME_LOG"
    ok "Identifiers rewritten"
else
    cat "$RENAME_LOG" >&2; rm -f "$RENAME_LOG"; die "rename.sh failed."
fi

# Template-only files we don't need in a user project. scripts/rename.sh is a
# one-shot installer tool (it has already run above and still embeds KMPilot's
# OLD identifiers); scripts/ is empty once it's gone. update.sh is KEPT — it is
# the downstream's entrypoint for pulling future releases. install.sh is the
# installer itself; already run, and not wanted in the generated project.
rm -f install.sh
rm -rf scripts

# iOS: bootstrap CocoaPods so Xcode can build out of the box. macOS only —
# pod install is required before the first iOS build because the Xcode project
# has a [CP] Check Pods Manifest.lock build phase that aborts the build until
# Pods/ and Podfile.lock exist.
if [[ "$(uname -s)" == "Darwin" ]]; then
    step "iOS setup"
    if command -v pod >/dev/null 2>&1; then
        if spinner "Running pod install ..." bash -c 'cd iosApp && pod install >/dev/null 2>&1'; then
            ok "Pods installed"
        else
            warn "pod install failed — run 'cd $NAME/iosApp && pod install' manually"
            substep "Common fix: brew install cocoapods"
        fi
    else
        warn "CocoaPods not installed — skipping pod install"
        substep "For iOS builds: brew install cocoapods && cd $NAME/iosApp && pod install"
    fi
fi

# Replace the template's README/CHANGELOG with fresh project docs (post-rename, so the
# upstream KMPilot credit links survive), then stamp the update manifest, then drop the
# upstream VERSION file — kmpilotVersion now lives in .kmpilot.json.
step "Writing project docs + manifest"
write_fresh_docs
write_manifest
rm -f VERSION
ok "README.md + CHANGELOG.md written"

step "Initializing git repository"
git init --quiet
git add -A
git -c user.email=kmpilot@local -c user.name=kmpilot commit --quiet \
    -m "Initial commit from KMPilot template"
ok "Initial commit created"

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
printf '\n'
printf '%s%s  ✓ %s is ready%s  %s%s\n' "$BOLD" "$GREEN" "$NAME" "$RESET" "$DIM" "$RESET"
printf '%s    %s%s\n\n' "$DIM" "$(pwd)$RESET" ""
printf '  %sNext%s\n' "$BOLD" "$RESET"
printf '    %s$%s cd %s\n'   "$DIM" "$RESET" "$NAME"
printf '    %s$%s claude\n'  "$DIM" "$RESET"
printf '\n'
printf '  %sthen try%s  %s/design-ui%s  or  %s/create-feature%s\n\n' \
    "$DIM" "$RESET" "$CYAN" "$RESET" "$CYAN" "$RESET"
