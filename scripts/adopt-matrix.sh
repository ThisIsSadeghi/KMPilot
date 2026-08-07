#!/usr/bin/env bash
# Compatibility matrix for `install.sh --adopt`.
#
# Generates the scratch KMP fixture once per variant, mutates it into the shape
# under test, runs `--adopt --dry-run`, and asserts the outcome. Three outcomes
# count as correct:
#
#   ADOPTS   — exit 0, and the plan says what it should say
#   WARNS    — exit 0, and the named warning is present
#   REFUSES  — non-zero exit, and the refusal explains itself
#
# A clean refusal is a PASS. Silently breaking someone's build is the failure we
# are testing for, and a wrong refusal (telling a real KMP project it is not one)
# is the second.
#
# Everything here is detection-only: `--adopt --dry-run` is pure bash — no Gradle,
# no JDK, no Android SDK — so a variant takes about a second. The full
# adopt → assembleDebug → archTest path is exercised separately on the baseline
# fixture only, because that part is slow and already known to work.
#
# Usage:
#   scripts/adopt-matrix.sh            # run every variant
#   scripts/adopt-matrix.sh iosx64     # run variants matching a substring
#   KEEP=1 scripts/adopt-matrix.sh     # keep the generated fixtures for poking at
#
# Exit code is the number of failing variants, so CI can gate on it.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KMPILOT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN="${SCRIPT_DIR}/make-adopt-target.sh"
FILTER="${1:-}"

WORK="$(mktemp -d)"
cleanup() { [[ "${KEEP:-0}" == "1" ]] && { echo "fixtures kept in $WORK"; return; }; rm -rf "$WORK"; }
trap cleanup EXIT

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
else
    GREEN=""; RED=""; DIM=""; BOLD=""; OFF=""
fi

# Cross-platform sed -i (BSD/macOS vs GNU/Linux) — this script runs locally on
# macOS and in CI on Linux, and the two disagree about -i's argument.
if [[ "$(uname -s)" == "Darwin" ]]; then
    sedi() { sed -i '' "$@"; }
else
    sedi() { sed -i "$@"; }
fi

# Insert TEXT after the first line CONTAINING the literal NEEDLE. awk rather
# than `sed i\`, whose syntax differs between BSD and GNU in ways that silently
# mangle output — and index() rather than a regex, because a pattern travelling
# through `awk -v` gets its backslashes eaten before it ever reaches the matcher.
insert_after() {  # insert_after <file> <literal-needle> <text>
    awk -v needle="$2" -v txt="$3" '
        !done && index($0, needle) { print; print txt; done = 1; next }
        { print }
    ' "$1" > "$1.tmp" && mv "$1.tmp" "$1"
}

prepend_line() {  # prepend_line <file> <text>
    { printf '%s\n' "$2"; cat "$1"; } > "$1.tmp" && mv "$1.tmp" "$1"
}

PASS=0; FAIL=0
ROWS=()

# ── variant mutations ────────────────────────────────────────────────────────
# Each takes the fixture root as $1 and edits it in place. The baseline fixture
# is already a valid, adoptable KMP project; these bend it out of shape.

mut_baseline() { :; }

mut_iosx64() {  # host builds the Intel simulator too
    insert_after "$1/shared/build.gradle.kts" 'iosArm64(),' '        iosX64(),'
}

mut_wasm() {    # a target KMPilot core has no actuals for
    insert_after "$1/shared/build.gradle.kts" 'kotlin {' '    wasmJs { browser() }'
}

mut_convention_plugins() {
    # The KMP plugin is applied by a convention plugin in an included build, so
    # no module build file names it. This is the shape that used to false-refuse.
    mkdir -p "$1/build-logic/src/main/kotlin"
    cat > "$1/build-logic/settings.gradle.kts" <<'EOF'
rootProject.name = "build-logic"
EOF
    cat > "$1/build-logic/build.gradle.kts" <<'EOF'
plugins { `kotlin-dsl` }
EOF
    cat > "$1/build-logic/src/main/kotlin/acme.kmp.library.gradle.kts" <<'EOF'
plugins {
    id("org.jetbrains.kotlin.multiplatform")
}
EOF
    prepend_line "$1/settings.gradle.kts" 'pluginManagement { includeBuild("build-logic") }'
    # module no longer names the KMP plugin itself
    sedi 's|    alias(libs.plugins.kotlinMultiplatform)|    id("acme.kmp.library")|' "$1/shared/build.gradle.kts"
}

