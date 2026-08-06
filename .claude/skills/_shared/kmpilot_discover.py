#!/usr/bin/env python3
"""
kmpilot_discover.py — read-only inventory of a KMP project, for migration planning.

Step 1 of `/kmp-to-kmpilot` (Phase 6, Stage B): answer "what is in this repo, where
would it go, and in what order" **without writing anything**. The rewriting half of
migration is built entirely on this answer, so it is a deterministic script rather
than model prose — for the same reason `kmpilot_check.py` is. A migration and a CI
run must not disagree about what a project contains.

    python3 .claude/skills/_shared/kmpilot_discover.py --root ~/code/theirapp
    python3 .claude/skills/_shared/kmpilot_discover.py --json-only
    python3 .claude/skills/_shared/kmpilot_discover.py --root . --report /tmp/disc.json

**Writes nothing unless `--report` is passed**, and `--report` is deliberately not
defaulted to a path inside the target: discovery is the one migration phase with a
hard no-side-effects guarantee, and a default report path would quietly break it.

## What is mechanical and what is a proposal

Everything this script *decides* is checkable by a second pair of eyes against the
files: the module graph, source layout, targets, catalog usage, Android-API
evidence with file:line, topological order.

Everything requiring judgment — which `:core:*` tier a shared package belongs in,
whether an odd-shaped feature is worth refusing — is emitted as a **proposal with
its reason and evidence**, for the plan phase and the human to accept or overrule.
Phase 6's central safety property is that a human confirms the plan before any
write; a script that silently made these calls would absorb that guarantee.

## Rule findings are not re-derived

Per-feature rule violations come from `kmpilot_check.py` itself (imported, run
in-process at `--baseline` grading), never re-implemented here. Two checkers that
can disagree are worse than one.

Note the scope limit that follows from it: the checker only looks at `feature/*`
(install.sh:1803). A project keeping its features at the repo root — a common real
shape — gets those features **found** by discovery and reported with a note saying
they cannot be graded until they move. That is a fact about the plan, not a bug.

## Android-locked detection, and the two traps in it

A feature is Android-locked when it reaches for an Android-only API **from a source
set that is not Android-specific**. An `android.content.Context` import inside
`androidMain` is not a problem — it is Rule 14 working as designed. The same import
in `commonMain` cannot compile for iOS.

Two APIs look Android-only and are deliberately NOT flagged, because KMPilot's own
`commonMain` uses both, and flagging them would refuse every feature in every real
repo:

  * `androidx.navigation.*` — `NavController`, `NavGraphBuilder`, `NavHostController`,
    `NavBackStackEntry`, `toRoute` are the Compose Multiplatform navigation artifact.
    Only `androidx.navigation.fragment` / `androidx.navigation.ui` are Android-locked.
  * `androidx.lifecycle.ViewModel` / `viewModelScope` — the KMP lifecycle artifact,
    and the base class the pipeline itself targets. Only `LiveData` and friends
    are Android-locked.

Comments and string literals are blanked before matching (`blank_noncode`), so a
commented-out `import retrofit2.Retrofit` cannot fabricate a refusal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# The checker is a sibling in this directory and ships with it (both are `.claude/
# skills` — OVERRIDE tier — so they arrive downstream together and cannot skew).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kmpilot_check as check  # noqa: E402

Source = check.Source
read = check.read
Palette = check.Palette
blank_noncode = check.blank_noncode

SETTINGS_GRADLE = check.SETTINGS_GRADLE
MANIFEST = check.MANIFEST
REPO_ROOT = check.REPO_ROOT

# 2 — feature rows carry `findingRows`: the checker's own violations with file:line,
#     not just per-rule counts. The plan phase groups them into rewrite passes, so a
#     count alone would have sent it back to the checker for a second opinion.
SCHEMA_VERSION = 2

# ─── Android-locked APIs ─────────────────────────────────────────────────────

# (id, import-prefix regex, human reason). Order matters only for output stability.
# Read the module docstring before adding `androidx.navigation.` or
# `androidx.lifecycle.` here — both are multiplatform and both are in use in
# KMPilot's own commonMain.
ANDROID_LOCKED = [
    ("framework", r"android\.(?!ic[uo]n)", "Android framework API"),
    ("hilt", r"(dagger\.|javax\.inject\.|androidx\.hilt\.)", "Hilt / Dagger DI"),
    ("retrofit", r"retrofit2\.", "Retrofit"),
    ("okhttp", r"okhttp3\.", "OkHttp"),
    ("room", r"androidx\.room\.", "Room (Android artifact)"),
    (
        "livedata",
        r"androidx\.lifecycle\.(Mutable)?LiveData|androidx\.lifecycle\.Observer"
        r"|androidx\.lifecycle\.(asLiveData|liveData|LiveDataScope)",
        "LiveData",
    ),
    ("rxjava", r"(io\.reactivex\.|rx\.(android|schedulers|subjects)\.)", "RxJava"),
    ("fragment", r"androidx\.fragment\.", "Fragments"),
    ("navcomponent", r"androidx\.navigation\.(fragment|ui)\.", "Navigation Component (fragment/ui)"),
    ("appcompat", r"androidx\.appcompat\.", "AppCompat"),
    ("databinding", r"[\w.]+\.databinding\.", "View / Data Binding"),
    ("viewsystem", r"androidx\.(recyclerview|constraintlayout|coordinatorlayout|cardview)\.", "Android View system"),
    ("workmanager", r"androidx\.work\.", "WorkManager"),
    ("preference", r"androidx\.preference\.", "Android Preference"),
    ("glide", r"(com\.bumptech\.glide\.|com\.squareup\.picasso\.)", "Android-only image loader"),
]
ANDROID_LOCKED = [(i, re.compile(rf"^{p}"), r) for i, p, r in ANDROID_LOCKED]

# A source set whose name mentions android is allowed to hold Android APIs — that is
# what it is for. `main` is the AGP-only convention and is treated the same way, but
# only for a module that has no Kotlin Multiplatform plugin (see `Module.is_kmp`).
ANDROID_SOURCESET = re.compile(r"android", re.IGNORECASE)
# `androidResources.enable = true` in the AGP KMP library DSL — what makes a module
# actually publish its compose resources into the Android assets of whatever consumes it.
ANDROID_RESOURCES = re.compile(r"androidResources\s*\.\s*enable\s*=\s*true")

# ─── Tier proposal ───────────────────────────────────────────────────────────

# What marks a file as belonging to the data tier / the design-system tier. Anything
# matching neither is a plain value type or helper and proposes `common.app`.
TIER_DATA = re.compile(
    r"@Serializable\b|\bio\.ktor\b|\bHttpClient\b|\bRetrofit\b|\bDao\b|RoomDatabase"
    r"|\bDataStore\b|SqlDriver|\bSettings\b|kotlinx\.serialization|\bHttpResponse\b"
)
TIER_UI = re.compile(r"@Composable\b|androidx\.compose\.|\bXTheme\b|\bColorScheme\b|\bTypography\b")
# Filenames that name a data concern regardless of what the body happens to import.
TIER_DATA_NAME = re.compile(
    r"(Dto|Response|Request|Api|Client|DataSource|Repository|Entity|Database|Preferences|Store)$"
)

TIER_LABELS = {
    "common": "core:common (common.app)",
    "data": "core:data (data.app)",
    "designsystem": "core:designsystem (designsystem.app)",
    "split": "split across tiers",
    "blocked": "no tier proposed — unhoistable",
}

# KMPilot's own vendored core packages — used to tell a host's `core/network` (to be
# hoisted) from the `core/data` KMPilot itself put there (already home).
KMPILOT_CORE = ("common", "data", "designsystem")

IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)")
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)")
COMPOSABLE_SCREEN = re.compile(r"\w*Screen$")


# ─── Gradle parsing ──────────────────────────────────────────────────────────


def strip_comments(text: str) -> str:
    """Blank comment bodies, **keep string literals intact**.

    The checker's `blank_noncode` blanks string contents too, which is right for
    Kotlin structural matching and catastrophic for Gradle: every fact a build script
    carries is inside a string — `include(":feature:search")`, `project(":core:data")`,
    `jvm("desktop")`, `id("com.android.application")`. Strings are skipped over here
    rather than blanked, so a `//` inside one is not mistaken for a comment."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
        elif c == "/" and nxt == "*":
            while i < n and not text.startswith("*/", i):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
        elif text.startswith('"""', i):
            out.append('"""')
            i += 3
            while i < n and not text.startswith('"""', i):
                out.append(text[i])
                i += 1
            out.append('"""')
            i += 3
        elif c == '"':
            out.append(c)
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    out.append(text[i : i + 2])
                    i += 2
                    continue
                out.append(text[i])
                i += 1
            out.append('"')
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_settings(root: Path) -> tuple[list[str], list[str]]:
    """Every `include(...)` path, plus the version-catalog accessors the project
    declares. Comments are blanked first so a commented-out include is not a module."""
    text = strip_comments(read(root / SETTINGS_GRADLE))
    includes: list[str] = []
    for call in re.finditer(r"\binclude\s*\(([^)]*)\)", text):
        includes.extend(re.findall(r"[\"']([^\"']+)[\"']", call.group(1)))
    # Kotlin DSL also allows `include ":a"` without parens in some setups.
    for call in re.finditer(r"^\s*include\s+[\"']([^\"']+)[\"']", text, re.MULTILINE):
        includes.append(call.group(1))

    catalogs = re.findall(r"\bcreate\s*\(\s*[\"']([^\"']+)[\"']\s*\)", text)
    if (root / "gradle/libs.versions.toml").is_file() and "libs" not in catalogs:
        catalogs.append("libs")  # the implicit default accessor
    return sorted(set(includes)), sorted(set(catalogs))


