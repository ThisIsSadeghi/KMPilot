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

if matches control-plan-managed; then
    variant control-plan-managed "NEGATIVE CONTROL: a promoted feature is never re-migrated"; dir="$VDIR"
    sedi 's/"managedFeatures": \[\]/"managedFeatures": ["portable"]/' "$dir/.kmpilot.json"
    plan "$dir"
    expect '^step .* migrate-portable .* done .* source=derived' \
        "a feature in managedFeatures is done, derived from the manifest, not from the ledger"
    finish
fi

# ─────────────────────────────────────────────────────────────────────────────

echo
echo "${DIM}────────────────────────────────────────────────────────────${OFF}"
echo "${BOLD}$PASSES passed · $FAILURES failed${OFF}"
if [[ $FAILURES -gt 0 ]]; then
    echo "${RED}failing variants:${OFF} ${FAILED_NAMES[*]}"
fi
exit $FAILURES
