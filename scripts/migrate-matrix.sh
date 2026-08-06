#!/usr/bin/env bash
# Outcome matrix for `/kmp-to-kmpilot` discovery and planning (Phase 6, Stage B).
#
# Builds the non-conforming fixture ONCE, then per variant copies it, mutates the
# copy into the shape under test, runs the discovery pass, and asserts the outcome.
# Three outcomes count as correct:
#
#   DISCOVERS  the inventory says what it should say
#   WARNS      the named note is present
#   REFUSES    the subject appears in refusals[] with a reason that explains itself
#
# A clean refusal is a PASS. The failures being tested for are a refusal that does
# not explain itself, and — worse — a WRONG refusal: telling a migratable feature it
# is Android-locked, which is how a migration tool loses a user's trust in one run.
# Every classifier therefore has a **negative control** variant: the same fixture
# with the trigger removed, asserting the finding disappears. An assertion that
# cannot fail is not a test.
#
# Everything here is static analysis: discovery reads files and writes nothing — no
# Gradle, no JDK, no Android SDK — so a variant takes about a second. Fixture
# generation (which does run `install.sh --adopt`) happens once, up front.
#
# Usage:
#   scripts/migrate-matrix.sh              # run every variant
#   scripts/migrate-matrix.sh refus        # run variants matching a substring
#   KEEP=1 scripts/migrate-matrix.sh       # keep the mutated fixtures for poking at
#
# Exit code is the number of failing variants, so CI can gate on it.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KMPILOT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN="${SCRIPT_DIR}/make-nonconforming-project.sh"
DISCOVER="${KMPILOT_ROOT}/.claude/skills/_shared/kmpilot_discover.py"
PLAN="${KMPILOT_ROOT}/.claude/skills/_shared/kmpilot_plan.py"
MIGRATE="${KMPILOT_ROOT}/.claude/skills/_shared/kmpilot_migrate.py"
INTEGRATE="${KMPILOT_ROOT}/.claude/skills/_shared/kmpilot_report.py"
FILTER="${1:-}"

WORK="$(mktemp -d)"
cleanup() { [[ "${KEEP:-0}" == "1" ]] && { echo "fixtures kept in $WORK"; return; }; rm -rf "$WORK"; }
trap cleanup EXIT

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
else
    GREEN=""; RED=""; DIM=""; BOLD=""; OFF=""
fi

# Cross-platform sed -i (BSD/macOS vs GNU/Linux disagree about -i's argument).
if [[ "$(uname -s)" == "Darwin" ]]; then
    sedi() { sed -i '' "$@"; }
else
    sedi() { sed -i "$@"; }
fi

PASSES=0
FAILURES=0
FAILED_NAMES=()
OUT=""            # discovery output for the variant under test
VDIR=""           # the mutated fixture for the variant under test
VARIANT=""
VARIANT_FAILED=0

# ── assertions ──────────────────────────────────────────────────────────────

fail() {
    echo "    ${RED}x${OFF} $1"
    VARIANT_FAILED=1
}

# expect <extended-regex> <what it proves>
expect() {
    grep -Eq -- "$1" <<<"$OUT" || fail "$2 ${DIM}(no line matching /$1/)${OFF}"
}

# reject <extended-regex> <what it proves>  — the negative-control primitive
reject() {
    if grep -Eq -- "$1" <<<"$OUT"; then
        fail "$2 ${DIM}(unexpected: $(grep -Em1 -- "$1" <<<"$OUT"))${OFF}"
    fi
}

# expect_plan <extended-regex> <what it proves> — asserts against the PLAN output.
# `invariant` runs both passes and leaves discovery in OUT; a step assertion has to
# be told which of the two it means, or it silently greps the wrong stream and can
# never match.
expect_plan() {
    grep -Eq -- "$1" <<<"$PLAN_OUT" || fail "$2 ${DIM}(no plan line matching /$1/)${OFF}"
}

# ── variant harness ─────────────────────────────────────────────────────────

# variant <name> <description> — copies the base fixture and sets VDIR to the copy.
# The caller mutates $VDIR, then calls `discover` and its assertions. VDIR is a global
# rather than a return value because the header below is printed on stdout, and
# command substitution would swallow it.
variant() {
    VARIANT="$1"
    VARIANT_FAILED=0
    printf '  %-28s %s\n' "$BOLD$1$OFF" "$DIM$2$OFF"
    VDIR="$WORK/$1"
    cp -R "$WORK/base" "$VDIR"
}

discover() {
    OUT="$(python3 "$DISCOVER" --root "$1" --compact 2>&1)"
    local code=$?
    if [[ $code -ne 0 ]]; then
        fail "discovery exited $code — it must always exit 0, a refusal is a finding"
    fi
}

# plan <dir> [args...] — run the plan phase against a variant. Sets OUT and PLAN_EXIT.
# Unlike discovery this one WRITES: exactly one file, the ledger, inside the target.
plan() {
    local dir="$1"; shift
    OUT="$(python3 "$PLAN" --root "$dir" --compact "$@" 2>&1)"
    PLAN_EXIT=$?
}

# mig <dir> <args...> — run the clean phase against a variant. Sets OUT and MIG_EXIT.
# Unlike discovery and the plan, this one commits: it owns the checkpoint branch.
mig() {
    local dir="$1"; shift
    OUT="$(python3 "$MIGRATE" --root "$dir" "$@" 2>&1)"
    MIG_EXIT=$?
}

# rep <dir> <args...> — run the integrate phase against a variant. Sets OUT and REP_EXIT.
# It writes two things and only two: MIGRATION-REPORT.md and .kmpilot.json.
rep() {
    local dir="$1"; shift
    OUT="$(python3 "$INTEGRATE" --root "$dir" "$@" 2>&1)"
    REP_EXIT=$?
}

# ── the universal invariant ─────────────────────────────────────────────────
#
# The goal is not "these N shapes work". It is that **any working KMP project is
# either migrated or refused with a reason** — never crashed on, never silently
# dropped, never handed a plan full of nonsense. That claim has to hold on shapes
# nobody anticipated, so it is asserted mechanically on every shape variant rather
# than restated per variant.
#
# invariant <dir> — runs discovery + plan and asserts the claim. Sets OUT to the
# discovery output so a variant can add its own shape-specific assertions after.
invariant() {
    local dir="$1" disc plan_out
    disc="$(python3 "$DISCOVER" --root "$dir" --compact 2>&1)"
    [[ $? -eq 0 ]] || fail "discovery exited non-zero — a refusal is a finding, not a crash"

    # 1. Nothing is written by a read-only pass.
    [[ -z "$(git -C "$dir" status --porcelain 2>/dev/null)" ]] || \
        fail "discovery wrote to the repo — it must read only"

    # 2. Every included module is accounted for. A module that matches no classifier
    #    and silently vanishes from the inventory is the worst failure here: the plan
    #    cannot migrate, refuse or even mention what it never saw.
    local declared classified
    declared="$(grep -cE '^\s*include\(' "$dir/settings.gradle.kts")"
    classified="$(grep -cE '^module  ' <<<"$disc")"
    [[ "$classified" -ge 1 ]] || fail "no module was classified at all"
    if [[ "$classified" -lt "$declared" ]]; then
        fail "only $classified of $declared included modules were inventoried — a module that is silently dropped can never be migrated or refused"
    fi

    # 3. Every feature has a disposition: a migrate step, or a refusal naming why.
    plan_out="$(python3 "$PLAN" --root "$dir" --compact 2>&1)"
    [[ $? -eq 0 ]] || fail "the plan exited non-zero on a discoverable project"
    local gp
    while read -r gp; do
        [[ -n "$gp" ]] || continue
        if ! grep -qE "^step .* migrate .* ${gp}( |$)" <<<"$plan_out" \
           && ! grep -qE "^refusal  ${gp}  " <<<"$disc"; then
            fail "$gp is a feature with neither a migrate step nor a refusal — it would be silently skipped"
        fi
    done < <(grep -E '^feature  ' <<<"$disc" | awk '{print $2}')

    # 4. Every refusal explains itself. A bare refusal is indistinguishable from a bug.
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        [[ "$(awk '{print NF}' <<<"$line")" -ge 4 ]] || \
            fail "a refusal with no reason: $line"
    done < <(grep -E '^refusal  ' <<<"$disc")

    # 5. The run always has a last step, whatever happened before it.
    grep -qE '^step .* report ' <<<"$plan_out" || fail "the plan has no closing report step"

    OUT="$disc"
    PLAN_OUT="$plan_out"
}