def parse_catalog(path: Path) -> dict[str, str]:
    """`[libraries]` of a version catalog as accessor-name → `group:artifact`.

    Only enough TOML for this one job: an alias's coordinate, so `libs.some.alias`
    in a build file can be reported as the Retrofit dependency it actually is.
    Aliases normalise `-` and `_` to `.`, matching Gradle's accessor generation."""
    text = read(path)
    if not text:
        return {}
    out: dict[str, str] = {}
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section != "libraries" or "=" not in stripped or stripped.startswith("#"):
            continue
        alias, _, value = stripped.partition("=")
        alias = alias.strip().strip('"').replace("-", ".").replace("_", ".")
        module = re.search(r'module\s*=\s*"([^"]+)"', value)
        if module:
            out[alias] = module.group(1)
            continue
        group = re.search(r'group\s*=\s*"([^"]+)"', value)
        name = re.search(r'name\s*=\s*"([^"]+)"', value)
        if group and name:
            out[alias] = f"{group.group(1)}:{name.group(1)}"
        elif value.strip().startswith('"'):
            coord = value.strip().strip('"')
            out[alias] = ":".join(coord.split(":")[:2])
    return out


# Coordinates worth naming in the report: each one implies migration work or a
# refusal. Anything else is noise at this altitude.
NOTABLE_COORD = [
    (re.compile(r"^com\.squareup\.retrofit2:"), "retrofit"),
    (re.compile(r"^com\.squareup\.okhttp3:"), "okhttp"),
    (re.compile(r"^com\.google\.dagger:"), "hilt"),
    (re.compile(r"^androidx\.room:"), "room"),
    (re.compile(r"^io\.reactivex"), "rxjava"),
    (re.compile(r"^androidx\.fragment:"), "fragment"),
    (re.compile(r"^androidx\.appcompat:"), "appcompat"),
    (re.compile(r"^androidx\.work:"), "workmanager"),
    (re.compile(r"^io\.ktor:"), "ktor"),
    (re.compile(r"^io\.insert-koin:"), "koin"),
]


