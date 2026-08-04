#!/usr/bin/env bash
# Pull a newer KMPilot release into this project WITHOUT corrupting your code.
#
# How it works:
#   • Reads .kmpilot.json (written at install) for your installed version and
#     package prefix.
#   • Fetches upstream at your installed version (base) and the target release.
#   • Tier 1 (always), two policies:
#       – OVERRIDE: .claude/skills, .claude/agents, .claude/commands,
#         .claude/hooks are upstream-owned rule layers — the release version is
#         applied as-is, local edits are overridden (never merged, no conflicts).
#         Files you created yourself (paths KMPilot never shipped) are untouched.
#       – MERGE: .claude/settings.json, CLAUDE.md, gradle wrapper, update.sh
#         are 3-way-merged so your own additions survive.
#   • Tier 2 (--core): re-applies YOUR package rename to upstream core/ changes,
#     then 3-way-merges. Conflicts are written as <<<<<<< markers — NEVER
#     auto-resolved.
#   • Upstream renames follow your local edits to the new path; upstream
#     deletions are applied only when your copy is unmodified — a locally
#     edited copy is never force-deleted, just flagged.
#   • If the target release changed update.sh itself, the run re-execs under
#     the NEW updater so its merge logic applies immediately (not next run).
#   • Every run ends with a stale sweep: unmodified template files the target
#     release no longer ships are deleted — so re-running ./update.sh heals
#     leftovers from updates made under an older updater.
#   • Never touches feature/, your app modules (composeApp/androidApp/iosApp),
#     or your per-feature specs (.claude/docs/<feature>/).
#   • Never commits. You review `git diff`, resolve any conflicts, then commit.
#
# Usage:
#   ./update.sh [--to vX.Y.Z] [--from vX.Y.Z] [--core] [--dry-run] [--stash]
#
#   --to      target release tag (default: latest vX.Y.Z published upstream)
#   --from    base release tag to diff from (default: your installed version)
#   --core    also merge core/ modules (rename-aware; may produce conflicts)
#   --dry-run show what would change; write nothing
#   --stash   auto-stash a dirty working tree before merging
#
set -euo pipefail

# ── output helpers ──────────────────────────────────────────────────────────
info() { printf '   %s\n' "$*"; }
warn() { printf '⚠  %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

# ── args ────────────────────────────────────────────────────────────────────
ORIG_ARGS=("$@")   # kept verbatim for the re-exec bootstrap below
WITH_CORE=no
DRY_RUN=no
STASH=no
TO_OVERRIDE=""
FROM_OVERRIDE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --core)     WITH_CORE=yes ;;
        --dry-run)  DRY_RUN=yes ;;
        --stash)    STASH=yes ;;
        --to)       TO_OVERRIDE="${2:-}"; shift ;;
        --to=*)     TO_OVERRIDE="${1#*=}" ;;
        --from)     FROM_OVERRIDE="${2:-}"; shift ;;
        --from=*)   FROM_OVERRIDE="${1#*=}" ;;
        -h|--help)  sed -n '2,38p' "$0"; exit 0 ;;
        *)          die "unknown argument '$1' (try --help)" ;;
    esac
    shift
done

command -v git >/dev/null 2>&1 || die "git is required"

# ── locate project root (dir holding .kmpilot.json) ─────────────────────────
ROOT="$(pwd)"
while [[ "$ROOT" != "/" && ! -f "$ROOT/.kmpilot.json" ]]; do ROOT="$(dirname "$ROOT")"; done
[[ -f "$ROOT/.kmpilot.json" ]] || die "no .kmpilot.json found — run this from inside a project installed by KMPilot's install.sh"
cd "$ROOT"
MANIFEST="$ROOT/.kmpilot.json"
# Absolute path of the script we are executing — decides whether update.sh can
# be written directly (re-exec'd from a temp copy) or must be staged as .new.
RUNNING_SELF="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)/$(basename "$0")"