# cap <word> — capitalize. `${x^}` is bash 4; macOS ships 3.2.
cap() { printf '%s%s' "$(printf '%s' "$1" | cut -c1 | tr '[:lower:]' '[:upper:]')" "$(printf '%s' "$1" | cut -c2-)"; }

# move_module <dir> <from-dir> <to-dir> <old-gradle-path> <new-gradle-path>
# Relocate a module and follow it in settings.gradle.kts — the mechanical part of
# putting a project into a different shape.
move_module() {
    local dir="$1" from="$2" to="$3" oldp="$4" newp="$5"
    mkdir -p "$dir/$(dirname "$to")"
    mv "$dir/$from" "$dir/$to"
    python3 - "$dir/settings.gradle.kts" "$oldp" "$newp" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
open(path, "w").write(text.replace(f'"{old}"', f'"{new}"'))
PY
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: $oldp -> $newp" >/dev/null 2>&1
}

# fingerprint <dir> <subpath> — content + existence of every file under a subtree, so
# an added or deleted file changes it as loudly as an edited one.
fingerprint() {
    (cd "$1" && find "$2" -type f | sort | xargs cksum | cksum)
}

finish() {
    if [[ $VARIANT_FAILED -eq 0 ]]; then
        echo "    ${GREEN}✓${OFF} pass"
        PASSES=$((PASSES + 1))
    else
        FAILURES=$((FAILURES + 1))
        FAILED_NAMES+=("$VARIANT")
    fi
}

matches() { [[ -z "$FILTER" || "$1" == *"$FILTER"* ]]; }

# ── the base fixture, generated once ────────────────────────────────────────

echo "${BOLD}Building the base fixture${OFF} ${DIM}(generate + adopt, once)${OFF}"
if ! bash "$GEN" "$WORK/base" >"$WORK/gen.log" 2>&1; then
    echo "${RED}Fixture generation failed:${OFF}"
    tail -20 "$WORK/gen.log"
    exit 1
fi
echo "  ${GREEN}✓${OFF} $WORK/base"
echo

PKG_PATH="com/acme/notes"

# ─────────────────────────────────────────────────────────────────────────────
# DISCOVERS — the inventory is right on the unmutated fixture
# ─────────────────────────────────────────────────────────────────────────────

if matches baseline; then
    variant baseline "an adopted, non-conforming project is inventoried"; dir="$VDIR"
    discover "$dir"
    expect '^project .* role=adopted .*migrationTarget=true' "an adopted repo is a migration target"
    expect '^feature  :feature:portable  portable' "the migratable feature is portable"
    expect '^feature  :oldscreen .* location=root' "a root-level feature is found and located"
    expect '^module  :app  app-android' "the AGP launcher module is recognised by plugin, not name"
    expect '^module  :core:common  core-kmpilot' "adopt's vendored core is told from the host's own"
    expect '^module  :core:model  core-host' "the host's own shared module is core-host"
    expect '^order  :core' "shared code is ordered before the features that consume it"
    finish
fi

if matches order; then
    variant order "dependencies are ordered before their consumers"; dir="$VDIR"
    discover "$dir"
    # :core:netcall must precede both features that consume it.
    order_line="$(grep -E '^order  ' <<<"$OUT")"
    pos() { awk -v n="$1" '{for(i=1;i<=NF;i++) if($i==n) print i}' <<<"$order_line"; }
    if [[ "$(pos :core:netcall)" -ge "$(pos :feature:portable)" ]]; then
        fail ":core:netcall must be ordered before :feature:portable"
    fi
    if [[ "$(pos :core:model)" -ge "$(pos :oldscreen)" ]]; then
        fail ":core:model must be ordered before :oldscreen"
    fi
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# REFUSES — and each refusal's negative control
# ─────────────────────────────────────────────────────────────────────────────

if matches refuse-android-locked; then
    variant refuse-android-locked "Retrofit + Hilt + LiveData + Context in commonMain"; dir="$VDIR"
    discover "$dir"
    expect '^refusal  :feature:legacy  feature .*Retrofit' "the refusal names Retrofit"
    expect '^refusal  :feature:legacy .*Hilt' "the refusal names Hilt/Dagger"
    expect '^refusal  :feature:legacy .*LiveData' "the refusal names LiveData"
    expect '^feature  :feature:legacy  android-locked' "the verdict is android-locked"
    finish
fi

if matches control-android-cleaned; then
    variant control-android-cleaned "NEGATIVE CONTROL: same feature, Android imports removed"; dir="$VDIR"
    # Replace the Android-locked screen with a portable one. If the classifier is
    # keying on anything other than the imports, this variant still refuses.
    cat > "$dir/feature/legacy/src/commonMain/kotlin/$PKG_PATH/legacy/LegacyScreen.kt" <<'EOF'
package com.acme.notes.legacy

import androidx.compose.runtime.Composable

@Composable
fun LegacyScreen() = Unit
EOF
    discover "$dir"
    reject '^refusal  :feature:legacy' "a cleaned feature must NOT be refused"
    reject '^feature  :feature:legacy  android-locked' "a cleaned feature must not stay android-locked"
    finish
fi

if matches control-commented-import; then
    variant control-commented-import "NEGATIVE CONTROL: commented-out Android imports"; dir="$VDIR"
    cat > "$dir/feature/headless/src/commonMain/kotlin/$PKG_PATH/headless/SyncWorker.kt" <<'EOF'
package com.acme.notes.headless

// import android.content.Context
// import retrofit2.Retrofit
/* import androidx.fragment.app.Fragment */
import androidx.compose.runtime.Composable

@Composable
fun HeadlessScreen() = Unit
EOF
    discover "$dir"
    reject '^refusal  :feature:headless' \
        "commented-out imports must not fabricate a refusal, and adding a composable clears the entry-point refusal"
    finish
fi

if matches control-androidmain; then
    variant control-androidmain "NEGATIVE CONTROL: Android APIs inside androidMain"; dir="$VDIR"
    # Rule 14 working as designed: an actual in androidMain legitimately uses Context.
    mkdir -p "$dir/feature/portable/src/androidMain/kotlin/$PKG_PATH/portable"
    cat > "$dir/feature/portable/src/androidMain/kotlin/$PKG_PATH/portable/Platform.android.kt" <<'EOF'
package com.acme.notes.portable

import android.content.Context
import android.os.Build

actual class PlatformInfo(private val context: Context) {
    actual fun label(): String = Build.MODEL
}
EOF
    discover "$dir"
    reject '^refusal  :feature:portable' \
        "an Android API in androidMain is Rule 14, not a portability defect"
    reject '^feature  :feature:portable  android-locked' "androidMain must not flip the verdict"
    finish
fi

if matches control-kmp-lookalikes; then
    variant control-kmp-lookalikes "NEGATIVE CONTROL: androidx.navigation + lifecycle.ViewModel"; dir="$VDIR"
    # Both are multiplatform artifacts KMPilot's own commonMain uses. Flagging either
    # would refuse essentially every real feature.
    cat > "$dir/feature/notes/src/commonMain/kotlin/$PKG_PATH/notes/NotesNav.kt" <<'EOF'
package com.acme.notes.notes

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.navigation.NavController
import androidx.navigation.NavGraphBuilder
import androidx.navigation.NavHostController
import androidx.navigation.toRoute
import androidx.navigation.compose.composable

fun NavGraphBuilder.notes(onBackClick: () -> Unit) = Unit

class NotesNavViewModel : ViewModel()
EOF
    discover "$dir"
    reject '^refusal  :feature:notes' \
        "androidx.navigation.* and androidx.lifecycle.ViewModel are multiplatform"
    finish
fi

if matches refuse-no-entry-point; then
    variant refuse-no-entry-point "a feature module with no @Composable"; dir="$VDIR"
    discover "$dir"
    expect '^refusal  :feature:headless  feature  no screen entry point' \
        "a feature with no composable is refused with that reason"
    expect '^feature  :feature:headless .* entry=none' "the missing entry point is reported"
    finish