class Module:
    """One Gradle module: what it is, what it targets, what it depends on."""

    def __init__(self, root: Path, gradle_path: str, catalogs: list[str]):
        self.gradle_path = gradle_path
        self.name = gradle_path.lstrip(":").split(":")[-1]
        self.dir_rel = gradle_path.lstrip(":").replace(":", "/")
        self.dir = root / self.dir_rel
        self.exists = (self.dir / "build.gradle.kts").is_file() or (
            self.dir / "build.gradle"
        ).is_file()
        build_file = self.dir / "build.gradle.kts"
        if not build_file.is_file():
            build_file = self.dir / "build.gradle"
        self.build_rel = (
            build_file.relative_to(root).as_posix() if build_file.is_file() else None
        )
        self.gradle = strip_comments(read(build_file)) if build_file.is_file() else ""

        self.plugins = self._plugins()
        self.is_kmp = any(
            p in self.plugins for p in ("kotlinMultiplatform", "androidMultiplatformLibrary")
        )
        self.targets = self._targets()
        self.jvm_target = self._jvm_target()
        self.project_deps = sorted(set(re.findall(r"project\s*\(\s*\"(:[^\"]+)\"", self.gradle)))
        self.catalogs = sorted(
            {c for c in catalogs if re.search(rf"\b{re.escape(c)}\.", self.gradle)}
        )
        self.sources: list[Source] = []
        self.source_sets: list[str] = []
        self.packages: set[str] = set()
        self.root_package = ""
        self.kind = "other"

    def _jvm_target(self) -> int | None:
        """The module's JVM bytecode target, or None when it does not name one.

        Read because KMPilot's `:core:*` modules are JVM 21 and expose **inline**
        functions (`setState`, the `Either`/`UiState` helpers). A host module pinned
        lower cannot inline them: the build fails with "Cannot inline bytecode built
        with JVM target 21 into bytecode that is being built with JVM target 11",
        which names neither the module that caused it nor the migration that surfaced
        it. Nothing else in the pipeline would catch it — verification is static.
        """
        found = [
            int(m.group(1))
            for m in re.finditer(r"JvmTarget\.JVM_(\d+)", self.gradle)
        ]
        found += [
            int(m.group(1))
            for m in re.finditer(r"""jvmTarget\s*(?:=|\.set\s*\(\s*)\s*["'](\d+)["']""", self.gradle)
        ]
        return max(found) if found else None

    def _plugins(self) -> set[str]:
        found: set[str] = set()
        for m in re.finditer(r"alias\s*\(\s*\w+\.plugins\.([\w.]+)\s*\)", self.gradle):
            found.add(m.group(1).split(".")[-1])
        for m in re.finditer(r'\bid\s*\(\s*"([^"]+)"', self.gradle):
            plugin = m.group(1)
            found.add(plugin)
            if plugin == "org.jetbrains.kotlin.multiplatform":
                found.add("kotlinMultiplatform")
            if plugin == "com.android.application":
                found.add("androidApplication")
            if plugin == "com.android.library":
                found.add("androidLibrary")
        return found

    def _targets(self) -> list[str]:
        t: set[str] = set()
        if (
            # Both call forms: `androidTarget()` and the block form `androidTarget { … }`,
            # which is what a module writes as soon as it configures compilerOptions or
            # androidResources. Matching only the paren form inventories such a module as
            # having no android target at all.
            re.search(r"\bandroidTarget\s*[({]", self.gradle)
            or re.search(r"^\s*android(Library)?\s*\{", self.gradle, re.MULTILINE)
            or "androidApplication" in self.plugins
            or "androidLibrary" in self.plugins
            or "androidMultiplatformLibrary" in self.plugins
        ):
            t.add("android")
        # Both call styles are in use: `iosArm64()` and the configuration-block form
        # `iosArm64 { … }` (which is what KMPilot's own core modules write).
        if re.search(r"\bios(Arm64|X64|SimulatorArm64)?\s*[({]", self.gradle):
            t.add("ios")
        if re.search(r'\bjvm\s*\(\s*"desktop"\s*\)', self.gradle):
            t.add("desktop")
        elif re.search(r"\bjvm\s*[({]", self.gradle):
            t.add("jvm")
        return sorted(t)

    def android_only(self) -> bool:
        """An AGP module with no Kotlin Multiplatform plugin — its `main` source set
        is Android code by construction, and none of it can move as-is."""
        return not self.is_kmp and (
            "androidApplication" in self.plugins or "androidLibrary" in self.plugins
        )

    def notable_deps(self, catalog: dict[str, str]) -> list[str]:
        """Dependencies worth naming: resolved through the version catalog where the
        build file uses an alias, plus any literal `"group:artifact:version"`."""
        coords: set[str] = set()
        for m in re.finditer(r"\b\w+\.((?:[a-zA-Z0-9]+\.)*[a-zA-Z0-9]+)\b", self.gradle):
            hit = catalog.get(m.group(1))
            if hit:
                coords.add(hit)
        for m in re.finditer(r'"([\w.\-]+:[\w.\-]+):[\w.\-]+"', self.gradle):
            coords.add(m.group(1))
        found: set[str] = set()
        for coord in coords:
            for pattern, label in NOTABLE_COORD:
                if pattern.search(coord):
                    found.add(label)
        return sorted(found)


# ─── Source scanning ─────────────────────────────────────────────────────────

SOURCE_SET_RE = re.compile(r"/src/([^/]+)/")


def source_set_of(rel: str) -> str:
    m = SOURCE_SET_RE.search(f"/{rel}")
    return m.group(1) if m else ""


def is_android_sourceset(name: str, module: Module) -> bool:
    if ANDROID_SOURCESET.search(name):
        return True
    return name == "main" and module.android_only()


def imports_of(src: Source) -> list[tuple[str, int]]:
    """Every `import` with its 1-based line, read from the comment-blanked view."""
    out = []
    for idx, line in enumerate(src.code_lines):
        m = IMPORT_RE.match(line)
        if m:
            out.append((m.group(1), idx + 1))
    return out


def package_of(src: Source) -> str:
    for line in src.code_lines[:40]:
        m = PACKAGE_RE.match(line)
        if m:
            return m.group(1)
    return ""


def scan_module(root: Path, module: Module) -> None:
    module.sources = check.collect_sources(root, module.dir)
    module.source_sets = sorted({source_set_of(s.rel) for s in module.sources} - {""})
    module.packages = {package_of(s) for s in module.sources} - {""}
    if module.packages:
        module.root_package = min(module.packages, key=lambda p: (p.count("."), p))


def android_evidence(module: Module) -> list[dict]:
    """Android-only imports reached from a source set that is not Android-specific.

    An Android-only *module* reports its imports too, but flagged `expected: True` —
    they are not a portability defect, they are the reason the whole module cannot
    move as-is."""
    out: list[dict] = []
    for src in module.sources:
        sset = source_set_of(src.rel)
        expected = is_android_sourceset(sset, module)
        for name, line in imports_of(src):
            for api_id, pattern, reason in ANDROID_LOCKED:
                if pattern.match(name):
                    out.append(
                        {
                            "api": api_id,
                            "import": name,
                            "reason": reason,
                            "file": src.rel,
                            "line": line,
                            "sourceSet": sset,
                            "expected": expected,
                        }
                    )
                    break
    return out


def blocking_evidence(module: Module) -> list[dict]:
    return [e for e in android_evidence(module) if not e["expected"]]


def find_entry_point(module: Module) -> dict | None:
    """A screen entry point: a top-level `@Composable fun *Screen(...)`. Falls back to
    any top-level `@Composable fun`, reported as a weaker match — a feature whose UI
    entry is called something else is unusual but migratable; a feature with no
    composable at all has no screen to migrate."""
    fallback = None
    for src in module.sources:
        for decl in src.declarations:
            if decl["kind"] != "fun" or "@Composable" not in decl["annotations"]:
                continue
            hit = {"composable": decl["name"], "file": src.rel, "line": decl["line"]}
            if COMPOSABLE_SCREEN.match(decl["name"]):
                return {**hit, "match": "screen"}
            fallback = fallback or {**hit, "match": "composable"}
    return fallback