mut_no_catalog() {
    rm -f "$1/gradle/libs.versions.toml"
    # inline coordinates instead of catalog accessors (text-level is enough here)
    sedi 's|libs\.plugins\.[A-Za-z.]*|PLUGIN_INLINE|g; s|libs\.[A-Za-z.]*|"group:artifact:1.0"|g' \
        "$1/shared/build.gradle.kts" "$1/app/build.gradle.kts" "$1/build.gradle.kts"
}

mut_no_koin() {
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/initKoin.kt"
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/di/AppModule.kt"
    sedi '/koin/d' "$1/shared/build.gradle.kts" "$1/app/build.gradle.kts" "$1/gradle/libs.versions.toml"
    sedi '/initKoin\|androidContext\|NotesApplication/d' "$1/app/src/main/AndroidManifest.xml"
    rm -f "$1/app/src/main/kotlin/com/acme/notes/android/NotesApplication.kt"
}

mut_arrow() {
    printf '\narrow-core = { module = "io.arrow-kt:arrow-core", version = "2.0.0" }\n' \
        >> "$1/gradle/libs.versions.toml"
    mkdir -p "$1/shared/src/commonMain/kotlin/com/acme/notes/util"
    cat > "$1/shared/src/commonMain/kotlin/com/acme/notes/util/Results.kt" <<'EOF'
package com.acme.notes.util

import arrow.core.Either

fun ok(): Either<String, Int> = Either.Right(1)
EOF
}

mut_own_ds() {
    mkdir -p "$1/designsystem/src/commonMain/kotlin/com/acme/notes/designsystem"
    cat > "$1/designsystem/build.gradle.kts" <<'EOF'
plugins { alias(libs.plugins.kotlinMultiplatform) }
EOF
    printf 'include(":designsystem")\n' >> "$1/settings.gradle.kts"
}

mut_groovy() {
    # Groovy settings — parked, must refuse cleanly
    sed 's|rootProject.name = "AcmeNotes"|rootProject.name = "AcmeNotes"|' "$1/settings.gradle.kts" \
        > "$1/settings.gradle"
    rm -f "$1/settings.gradle.kts"
}

# ── how the vendored core names the AGP plugin (Phase 6 step 9, finding 15) ──
#
# Once ANY `com.android.*` plugin is declared in the root build file, the whole AGP
# artifact is on the build classpath — including the plugins the root did not name.
# A vendored core module that then requests `com.android.kotlin.multiplatform.library`
# WITH a version is refused outright:
#
#   Error resolving plugin [id: '…', version: '9.0.1']
#   > already on the classpath with an unknown version
#
# The base fixture names both `androidApplication` and `androidKmpLibrary` in its root,
# so both versions are known and a versioned alias resolves. A single-module project —
# one composeApp that is BOTH the KMP module and the Android application, which is what
# the Phase-6 monolith test bed is — names only `androidApplication`. That project could
# not run `./gradlew help` after adoption at all, and Phase 2 never saw it because all
# four of its test repos have a separate android app module.
mut_agp_only_application_root() {
    sedi '/alias(libs.plugins.androidKmpLibrary) apply false/d' "$1/build.gradle.kts"
}

post_agp_only_application_root() {
    local dir="$1" m
    for m in common data designsystem; do
        grep -q 'id("com.android.kotlin.multiplatform.library")' "$dir/core/$m/build.gradle.kts" \
            || { echo "core/$m names a version for the AGP plugin the root already put on the classpath — Gradle refuses that"; return 1; }
    done
    return 0
}