fi

if matches control-entry-point; then
    variant control-entry-point "NEGATIVE CONTROL: give the headless module a screen"; dir="$VDIR"
    cat > "$dir/feature/headless/src/commonMain/kotlin/$PKG_PATH/headless/HeadlessScreen.kt" <<'EOF'
package com.acme.notes.headless

import androidx.compose.runtime.Composable

@Composable
fun HeadlessScreen() = Unit
EOF
    discover "$dir"
    reject 'refusal  :feature:headless  feature  no screen entry point' \
        "a module with a *Screen composable has an entry point"
    expect 'entry=feature/headless.*HeadlessScreen' "the entry point is located"
    finish
fi

if matches refuse-unhoistable; then
    variant refuse-unhoistable "shared code that cannot leave Android"; dir="$VDIR"
    discover "$dir"
    expect '^refusal  :core:androidutil  shared .*cannot be hoisted' \
        "unhoistable shared code is refused with that reason"
    expect '^shared  :core:androidutil .* hoistable=false' "it is marked unhoistable"
    finish
fi

if matches control-hoistable; then
    variant control-hoistable "NEGATIVE CONTROL: same module, Context removed"; dir="$VDIR"
    cat > "$dir/core/androidutil/src/commonMain/kotlin/$PKG_PATH/androidutil/Toaster.kt" <<'EOF'
package com.acme.notes.androidutil

class Toaster {
    fun show(message: String) = Unit
}
EOF
    discover "$dir"
    reject '^refusal  :core:androidutil' "shared code with no Android API is hoistable"
    expect '^shared  :core:androidutil .* hoistable=true' "it flips to hoistable"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# WARNS — the notes, each with its control
# ─────────────────────────────────────────────────────────────────────────────

if matches warn-cross-feature; then
    variant warn-cross-feature "one feature depending on another"; dir="$VDIR"
    discover "$dir"
    expect '^note  cross-feature-dependency  :feature:portable → :feature:notes' \
        "the cross-feature edge is reported"
    expect '^infeature  :feature:portable uses :feature:notes' \
        "the shared code inside the feature is identified"
    expect '^infeature .*-> core:data' "and given a tier proposal"
    finish
fi

if matches control-integration-point; then
    variant control-integration-point "NEGATIVE CONTROL: the app module importing features"; dir="$VDIR"
    discover "$dir"
    reject '^infeature  :shared uses' \
        "the app shell importing a feature screen is Integration Point 4, not shared code"
    finish
fi

if matches warn-root-level-feature; then
    variant warn-root-level-feature "a feature that never moved under feature/"; dir="$VDIR"
    discover "$dir"
    expect '^note  feature-outside-featuredir  :oldscreen' "the misplaced feature is reported"
    expect '^feature  :oldscreen  .*location=root .*findings=-' \
        "and flagged as ungradable until it moves — the checker only sees feature/*"
    finish
fi

if matches warn-missing-desktop; then
    variant warn-missing-desktop "features targeting only android + ios"; dir="$VDIR"
    discover "$dir"
    expect '^note  missing-desktop-target  :feature:portable' "the missing desktop target is reported"
    finish
fi

if matches control-desktop-present; then
    variant control-desktop-present "NEGATIVE CONTROL: add the desktop target"; dir="$VDIR"
    sedi 's|    listOf(|    jvm("desktop")\n    listOf(|' "$dir/feature/portable/build.gradle.kts"
    discover "$dir"
    reject '^note  missing-desktop-target  :feature:portable' \
        "a module with jvm(\"desktop\") must not be flagged"
    expect '^module  :feature:portable .*targets=android,desktop,ios' "the target is detected"
    finish
fi

if matches warn-catalog-split; then
    variant warn-catalog-split "the host's libs plus adopt's kmpilotLibs"; dir="$VDIR"
    discover "$dir"
    expect '^note  catalog-split .*kmpilotLibs, libs' "both catalogs are reported"
    finish
fi

if matches warn-dependency-cycle; then
    variant warn-dependency-cycle "two features depending on each other"; dir="$VDIR"
    # :feature:portable already depends on :feature:notes — close the loop.
    sedi 's|            implementation(project(":core:widgets"))|            implementation(project(":core:widgets"))\n            implementation(project(":feature:portable"))|' \
        "$dir/feature/notes/build.gradle.kts"
    discover "$dir"
    expect '^note  dependency-cycle' "the cycle is reported"
    expect '^note  dependency-cycle .*portable' "and names its members"
    # A cycle must not be silently linearised into the order.
    if grep -E '^order  ' <<<"$OUT" | grep -q ':feature:portable'; then
        fail "a cycle member must be held out of the migration order, not linearised"
    fi
    finish
fi

if matches control-no-cycle; then
    variant control-no-cycle "NEGATIVE CONTROL: the acyclic fixture"; dir="$VDIR"
    discover "$dir"
    reject '^note  dependency-cycle' "an acyclic graph must not report a cycle"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# Tier proposals — one variant per outcome
# ─────────────────────────────────────────────────────────────────────────────

if matches tier-proposals; then
    variant tier-proposals "value types / wire / composables / mixed"; dir="$VDIR"
    discover "$dir"
    expect '^shared  :core:model  -> core:common \(common.app\)' "plain value types → common.app"
    expect '^shared  :core:netcall  -> core:data \(data.app\)' "Ktor + wire DTOs → data.app"
    expect '^shared  :core:widgets  -> core:designsystem \(designsystem.app\)' \
        "composables → designsystem.app"
    finish
fi

if matches tier-split; then
    variant tier-split "a shared module whose files disagree"; dir="$VDIR"
    # Add a composable to the value-types module: no single tier can claim it now.
    cat > "$dir/core/model/src/commonMain/kotlin/$PKG_PATH/model/NoteRow.kt" <<'EOF'
package com.acme.notes.model

import androidx.compose.runtime.Composable

@Composable
fun NoteRow(note: Note) = Unit
EOF
    discover "$dir"
    expect '^shared  :core:model  -> split across tiers' \
        "a module mixing tiers proposes a split rather than guessing a majority"
    finish
fi

if matches tier-dry-corollary; then
    variant tier-dry-corollary "shared remote used by 2+ features"; dir="$VDIR"
    OUT="$(python3 "$DISCOVER" --root "$dir" --json-only 2>&1)"
    if ! python3 -c "
import json, sys
report = json.loads(sys.stdin.read())
row = next(s for s in report['shared'] if s['gradlePath'] == ':core:netcall')
assert len(row['featureConsumers']) >= 2, row['featureConsumers']
assert 'DRY corollary' in row['reason'], row['reason']
" <<<"$OUT" 2>/dev/null; then
        fail "a shared remote with 2+ feature consumers must cite the DRY corollary"
    fi
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# Project role — migration applies to exactly one of four situations
# ─────────────────────────────────────────────────────────────────────────────

if matches role-unadopted; then
    variant role-unadopted "a KMP repo with no KMPilot in it"; dir="$VDIR"
    rm -f "$dir/.kmpilot.json"
    rm -rf "$dir/core/common" "$dir/core/data" "$dir/core/designsystem"
    discover "$dir"
    expect '^project .* role=unadopted .*migrationTarget=false' "an unadopted repo is not a target"
    expect '^note  not-adopted .*install.sh --adopt' "and is told what to run first"
    finish
fi

if matches role-template; then
    variant role-template "a project generated by install.sh"; dir="$VDIR"
    sedi 's/"installMode": "adopt"/"installMode": "template"/' "$dir/.kmpilot.json"
    discover "$dir"
    expect '^project .* role=template .*migrationTarget=false' "a template project is not a target"
    expect '^note  template-mode' "and is told its features are already KMPilot's"
    reject '^feature .*  portable ' \
        "no feature in a template project is a migration candidate, however many findings it has"
    finish
fi

if matches role-managed-feature; then
    variant role-managed-feature "a feature already promoted to managedFeatures"; dir="$VDIR"
    sedi 's/"managedFeatures": \[\]/"managedFeatures": ["portable"]/' "$dir/.kmpilot.json"
    discover "$dir"
    expect '^feature  :feature:portable  conforming' \
        "a promoted feature is done — migration must never re-migrate it"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# The guarantee the whole phase leans on