def screen_roots(module: Module) -> list[str]:
    """The distinct packages that each declare a top-level `@Composable fun *Screen`.

    A module holding several of these is not one feature — it is several, and the
    rules cannot be satisfied by rewriting it as one: `Screen.kt`'s allowlist admits
    exactly one screen (plus its Root), and a feature has one DI module, one nav
    extension and one package. That case is named in the clean phase as a reason to
    refuse mid-rewrite; finding it here means the user hears it while the plan is
    still a plan.

    Two deliberate limits on how loudly this is claimed:

    * **Only `*Screen`-named top-level composables count.** A conforming feature has
      one or two of them (`XScreen` + `XScreenRoot`) in a single package, and dozens
      of ordinary composables under `components/` that must not be counted.
    * **Ancestor packages collapse into their descendants.** A feature with a
      secondary screen in a subpackage (the documented `kind: screen` case) is one
      feature, not two.

    It drives a **note, not a refusal**. The heuristic can be wrong — a genuine single
    feature may spread screens across sibling packages — and the cost of being wrong
    has to stay one line of output rather than a wrongly refused feature.
    """
    packages = {
        package_of(src)
        for src in module.sources
        for decl in src.declarations
        if decl["kind"] == "fun"
        and "@Composable" in decl["annotations"]
        and COMPOSABLE_SCREEN.match(decl["name"])
        and package_of(src)
    }
    return sorted(
        pkg for pkg in packages
        if not any(other != pkg and other.startswith(pkg + ".") for other in packages)
    )


# ─── Tier proposal ───────────────────────────────────────────────────────────


def files_declaring(module: Module, symbols: set[str]) -> set[str]:
    """The module's files that declare any of `symbols` (fully-qualified names).

    Precision matters here: when one feature imports `…favorites.FavoritesRepository`,
    what has to move is that declaration, not every file in the `favorites` package —
    which also holds the screen and would drag a UI proposal into a data question."""
    wanted = {s.rsplit(".", 1)[-1] for s in symbols}
    out: set[str] = set()
    for src in module.sources:
        if any(d["name"] in wanted for d in src.declarations):
            out.add(src.rel)
    return out


def propose_tier(
    module: Module, only: set[str] | None = None
) -> tuple[str, str, dict[str, list[str]]]:
    """Which `:core:*` tier this content belongs in, per file, then rolled up.

    `only` narrows the question to specific files (repo-relative) of the module — used
    for code that lives inside a feature and is consumed from outside it, where only
    the imported declarations move, not the whole feature.

    Returns (tier, reason, files-by-tier). The reason names the **trigger that fired**
    so the proposal is auditable rather than an opaque verdict. A module whose files
    disagree proposes `split` and hands the per-file breakdown to the plan phase
    rather than guessing a majority — a wrong split is cheap to fix on paper and
    expensive in code."""
    by_tier: dict[str, list[str]] = {"common": [], "data": [], "designsystem": []}
    triggers: dict[str, Counter] = {t: Counter() for t in by_tier}
    for src in module.sources:
        if only is not None and src.rel not in only:
            continue
        stem = Path(src.rel).stem
        ui = TIER_UI.search(src.text)
        data = TIER_DATA.search(src.text)
        name_hit = TIER_DATA_NAME.search(stem)
        if ui:
            by_tier["designsystem"].append(src.rel)
            triggers["designsystem"][ui.group(0).strip()] += 1
        elif data or name_hit:
            by_tier["data"].append(src.rel)
            triggers["data"][(data or name_hit).group(0).strip()] += 1
        else:
            by_tier["common"].append(src.rel)
    present = {t: f for t, f in by_tier.items() if f}
    if not present:
        return "common", "no production sources found", by_tier
    if len(present) == 1:
        tier = next(iter(present))
        if tier == "common":
            return tier, "value types and helpers only — no wire, storage or UI markers", by_tier
        marks = ", ".join(f"`{t}`" for t, _ in triggers[tier].most_common(3))
        kind = "wire / storage markers" if tier == "data" else "UI markers"
        return tier, f"{kind}: {marks}", by_tier
    biggest = max(present, key=lambda t: len(present[t]))
    counts = ", ".join(f"{len(present[t])}× {t}" for t in sorted(present))
    return "split", f"mixed content ({counts}) — largest share is {biggest}", by_tier


# ─── Graph ───────────────────────────────────────────────────────────────────


def source_level_edges(modules: dict[str, Module]) -> dict[tuple[str, str], list[dict]]:
    """Edges implied by imports rather than by Gradle. Catches the case Gradle hides:
    a package that lives inside one feature and is imported by another, which is the
    shared code a per-feature migration had no vantage point to see."""
    owners: list[tuple[str, str]] = []  # (package, gradle_path), longest package first
    for path, module in modules.items():
        for pkg in module.packages:
            owners.append((pkg, path))
    owners.sort(key=lambda o: -len(o[0]))

    edges: dict[tuple[str, str], list[dict]] = {}
    for path, module in modules.items():
        for src in module.sources:
            for name, line in imports_of(src):
                for pkg, owner in owners:
                    if owner == path or not name.startswith(pkg + "."):
                        continue
                    if name.rsplit(".", 1)[0] != pkg:
                        continue  # a deeper sub-package; its own owner wins
                    edges.setdefault((path, owner), []).append(
                        {"symbol": name, "package": pkg, "file": src.rel, "line": line}
                    )
                    break
    return edges


def topo_order(nodes: list[str], edges: set[tuple[str, str]]) -> tuple[list[str], list[list[str]]]:
    """Dependencies before consumers, ties broken alphabetically for a stable report.

    Cycles are removed from the ordering and returned separately: a cross-feature
    cycle is not orderable and is precisely what hoisting exists to break, so it is
    reported as work rather than silently linearised."""
    incoming = {n: set() for n in nodes}
    outgoing = {n: set() for n in nodes}
    for consumer, dependency in edges:
        if consumer in incoming and dependency in incoming and consumer != dependency:
            incoming[consumer].add(dependency)
            outgoing[dependency].add(consumer)

    order: list[str] = []
    ready = sorted(n for n in nodes if not incoming[n])
    remaining = dict(incoming)
    while ready:
        node = ready.pop(0)
        order.append(node)
        for consumer in sorted(outgoing[node]):
            remaining[consumer].discard(node)
            if not remaining[consumer] and consumer not in order and consumer not in ready:
                ready.append(consumer)
        ready.sort()

    stuck = [n for n in nodes if n not in order]
    cycles = find_cycles(stuck, edges) if stuck else []
    return order, cycles