# ── read manifest (no jq dependency) ────────────────────────────────────────
json_get() { sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$MANIFEST" | head -n1; }
CUR_VER="$(json_get kmpilotVersion)"
NAME="$(json_get projectName)"
PKG="$(json_get packagePrefix)"
REPO="$(json_get templateRepo)"
U_PKG="$(json_get upstreamPkg)"
U_NAME="$(json_get upstreamName)"
# Added by install.sh --adopt. Absent in every manifest written before adopt mode
# existed, which is exactly what "template" means — so the default keeps every
# older project valid (the release back-compat contract: add fields, never
# rename or repurpose one).
INSTALL_MODE="$(json_get installMode)"; INSTALL_MODE="${INSTALL_MODE:-template}"
APP_MODULE="$(json_get appModule)"; APP_MODULE="${APP_MODULE:-composeApp}"
[[ -n "$CUR_VER" && -n "$PKG" && -n "$REPO" && -n "$U_PKG" && -n "$U_NAME" ]] \
    || die ".kmpilot.json is missing required fields"

# ── derived rename identifiers (mirror scripts/rename.sh exactly) ───────────
PKG_PATH="${PKG//.//}"
# Same sanitization scripts/rename.sh applies (Compose Multiplatform derives the
# generated-resources package from rootProject.name, replacing non-identifier
# characters with '_'). A no-op for a PascalCase template project; required for an
# adopted one whose rootProject.name is e.g. `acme-notes`.
NAME_LOWER="$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_]/_/g')"
U_PKG_PATH="$U_PKG"
U_NAME_LOWER="$(printf '%s' "$U_NAME" | tr '[:upper:]' '[:lower:]')"
U_APPNS="${U_PKG}.${U_NAME_LOWER}"
U_APPID="com.${U_APPNS}"

# Cross-platform sed -i (BSD/macOS vs GNU/Linux)
if [[ "$(uname -s)" == "Darwin" ]]; then sedi() { sed -i '' "$@"; }; else sedi() { sed -i "$@"; }; fi

# rename_stream <src> <dest>: rewrite upstream identifiers → this project's
# (two-phase sentinel, byte-for-byte consistent with scripts/rename.sh:101-108).
rename_stream() {
    cp "$1" "$2"
    sedi "s|${U_APPID}|@@K_APPID@@|g" "$2"
    sedi "s|${U_APPNS}|@@K_APPNS@@|g" "$2"
    sedi "s|${U_PKG}|@@K_PKG@@|g" "$2"
    sedi "s|@@K_APPID@@|${PKG}|g" "$2"
    sedi "s|@@K_APPNS@@|${PKG}|g" "$2"
    sedi "s|@@K_PKG@@|${PKG}|g" "$2"
    sedi "s|${U_NAME}|${NAME}|g" "$2"
    sedi "s|${U_NAME_LOWER}\\.|${NAME_LOWER}.|g" "$2"
}

# rename_path <upstream-path>: core sources live under kotlin/<upstreamPkg>/…;
# remap that one segment to this project's package path. Other paths unchanged.
rename_path() { printf '%s' "$1" | sed "s#/kotlin/${U_PKG_PATH}/#/kotlin/${PKG_PATH}/#"; }

# ── temp upstream checkout ──────────────────────────────────────────────────
TMP="$(mktemp -d)"
STASHED=no
cleanup() {
    rm -rf "$TMP"
    [[ "$STASHED" == "yes" ]] && { info "restoring stashed changes"; git -C "$ROOT" stash pop >/dev/null 2>&1 || true; }
}
trap cleanup EXIT
UP="$TMP/upstream"

echo "→ Fetching upstream ($REPO)…"
git clone --quiet --no-checkout "$REPO" "$UP" || die "clone failed: $REPO"

# ── resolve base + target tags ──────────────────────────────────────────────
BASE_TAG="${FROM_OVERRIDE:-v$CUR_VER}"
if [[ -n "$TO_OVERRIDE" ]]; then
    TARGET_TAG="$TO_OVERRIDE"
else
    TARGET_TAG="$(git -C "$UP" tag -l 'v*' --sort=-v:refname | head -n1)"
fi
[[ -n "$TARGET_TAG" ]] || die "no release tags found upstream"
git -C "$UP" rev-parse -q --verify "refs/tags/${BASE_TAG}^{commit}" >/dev/null \
    || die "base tag '$BASE_TAG' not found upstream — pass --from=<a real release tag>"
git -C "$UP" rev-parse -q --verify "refs/tags/${TARGET_TAG}^{commit}" >/dev/null \
    || die "target tag '$TARGET_TAG' not found upstream"
TARGET_VER="${TARGET_TAG#v}"

# ── bootstrap: run under the TARGET release's updater ───────────────────────
# If update.sh itself changed in the target release, re-exec this run under the
# new updater so its merge logic applies to THIS update, not just the next one.
# KMPILOT_REEXEC guards against loops (e.g. a locally edited update.sh that
# never matches upstream). Must happen before any stash: exec skips the EXIT
# trap, so a stash pushed here would never be popped.
if [[ -z "${KMPILOT_REEXEC:-}" ]]; then
    git -C "$UP" show "${TARGET_TAG}:update.sh" > "$TMP/update.sh.upstream" 2>/dev/null || true
    if [[ -s "$TMP/update.sh.upstream" ]] && ! cmp -s "$TMP/update.sh.upstream" "$0"; then
        echo "↻ Updater changed in $TARGET_TAG — re-running under the new updater…"
        NEW_SELF="$(mktemp "${TMPDIR:-/tmp}/kmpilot-update.XXXXXX")"
        cp "$TMP/update.sh.upstream" "$NEW_SELF"
        # exec never fires the EXIT trap — drop the clone ourselves first
        trap - EXIT
        rm -rf "$TMP"
        KMPILOT_REEXEC=1 exec bash "$NEW_SELF" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
        die "re-exec failed"   # unreachable unless exec itself errored
    fi
fi

# ── path tiers ──────────────────────────────────────────────────────────────
# OVERRIDE: upstream-owned rule layers — downstream must follow upstream; the
# release version is applied as-is (local edits overridden, no merge conflicts).
OVERRIDE_PATHS=(.claude/skills .claude/agents .claude/commands .claude/hooks)
# MERGE: shared tooling where downstream additions are legitimate — 3-way-merged.
TIER1_PATHS=(.claude/settings.json .claude/docs/_shared
             CLAUDE.md gradlew gradlew.bat gradle/wrapper update.sh)
TIER2_PATHS=(core)
# MANUAL: surfaced for review, never auto-merged. An ADOPTED project has none of
# KMPilot's app modules — its app module is its own, and upstream changes to
# composeApp/androidApp/iosApp are about a shell it does not have. Reporting them
# would be pure noise, so the app-shell entries are dropped there. The Gradle
# files stay in both modes: adopt mode edits settings.gradle.kts and the root
# build (includes, the kmpilotLibs catalog, the archTest task).
MANUAL_PATHS=(gradle/libs.versions.toml build.gradle.kts settings.gradle.kts)
if [[ "$INSTALL_MODE" != "adopt" ]]; then
    MANUAL_PATHS+=(composeApp androidApp iosApp)
fi

# In adopt mode, a file KMPilot could not claim (the target already had its own
# CLAUDE.md or .claude/settings.json) was written beside it as *.kmpilot.*.
# Updates must land on the sidecar — 3-way merging upstream into a file the
# project owns would splice KMPilot's content into their document.
redirect_down_path() {  # <downstream-path> → the path to actually write
    local p="$1" side
    if [[ "$INSTALL_MODE" == "adopt" ]]; then
        side="${p%.*}.kmpilot.${p##*.}"
        if [[ -f "$side" ]]; then printf '%s' "$side"; return 0; fi
    fi
    printf '%s' "$p"
}

applied=0; merged=0; moved=0; deleted=0; conflicts=0; skipped=0; manual=0

up_show() { git -C "$UP" show "${1}:${2}" 2>/dev/null || true; }

# make the downstream file executable when upstream tracks it as 100755
# (files land via `git show >`/cp, which drops the exec bit — hooks would
# silently stop firing without this)
apply_mode() {  # <upstream-path> <downstream-path>
    local mode
    mode="$(git -C "$UP" ls-tree -r "$TARGET_TAG" -- "$1" 2>/dev/null | awk '{print $1; exit}')"
    if [[ "$mode" == "100755" ]]; then chmod +x "$2"; fi
}

# git's own binary heuristic: numstat prints "-<TAB>-" for binary content
is_binary() {  # <file>
    local n
    n="$(git diff --no-index --numstat /dev/null "$1" 2>/dev/null || true)"
    [[ "$n" == "-"$'\t'"-"$'\t'* ]]
}

# delete a downstream file and prune now-empty parent dirs
remove_down() {  # <downstream-path>
    if [[ "$DRY_RUN" == "no" ]]; then
        rm -f "$1"
        rmdir -p "$(dirname "$1")" 2>/dev/null || true
    fi
}

# is_override_path <path>: true when the path lives in an upstream-owned layer
is_override_path() {
    local p="$1" o
    for o in "${OVERRIDE_PATHS[@]}"; do
        if [[ "$p" == "$o" || "$p" == "$o"/* ]]; then return 0; fi
    done
    return 1
}

# ── stale sweep ──────────────────────────────────────────────────────────────
# Delete Tier-1 files the target release no longer ships. Heals a project whose
# previous update ran under a pre-0.1.2 updater that could not apply
# deletions/renames (it left old skill/agent/command copies behind).
#   • OVERRIDE layers: swept whenever the path shipped in ANY upstream release —
#     upstream owns these files, local edits included (recoverable via git).
#   • MERGE paths: swept only when byte-identical to what some release shipped
#     at that path — locally edited copies are kept.
# Files you created yourself (paths upstream never shipped) are never touched.
stale_sweep() {
    local f blob t t_blob all_tags
    all_tags="$(git -C "$UP" tag -l 'v*')"
    while IFS= read -r f; do
        f="${f#./}"
        [[ -f "$f" ]] || continue
        # still shipped at the target release → not stale
        if git -C "$UP" cat-file -e "${TARGET_TAG}:${f}" 2>/dev/null; then continue; fi
        if is_override_path "$f"; then
            for t in $all_tags; do
                if git -C "$UP" cat-file -e "${t}:${f}" 2>/dev/null; then
                    remove_down "$f"
                    info "sweep:   $f (upstream rule file no longer shipped)"
                    deleted=$((deleted+1))
                    break
                fi
            done
        else
            blob="$(git hash-object "$f")"
            for t in $all_tags; do
                t_blob="$(git -C "$UP" rev-parse -q --verify "${t}:${f}" 2>/dev/null || true)"
                if [[ -n "$t_blob" && "$t_blob" == "$blob" ]]; then
                    remove_down "$f"
                    info "sweep:   $f (no longer shipped upstream)"
                    deleted=$((deleted+1))
                    break
                fi
            done
        fi
    done < <(find "${OVERRIDE_PATHS[@]}" "${TIER1_PATHS[@]}" -type f 2>/dev/null)
}

# ── already on the latest release: still sweep, then exit ───────────────────
if [[ "$BASE_TAG" == "$TARGET_TAG" ]]; then
    echo "✓ Already on the latest release ($TARGET_TAG)."
    if [[ "$DRY_RUN" == "no" ]] && { ! git -C "$ROOT" diff --quiet 2>/dev/null || ! git -C "$ROOT" diff --cached --quiet 2>/dev/null; }; then
        info "working tree not clean — skipped the stale-file sweep (commit or stash, then re-run)"
    else
        stale_sweep
        if [[ "$deleted" -gt 0 ]]; then
            if [[ "$DRY_RUN" == "yes" ]]; then
                echo "  Dry run — $deleted stale file(s) from an older updater would be deleted."
            else
                echo "  Swept $deleted stale file(s) left by an older updater — review with: git diff"
            fi
        else
            echo "  Nothing to do."
        fi
    fi
    exit 0
fi

# ── preflight: clean working tree (so merge results are reviewable) ─────────
if [[ "$DRY_RUN" == "no" ]]; then
    # `update.sh` is tracked, so the documented bootstrap for pre-0.1.2 projects
    #   curl …/update.sh -o update.sh
    # dirties the tree by itself — and the very next command used to refuse
    # because of it. A tree whose ONLY change is the updater is clean enough:
    # this run is about to overwrite that file anyway.
    DIRTY_PATHS="$(git -C "$ROOT" status --porcelain -- . 2>/dev/null | awk '{print $NF}' | grep -v '^update\.sh$' || true)"
    if [[ -n "$DIRTY_PATHS" ]]; then
        if [[ "$STASH" == "yes" ]]; then
            git -C "$ROOT" stash push -u -m "kmpilot-update" >/dev/null; STASHED=yes
            info "stashed your working changes"
        else
            die "working tree not clean. Commit or stash first, or pass --stash."
        fi
    elif ! git -C "$ROOT" diff --quiet -- update.sh 2>/dev/null; then
        info "update.sh is modified (the bootstrap step) — continuing; this run replaces it"
    fi
    # stray artifact from an aborted earlier run — the bootstrap supersedes it
    rm -f update.sh.new
fi

[[ "$DRY_RUN" == "yes" ]] && echo "→ Updating $BASE_TAG → $TARGET_TAG  (dry run)" || echo "→ Updating $BASE_TAG → $TARGET_TAG"
[[ "$INSTALL_MODE" == "adopt" ]] && info "adopted project — app module '$APP_MODULE'; KMPilot's app shell is not tracked here"

# process_file <up-old-path> <up-new-path> <status A|M|D|R> <rename yes|no> <policy merge|override>
# For A/M/D old == new; for R (upstream rename) they differ. Reads the caller's
# BASE at the old path, writes the result to the new path. Policy `override`
# applies upstream as-is (no 3-way merge, local edits overridden); `merge` is
# the conflict-surfacing 3-way merge.
process_file() {
    local up_old="$1" up_new="$2" status="$3" rename="$4" policy="$5" down_old down_new
    if [[ "$rename" == "yes" ]]; then
        down_old="$(rename_path "$up_old")"; down_new="$(rename_path "$up_new")"
    else
        down_old="$up_old"; down_new="$up_new"
    fi
    down_old="$(redirect_down_path "$down_old")"
    down_new="$(redirect_down_path "$down_new")"

    # Never overwrite the RUNNING updater in place — bash may re-read $0 mid-run
    # and corrupt this very invocation. Only needed when this process actually
    # executes ROOT/update.sh; a re-exec'd run (temp copy) writes it directly.
    if [[ "$down_new" == "update.sh" && "$RUNNING_SELF" == "$ROOT/update.sh" ]]; then down_new="update.sh.new"; fi

    local base_f="$TMP/base" theirs_f="$TMP/theirs" ours_f="$TMP/ours" result="$TMP/result"
    : > "$base_f"; : > "$theirs_f"

    if [[ "$status" != "D" ]]; then
        if [[ "$rename" == "yes" ]]; then
            up_show "$TARGET_TAG" "$up_new" > "$TMP/raw"; rename_stream "$TMP/raw" "$theirs_f"
        else
            up_show "$TARGET_TAG" "$up_new" > "$theirs_f"
        fi
    fi
    if [[ "$status" != "A" ]]; then
        if [[ "$rename" == "yes" ]]; then
            up_show "$BASE_TAG" "$up_old" > "$TMP/raw"; rename_stream "$TMP/raw" "$base_f"
        else
            up_show "$BASE_TAG" "$up_old" > "$base_f"
        fi
    fi

    # ── override policy: upstream rule layers are authoritative ──
    # No 3-way merge and no conflicts: the target release's file lands as-is,
    # deletions/renames apply even over local edits (recoverable via git).
    if [[ "$policy" == "override" ]]; then
        if [[ "$status" == "D" ]]; then
            if [[ -f "$down_old" ]]; then
                remove_down "$down_old"
                info "delete:  $down_old"; deleted=$((deleted+1))
            else
                skipped=$((skipped+1))
            fi
            return
        fi
        if [[ "$down_old" != "$down_new" && -f "$down_old" ]]; then
            remove_down "$down_old"
            info "move:    $down_old → $down_new"; moved=$((moved+1))
        fi
        if [[ -f "$down_new" ]] && cmp -s "$down_new" "$theirs_f"; then
            skipped=$((skipped+1)); return
        fi
        if [[ ! -f "$down_new" ]]; then
            info "add:     $down_new"
        elif cmp -s "$down_new" "$base_f"; then
            info "update:  $down_new"
        else
            info "force:   $down_new (upstream rule file — local changes overridden)"
        fi
        if [[ "$DRY_RUN" == "no" ]]; then
            mkdir -p "$(dirname "$down_new")"; cp "$theirs_f" "$down_new"
            apply_mode "$up_new" "$down_new"
        fi
        applied=$((applied+1))
        return
    fi

    # ── upstream deletion: apply only when your copy is unmodified ──
    if [[ "$status" == "D" ]]; then
        if [[ ! -f "$down_old" ]]; then skipped=$((skipped+1)); return; fi
        if cmp -s "$down_old" "$base_f"; then
            remove_down "$down_old"
            info "delete:  $down_old"; deleted=$((deleted+1))
        else
            warn "upstream removed $up_old but your $down_old has local changes — delete manually if unused"
            manual=$((manual+1))
        fi
        return
    fi

    # ours: prefer the new path (a rename may already be half-applied), else the
    # old path (upstream rename — your edits live there and must follow the move)
    local ours_src=""
    if [[ -f "$down_new" ]]; then ours_src="$down_new"
    elif [[ -f "$down_old" ]]; then ours_src="$down_old"; fi

    # downstream missing entirely → straight add
    if [[ -z "$ours_src" ]]; then
        if [[ "$DRY_RUN" == "no" ]]; then
            mkdir -p "$(dirname "$down_new")"; cp "$theirs_f" "$down_new"
            apply_mode "$up_new" "$down_new"
        fi
        info "add:     $down_new"; applied=$((applied+1)); return
    fi
    cp "$ours_src" "$ours_f"

    # clear_old_path: after content landed at $down_new, deal with a leftover
    # old-path copy. Uses the caller's locals (bash dynamic scoping).
    clear_old_path() {
        if [[ "$down_old" == "$down_new" || ! -f "$down_old" ]]; then return 0; fi
        if [[ "$ours_src" == "$down_old" ]]; then
            # this copy was the merge input — its content now lives at the new path
            remove_down "$down_old"
            info "move:    $down_old → $down_new"; moved=$((moved+1))
        elif cmp -s "$down_old" "$base_f"; then
            # stale unmodified leftover from an earlier update run
            remove_down "$down_old"
            info "delete:  $down_old (renamed upstream)"; deleted=$((deleted+1))
        else
            warn "renamed upstream, but old copy $down_old has local edits — delete manually"
            manual=$((manual+1))
        fi
        return 0
    }

    # already identical to upstream target → at most finish the move
    if cmp -s "$ours_f" "$theirs_f"; then
        if [[ "$ours_src" != "$down_new" ]]; then
            if [[ "$DRY_RUN" == "no" ]]; then
                mkdir -p "$(dirname "$down_new")"; cp "$theirs_f" "$down_new"
                apply_mode "$up_new" "$down_new"
            fi
            clear_old_path
        else
            clear_old_path
            skipped=$((skipped+1))
        fi
        return
    fi

    # ── binary: never text-merge (merge-file would corrupt it) ──
    if is_binary "$theirs_f" || is_binary "$ours_f"; then
        if cmp -s "$ours_f" "$base_f"; then
            if [[ "$DRY_RUN" == "no" ]]; then
                mkdir -p "$(dirname "$down_new")"; cp "$theirs_f" "$down_new"
                apply_mode "$up_new" "$down_new"
            fi
            clear_old_path
            info "binary:  $down_new (took upstream — your copy was unmodified)"
            applied=$((applied+1))
        else
            warn "binary changed upstream AND locally: $down_new — reconcile manually"
            manual=$((manual+1))
        fi
        return
    fi

    set +e
    git merge-file -p -L "yours" -L "base ($BASE_TAG)" -L "upstream ($TARGET_TAG)" \
        "$ours_f" "$base_f" "$theirs_f" > "$result" 2>/dev/null
    local rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then
        if [[ "$DRY_RUN" == "no" ]]; then
            mkdir -p "$(dirname "$down_new")"; cp "$result" "$down_new"
            apply_mode "$up_new" "$down_new"
        fi
        info "merge:   $down_new"; merged=$((merged+1))
        clear_old_path
    elif [[ $rc -ge 1 && $rc -lt 128 ]]; then
        if [[ "$DRY_RUN" == "no" ]]; then
            mkdir -p "$(dirname "$down_new")"; cp "$result" "$down_new"
            apply_mode "$up_new" "$down_new"
        fi
        warn "CONFLICT: $down_new — resolve the <<<<<<< markers"; conflicts=$((conflicts+1))
        clear_old_path
    else
        warn "merge error on $down_new (skipped)"; manual=$((manual+1))
    fi
}

# process_tier <rename yes|no> <policy merge|override> <path...>
process_tier() {
    local rename="$1" policy="$2"; shift 2
    while IFS=$'\t' read -r status p1 p2; do
        [[ -z "${status:-}" ]] && continue
        case "${status:0:1}" in
            A) process_file "$p1" "$p1" A "$rename" "$policy" ;;
            D) process_file "$p1" "$p1" D "$rename" "$policy" ;;
            R) process_file "$p1" "$p2" R "$rename" "$policy" ;;
            *) process_file "$p1" "$p1" M "$rename" "$policy" ;;
        esac
    done < <(git -C "$UP" diff --name-status --find-renames "$BASE_TAG" "$TARGET_TAG" -- "$@")
}

echo "→ Tier 1 — upstream rule layers (skills/agents/commands/hooks — upstream wins)…"
process_tier no override "${OVERRIDE_PATHS[@]}"

echo "→ Tier 1 — merged tooling (settings, CLAUDE.md, gradle wrapper, updater)…"
process_tier no merge "${TIER1_PATHS[@]}"

if [[ "$WITH_CORE" == "yes" ]]; then
    echo "→ Tier 2 — core/ (rename-aware)…"
    process_tier yes merge "${TIER2_PATHS[@]}"
else
    core_changed="$(git -C "$UP" diff --name-only --no-renames "$BASE_TAG" "$TARGET_TAG" -- "${TIER2_PATHS[@]}" 2>/dev/null | grep -c . || true)"
    [[ "${core_changed:-0}" -gt 0 ]] && info "core/ changed in $core_changed file(s) — re-run with --core to merge them"
fi

echo "→ Stale sweep (unmodified files no longer shipped upstream)…"
stale_sweep

# ── manual-review report ────────────────────────────────────────────────────
changed_manual="$(git -C "$UP" diff --name-only --no-renames "$BASE_TAG" "$TARGET_TAG" -- "${MANUAL_PATHS[@]}" 2>/dev/null || true)"
if [[ -n "$changed_manual" ]]; then
    warn "Manual review — upstream changed these; reconcile by hand (NOT auto-applied):"
    if [[ "$INSTALL_MODE" == "adopt" ]]; then
        # Upstream's catalog is vendored as gradle/kmpilot.versions.toml here;
        # the project's own gradle/libs.versions.toml is never touched by KMPilot.
        printf '%s\n' "$changed_manual" \
            | sed 's|^gradle/libs\.versions\.toml$|gradle/kmpilot.versions.toml   (KMPilot catalog — yours is untouched; ./install.sh --adopt --force regenerates it)|' \
            | sed 's/^/      /' >&2
    else
        printf '%s\n' "$changed_manual" | sed 's/^/      /' >&2
    fi
    manual=$(( manual + $(printf '%s\n' "$changed_manual" | grep -c .) ))
    # The app-version caveat is about the template's own app module; an adopted
    # project keeps its version in its own catalog, which KMPilot never edits.
    if [[ "$INSTALL_MODE" != "adopt" ]] && printf '%s\n' "$changed_manual" | grep -qE 'libs\.versions\.toml|iosApp/iosApp/Info\.plist'; then
        warn "  ↳ Your APP version lives in these (android-versionName/versionCode, iOS CFBundleShortVersionString/CFBundleVersion)."
        warn "    That version is YOURS and independent of KMPilot's — merge only dependency/config changes;"
        warn "    do NOT copy KMPilot's version numbers over your own."
    fi
fi

# ── bump manifest version (unless dry run) ──────────────────────────────────
if [[ "$DRY_RUN" == "no" ]]; then
    sedi "s|\"kmpilotVersion\"[[:space:]]*:[[:space:]]*\"[^\"]*\"|\"kmpilotVersion\": \"${TARGET_VER}\"|" "$MANIFEST"
fi

# ── summary ─────────────────────────────────────────────────────────────────
echo ""
echo "── Update summary ($BASE_TAG → $TARGET_TAG) ──"
echo "   added:     $applied"
echo "   merged:    $merged"
echo "   moved:     $moved"
echo "   deleted:   $deleted"
echo "   unchanged: $skipped"
echo "   conflicts: $conflicts"
echo "   manual:    $manual"
echo ""
if [[ "$DRY_RUN" == "yes" ]]; then
    echo "Dry run — nothing written. Re-run without --dry-run to apply."
    exit 0
fi
# The updater changed and was staged as update.sh.new (couldn't cp over the running script's
# inode mid-run). Swapping it in with `mv` (rename) IS safe — this process keeps executing its
# original inode, the new file only takes effect next run. update.sh is tracked in git, so the
# change shows in `git diff` and is revertible. Only exists when DRY_RUN=no, so no guard needed.
if [[ -f update.sh.new ]]; then
    mv -f update.sh.new update.sh && chmod +x update.sh
    echo "↻ Updated the updater itself (update.sh) — change is in your git diff; revert if unwanted."
    echo ""
fi
echo "Release notes: ${REPO%.git}/blob/main/CHANGELOG.md"
if [[ "$conflicts" -gt 0 ]]; then
    echo "Resolve the <<<<<<< conflict markers, then:  git diff  →  git add -A  →  git commit"
    echo "Abandoning instead? Revert EVERYTHING in one go:  git checkout -- ."
    echo "(.kmpilot.json was already bumped to ${TARGET_VER} — a partial revert that keeps it"
    echo " makes the next update diff from the wrong base and silently skip changes.)"
    exit 1
fi
echo "Review with:  git diff   then commit when satisfied (this script never commits)."