# ─────────────────────────────────────────────────────────────────────────────

if matches no-side-effects; then
    variant no-side-effects "discovery must not touch the target repo"; dir="$VDIR"
    before="$(cd "$dir" && git status --porcelain; find . -type f -not -path './.git/*' | sort | xargs cksum 2>/dev/null | cksum)"
    python3 "$DISCOVER" --root "$dir" --compact >/dev/null 2>&1
    python3 "$DISCOVER" --root "$dir" --json-only >/dev/null 2>&1
    after="$(cd "$dir" && git status --porcelain; find . -type f -not -path './.git/*' | sort | xargs cksum 2>/dev/null | cksum)"
    if [[ "$before" != "$after" ]]; then
        fail "discovery modified the target repo"
        (cd "$dir" && git status --short | head -10)
    fi
    # And it must not have left a check-report.json behind either: discovery runs the
    # checker in-process precisely so nothing is written.
    if [[ -f "$dir/.claude/docs/_project/check-report.json" ]]; then
        fail "the in-process checker wrote a report into the target"
    fi
    finish
fi

if matches report-opt-in; then
    variant report-opt-in "--report writes exactly where asked, and nowhere else"; dir="$VDIR"
    out_json="$WORK/opt-in-report.json"
    python3 "$DISCOVER" --root "$dir" --report "$out_json" --json-only >/dev/null 2>&1
    if [[ ! -f "$out_json" ]]; then
        fail "--report did not write the report"
    elif ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$out_json" 2>/dev/null; then
        fail "--report wrote invalid JSON"
    fi
    if [[ -n "$(cd "$dir" && git status --porcelain)" ]]; then
        fail "--report wrote inside the target repo"
    fi
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# PLAN — the gate. Everything here is asserted against the plan the user would be
# shown and asked to confirm; the rewrite phases consume the same ledger.
# ─────────────────────────────────────────────────────────────────────────────

if matches plan-steps; then
    variant plan-steps "every unit of work becomes an ordered step"; dir="$VDIR"
    plan "$dir"
    [[ $PLAN_EXIT -eq 0 ]] || fail "plan exited $PLAN_EXIT on a migration target"
    expect '^plan .* status=draft' "a freshly generated plan is a draft, never pre-approved"
    expect '^step  [0-9]+  hoist-core-netcall  hoist  :core:netcall  pending  -> core:data' \
        "shared code becomes a hoist step carrying its tier"
    expect '^step .* extract-notes  extract ' "shared code inside a feature becomes an extract step"
    expect '^step .* relocate-oldscreen  relocate ' "a feature outside feature/ is moved first"
    expect '^step .* migrate-portable  migrate ' "each feature becomes a migrate step"
    expect '^step .* report  report ' "the run closes with the report step"
    expect '^pass  migrate-notes .* agent=(ui-layer|integrator|data-layer|platform)' \
        "the work list is routed to an agent that already exists"
    finish
fi

if matches plan-order; then
    variant plan-order "nothing is scheduled before the code it consumes"; dir="$VDIR"
    plan "$dir"
    steps="$(grep -E '^step  ' <<<"$OUT" | awk '{print $3}')"
    pos() { grep -n "^$1$" <<<"$steps" | cut -d: -f1; }
    for pair in "hoist-core-netcall migrate-notes" "hoist-core-model migrate-portable" \
                "extract-notes migrate-portable" "relocate-oldscreen migrate-oldscreen"; do
        set -- $pair
        if [[ -z "$(pos "$1")" || -z "$(pos "$2")" || "$(pos "$1")" -ge "$(pos "$2")" ]]; then
            fail "$1 must be ordered before $2"
        fi
    done
    [[ "$(tail -1 <<<"$steps")" == "report" ]] || fail "the report step must be last"
    # A feature waiting on another feature's rewrite would make one refused feature
    # block every feature that imports it today — the extract step is what removes
    # that edge.
    if grep -E '^step .*  migrate-[a-z]+ .* depends=[^ ]*migrate-' <<<"$OUT" >/dev/null; then
        fail "a migrate step must never depend on another migrate step"
    fi
    finish
fi

if matches plan-refusals; then
    variant plan-refusals "a refusal is a step with no work list"; dir="$VDIR"
    plan "$dir"
    expect '^step .* migrate-legacy  migrate  :feature:legacy  refused' \
        "the Android-locked feature is refused in the plan, not scheduled"
    expect '^step .* hoist-core-androidutil .* refused' "unhoistable shared code is refused"
    expect '^step .* migrate-portable .* blocked' \
        "a consumer of unhoistable shared code is blocked, not silently planned"
    reject '^pass  migrate-legacy ' "a refused feature must carry no rewrite passes"
    finish
fi

if matches plan-gate; then
    variant plan-gate "confirmation, and what makes it lapse"; dir="$VDIR"
    plan "$dir"
    plan "$dir" --confirm
    expect '^plan .* status=confirmed' "--confirm confirms a plan the user has seen"
    # A feature that appears after confirmation is work nobody approved.
    mkdir -p "$dir/feature/latecomer/src/commonMain/kotlin/$PKG_PATH/latecomer"
    cat > "$dir/feature/latecomer/build.gradle.kts" <<'EOF'
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
}
EOF
    cat > "$dir/feature/latecomer/src/commonMain/kotlin/$PKG_PATH/latecomer/LatecomerScreen.kt" <<'EOF'
package com.acme.notes.latecomer

import androidx.compose.runtime.Composable

@Composable
fun LatecomerScreen() = Unit
EOF
    printf '\ninclude(":feature:latecomer")\n' >> "$dir/settings.gradle.kts"
    plan "$dir"
    expect '^plan .* status=draft' "a plan whose step list changed drops back to draft"
    expect '^plannote  plan-reconfirm-required' "and says why"
    finish
fi

if matches plan-resume; then
    variant plan-resume "progress survives, refusals are not overridable"; dir="$VDIR"
    plan "$dir"
    plan "$dir" --mark hoist-core-model=done
    plan "$dir"
    expect '^step .* hoist-core-model .* done .* source=ledger' \
        "a step marked done stays done across regeneration"
    reject '^plan .* next=hoist-core-model' "a completed step is not handed back as the next one"
    plan "$dir" --mark migrate-legacy=done
    [[ $PLAN_EXIT -ne 0 ]] || fail "marking a refused step done must be rejected"
    finish
fi

if matches plan-decisions; then
    variant plan-decisions "a tier proposal is the user's to overrule"; dir="$VDIR"
    plan "$dir" --set-tier hoist-core-netcall=common
    expect '^step .* hoist-core-netcall .* -> core:common' "--set-tier changes the destination"
    expect '^decision  hoist-core-netcall  tier=common' "and is recorded in the ledger"
    plan "$dir"
    expect '^step .* hoist-core-netcall .* -> core:common' "the decision survives regeneration"
    plan "$dir" --set-tier hoist-core-netcall=nowhere
    [[ $PLAN_EXIT -ne 0 ]] || fail "an unknown tier must be rejected"
    finish
fi

if matches plan-writes-one-file; then
    variant plan-writes-one-file "the plan phase touches no source"; dir="$VDIR"
    before="$(cd "$dir" && find . -type f -not -path './.git/*' -not -path './.claude/docs/_project/*' | sort | xargs cksum 2>/dev/null | cksum)"
    plan "$dir"
    after="$(cd "$dir" && find . -type f -not -path './.git/*' -not -path './.claude/docs/_project/*' | sort | xargs cksum 2>/dev/null | cksum)"
    [[ "$before" == "$after" ]] || fail "the plan phase modified source outside the ledger"
    [[ -f "$dir/.claude/docs/_project/migration-plan.json" ]] || fail "the ledger was not written"
    python3 -c "import json,sys; json.load(open(sys.argv[1]))" \
        "$dir/.claude/docs/_project/migration-plan.json" 2>/dev/null || fail "the ledger is not valid JSON"
    finish
fi