def find_cycles(nodes: list[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    """Strongly connected components of the leftover subgraph (Tarjan, iterative)."""
    scope = set(nodes)
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for consumer, dependency in edges:
        if consumer in scope and dependency in scope:
            adj[consumer].append(dependency)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    out: list[list[str]] = []

    def strongconnect(start: str) -> None:
        work = [(start, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            recursed = False
            for i in range(child_i, len(adj[node])):
                nxt = adj[node][i]
                work[-1] = (node, i + 1)
                if nxt not in index:
                    work.append((nxt, 0))
                    recursed = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if recursed:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    component.append(popped)
                    if popped == node:
                        break
                if len(component) > 1:
                    out.append(sorted(component))

    for node in sorted(nodes):
        if node not in index:
            strongconnect(node)
    return sorted(out)


# ─── Driver ──────────────────────────────────────────────────────────────────


def manifest_field(root: Path, field: str) -> str | None:
    manifest = root / MANIFEST
    if not manifest.is_file():
        return None
    m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', read(manifest))
    return m.group(1) if m else None


def project_role(root: Path, install_mode: str) -> str:
    """Which of four situations this repo is in — migration only applies to one.

      pipeline-source  KMPilot itself (or a checkout of it): installer + checker
                       present, no manifest. The reference implementation, never a
                       migration target — it already conforms by construction.
      template         Generated by `install.sh`: its features are KMPilot's own, so
                       rule violations are `/modify-feature` work, not migration.
      adopted          `install.sh --adopt` ran here. The only migration target.
      unadopted        A KMP repo with no KMPilot in it. Adopt first — migration
                       rewrites source against the manifest.
    """
    if (root / MANIFEST).is_file():
        return "adopted" if install_mode == "adopt" else "template"
    if (root / "install.sh").is_file() and (
        root / ".claude/skills/_shared/kmpilot_check.py"
    ).is_file():
        return "pipeline-source"
    return "unadopted"


def project_name(root: Path) -> str:
    """`rootProject.name` wins over the manifest's `projectName`: adopt records the
    name at adoption time, and settings.gradle.kts is what Gradle actually uses."""
    m = re.search(r'rootProject\.name\s*=\s*"([^"]+)"', read(root / SETTINGS_GRADLE))
    return m.group(1) if m else (manifest_field(root, "projectName") or root.name)


def classify_kind(module: Module, root_pkg_prefix: str, app_module: str) -> str:
    """app / core-kmpilot / core-host / feature / shared / other.

    `core-kmpilot` is the vendored `:core:{common,data,designsystem}` that adopt mode
    wrote — already home. `core-host` and `shared` are the project's own shared code,
    which is what step 2 has to find a tier for."""
    if module.name == app_module or module.dir_rel == app_module:
        return "app"
    if "androidApplication" in module.plugins:
        # An AGP *application* module beside the KMP app module is the Android
        # launcher, whatever it is called (`androidApp` in KMPilot, `app` almost
        # everywhere else) — matching on the name missed the common case.
        return "app-android"
    if module.android_only():
        return "other"
    if module.dir_rel.startswith("feature/"):
        return "feature"
    if module.dir_rel.startswith("core/"):
        return "core-kmpilot" if module.name in KMPILOT_CORE else "core-host"
    if module.sources and any(
        d["kind"] == "fun" and "@Composable" in d["annotations"]
        for s in module.sources
        for d in s.declarations
    ):
        return "feature"  # a root-level feature: Compose UI, not under feature/
    return "shared" if module.sources else "other"


def discover(root: Path) -> dict:
    includes, catalogs = parse_settings(root)
    app_module = check.resolve_app_module(root)
    pkg_prefix = check.resolve_pkg_prefix(root)
    install_mode = check.resolve_install_mode(root)
    managed = check.resolve_managed_features(root)

    catalog_map: dict[str, str] = {}
    for name in catalogs:
        for candidate in (f"gradle/{name}.versions.toml", "gradle/libs.versions.toml"):
            catalog_map.update(parse_catalog(root / candidate))

    modules: dict[str, Module] = {}
    for gradle_path in includes:
        module = Module(root, gradle_path, catalogs)
        if not module.exists:
            continue
        scan_module(root, module)
        modules[gradle_path] = module
    for module in modules.values():
        module.kind = classify_kind(module, pkg_prefix, app_module)

    src_edges = source_level_edges(modules)
    gradle_edges = {
        (path, dep) for path, m in modules.items() for dep in m.project_deps if dep in modules
    }
    all_edges = gradle_edges | set(src_edges)

    features = [p for p, m in modules.items() if m.kind == "feature"]
    shared = [p for p, m in modules.items() if m.kind in ("core-host", "shared")]

    # ── rule findings, from the checker, never re-derived ────────────────────
    gradable = sorted(
        modules[p].name for p in features if modules[p].dir_rel.startswith("feature/")
    )
    findings: dict[str, list[dict]] = {}
    if gradable:
        violations, _ = check.run(root, gradable)
        for v in violations:
            if v["feature"]:
                findings.setdefault(v["feature"], []).append(v)

    notes: list[dict] = []
    refusals: list[dict] = []
    role = project_role(root, install_mode)

    # The bar every host module has to clear to be able to inline from `:core:*`.
    core_jvm_targets = [
        m.jvm_target
        for m in modules.values()
        if m.kind == "core-kmpilot" and m.jvm_target is not None
    ]
    core_jvm_target = max(core_jvm_targets) if core_jvm_targets else None

    # Whether the vendored core turns Android resource processing on. It is needed when
    # the app module is itself a KMP library behind a thin android app, because compose
    # resources have to propagate feature → app-library → application; where it is not
    # needed, adopt does not write it. So the core modules in *this* repo are the signal
    # for what a migrated feature has to do here — not a rule that holds everywhere.
    core_android_resources = any(
        m.kind == "core-kmpilot" and ANDROID_RESOURCES.search(m.gradle)
        for m in modules.values()
    )

    if role == "template":
        notes.append(
            {
                "id": "template-mode",
                "subject": MANIFEST.as_posix(),
                "message": "installMode is template — this project was generated by KMPilot, so "
                "its features are already KMPilot's and there is nothing to migrate. Rule findings "
                "here are /modify-feature work.",
            }
        )
    elif role == "pipeline-source":
        notes.append(
            {
                "id": "pipeline-source",
                "subject": root.name,
                "message": "This is KMPilot itself — the reference implementation, not a migration "
                "target. Its features conform by construction; run kmpilot_check.py to hold them "
                "to that.",
            }
        )
    elif role == "unadopted":
        notes.append(
            {
                "id": "not-adopted",
                "subject": MANIFEST.as_posix(),
                "message": "No .kmpilot.json — this repo has not been adopted. Run "
                "`install.sh --adopt` first; migration rewrites source against the manifest.",
            }
        )

    # ── features ────────────────────────────────────────────────────────────
    feature_rows: list[dict] = []
    for path in sorted(features):
        module = modules[path]
        blocking = blocking_evidence(module)
        entry = find_entry_point(module)
        name = module.name
        rows = findings.get(name, [])
        in_managed = bool(managed) and name in (managed or [])

        if role in ("template", "pipeline-source"):
            # KMPilot wrote these. Violations are /modify-feature work; nothing here
            # is a migration candidate, however many findings it carries.
            verdict = "conforming" if not rows else "owned"
        elif in_managed:
            verdict = "conforming"
        elif blocking:
            verdict = "android-locked"
        elif entry is None:
            # Refused below for the same reason. A feature reported `portable` while
            # also appearing in refusals[] reads as a contradiction, so the verdict
            # carries the refusal rather than the rule findings.
            verdict = "no-entry-point"
        elif not rows and module.dir_rel.startswith("feature/"):
            verdict = "conforming"
        else:
            verdict = "portable"

        consumes = sorted({dep for consumer, dep in all_edges if consumer == path})
        consumed_by = sorted({c for c, dep in all_edges if dep == path})

        feature_rows.append(
            {
                "name": name,
                "gradlePath": path,
                "dir": module.dir_rel,
                "package": module.root_package,
                "location": "featuredir" if module.dir_rel.startswith("feature/") else "root",
                "targets": module.targets,
                "catalogs": module.catalogs,
                "sourceSets": module.source_sets,
                "entryPoint": entry,
                "verdict": verdict,
                "inManagedFeatures": in_managed,
                "androidEvidence": blocking,
                "notableDeps": module.notable_deps(catalog_map),
                # Counts mean *work*, everywhere: a feature reported as "5 findings" and
                # then migrated to 0 must be able to reach 0. Advisory rows have no fix,
                # so counting them would make every total in the plan and the report an
                # unreachable target. They are carried in `findingRows` and counted
                # separately instead of dropped — losing them would hide the checker's
                # own advice from the reader.
                "findings": dict(Counter(v["rule"] for v in check.actionable(rows))),
                "findingCount": len(check.actionable(rows)),
                "advisoryCount": len(rows) - len(check.actionable(rows)),
                # The per-feature work list, straight from the checker. The plan phase
                # clusters these into rewrite passes; keeping only the counts would
                # have forced it to run the checker a second time to find out where.
                "findingRows": [
                    {
                        "rule": v["rule"],
                        "severity": v["severity"],
                        "file": v["file"],
                        "line": v["line"],
                        "message": v["message"],
                        **({"advisory": True} if v.get("advisory") else {}),
                    }
                    for v in rows
                ],
                "consumes": consumes,
                "consumedBy": consumed_by,
            }
        )

        roots = screen_roots(module)
        if len(roots) > 1:
            notes.append(
                {
                    "id": "multi-feature-module",
                    "subject": path,
                    "message": f"{len(roots)} screen entry points in separate packages "
                    f"({', '.join(roots)}) — this module looks like several features, not one. "
                    "KMPilot is feature-sliced: one screen, one DI module, one nav extension, "
                    "one package per feature module. Split it before migrating, or expect to "
                    "refuse it mid-rewrite.",
                }
            )
        if not module.dir_rel.startswith("feature/"):
            notes.append(
                {
                    "id": "feature-outside-featuredir",
                    "subject": path,
                    "message": f"{module.dir_rel}/ is a feature but does not live under feature/ — "
                    "kmpilot_check.py only grades feature/*, so it has no rule findings yet. "
                    "Moving it is part of the plan.",
                }
            )
        if "desktop" not in module.targets and "jvm" not in module.targets:
            notes.append(
                {
                    "id": "missing-desktop-target",
                    "subject": path,
                    "message": "no desktop/jvm target — KMPilot's rules assume android + ios + "
                    "desktop, and every expect needs a desktop actual or the build breaks.",
                }
            )
        if core_android_resources and "android" in module.targets \
                and not ANDROID_RESOURCES.search(module.gradle):
            notes.append(
                {
                    "id": "android-resources-not-enabled",
                    "subject": path,
                    "message": "no `androidResources.enable = true`, but this project's "
                    ":core:* modules set it — so compose resources reach the APK there and "
                    "will not from here. Rule 12 gives every migrated feature a "
                    "composeResources/values/strings.xml, and without the flag it is "
                    "silently absent at runtime: the build succeeds and the app dies on the "
                    "first stringResource() with MissingResourceException.",
                }
            )
        if core_jvm_target is not None and module.jvm_target is not None \
                and module.jvm_target < core_jvm_target:
            notes.append(
                {
                    "id": "jvm-target-below-core",
                    "subject": path,
                    "message": f"compiles to JVM {module.jvm_target} but KMPilot's :core:* "
                    f"modules are JVM {core_jvm_target}, and they expose inline functions "
                    "(setState, the Either/UiState helpers). Raise this module to "
                    f"JVM {core_jvm_target} or the migrated feature will not compile. "
                    "On an Android module raise BOTH halves — `compilerOptions.jvmTarget` "
                    "AND `compileOptions.source/targetCompatibility`; moving only the "
                    "Kotlin one fails with \"Inconsistent JVM targets between Java and "
                    "Kotlin compile tasks\".",
                }
            )
        cross = [c for c in consumes if modules.get(c) and modules[c].kind == "feature"]
        for other in cross:
            notes.append(
                {
                    "id": "cross-feature-dependency",
                    "subject": f"{path} → {other}",
                    "message": "features never depend on other features — the shared code has to "
                    "reach :core:* before either feature is migrated.",
                }
            )

        if blocking:
            refusals.append(
                {
                    "subject": path,
                    "kind": "feature",
                    "reason": "Android-only APIs in non-Android source sets: "
                    + ", ".join(sorted({e["reason"] for e in blocking})),
                    "evidence": [f"{e['file']}:{e['line']} {e['import']}" for e in blocking[:8]],
                }
            )
        elif entry is None:
            refusals.append(
                {
                    "subject": path,
                    "kind": "feature",
                    "reason": "no screen entry point — no top-level @Composable fun in the module, "
                    "so there is no screen to migrate",
                    "evidence": [],
                }
            )

    # ── shared code ─────────────────────────────────────────────────────────
    shared_rows: list[dict] = []
    for path in sorted(shared):
        module = modules[path]
        blocking = blocking_evidence(module)
        tier, reason, by_tier = propose_tier(module)
        consumers = sorted({c for c, dep in all_edges if dep == path})
        feature_consumers = [c for c in consumers if modules.get(c) and modules[c].kind == "feature"]
        if tier == "data" and len(feature_consumers) >= 2:
            reason += f" — used by {len(feature_consumers)} features, so data.app per the DRY corollary"

        # A destination for something that cannot move is noise, and misleading
        # besides: the Rule-14 treatment that unblocks it (hide the platform API
        # behind a DataSource) usually changes which tier it lands in anyway. Report
        # the blocker; let the plan phase pick a tier once it is resolved.
        if blocking:
            tier, reason = "blocked", "tier undecided until the Android blocker is resolved"

        shared_rows.append(
            {
                "gradlePath": path,
                "dir": module.dir_rel,
                "package": module.root_package,
                "targets": module.targets,
                "consumers": consumers,
                "featureConsumers": feature_consumers,
                "proposedTarget": TIER_LABELS[tier],
                "proposedTier": tier,
                "reason": reason,
                "filesByTier": {t: f for t, f in by_tier.items() if f},
                "hoistable": not blocking,
                "blockers": blocking,
                "notableDeps": module.notable_deps(catalog_map),
            }
        )
        if blocking:
            refusals.append(
                {
                    "subject": path,
                    "kind": "shared",
                    "reason": "shared code cannot be hoisted — Android-only APIs in non-Android "
                    "source sets: " + ", ".join(sorted({e["reason"] for e in blocking})),
                    "evidence": [f"{e['file']}:{e['line']} {e['import']}" for e in blocking[:8]],
                    "blocks": consumers,
                }
            )

    # Shared code living *inside* a feature: an edge into a feature module whose
    # imported packages are not that feature's own screen surface. Gradle cannot see
    # this; it is the placement question project scope exists to answer.
    in_feature_shared: list[dict] = []
    for (consumer, owner), uses in sorted(src_edges.items()):
        if modules[owner].kind != "feature" or consumer == owner:
            continue
        # The app module importing a feature's screen is Integration Point 4 working
        # as designed, not shared code that needs hoisting.
        if modules[consumer].kind in ("app", "app-android"):
            continue
        symbols = {u["symbol"] for u in uses}
        declaring = files_declaring(modules[owner], symbols)
        tier, reason, _ = propose_tier(modules[owner], declaring or None)
        in_feature_shared.append(
            {
                "owner": owner,
                "consumer": consumer,
                "packages": sorted({u["package"] for u in uses}),
                "symbols": sorted(symbols),
                "declaredIn": sorted(declaring),
                "evidence": [f"{u['file']}:{u['line']}" for u in uses[:8]],
                "proposedTarget": TIER_LABELS[tier],
                "proposedTier": tier,
                "reason": reason,
            }
        )

    # ── order ───────────────────────────────────────────────────────────────
    scope = sorted(set(features) | set(shared))
    order, cycles = topo_order(scope, all_edges)
    for cycle in cycles:
        notes.append(
            {
                "id": "dependency-cycle",
                "subject": " ↔ ".join(cycle),
                "message": "dependency cycle — not orderable as-is; hoisting the shared code out "
                "is what breaks it.",
            }
        )

    host_catalogs = sorted({c for m in modules.values() for c in m.catalogs})
    if len(host_catalogs) > 1:
        notes.append(
            {
                "id": "catalog-split",
                "subject": ", ".join(host_catalogs),
                "message": "two version catalogs in play — migrated features must read the one "
                f"{MANIFEST.as_posix()} names (catalogAccessor), not whichever they read today.",
            }
        )

    refused = {r["subject"] for r in refusals}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": {
            "root": str(root),
            "rootProjectName": project_name(root),
            "packagePrefix": pkg_prefix,
            "installMode": install_mode,
            "role": role,
            "migrationTarget": role == "adopted",
            "kmpilotVersion": manifest_field(root, "kmpilotVersion"),
            "appModule": app_module,
            "catalogAccessor": manifest_field(root, "catalogAccessor"),
            "catalogs": catalogs,
            "managedFeatures": managed,
            "moduleCount": len(modules),
        },
        "modules": [
            {
                "gradlePath": p,
                "dir": m.dir_rel,
                "kind": m.kind,
                "package": m.root_package,
                "targets": m.targets,
                "sourceSets": m.source_sets,
                "catalogs": m.catalogs,
                "projectDeps": m.project_deps,
                "notableDeps": m.notable_deps(catalog_map),
                "sourceFiles": len(m.sources),
            }
            for p, m in sorted(modules.items())
        ],
        "features": feature_rows,
        "shared": shared_rows,
        "inFeatureShared": in_feature_shared,
        "graph": {
            "gradleEdges": sorted(list(e) for e in gradle_edges),
            "sourceEdges": sorted(list(e) for e in src_edges),
            "order": order,
            "cycles": cycles,
        },
        "refusals": refusals,
        "notes": notes,
        "summary": {
            "modules": len(modules),
            "features": len(feature_rows),
            "migratable": sum(
                1
                for f in feature_rows
                if f["gradlePath"] not in refused
                and f["verdict"] not in ("conforming", "owned")
            ),
            "conforming": sum(1 for f in feature_rows if f["verdict"] == "conforming"),
            "owned": sum(1 for f in feature_rows if f["verdict"] == "owned"),
            "refused": len(refusals),
            "sharedPackages": len(shared_rows),
            "hoistable": sum(1 for s in shared_rows if s["hoistable"]),
            "findings": sum(f["findingCount"] for f in feature_rows),
            "notes": len(notes),
        },
    }


# ─── Output ──────────────────────────────────────────────────────────────────


def print_compact(report: dict) -> None:
    """One greppable line per row — what the matrix and CI assert against."""
    p = report["project"]
    print(f"project  {p['rootProjectName']}  role={p['role']}  "
          f"migrationTarget={str(p['migrationTarget']).lower()}  app={p['appModule']}  "
          f"pkg={p['packagePrefix']}  modules={p['moduleCount']}")
    for m in report["modules"]:
        print(f"module  {m['gradlePath']}  {m['kind']}  targets={','.join(m['targets']) or '-'}  "
              f"catalogs={','.join(m['catalogs']) or '-'}  files={m['sourceFiles']}")
    for f in report["features"]:
        rules = " ".join(f"{r}×{n}" for r, n in sorted(f["findings"].items())) or "-"
        entry = f"{f['entryPoint']['file']}:{f['entryPoint']['line']}" if f["entryPoint"] else "none"
        # Advisory rows are excluded from `findings` because they are not work, but they
        # are still the checker talking — printed under their own label so dropping them
        # from the count does not also drop them from view.
        advisory = f"  advisory={f['advisoryCount']}" if f.get("advisoryCount") else ""
        print(f"feature  {f['gradlePath']}  {f['verdict']}  location={f['location']}  "
              f"entry={entry}  findings={rules}{advisory}")
    for s in report["shared"]:
        print(f"shared  {s['gradlePath']}  -> {s['proposedTarget']}  "
              f"consumers={len(s['consumers'])}  hoistable={str(s['hoistable']).lower()}")
    for s in report["inFeatureShared"]:
        print(f"infeature  {s['consumer']} uses {s['owner']}  packages={','.join(s['packages'])}  "
              f"-> {s['proposedTarget']}")
    for r in report["refusals"]:
        print(f"refusal  {r['subject']}  {r['kind']}  {r['reason']}")
    for n in report["notes"]:
        print(f"note  {n['id']}  {n['subject']}  {n['message']}")
    if report["graph"]["order"]:
        print("order  " + "  ".join(report["graph"]["order"]))


def print_grouped(report: dict, color: Palette) -> None:
    width = min(shutil.get_terminal_size((100, 24)).columns, 100)

    def wrap(text: str, indent: str) -> None:
        for line in textwrap.wrap(text, width=max(width - len(indent), 40)):
            print(f"{indent}{line}")

    p, s = report["project"], report["summary"]
    print(f"\n{color.bold}{p['rootProjectName']}{color.off} {color.dim}— {p['role']} · "
          f"{p['packagePrefix'] or 'package prefix unknown'} · app module {p['appModule']} · "
          f"{p['moduleCount']} modules{color.off}")
    if not p["migrationTarget"]:
        print(f"{color.warning}not a migration target{color.off} {color.dim}— see NOTES{color.off}")
    catalogs = ", ".join(p["catalogs"]) or "none"
    print(f"{color.dim}catalogs: {catalogs}"
          + (f" · manifest names {p['catalogAccessor']}" if p["catalogAccessor"] else "")
          + f"{color.off}")

    print(f"\n{color.bold}FEATURES ({len(report['features'])}){color.off}")
    if not report["features"]:
        print(f"  {color.dim}none found{color.off}")
    for f in report["features"]:
        tint = {
            "android-locked": color.error,
            "no-entry-point": color.error,
            "portable": color.warning,
            "owned": color.warning,
        }.get(f["verdict"], "")
        entry = (
            f"{Path(f['entryPoint']['file']).name}:{f['entryPoint']['line']}"
            if f["entryPoint"]
            else "no entry point"
        )
        # 16 wide: `android-locked` and `no-entry-point` are 14 chars, so a 14-wide
        # column runs the verdict straight into the feature name.
        print(f"  {tint}{f['verdict']:<16}{color.off}{color.bold}{f['name']:<14}{color.off}"
              f"{color.dim}{f['dir']}  {entry}{color.off}")
        bits = []
        if f["findings"]:
            bits.append(" ".join(f"{r}×{n}" for r, n in sorted(f["findings"].items())))
        if f["location"] == "root":
            bits.append("outside feature/")
        if "desktop" not in f["targets"] and "jvm" not in f["targets"]:
            bits.append("no desktop target")
        if f["consumes"]:
            bits.append("consumes " + " ".join(f["consumes"]))
        if bits:
            wrap(" · ".join(bits), "                 ")

    print(f"\n{color.bold}SHARED CODE ({len(report['shared'])}){color.off}")
    if not report["shared"]:
        print(f"  {color.dim}none found{color.off}")
    for sh in report["shared"]:
        mark = "" if sh["hoistable"] else color.error + "UNHOISTABLE " + color.off
        print(f"  {color.bold}{sh['gradlePath']:<20}{color.off}→ {mark}{sh['proposedTarget']}"
              f"{color.dim}   {len(sh['consumers'])} consumer(s){color.off}")
        wrap(f"proposal: {sh['reason']}", "    ")
        if sh["proposedTier"] == "split":
            for tier, files in sorted(sh["filesByTier"].items()):
                wrap(f"{tier}: {', '.join(Path(f).name for f in files)}", "      ")

    if report["inFeatureShared"]:
        print(f"\n{color.bold}SHARED CODE INSIDE A FEATURE ({len(report['inFeatureShared'])}){color.off}")
        for sh in report["inFeatureShared"]:
            print(f"  {color.bold}{sh['consumer']}{color.off} uses {color.bold}{sh['owner']}"
                  f"{color.off} → {sh['proposedTarget']}")
            wrap(", ".join(sh["symbols"][:6]), "    ")

    order = report["graph"]["order"]
    print(f"\n{color.bold}MIGRATION ORDER{color.off}")
    if order:
        wrap("  ".join(f"{i}. {node}" for i, node in enumerate(order, 1)), "  ")
    else:
        print(f"  {color.dim}nothing to order{color.off}")

    print(f"\n{color.bold}REFUSALS ({len(report['refusals'])}){color.off}")
    if not report["refusals"]:
        print(f"  {color.dim}none — every feature and shared package can be migrated{color.off}")
    for r in report["refusals"]:
        print(f"  {color.error}{r['kind']:<8}{color.off}{color.bold}{r['subject']}{color.off}")
        wrap(r["reason"], "    ")
        for line in r["evidence"][:4]:
            print(f"    {color.dim}{line}{color.off}")

    if report["notes"]:
        # Grouped by id: the same note on five modules is one fact about the project,
        # and five copies of it buries the four other notes. The JSON keeps them
        # per-subject for machine consumers.
        grouped: dict[str, dict] = {}
        for n in report["notes"]:
            entry = grouped.setdefault(n["id"], {"subjects": [], "message": n["message"]})
            entry["subjects"].append(n["subject"])
        print(f"\n{color.bold}NOTES ({len(grouped)}){color.off}")
        for note_id, entry in grouped.items():
            subjects = ", ".join(entry["subjects"])
            print(f"  {color.warning}{note_id}{color.off} {color.dim}{subjects}{color.off}")
            wrap(entry["message"], "    ")

    print(f"\n{color.dim}{'─' * width}{color.off}")
    print(f"{s['features']} feature(s) · {s['migratable']} to migrate · {s['conforming']} already "
          f"conforming"
          + (f" · {s['owned']} KMPilot-owned with findings" if s["owned"] else "")
          + f" · {s['refused']} refused · {s['sharedPackages']} shared package(s) "
          f"({s['hoistable']} hoistable) · {s['findings']} rule finding(s) · {s['notes']} note(s)")
    print(f"{color.bold}DISCOVERY ONLY — nothing was written{color.off}")


# ─── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kmpilot_discover.py",
        description="Read-only inventory of a KMP project, for migration planning.",
    )
    parser.add_argument(
        "--root", default=None, help="repo root to inventory (default: cwd, else this repo)"
    )
    parser.add_argument("--json-only", action="store_true", help="print the JSON report to stdout")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="one greppable line per row (the default when output is piped)",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="write the JSON report here. Omitted by default on purpose: discovery "
        "writes nothing unless asked.",
    )
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root).expanduser().resolve()
    elif (Path.cwd() / SETTINGS_GRADLE).is_file():
        root = Path.cwd()
    else:
        root = REPO_ROOT
    if not (root / SETTINGS_GRADLE).is_file():
        print(f"error: no settings.gradle.kts at {root}", file=sys.stderr)
        return 2

    report = discover(root)

    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = report_path.with_suffix(f".{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, report_path)

    if args.json_only:
        print(json.dumps(report, indent=2))
    else:
        tty = sys.stdout.isatty()
        if args.compact or not tty:
            print_compact(report)
        else:
            print_grouped(report, Palette(os.environ.get("NO_COLOR") is None))
        if args.report:
            print(f"report: {args.report}")

    # Discovery never fails a build — a refusal is a finding, not an error. Only an
    # unusable target repo (no settings.gradle.kts, above) is a non-zero exit.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