# The other side of the branch, and the one that would otherwise never be exercised:
# a root that puts NO AGP on the classpath. Here each subproject resolves the plugin
# marker itself, so the vendored core MUST name a version — a bare id() would fail with
# "plugin not found". An always-version-less rewrite passes the variant above and breaks
# this one.
mut_agp_not_in_root() {
    sedi '/alias(libs.plugins.androidApplication) apply false/d;/alias(libs.plugins.androidKmpLibrary) apply false/d' \
        "$1/build.gradle.kts"
    # The app and shared modules still declare their own plugins, so the build is
    # coherent — the plugins are simply resolved per-project instead of at the root.
}

post_agp_not_in_root() {
    local dir="$1" m
    for m in common data designsystem; do
        grep -q 'alias(kmpilotLibs.plugins.androidKotlinMultiplatformLibrary)' \
            "$dir/core/$m/build.gradle.kts" \
            || { echo "core/$m dropped the version, but nothing else puts AGP on the classpath here"; return 1; }
    done
    return 0
}

mut_plain_android() {
    # not KMP at all: no commonMain anywhere, no KMP plugin
    rm -rf "$1/shared"
    sedi '/include(":shared")/d' "$1/settings.gradle.kts"
    sedi '/project(":shared")/d' "$1/app/build.gradle.kts"
    sedi '/kotlinMultiplatform\|androidKmpLibrary\|composeMultiplatform/d' "$1/build.gradle.kts"
}

# The shape that shipped a wrong answer: core/* library modules present, no
# startKoin, no NavHost. The old fallback took the first commonMain module in
# alphabetical order — core/model — and derived the package prefix from it,
# renaming the whole vendored core into a data module's package.
mut_library_modules() {
    local m
    for m in model network; do
        mkdir -p "$1/core/$m/src/commonMain/kotlin/com/acme/notes/$m"
        cat > "$1/core/$m/build.gradle.kts" <<EOF
plugins { alias(libs.plugins.kotlinMultiplatform) }
EOF
        cat > "$1/core/$m/src/commonMain/kotlin/com/acme/notes/$m/Placeholder.kt" <<EOF
package com.acme.notes.$m

class Placeholder
EOF
        printf 'include(":core:%s")\n' "$m" >> "$1/settings.gradle.kts"
    done
    # strip both strong signals, leaving only the app-module dependency edge
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/initKoin.kt"
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/App.kt"
    rm -f "$1/app/src/main/kotlin/com/acme/notes/android/NotesApplication.kt"
    sedi '/NotesApplication/d' "$1/app/src/main/AndroidManifest.xml"
    sedi '/App()/d' "$1/app/src/main/kotlin/com/acme/notes/android/MainActivity.kt"
    sedi '/import com.acme.notes.App/d' "$1/app/src/main/kotlin/com/acme/notes/android/MainActivity.kt"
}

# Gradle files hand-edited without a trailing newline are common, and every
# `>>` append then splices onto the last line:
#   include(":core:network")include(":core:common")
# which is not valid Kotlin. Needs a REAL apply to catch — a dry-run writes
# nothing, so the malformed file never exists.
mut_no_trailing_newline() {
    printf '%s' "$(cat "$1/settings.gradle.kts")" > "$1/settings.gradle.kts.tmp"
    mv "$1/settings.gradle.kts.tmp" "$1/settings.gradle.kts"
}

# Leftover Gradle output where a vendored module will go. git-ignored, so it
# survives `git clean -fd` after an adoption is removed — and a plain -e test
# then calls it "already exists", skips vendoring, and still writes the include.
mut_stale_core_build() {
    mkdir -p "$1/core/common/build/classes" "$1/core/designsystem/build"
    : > "$1/core/common/build/classes/stale.txt"
    # Removing an adoption by hand deletes the files but leaves the skeleton:
    # an empty src/ with no build.gradle.kts. That is not a module either.
    mkdir -p "$1/core/data/src/commonMain/kotlin"
}