if matches control-plan-dry-run; then
    variant control-plan-dry-run "NEGATIVE CONTROL: --dry-run writes nothing at all"; dir="$VDIR"
    plan "$dir" --dry-run
    [[ $PLAN_EXIT -eq 0 ]] || fail "--dry-run exited $PLAN_EXIT"
    expect '^plan .* status=draft' "--dry-run still produces the plan"
    if [[ -f "$dir/.claude/docs/_project/migration-plan.json" ]]; then
        fail "--dry-run wrote the ledger"
    fi
    finish
fi

if matches control-plan-non-target; then
    variant control-plan-non-target "NEGATIVE CONTROL: a template project gets no plan"; dir="$VDIR"
    sedi 's/"installMode": "adopt"/"installMode": "template"/' "$dir/.kmpilot.json"
    plan "$dir"
    [[ $PLAN_EXIT -ne 0 ]] || fail "a project with nothing to migrate must not get a plan"
    expect 'not a migration target' "and is told why"
    if [[ -f "$dir/.claude/docs/_project/migration-plan.json" ]]; then
        fail "a refused project got a plan file written into it"
    fi
    finish
fi

if matches plan-refuse-rewrite; then
    variant plan-refuse-rewrite "a blocker found once a pass opened the feature"; dir="$VDIR"
    plan "$dir"
    expect '^pass  migrate-notes ' "the feature carries a work list before it is refused"
    plan "$dir" --confirm
    plan "$dir" --mark migrate-notes=in-progress
    plan "$dir" --refuse migrate-notes --reason "custom threading model, no KMP equivalent" \
        --evidence Sync.kt:44
    [[ $PLAN_EXIT -eq 0 ]] || fail "--refuse exited $PLAN_EXIT"
    expect '^step .* migrate-notes  migrate  :feature:notes  refused' \
        "the step is refused, not left in progress"
    expect '^refusal-rewrite  migrate-notes .* prior=in-progress' \
        "the refusal names the step and the status it held — what says a revert was owed"
    reject '^pass  migrate-notes ' "a refused feature must carry no rewrite passes"
    expect '^plan .* refused-rewrite=1' "the summary counts it separately from a discovery refusal"
    # A refusal is a pass: the run continues rather than stopping for re-approval.
    expect '^plan .* status=confirmed' "a refusal must not lapse confirmation"
    # Features do not depend on features, so refusing one cannot stall another.
    reject '^step .* blocked .*=[^ ]*migrate-notes' \
        "refusing one feature must never block another"
    plan "$dir"
    expect '^step .* migrate-notes .* refused' \
        "it survives regeneration — discovery cannot re-derive what it never saw"
    finish
fi

if matches control-plan-refuse-withdrawn; then
    variant control-plan-refuse-withdrawn \
        "NEGATIVE CONTROL: --unrefuse gives the feature and its work list back"; dir="$VDIR"
    plan "$dir"
    plan "$dir" --refuse migrate-notes --reason "custom threading model, no KMP equivalent"
    plan "$dir" --unrefuse migrate-notes
    [[ $PLAN_EXIT -eq 0 ]] || fail "--unrefuse exited $PLAN_EXIT"
    expect '^step .* migrate-notes  migrate  :feature:notes  pending' \
        "the step comes back as pending — a recorded refusal has to be revocable"
    expect '^pass  migrate-notes ' "and its work list comes back with it"
    reject '^refusal-rewrite ' "a withdrawn refusal must leave nothing behind"
    expect '^plan .* refused-rewrite=0' "and stops being counted"
    plan "$dir" --unrefuse migrate-legacy
    [[ $PLAN_EXIT -ne 0 ]] || \
        fail "a discovery refusal is cleared by fixing the source, not withdrawn from the ledger"
    finish
fi

if matches control-plan-refuse-not-progress; then
    variant control-plan-refuse-not-progress \
        "NEGATIVE CONTROL: a refusal cannot be smuggled in as progress"; dir="$VDIR"
    plan "$dir"
    # A remembered `refused` status would outlive the blocker being fixed and nothing
    # could then clear it — a permanent wrong refusal, the failure that costs trust.
    plan "$dir" --mark migrate-notes=refused
    [[ $PLAN_EXIT -ne 0 ]] || fail "--mark ID=refused must be rejected"
    expect '\-\-refuse migrate-notes' "and must point at the flag that does record one"
    plan "$dir" --refuse migrate-notes
    [[ $PLAN_EXIT -ne 0 ]] || fail "a refusal with no --reason must be rejected"
    plan "$dir" --refuse migrate-legacy --reason x
    [[ $PLAN_EXIT -ne 0 ]] || fail "refusing what discovery already refuses must be rejected"
    plan "$dir" --refuse migrate-portable --reason x
    [[ $PLAN_EXIT -ne 0 ]] || \
        fail "refusing a blocked step must be rejected — no pass has opened it"
    plan "$dir" --refuse migrate-nosuchthing --reason x
    [[ $PLAN_EXIT -ne 0 ]] || fail "refusing a step that does not exist must be rejected"
    plan "$dir"
    reject '^refusal-rewrite ' "none of the rejected attempts may leave a record behind"
    finish
fi

if matches plan-refuse-blocks; then
    variant plan-refuse-blocks "shared code refused mid-rewrite blocks its consumers"; dir="$VDIR"
    plan "$dir"
    plan "$dir" --confirm
    plan "$dir" --refuse hoist-core-netcall --reason "wire models come from an Android-only codegen"
    expect '^step .* migrate-notes .* blocked .*=[^ ]*hoist-core-netcall' \
        "a feature consuming the refused module is blocked, naming what blocks it"
    # The plan was confirmed to do that work; quietly dropping it is the failure.
    expect '^plannote  rewrite-refusal-blocks-work' \
        "work the user approved that will now not run is called out"
    plan "$dir" --unrefuse hoist-core-netcall
    reject '^step .* migrate-notes .* blocked' "withdrawing it unblocks the consumers again"
    reject '^plannote  rewrite-refusal-blocks-work' "and retracts the warning"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# CLEANS — the execution envelope: the branch, the checkpoints, the restore
# ─────────────────────────────────────────────────────────────────────────────

if matches clean-begin; then
    variant clean-begin "a dirty tree is absorbed, not refused"; dir="$VDIR"
    echo "work the user had not committed" > "$dir/MY-NOTES.txt"
    base_branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
    plan "$dir"; plan "$dir" --confirm
    mig "$dir" begin
    [[ $MIG_EXIT -eq 0 ]] || fail "begin exited $MIG_EXIT"
    expect 'migration begun on kmpilot/migrate-' "the run gets its own branch"
    expect 'undo everything: git switch -' "and says how to undo the whole thing"
    # `git switch -` restores the pre-migration *committed* state, so uncommitted work
    # has to be asked for by name. Not saying so strands it.
    expect 'the tree was dirty' "a dirty tree is called out, not silently swallowed"
    expect 'git restore --source=' "and the way to get that work back is spelled out"
    branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
    [[ "$branch" == kmpilot/migrate-* ]] || fail "the repo is on $branch, not a migration branch"
    [[ -z "$(git -C "$dir" status --porcelain -- MY-NOTES.txt)" ]] || \
        fail "the uncommitted file was left uncommitted — the checkpoint must absorb it"
    git -C "$dir" switch - >/dev/null 2>&1
    [[ "$(git -C "$dir" rev-parse --abbrev-ref HEAD)" == "$base_branch" ]] || \
        fail "git switch - must land back on $base_branch"
    finish
fi

if matches control-clean-gate; then
    variant control-clean-gate "NEGATIVE CONTROL: a draft plan rewrites nothing"; dir="$VDIR"
    plan "$dir"      # generated, deliberately NOT confirmed
    before="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
    mig "$dir" begin
    [[ $MIG_EXIT -ne 0 ]] || fail "begin on an unconfirmed plan must be refused"
    expect 'not confirmed' "and must say the plan was never approved"
    # A gate only `begin` enforced is walked around by starting at step two. Assert the
    # *reason*, not just the exit code: an unbegun run refuses these commands anyway, so
    # a bare exit-code check would pass with the gate deleted.
    mig "$dir" checkpoint hoist-core-model
    [[ $MIG_EXIT -ne 0 ]] || fail "a per-step command on a draft plan must be refused too"
    expect 'not confirmed' "and refused for being unconfirmed, not merely for being unbegun"
    [[ "$(git -C "$dir" rev-parse --abbrev-ref HEAD)" == "$before" ]] || \
        fail "a refused run cut a branch anyway"
    finish
