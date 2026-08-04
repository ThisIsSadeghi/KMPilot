#!/usr/bin/env bash
# Cut a KMPilot release locally: bump VERSION + libs.versions.toml, roll the
# CHANGELOG [Unreleased] section into a dated release, commit, and tag. Does NOT
# push — review the commit + tag, then push both yourself:
#
#   git push <remote> main && git push <remote> vX.Y.Z
#
# The pushed tag triggers .github/workflows/release.yml, which verifies the
# version is single-sourced and publishes install.sh + update.sh as assets.
#
# Usage: scripts/release.sh X.Y.Z
#        scripts/release.sh --dry-run     # preflight only: verify the generated
#                                         # surfaces match pipeline/, write nothing
set -euo pipefail

DRY_RUN=no
VER="${1:-}"
if [[ "$VER" == "--dry-run" ]]; then
    DRY_RUN=yes
    VER=""
elif [[ ! "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Usage: scripts/release.sh X.Y.Z | scripts/release.sh --dry-run" >&2
    exit 1
fi
TAG="v$VER"

cd "$(git rev-parse --show-toplevel)"

# ── generated-tree gate (maintainer machines only) ──────────────────────────
# `.claude/skills|agents|commands|hooks` is build output. Its source and the
# generator are gitignored, so this check only runs where they exist; a clone
# without them has nothing to verify and skips straight through. Releasing a
# `.claude/` that no longer matches its source would ship stale skills, so where
# the check can run it is a hard failure.
if [[ -f scripts/gen-surfaces.py ]]; then
    if ! python3 scripts/gen-surfaces.py --check; then
        echo "Error: .claude/ is stale. Run: python3 scripts/gen-surfaces.py" >&2
        exit 1
    fi
elif [[ "$DRY_RUN" == "yes" ]]; then
    echo "Note: no generator on this machine — nothing to verify."
fi

if [[ "$DRY_RUN" == "yes" ]]; then
    echo "✓ Dry run: nothing written."
    exit 0
fi

[[ -z "$(git status --porcelain)" ]] || { echo "Error: working tree not clean — commit or stash first" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "Error: tag $TAG already exists" >&2; exit 1; }
grep -q '^## \[Unreleased\]' CHANGELOG.md || { echo "Error: CHANGELOG.md has no '## [Unreleased]' section" >&2; exit 1; }

# Cross-platform sed -i (BSD/macOS vs GNU/Linux)
if [[ "$(uname -s)" == "Darwin" ]]; then sedi() { sed -i '' "$@"; }; else sedi() { sed -i "$@"; }; fi

DATE="$(date -u +%Y-%m-%d)"

printf '%s\n' "$VER" > VERSION

# ── marketing version (X.Y.Z) — must match the tag ──────────────────────────
# Android: androidApp reads this from the version catalog.
sedi "s/^android-versionName *= *\".*\"/android-versionName = \"$VER\"/" gradle/libs.versions.toml
# iOS: CFBundleShortVersionString is hardcoded in Info.plist — bump the <string> on the
# line right after the key.
sedi '/<key>CFBundleShortVersionString<\/key>/{n;s|<string>.*</string>|<string>'"$VER"'</string>|;}' \
    iosApp/iosApp/Info.plist

# ── build number (monotonic counter) — +1 on every release, not tied to the tag ──
# Android versionCode.
CUR_CODE="$(sed -n 's/^android-versionCode *= *"\([0-9]*\)".*/\1/p' gradle/libs.versions.toml | head -n1)"
: "${CUR_CODE:=0}"
NEW_CODE=$(( CUR_CODE + 1 ))
sedi "s/^android-versionCode *= *\".*\"/android-versionCode = \"$NEW_CODE\"/" gradle/libs.versions.toml
# iOS CFBundleVersion (the equivalent of Android's versionCode).
CUR_BUILD="$(sed -n '/<key>CFBundleVersion<\/key>/{n;s/.*<string>\([0-9]*\)<\/string>.*/\1/p;}' iosApp/iosApp/Info.plist)"
: "${CUR_BUILD:=0}"
NEW_BUILD=$(( CUR_BUILD + 1 ))
sedi '/<key>CFBundleVersion<\/key>/{n;s|<string>.*</string>|<string>'"$NEW_BUILD"'</string>|;}' \
    iosApp/iosApp/Info.plist
echo "→ build number: $CUR_CODE → $NEW_CODE (android) / $CUR_BUILD → $NEW_BUILD (ios)"

# Open a new dated release section directly under [Unreleased].
sedi "s|^## \[Unreleased\]|## [Unreleased]\n\n## [$VER] — $DATE|" CHANGELOG.md
# Refresh the [Unreleased] compare link and add the [X.Y.Z] tag link if missing.
sedi "s|^\[Unreleased\]:.*|[Unreleased]: https://github.com/ThisIsSadeghi/KMPilot/compare/$TAG...HEAD|" CHANGELOG.md
if ! grep -q "^\[$VER\]:" CHANGELOG.md; then
    printf '[%s]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/%s\n' "$VER" "$TAG" >> CHANGELOG.md
fi

# Re-run the generator so anything version-stamped in the build output picks up
# the new VERSION. No-op on a machine without the source tree.
[[ -f scripts/gen-surfaces.py ]] && python3 scripts/gen-surfaces.py

git add VERSION gradle/libs.versions.toml iosApp/iosApp/Info.plist CHANGELOG.md
git commit -m "Release $VER"
git tag "$TAG"

echo "✓ Committed + tagged $TAG (nothing pushed). Review, then:"
echo "    git push <remote> main && git push <remote> $TAG"