# The host already owns a module at one of the paths KMPilot vendors into.
# Skipping the copy but still wiring implementation(project(":core:common"))
# would point features at THEIR module and break every Either/UiState import.
mut_core_name_clash() {
    mkdir -p "$1/core/common/src/commonMain/kotlin/com/acme/notes/common"
    cat > "$1/core/common/build.gradle.kts" <<'EOF'
plugins { alias(libs.plugins.kotlinMultiplatform) }
EOF
    printf 'include(":core:common")\n' >> "$1/settings.gradle.kts"
}

# Koin's COMPOSE bootstrap — no global startKoin anywhere. This is the shape the
# bookshelf test project actually had, and detecting only `startKoin` made adopt
# write a dead InitKmpilotKoin.kt into a project that already had DI, then record
# koinBootstrap: "supplied" for it.
mut_compose_koin() {
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/initKoin.kt"
    rm -f "$1/app/src/main/kotlin/com/acme/notes/android/NotesApplication.kt"
    sedi '/NotesApplication/d' "$1/app/src/main/AndroidManifest.xml"
    cat > "$1/shared/src/commonMain/kotlin/com/acme/notes/App.kt" <<'EOF'
package com.acme.notes

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import com.acme.notes.di.appModule
import org.koin.compose.KoinApplication
import org.koin.dsl.koinConfiguration

@Composable
fun App() {
    KoinApplication(
        configuration = koinConfiguration { modules(appModule) },
    ) {
        MaterialTheme { Text("Acme Notes") }
    }
}
EOF
}

# Adopt must recognise that as a working Koin bootstrap: no supplied initKoin,
# and the manifest must say the host owns it.
post_compose_koin() {
    [[ ! -f "$1/shared/src/commonMain/kotlin/com/acme/notes/kmpilot/InitKmpilotKoin.kt" ]] \
        || { echo "wrote a dead InitKmpilotKoin.kt into a project that already bootstraps Koin"; return 1; }
    grep -q '"koinBootstrap": "host"' "$1/.kmpilot.json" \
        || { echo "koinBootstrap should be host (Compose bootstrap detected)"; return 1; }
    return 0
}

# App module with exactly ONE sub-package deeper than the rest. The longest-
# common-prefix walk counted only non-empty segments, so the shorter package
# contributed an invisible empty one, the depth looked unanimous, and the prefix
# came out as "com.acme.notes.\nnotes". Adoption itself creates this shape (it
# adds a `kmpilot/` sub-package), so every RE-adoption tripped it.
mut_single_subpackage() {
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/data/NotesApi.kt"
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/notes/NoteListScreen.kt"
    rm -f "$1/shared/src/commonMain/kotlin/com/acme/notes/App.kt"
    rmdir "$1/shared/src/commonMain/kotlin/com/acme/notes/data" \
          "$1/shared/src/commonMain/kotlin/com/acme/notes/notes" 2>/dev/null || true
}

# Exact match, not a substring: "com.acme.notes.di" contains "com.acme.notes",
# so a prefix that swallowed one segment too many would pass a grep.
post_single_subpackage() {
    grep -q '"packagePrefix": "com.acme.notes"' "$1/.kmpilot.json" \
        || { echo "wrong packagePrefix: $(grep packagePrefix "$1/.kmpilot.json")"; return 1; }
    return 0
}

# Nothing at the app module's ROOT package: every file sits one level down
# (…notes.ui, …notes.di, …notes.data, …notes.notes) and no .kt declares
# `package com.acme.notes` itself. The longest-common-prefix walk still resolves
# — and nothing confirms it. This is the code path that once emitted
# `com.example.bookshelf.` with a newline glued on.
#
# Every source set, not just commonMain: the walk greps the module's whole src/
# tree, so one leftover root-package file in iosMain is enough to make the shape
# under test not the shape being tested.
mut_subpackages_only() {
    local f dir
    while IFS= read -r f; do
        dir="$(dirname "$f")/ui"
        mkdir -p "$dir"
        sedi 's|^package com\.acme\.notes$|package com.acme.notes.ui|' "$f"
        mv "$f" "$dir/"
    done < <(grep -rl '^package com\.acme\.notes$' "$1/shared/src" 2>/dev/null)
}