fi

if matches clean-refuse-restores; then
    variant clean-refuse-restores "a refusal leaves the subject exactly as found"; dir="$VDIR"
    plan "$dir"; plan "$dir" --confirm; mig "$dir" begin
    mig "$dir" checkpoint hoist-core-model
    [[ $MIG_EXIT -eq 0 ]] || fail "checkpoint exited $MIG_EXIT: $OUT"
    before="$(fingerprint "$dir" core/model)"
    # A pass that modifies, adds AND deletes — where a naive restore quietly leaks.
    kt="$(find "$dir/core/model" -name '*.kt' | head -1)"
    echo "// mangled by a pass" >> "$kt"
    echo "package added" > "$(dirname "$kt")/AddedByPass.kt"
    rm "$dir/core/model/build.gradle.kts"
    mig "$dir" refuse hoist-core-model --reason "annotation-processor generated" --evidence Note.kt:1
    [[ $MIG_EXIT -eq 0 ]] || fail "refuse exited $MIG_EXIT: $OUT"
    [[ "$(fingerprint "$dir" core/model)" == "$before" ]] || \
        fail "the refused subject is not byte-identical to how it was found"
    [[ -f "$dir/core/model/build.gradle.kts" ]] || fail "a deleted file was not restored"
    # Nothing is rewritten out of history: the work in progress survives as a commit.
    # A here-string, not a pipe: `grep -q` exits on the first match, git takes SIGPIPE,
    # and `pipefail` would report the assertion as failed while it actually passed.
    grep -q 'wip on hoist-core-model' <<<"$(git -C "$dir" log --oneline)" || \
        fail "the work in progress must be kept, not discarded by a reset"
    [[ -z "$(git -C "$dir" status --porcelain)" ]] || fail "a refusal must leave a clean tree"
    plan "$dir" --status
    expect '^step .* hoist-core-model .* refused' "the step ends up refused"
    expect '^refusal-rewrite  hoist-core-model .* prior=in-progress' \
        "recorded as found at rewrite, with the status that says a revert was owed"
    finish
fi

if matches control-clean-order; then
    variant control-clean-order "NEGATIVE CONTROL: a step cannot jump its dependencies"; dir="$VDIR"
    plan "$dir"; plan "$dir" --confirm; mig "$dir" begin
    # migrate-notes consumes shared code that has not reached :core:* yet. Rewriting it
    # now would rewrite it against imports that are about to move under it.
    mig "$dir" checkpoint migrate-notes
    [[ $MIG_EXIT -ne 0 ]] || fail "opening a step with unfinished dependencies must be refused"
    expect 'depends on' "and must name what it is waiting for"
    mig "$dir" complete migrate-notes
    [[ $MIG_EXIT -ne 0 ]] || fail "completing unfinished work must be refused"
    finish
fi

if matches control-plan-managed; then
    variant control-plan-managed "NEGATIVE CONTROL: a promoted feature is never re-migrated"; dir="$VDIR"
    sedi 's/"managedFeatures": \[\]/"managedFeatures": ["portable"]/' "$dir/.kmpilot.json"
    plan "$dir"
    expect '^step .* migrate-portable .* done .* source=derived' \
        "a feature in managedFeatures is done, derived from the manifest, not from the ledger"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATES — the closing phase: promotion, the report, and the gate on both
# ─────────────────────────────────────────────────────────────────────────────

if matches control-integrate-gate; then
    variant control-integrate-gate \
        "NEGATIVE CONTROL: nothing is promoted or reported before the run exists"; dir="$VDIR"
    plan "$dir"      # generated, deliberately NOT confirmed
    rep "$dir" promote
    [[ $REP_EXIT -ne 0 ]] || fail "promoting against an unconfirmed plan must be refused"
    expect 'not confirmed' "and must say the plan was never approved"
    plan "$dir" --confirm
    # Confirmed but never begun: there is no run to report on, and promoting here would
    # mark features as migrated by a migration that has not happened.
    rep "$dir" write
    [[ $REP_EXIT -ne 0 ]] || fail "reporting on a migration that never began must be refused"
    expect 'has not begun' "and must say why"
    [[ ! -f "$dir/MIGRATION-REPORT.md" ]] || fail "a refused integrate wrote a report anyway"
    grep -q '"managedFeatures": \[\]' "$dir/.kmpilot.json" || \
        fail "a refused integrate promoted something anyway"
    # The read-only preview stays answerable — knowing what promotion *would* do is a
    # fair question to ask before approving the plan that leads to it.
    rep "$dir" plan --compact
    [[ $REP_EXIT -eq 0 ]] || fail "the dry run must work without a begun run: $OUT"
    expect '^integrate .* report=no' "and must report that nothing has been written"
    [[ ! -f "$dir/MIGRATION-REPORT.md" ]] || fail "the dry run wrote a report"
    finish
fi

if matches integrate-forced-not-promoted; then
    variant integrate-forced-not-promoted \
        "a forced completion is not promoted — promotion re-runs the checker"; dir="$VDIR"
    plan "$dir"; plan "$dir" --confirm; mig "$dir" begin
    # `done` is a claim. Promotion is where the claim is checked, because a promoted
    # feature is graded strictly from then on: promoting one the checker never passed
    # turns the next archTest red on work the migration called finished.
    mig "$dir" checkpoint migrate-notes
    mig "$dir" complete migrate-notes --force
    [[ $MIG_EXIT -eq 0 ]] || fail "--force must be able to record a sign-off: $OUT"
    rep "$dir" promote
    [[ $REP_EXIT -eq 0 ]] || fail "promote exited $REP_EXIT: $OUT"
    expect 'not promoted .*notes' "a forced completion must be refused promotion, out loud"
    grep -q '"managedFeatures": \[\]' "$dir/.kmpilot.json" || \
        fail "a feature the checker still finds work in reached managedFeatures"
    # And the closing step must not tick while that claim stands.
    mig "$dir" verify report
    [[ $MIG_EXIT -ne 0 ]] || fail "the report step must not verify with a done, unpromoted feature"
    expect 'not promoted' "and must name the inconsistency rather than just failing"
    finish
fi

if matches integrate-report; then
    variant integrate-report "the report names refusals, untested features and missing specs"; dir="$VDIR"
    plan "$dir"; plan "$dir" --confirm; mig "$dir" begin
    mig "$dir" checkpoint migrate-notes
    mig "$dir" complete migrate-notes --force
    rep "$dir" write
    [[ $REP_EXIT -eq 0 ]] || fail "write exited $REP_EXIT: $OUT"
    [[ -f "$dir/MIGRATION-REPORT.md" ]] || fail "no MIGRATION-REPORT.md was written"
    OUT="$(cat "$dir/MIGRATION-REPORT.md")"
    expect '^# Migration report' "the report must be a report"
    expect '## Refusals' "a refusal that is not written down is indistinguishable from a bug"
    expect 'legacy' "discovery's refusals must be named in it"
    expect '## Behavioural risk' "tests are out of scope, so naming the risk is the mitigation"
    expect 'git switch -' "the undo belongs in the artifact, not only in scrollback"
    expect '/audit-spec' "a migrated feature with no spec must be pointed at the one spec writer"
    # Regenerated in full, never appended to: a report that accretes stale sections
    # reads as current, which is worse than not having one.
    rep "$dir" write
    [[ "$(grep -c '^# Migration report' "$dir/MIGRATION-REPORT.md")" -eq 1 ]] || \
        fail "the report was appended to rather than regenerated"
    finish
fi

if matches control-integrate-no-managed-key; then
    variant control-integrate-no-managed-key \
        "NEGATIVE CONTROL: no managedFeatures key means nothing to promote, not an error"; dir="$VDIR"
    # A template project has no such key: every feature is KMPilot's and already graded
    # strictly. Erroring here would refuse a project that has nothing wrong with it.
    sedi '/"managedFeatures"/d' "$dir/.kmpilot.json"
    plan "$dir"; plan "$dir" --confirm; mig "$dir" begin
    rep "$dir" promote
    [[ $REP_EXIT -eq 0 ]] || fail "a project with no managedFeatures key must not be an error: $OUT"
    expect 'nothing to promote' "and must say why rather than silently doing nothing"
    rep "$dir" write
    [[ $REP_EXIT -eq 0 ]] || fail "and the report must still be written: $OUT"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────
# SHAPES — the project layout itself varies, and the invariant must still hold
#
# The claim under test is NOT "these layouts are supported". It is that a working
# KMP project in ANY layout is either migrated or refused with a reason. Each
# variant reshapes the fixture and asserts `invariant`, plus whatever is specific
# to that shape.
# ─────────────────────────────────────────────────────────────────────────────

if matches shape-features-plural; then
    variant shape-features-plural "features live in features/, not feature/"; dir="$VDIR"
    move_module "$dir" feature/portable features/portable :feature:portable :features:portable
    invariant "$dir"
    expect '^feature  :features:portable' "a feature outside feature/ is still found"
    expect_plan '^step .* relocate  :features:portable' \
        "and gets a relocate step — the checker only grades feature/*"
    finish
fi

if matches shape-nested; then
    variant shape-nested "features nested under the app module"; dir="$VDIR"
    move_module "$dir" feature/portable app/features/portable \
        :feature:portable :app:features:portable
    invariant "$dir"
    expect '^feature  :app:features:portable' "a deeply nested feature is still found"
    finish
fi

if matches shape-prefixed-modules; then
    variant shape-prefixed-modules "modules/feature-{name} naming"; dir="$VDIR"
    move_module "$dir" feature/portable modules/feature-portable \
        :feature:portable :modules:feature-portable
    invariant "$dir"
    expect '^feature  :modules:feature-portable' "a prefixed module name is still found"
    finish
fi

if matches shape-shared-not-core; then
    variant shape-shared-not-core "shared code in common/ and libs/, not core/"; dir="$VDIR"
    move_module "$dir" core/model common/model :core:model :common:model
    move_module "$dir" core/netcall libs/netcall :core:netcall :libs:netcall
    invariant "$dir"
    # The tier proposal is driven by what the code *is*, not where it sits.
    expect '^shared  :common:model' "shared code outside core/ is still inventoried as shared"
    expect '^shared  :libs:netcall' "…from any directory"
    finish
fi

if matches shape-multi-feature-module; then
    variant shape-multi-feature-module "one module holding three unrelated screens"; dir="$VDIR"
    # "A feature that turns out to be three" is named in phase-3-clean.md as a
    # mid-rewrite refusal cause. Nothing produces it, so nothing has ever checked
    # that it is *detected* rather than rewritten into one impossible feature: the
    # Screen.kt allowlist admits one screen, not three.
    for extra in billing profile; do
        Cap="$(cap "$extra")"
        mkdir -p "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/$extra"
        cat > "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/$extra/${Cap}Screen.kt" <<EOF
package com.acme.notes.$extra

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun ${Cap}Screen() {
    Text(text = "$extra")
}
EOF
    done
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "three screens in one module" >/dev/null 2>&1
    invariant "$dir"
    expect '^note  multi-feature-module  :feature:portable' \
        "a module holding several unrelated screens must be called out — rewriting it as one feature is impossible, the Screen.kt allowlist admits one screen"
    finish
fi

if matches control-multi-feature-single; then
    variant control-multi-feature-single \
        "NEGATIVE CONTROL: an ordinary one-screen feature is not called several"; dir="$VDIR"
    # The whole risk of the multi-feature classifier is firing on a normal feature —
    # a module full of components/ composables, or one carrying a secondary screen in
    # a subpackage, is still ONE feature. A finding here would appear on every real
    # feature in every real repo.
    mkdir -p "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/ui/components"
    cat > "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/ui/components/Card.kt" <<EOF
package com.acme.notes.portable.presentation.ui.components

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun NoteCard() {
    Text(text = "card")
}
EOF
    # A secondary screen in a SUBPACKAGE of the feature — one feature, not two.
    mkdir -p "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/edit"
    cat > "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/edit/PortableEditScreen.kt" <<EOF
package com.acme.notes.portable.edit

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun PortableEditScreen() {
    Text(text = "edit")
}
EOF
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "components and a secondary screen" >/dev/null 2>&1
    invariant "$dir"
    reject '^note  multi-feature-module' \
        "an ordinary feature — components/ composables plus a secondary screen in a subpackage — must never be reported as several features"
    finish
fi

if matches shape-layer-sliced; then
    variant shape-layer-sliced "layer-sliced repo: ui/ + domain/ + data/, no feature module"; dir="$VDIR"
    # An extremely common Android/KMP layout with no per-feature module at all.
    # KMPilot is feature-sliced by construction, so the honest outcome is a clean
    # refusal or a loud note — never silently treating the whole `ui` layer as one
    # feature and emitting a work list nobody can complete.
    cp -R "$dir/feature/portable" "$dir/uilayer"
    rm -rf "$dir/uilayer/src/commonMain/kotlin/$PKG_PATH/portable"
    for concern in cart checkout; do
        Cap="$(cap "$concern")"
        mkdir -p "$dir/uilayer/src/commonMain/kotlin/$PKG_PATH/ui/$concern"
        cat > "$dir/uilayer/src/commonMain/kotlin/$PKG_PATH/ui/$concern/${Cap}Screen.kt" <<EOF
package com.acme.notes.ui.$concern

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun ${Cap}Screen() {
    Text(text = "$concern")
}
EOF
    done
    printf '\ninclude(":uilayer")\n' >> "$dir/settings.gradle.kts"
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "layer-sliced ui module" >/dev/null 2>&1
    invariant "$dir"
    expect '^note  multi-feature-module  :uilayer' \
        "a whole UI layer is several features, and must be reported as such rather than migrated as one"
    finish
fi

if matches shape-flat-root; then
    variant shape-flat-root "every module at the repo root, no directory grouping"; dir="$VDIR"
    move_module "$dir" feature/portable portable :feature:portable :portable
    move_module "$dir" core/model model :core:model :model
    invariant "$dir"
    expect '^feature  :portable' "a root-level feature is found"
    finish
fi

# ── advisory findings: reported, but not work ───────────────────────────────
#
# From the real repos: an adopted project may navigate by hoisted state instead of a
# NavHost, which the checker itself calls a valid architecture rather than a defect.
# It still reports I4 once per feature, and no edit to a feature clears it. Counted as
# work, it made every feature in such a repo uncompletable — `complete` refused,
# promotion refused the forced sign-off that followed, and the run could not close.

if matches advisory-no-navhost; then
    variant advisory-no-navhost "adopted project that navigates by hoisted state, not a NavHost"; dir="$VDIR"
    cat > "$dir/shared/src/commonMain/kotlin/$PKG_PATH/App.kt" <<'EOF'
package com.acme.notes

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable

@Composable
fun App() {
    MaterialTheme { }
}
EOF
    # Commit the reshape, as move_module does: invariant #1 asserts *discovery* wrote
    # nothing, and it reads `git status` — an uncommitted setup edit would read as one.
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: navigate without a NavHost" >/dev/null 2>&1
    invariant "$dir"
    expect '^feature  :feature:portable .*advisory=' \
        "an advisory finding stays visible — not counted as work is not the same as not shown"
    reject '^feature  :feature:portable .*findings=[^=]*I4' \
        "an unfixable I4 must not be counted as work — that total can never be reached"
    plan "$dir"
    reject '^pass .*rules=[^ ]*I4' \
        "an advisory finding must carry no rewrite pass — no agent can fix a NavHost that is a design choice"
    finish
fi

if matches control-advisory-real-i4; then
    variant control-advisory-real-i4 "NEGATIVE CONTROL: the project HAS a NavHost, feature unregistered"; dir="$VDIR"
    # The base ships a NavHost registering only the notes feature. Every other feature
    # is genuinely unregistered — a real I4 with a real fix. If `advisory` is keying on
    # the rule rather than on the checker's judgement, this stops being reported as work
    # and a feature ships unreachable.
    invariant "$dir"
    reject '^feature  :feature:portable .*advisory=' \
        "a fixable I4 must never be marked advisory"
    plan "$dir"
    expect '^pass .*rules=[^ ]*I4' \
        "a real I4 must still be routed to the integrator as work"
    finish