# Exactly the shared prefix — not `com.acme.notes.ui` (the walk swallowing a
# sub-package) and not `com.acme.notes.` with a stray segment appended.
post_subpackages_only() {
    grep -q '"packagePrefix": "com.acme.notes"' "$1/.kmpilot.json" \
        || { echo "wrong packagePrefix: $(grep packagePrefix "$1/.kmpilot.json")"; return 1; }
    return 0
}

# The inferred package prefix swallowing a sub-package. Every source moves one
# level down in the package tree while the Android build keeps declaring
# `com.acme.notes`, so the longest-common-prefix walk yields `com.acme.notes.ui`
# — a perfectly well-formed package, and wrong: every vendored core module would
# be renamed into a UI sub-package. Only the declared namespace/applicationId can
# see it, because only they state the identity rather than infer it.
mut_namespace_mismatch() {
    local f
    while IFS= read -r f; do
        sedi 's|^package com\.acme\.notes|package com.acme.notes.ui|' "$f"
    done < <(grep -rl '^package com\.acme\.notes' "$1/shared/src" 2>/dev/null)
}

# Naming the disagreement is half the job; offering the declared identity as the
# fix is the other half.
post_namespace_mismatch() {
    printf '%s' "$2" | grep -qF 'install.sh --adopt AcmeNotes com.acme.notes' \
        || { echo "warned but did not offer the declared identity as the fix"; return 1; }
    return 0
}

# Kotlin below KMPilot's tested floor. The target's own version always wins — it
# is never overridden — so this warns and adopts rather than refusing.
mut_old_versions() {
    sedi 's|^kotlin = ".*"|kotlin = "2.0.0"|' "$1/gradle/libs.versions.toml"
}

# Someone who hand-vendored KMPilot before adopt mode existed: a kmpilotLibs
# catalog, its registration in settings, and core modules namespaced out of the
# way — and no .kmpilot.json, because there was nothing to write one. Adopting
# again would lay a second copy over the top. The `already-adopted` variant
# covers the same shape WITH a manifest; this is the one without.
mut_pre_vendored() {
    printf '[versions]\nkmpilot = "0.1.0"\n' > "$1/gradle/kmpilot.versions.toml"
    cat >> "$1/settings.gradle.kts" <<'EOF'

dependencyResolutionManagement {
    versionCatalogs {
        create("kmpilotLibs") { from(files("gradle/kmpilot.versions.toml")) }
    }
}
EOF
    mkdir -p "$1/core/kmpilotcommon/src/commonMain/kotlin"
    printf 'plugins { alias(libs.plugins.kotlinMultiplatform) }\n' \
        > "$1/core/kmpilotcommon/build.gradle.kts"
    printf 'include(":core:kmpilotcommon")\n' >> "$1/settings.gradle.kts"
}

# Every artefact named in one inventory, and nothing written. A refusal that
# mentions only the first thing it found sends the reader round the loop again.
post_pre_vendored() {
    local a
    for a in 'gradle/kmpilot.versions.toml' 'already registers kmpilotLibs' 'core/kmpilotcommon'; do
        printf '%s' "$2" | grep -qF "$a" \
            || { echo "refusal did not name: $a"; return 1; }
    done
    printf '%s' "$(git -C "$1" status --porcelain)" | grep -q . \
        && { echo "refused but left the tree dirty — something was written"; return 1; }
    [[ ! -f "$1/.kmpilot.json" ]] || { echo "refused but wrote .kmpilot.json"; return 1; }
    return 0
}