fi

# ── compose resources have to reach the APK ─────────────────────────────────
#
# From the first real run: the migrated app built, passed every static check, and died
# on launch with MissingResourceException. Rule 12 gives every migrated feature a
# composeResources/values/strings.xml, and in a project whose app module is itself a KMP
# library the resources only propagate when the module sets `androidResources.enable`.
# `install.sh --adopt` writes it into every core/* it vendors, so the core is the signal.

if matches android-resources-missing; then
    variant android-resources-missing "adopted core enables androidResources, a feature does not"; dir="$VDIR"
    invariant "$dir"
    expect '^note  android-resources-not-enabled  :feature:portable' \
        "a feature without the flag must be named — the failure is runtime-only, so nothing else catches it"
    finish
fi

if matches control-android-resources-core-off; then
    variant control-android-resources-core-off "NEGATIVE CONTROL: core does not enable it either"; dir="$VDIR"
    # A repo where the core never sets the flag does not need it — that is KMPilot's own
    # topology, where the app module IS the application. Telling those features to add it
    # is the wrong-warning failure, so the note is keyed on what this project's core does.
    for f in "$dir"/core/*/build.gradle.kts; do
        [ -f "$f" ] || continue
        python3 - "$f" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace("androidResources.enable = true", ""))
PY
    done
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: core does not enable androidResources" >/dev/null 2>&1
    invariant "$dir"
    reject '^note  android-resources-not-enabled' \
        "with the core not enabling it either, no feature needs the flag"
    finish
fi

# -- the nav host is usually a WRAPPER ---------------------------------------
#
# From the second real run: KMPilot's own design system ships `XNavHost`, and it is what
# /create-feature and the template generate against -- so an adopted project's nav host is
# far more often `XNavHost(` than `NavHost(`. A \b-anchored match cannot see it (no word
# boundary between two word characters), and the project was told it had no NavHost at all.
# The expensive half is not the wrong message: with no nav host found, the real I4 check
# never runs, so a feature genuinely missing from the nav graph goes unreported.

if matches nav-host-wrapper; then
    variant nav-host-wrapper "the app's NavHost is the design system's XNavHost wrapper"; dir="$VDIR"
    python3 - "$dir/shared/src/commonMain/kotlin/$PKG_PATH/App.kt" <<'WRAP'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()
s = s.replace("import androidx.navigation.compose.NavHost",
              "import com.acme.notes.designsystem.XNavHost")
s = s.replace("NavHost(", "XNavHost(")
p.write_text(s)
WRAP
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: navigate through the XNavHost wrapper" >/dev/null 2>&1
    invariant "$dir"
    reject '^feature  :feature:portable .*advisory=' \
        "a project that HAS a nav host must not be told it has none - that suppresses the real I4"
    expect '^feature  :feature:portable .*findings=[^=]*I4' \
        "with the wrapper recognised, an unregistered feature is real, fixable work again"
    finish
fi

# -- @Serializable without the compiler plugin -------------------------------
#
# From the second real run: the migrated app compiled on all three targets, passed strict
# archTest, installed -- and died on launch with "Serializer for class 'SearchRoute' is not
# found". Integration Point 4 hands each migrated feature a type-safe @Serializable nav
# route it did not have before, while the plugins block is inherited from whatever the
# module was before the rewrite. Nothing static caught it.

if matches serialization-plugin-missing; then
    variant serialization-plugin-missing "a feature declares @Serializable, its module applies no serialization plugin"; dir="$VDIR"
    mkdir -p "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/navigation"
    cat > "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/navigation/PortableNavigation.kt" <<'ROUTE'
package com.acme.notes.portable.presentation.navigation

import kotlinx.serialization.Serializable

@Serializable
data object PortableRoute
ROUTE
    python3 - "$dir/feature/portable/build.gradle.kts" <<'STRIP'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace("    alias(libs.plugins.kotlinSerialization)\n", ""))
STRIP
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: serializable route, no plugin" >/dev/null 2>&1
    invariant "$dir"
    expect '^feature  :feature:portable .*findings=[^=]*S5' \
        "a serializer that is never generated must be caught statically - only a launch finds it otherwise"
    finish
fi

if matches control-serialization-plugin-present; then
    variant control-serialization-plugin-present "NEGATIVE CONTROL: the plugin IS applied"; dir="$VDIR"
    mkdir -p "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/navigation"
    cat > "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/navigation/PortableNavigation.kt" <<'ROUTE'
package com.acme.notes.portable.presentation.navigation

import kotlinx.serialization.Serializable

@Serializable
data object PortableRoute
ROUTE
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: serializable route, plugin applied" >/dev/null 2>&1
    invariant "$dir"
    reject '^feature  :feature:portable .*findings=[^=]*S5' \
        "a correctly configured module must not be told to add a plugin it already has"
    finish
fi

if matches control-serialization-lib-only; then
    variant control-serialization-lib-only "NEGATIVE CONTROL: the runtime LIBRARY is present, the plugin is not"; dir="$VDIR"
    # The library dependency and the compiler plugin are different things, and only the
    # plugin generates serializers. Matching "serialization" anywhere in the build file
    # would read the dependency as the fix and report the crash as already handled.
    mkdir -p "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/navigation"
    cat > "$dir/feature/portable/src/commonMain/kotlin/$PKG_PATH/portable/presentation/navigation/PortableNavigation.kt" <<'ROUTE'
package com.acme.notes.portable.presentation.navigation

import kotlinx.serialization.Serializable

@Serializable
data object PortableRoute
ROUTE
    python3 - "$dir/feature/portable/build.gradle.kts" <<'LIBONLY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text().replace("    alias(libs.plugins.kotlinSerialization)\n", "")
s = s.replace("commonMain.dependencies {",
              "commonMain.dependencies {\n            implementation(libs.kotlinx.serialization.json)", 1)
s = s.replace("commonMain {\n            dependencies {",
              "commonMain {\n            dependencies {\n                implementation(libs.kotlinx.serialization.json)", 1)
p.write_text(s)
LIBONLY
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: serialization library but no plugin" >/dev/null 2>&1
    invariant "$dir"
    expect '^feature  :feature:portable .*findings=[^=]*S5' \
        "the runtime library is not the compiler plugin - the crash is still live"
    finish
fi


if matches control-navhost-mention-only; then
    variant control-navhost-mention-only "NEGATIVE CONTROL: NavHostController named, no nav host called"; dir="$VDIR"
    # The wrapper match is `\w*NavHost\s*\(` and not a bare `NavHost` for a reason the
    # checker's own docstring records: a file that merely NAMES the type must not earn
    # the nav-host role. `NavHostController` is the near-miss that nearly did it. If the
    # `\(` ever goes, this file is picked as the nav host, the advisory disappears, and
    # I4 is checked against a file that registers nothing — reporting every feature as
    # unregistered. Not a wrong advisory: a whole repo of wrong errors.
    cat > "$dir/shared/src/commonMain/kotlin/$PKG_PATH/App.kt" <<'MENTION'
package com.acme.notes

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController

@Composable
fun App(controller: NavHostController? = null) {
    val stand: NavHostController? = controller
    MaterialTheme { }
}
MENTION
    git -C "$dir" add -A >/dev/null 2>&1
    git -C "$dir" -c core.hooksPath=/dev/null -c user.email=f@l -c user.name=f \
        commit --quiet --no-verify -m "reshape: names NavHostController, calls no nav host" >/dev/null 2>&1
    invariant "$dir"
    expect '^feature  :feature:portable .*advisory=' \
        "naming NavHostController is not having a nav host — the advisory must stand"
    reject '^feature  :feature:portable .*findings=[^=]*I4' \
        "a file that only names the type must not be graded as the nav host"
    finish
fi


echo
echo "${DIM}────────────────────────────────────────────────────────────${OFF}"
echo "${BOLD}$PASSES passed · $FAILURES failed${OFF}"
if [[ $FAILURES -gt 0 ]]; then
    echo "${RED}failing variants:${OFF} ${FAILED_NAMES[*]}"
fi
exit $FAILURES