# A monorepo: git root on top, the KMP build in android/, a non-KMP Gradle root
# in backend/, and an ios/ that is neither. Running --adopt from the top is an
# ordinary mistake in a company codebase, and the refusal it used to get was
# either "No settings.gradle.kts here" or — worse, when the top happens to be a
# Gradle root of its own — "this does not look like a Kotlin Multiplatform
# project", about a repo that plainly contains one.
mut_monorepo() {
    local f base
    mkdir -p "$1/android"
    for f in "$1"/* "$1"/.[!.]*; do
        [[ -e "$f" ]] || continue
        base="$(basename "$f")"
        case "$base" in android|.git) continue ;; esac
        mv "$f" "$1/android/"
    done
    mkdir -p "$1/ios"
    : > "$1/ios/Podfile"
    mkdir -p "$1/backend/src/main/kotlin"
    printf 'plugins { id("java") }\n' > "$1/backend/build.gradle.kts"
    printf 'rootProject.name = "backend"\n' > "$1/backend/settings.gradle.kts"
}

# backend/ is a Gradle root too, and is NOT KMP. Naming it would send the reader
# to the wrong directory, which is worse than not pointing anywhere.
post_monorepo() {
    printf '%s' "$2" | grep -q 'backend' \
        && { echo "named backend/ — a Gradle root that is not Kotlin Multiplatform"; return 1; }
    return 0
}

# A rootProject.name with NOTHING in common with the package prefix. The two are
# independent inputs to the rename and both are rewritten in the same pass: the
# generated-resources package comes from rootProject.name lowercased and
# sanitized, the Kotlin package from the app module's sources. A rename that
# conflated them would still look right whenever the two happen to share a token
# — which, in the baseline fixture and in the template, they do.
#
# The hyphen is deliberate: Compose Multiplatform sanitizes `paper-trail` to
# `paper_trail` for the resources package and rename.sh has to mirror that
# exactly, or every rewritten import names a package the plugin never generates.
mut_unrelated_root_name() {
    sedi 's|rootProject.name = "AcmeNotes"|rootProject.name = "Paper-Trail"|' \
        "$1/settings.gradle.kts"
}

post_unrelated_root_name() {
    local ds="$1/core/designsystem/src"
    grep -rqs 'paper_trail\.core\.designsystem\.generated\.resources' "$ds" \
        || { echo "resources package not derived from rootProject.name (expected paper_trail.core.designsystem.generated.resources)"; return 1; }
    grep -rqs '^package com\.acme\.notes\.designsystem' "$ds" \
        || { echo "Kotlin package not derived from the app module's sources (expected com.acme.notes.designsystem)"; return 1; }
    # Neither input may leak the template's own values into the other's slot.
    grep -rqs 'kmpilot\.core\.[a-z]*\.generated\.resources' "$1/core" \
        && { echo "left an un-renamed kmpilot.core.* resources import behind"; return 1; }
    grep -rqs '^package thisissadeghi\.' "$1/core" \
        && { echo "left an un-renamed thisissadeghi.* package behind"; return 1; }
    return 0
}

# Two modules both bootstrapping Koin. Detection used to return the FIRST match
# in the winning signal tier — `mobile` here, purely because it sorts before
# `shared` — and label it `strong`, so the second candidate was never seen. Every
# downstream value (package prefix, which build file gets the core dependencies,
# where the Koin glue lands) then came off a module that is not the app shell.
mut_two_app_modules() {
    mkdir -p "$1/mobile/src/commonMain/kotlin/com/acme/notes/mobile"
    cat > "$1/mobile/build.gradle.kts" <<'EOF'
plugins { alias(libs.plugins.kotlinMultiplatform) }
EOF
    cat > "$1/mobile/src/commonMain/kotlin/com/acme/notes/mobile/Bootstrap.kt" <<'EOF'
package com.acme.notes.mobile

import org.koin.core.context.startKoin

fun bootstrap() {
    startKoin { }
}
EOF
    printf 'include(":mobile")\n' >> "$1/settings.gradle.kts"
}

# Runs WITHOUT --dry-run on purpose: the claim under test is that a real run
# refuses during detection, before anything is staged or written.
post_two_app_modules() {
    printf '%s' "$(git -C "$1" status --porcelain)" | grep -q . \
        && { echo "refused but left the tree dirty — something was written"; return 1; }
    [[ ! -f "$1/.kmpilot.json" ]] || { echo "refused but wrote .kmpilot.json"; return 1; }
    [[ ! -d "$1/core" ]] || { echo "refused but vendored core/"; return 1; }
    return 0
}

mut_dirty_tree() {
    printf '\n// uncommitted\n' >> "$1/app/build.gradle.kts"
}

mut_already_adopted() {
    printf '{ "kmpilotVersion": "0.1.3", "installMode": "adopt" }\n' > "$1/.kmpilot.json"
    git -C "$1" add -A >/dev/null 2>&1
    git -C "$1" -c user.email=f@l -c user.name=f commit --quiet -m "prior adoption"
}

# ── the matrix ───────────────────────────────────────────────────────────────
# name | mutation | expected outcome | pattern that must appear | extra flags
#
# `refuses` asserts a NON-ZERO exit; `adopts` and `warns` assert exit 0.
# Everything runs with --dry-run except dirty-tree: --dry-run deliberately only
# WARNS about a dirty tree (it writes nothing, so there is nothing to protect),
# and the refusal it is testing belongs to a real run. That run stops during
# detection, long before anything is staged or written.
VARIANTS=(
  "baseline|mut_baseline|adopts|vendor core/common|--dry-run"
  "iosx64|mut_iosx64|adopts|iosX64 (added to match yours)|--dry-run"
  "wasm|mut_wasm|refuses|cannot serve|--dry-run"
  "convention-plugins|mut_convention_plugins|adopts|app module|--dry-run"
  "no-catalog|mut_no_catalog|adopts|kmpilotLibs|--dry-run"
  "no-koin|mut_no_koin|adopts|Koin|--dry-run"
  "arrow|mut_arrow|warns|own 'Either'|--dry-run"
  "own-design-system|mut_own_ds|warns|already has a design-system module|--dry-run"
  "groovy-dsl|mut_groovy|refuses|does not support yet|--dry-run"
  "agp-only-application-root|mut_agp_only_application_root|applies|without a version|"
  "agp-not-in-root|mut_agp_not_in_root|applies|core/designsystem|"
  "plain-android|mut_plain_android|refuses|does not look like a Kotlin Multiplatform|--dry-run"
  "library-modules|mut_library_modules|adopts|app module shared|--dry-run"
  "no-trailing-newline|mut_no_trailing_newline|applies|kmpilotLibs catalog registered|"
  "stale-core-build|mut_stale_core_build|applies|core/common|"
  "compose-koin|mut_compose_koin|applies|starts Koin already|"
  "single-subpackage|mut_single_subpackage|applies|core/common|"
  "core-name-clash|mut_core_name_clash|refuses|names collide|--dry-run"
  "dirty-tree|mut_dirty_tree|refuses|not clean|"
  "already-adopted|mut_already_adopted|refuses|--force|--dry-run"
  "two-app-modules|mut_two_app_modules|refuses|--app-module=mobile|"
  "subpackages-only|mut_subpackages_only|applies|core/common|"
  "unrelated-root-name|mut_unrelated_root_name|applies|core/designsystem|"
  "monorepo|mut_monorepo|refuses|cd android|--dry-run"
  "pre-vendored|mut_pre_vendored|refuses|already carries KMPilot, but has no .kmpilot.json|"
  "namespace-mismatch|mut_namespace_mismatch|warns|does not match what your Android build declares|--dry-run"
  "old-versions|mut_old_versions|warns|below KMPilot's tested floor|--dry-run"
)

run_variant() {
    local name="$1" mutate="$2" expect="$3" pattern="$4" flags="${5:-}"
    local dir="$WORK/$name" out rc

    if ! bash "$GEN" "$dir" >/dev/null 2>&1; then
        ROWS+=("$name|FAIL|fixture generation failed")
        FAIL=$((FAIL + 1)); return
    fi
    "$mutate" "$dir"
    # Commit the mutation so only the variants that mean to be dirty are dirty.
    if [[ "$name" != "dirty-tree" ]]; then
        git -C "$dir" add -A >/dev/null 2>&1
        git -C "$dir" -c user.email=f@l -c user.name=f commit --quiet -m "variant: $name" >/dev/null 2>&1
    fi

    # KMPILOT_NONINTERACTIVE: capturing stdout does not make a run non-interactive
    # — launched from a terminal, install.sh can still open /dev/tty and would
    # block on the first prompt. The matrix asserts what happens when nobody can
    # be asked, so it says so rather than relying on how it was invoked.
    out="$(cd "$dir" && NO_COLOR=1 KMPILOT_ASSUME_YES=1 KMPILOT_NONINTERACTIVE=1 \
        KMPILOT_SOURCE_DIR="$KMPILOT_ROOT" \
        bash "$KMPILOT_ROOT/install.sh" --adopt $flags 2>&1)"
    rc=$?

    local ok=yes reason=""
    case "$expect" in
        refuses)
            [[ $rc -ne 0 ]] || { ok=no; reason="expected refusal, exited 0"; }
            ;;
        adopts|warns)
            [[ $rc -eq 0 ]] || { ok=no; reason="expected success, exited $rc"; }
            ;;
        applies)
            # a real run: assert it succeeded AND that what it wrote is well formed
            if [[ $rc -ne 0 ]]; then
                ok=no; reason="expected success, exited $rc"
            elif ! awk '/include\(/ { n = gsub(/include\(/, "&"); if (n > 1) exit 1 }' \
                    "$dir/settings.gradle.kts"; then
                ok=no; reason="settings.gradle.kts has two include() calls spliced onto one line"
            else
                # anything included must be a real module, or Gradle cannot configure
                local cm
                for cm in common data designsystem; do
                    if grep -qs "\":core:${cm}\"" "$dir/settings.gradle.kts" \
                       && [[ ! -f "$dir/core/$cm/build.gradle.kts" ]]; then
                        ok=no; reason="include(\":core:$cm\") written but core/$cm has no build.gradle.kts"
                        break
                    fi
                done
            fi
            ;;
    esac
    if [[ "$ok" == "yes" ]] && ! printf '%s' "$out" | grep -qF -- "$pattern"; then
        ok=no; reason="missing expected text: $pattern"
    fi

    # Optional per-variant assertion, called as post_<name> <fixture-dir> <output>.
    # Available to every outcome, not just `applies`: "it refused AND wrote
    # nothing" is exactly the kind of claim worth checking against the filesystem
    # rather than the log. The output is passed too because the single `pattern`
    # column can assert that a string IS present but never that one is ABSENT.
    local fn="post_${name//-/_}" detail
    if [[ "$ok" == "yes" ]] && declare -F "$fn" >/dev/null; then
        if ! detail="$("$fn" "$dir" "$out")"; then
            ok=no; reason="${detail:-post-check failed}"
        fi
    fi

    if [[ "$ok" == "yes" ]]; then
        ROWS+=("$name|PASS|$expect"); PASS=$((PASS + 1))
    else
        ROWS+=("$name|FAIL|$reason"); FAIL=$((FAIL + 1))
        printf '%s--- %s output ---%s\n%s\n' "$DIM" "$name" "$OFF" "$(printf '%s' "$out" | tail -n 12)"
    fi
}

printf '\n%sAdopt compatibility matrix%s  %s(detection only — no Gradle)%s\n\n' "$BOLD" "$OFF" "$DIM" "$OFF"

for v in "${VARIANTS[@]}"; do
    IFS='|' read -r name mutate expect pattern flags <<< "$v"
    [[ -z "$FILTER" || "$name" == *"$FILTER"* ]] || continue
    run_variant "$name" "$mutate" "$expect" "$pattern" "$flags"
done

printf '\n'
for row in "${ROWS[@]}"; do
    IFS='|' read -r name status detail <<< "$row"
    if [[ "$status" == "PASS" ]]; then
        printf '  %s✓%s  %-20s %s%s%s\n' "$GREEN" "$OFF" "$name" "$DIM" "$detail" "$OFF"
    else
        printf '  %s✗%s  %-20s %s%s%s\n' "$RED" "$OFF" "$name" "$RED" "$detail" "$OFF"
    fi
done
printf '\n  %d passed · %d failed\n\n' "$PASS" "$FAIL"

exit "$FAIL"
