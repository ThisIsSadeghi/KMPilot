#!/usr/bin/env python3
"""
kmpilot_check.py — deterministic architecture checker for KMPilot feature modules.

Mechanizes the greppable half of the 14 architecture rules (see
`.claude/skills/_shared/patterns.md`). Everything here used to live as prose grep
instructions inside `.claude/agents/code-quality/code-reviewer.md`, executed by a
model. This script makes those checks reproducible, CI-runnable, and available to
anyone who is not running Claude Code.

    python3 .claude/skills/_shared/kmpilot_check.py --all
    python3 .claude/skills/_shared/kmpilot_check.py dashboard send
    python3 .claude/skills/_shared/kmpilot_check.py --all --json-only
    python3 .claude/skills/_shared/kmpilot_check.py --all --baseline

Writes `.claude/docs/_project/check-report.json`. Exits 1 if any `error`-severity
violation exists; warnings never fail the build.

One rule set, three strictness tiers — leniency is scoped to code that never agreed
to the rules, never to code KMPilot wrote.

  strict      A KMPilot project, this repo included, is held to every rule as an
              `error`. The reference implementation has no standing to violate what
              it enforces.
  --baseline  Identical checks, every error reported as a warning, exit code always
              0. Answers "how far is this repo from the rules" for a codebase that
              has not migrated yet, without pretending the violations are not there.
  per-feature In an ADOPTED project, `.kmpilot.json` lists `managedFeatures` — the
              features KMPilot generated. Anything else predates the pipeline and is
              graded like `--baseline`: reported in full, never failing the build.
              Otherwise the first `archTest` after adoption fails on working, shipped
              code over rules its author never chose. Features KMPilot generates stay
              strict, and the summary names every unenforced feature, so a generated
              feature missing from the list is visible rather than silently exempt.

Deliberately NOT mechanized — these stay judgment calls in `code-reviewer.md`:

    Rule 1  Interface + Impl semantics (incl. the shared `data.app` datasource
            exception, where an absent per-feature `datasource/` is correct).
    Rule 2  Which operations are genuinely fallible and so need `Either<T>`.
    Rule 4  Whether all four UI states are *genuinely* handled (a `when` branch
            that exists but renders nothing passes any grep).
    Rule 10 Callbacks vs `navController` — partly greppable, but a legitimate
            `navController` pass-through needs reading the nav graph.
    Rule 14 Only applies when the spec's Platform Profile is `platform-capability`
            / `native-view` / `mixed`; requires reading the spec.

Rationale for the mechanized checks, carried over from `code-reviewer.md` so it
travels with the code:

    R3   `_uiModel.value = …` bypasses `setState`, which is the only path that
         guarantees a single atomic state transition.
    R5   Material3 *components* are forbidden; the theme accessors
         (`MaterialTheme`, `Shapes`, `darkColorScheme`, `lightColorScheme`) are
         allowed because `XTheme` wraps MaterialTheme.
    R7   Package segments must be lowercase — hyphens/underscores/camelCase in a
         package break the generated-resources package and Android's aapt.
    R8   A feature exposes exactly one top-level `val {featurename}Module`; leaf
         modules stay internal. `singleOf(::Impl).bind<Interface>()` is the
         idiomatic Koin binding for the interface+impl pair.
    R9   No UseCase layer — ViewModels invoke repositories directly.
    R11  One `*UiModel` per feature, `UiState<DTO>` slots inside it, and no
         `data/` → `presentation/` import. A `*UiState.kt` file is the classic
         pre-Rule-11 leftover.
    R12  Every user-facing string comes from a resource. The allowlist exists
         because four categories legitimately stay literals: `@Preview` fixtures,
         control sentinels compared in logic, single-glyph symbols, and
         repository-supplied data (which is never a literal, so it never matches).
    R13  Exactly one Scaffold, in the app shell. A feature-level Scaffold nests a
         second one and double-applies safe-area insets. Features touch no insets
         except the bottom nav-bar inset on their own bottom bar / scroll list,
         and for a sticky bottom action bar the `exclude(ime)` form is required so
         the pad collapses when the shell's `imePadding()` lifts the screen.
    S1   `{Feature}Screen.kt` has a fixed 3-name allowlist. Loading/Failed route
         to the shared `AppLoadingState`/`AppErrorState`, so a private
         `LoadingContent`/`FailedContent` is itself a violation. Enforced per
         `*Screen.kt` file, so a `kind: screen` secondary screen carries its own
         independent allowlist.
    S2   `components/` holds composables only; formatters/validators/mappers go in
         `presentation/ui/{Feature}Utils.kt`.
    S3   Generic core code must never import its module's `.app` tier — that tier
         is stripped/neutralized by `install.sh`, so such an import breaks the
         build downstream. `core/data/**/DataModules.kt` is the one sanctioned
         exception (it references `appDataModule`, the strip seam).
    S4   `androidx.compose.ui.tooling.preview.Preview` (CMP 1.11.0+), not the
         deprecated `org.jetbrains.compose…` one.
    S7   The other half of Rule 13, and the only repo-scoped one: `XScreen` adds no
         insets, so the app shell has to. Fires only when the app module has
         **neither** a `Scaffold`/`XScaffold` **nor** any window-insets call — it
         never grades which mechanism, because three different shells conform and
         one of them relies on `Scaffold`'s own default.
    I1-4 The four integration points every feature needs.

Regex-based Kotlin parsing is approximate by design. Comments and string literals
are blanked before structural matching, and `error`-severity checks are kept to
patterns that cannot reasonably false-positive; approximation is tolerated only
for `warning`-severity checks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ─── Layout ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = Path(".claude/docs/_project/check-report.json")
SETTINGS_GRADLE = Path("settings.gradle.kts")
MANIFEST = Path(".kmpilot.json")
DEFAULT_APP_MODULE = "composeApp"


def resolve_app_module(root: Path) -> str:
    """The module holding `initKoin` and the NavHost — `composeApp` in a template
    project, anything at all in an adopted one (`install.sh --adopt` records the
    real name in `.kmpilot.json`). Integration points I2/I3/I4 are checked against
    this module, so hardcoding `composeApp` would fail every adopted repo."""
    manifest = root / MANIFEST
    if manifest.is_file():
        m = re.search(r'"appModule"\s*:\s*"([^"]+)"', read(manifest))
        if m and (root / m.group(1)).is_dir():
            return m.group(1)
    if (root / DEFAULT_APP_MODULE).is_dir():
        return DEFAULT_APP_MODULE
    # No manifest and no composeApp: fall back to the module that starts Koin.
    for candidate in sorted(root.glob("*/src/*/kotlin/**/initKoin.kt")):
        return candidate.relative_to(root).parts[0]
    return DEFAULT_APP_MODULE

# Source sets that are production code. Anything with "test" in the source-set
# name is skipped — test fixtures legitimately hold literals, Scaffolds, etc.
TEST_SOURCESET = re.compile(r"test", re.IGNORECASE)

# ─── Rule data ───────────────────────────────────────────────────────────────

# Material3 component imports (Rule 5). Theme accessors are deliberately absent.
FORBIDDEN_M3 = [
    "Button", "Text", "Card", "Scaffold", "TextField", "OutlinedTextField",
    "Icon", "IconButton", "CircularProgressIndicator", "LinearProgressIndicator",
    "RadioButton", "Checkbox", "Switch", "Surface", "TopAppBar", "BottomAppBar",
    "NavigationBar", "FloatingActionButton", "SnackbarHost", "ModalBottomSheet",
    "AlertDialog", "Divider",
]
ALLOWED_M3 = {"MaterialTheme", "Shapes", "darkColorScheme", "lightColorScheme"}

APP_TIERS = ("designsystem", "data", "common")

# S7 — the two mechanisms an app shell can provide the safe area with. Either one is
# enough; the check fires only when BOTH are absent from the whole app module.
#
# `\w*Scaffold` so the design system's own `XScaffold` counts — `\bScaffold` cannot
# match it (no word boundary between two word characters), the same near-miss that
# made `XNavHost` invisible to I4 in step 9 finding 6.
#
# `[({]` because `Scaffold { … }` — the trailing-lambda-only form, which is what you
# write when the content lambda is the only argument — has no parentheses at all. This
# is finding 4 again (`androidTarget { }` was invisible for exactly this reason), and it
# is the more common shape in a shell that has not been wired for insets yet.
SHELL_SCAFFOLD = re.compile(r"\w*Scaffold\s*[({]")
# `imePadding` is deliberately absent: lifting content for the keyboard says nothing
# about the status bar, which is the inset whose absence breaks the top edge.
SHELL_INSETS = re.compile(
    r"\b(windowInsetsPadding|safeDrawingPadding|safeContentPadding|systemBarsPadding"
    r"|statusBarsPadding|navigationBarsPadding|displayCutoutPadding)\s*\("
    r"|\bcontentWindowInsets\s*="
    r"|\bWindowInsets\.(safeDrawing|safeContent|systemBars|statusBars|displayCutout)"
)
# The shell composable, used only to point the finding at a file rather than a module.
SHELL_COMPOSABLE = re.compile(r"@Composable[\s\S]{0,120}?\bfun\s+App\w*\s*\(")

# Names that mark a top-level declaration as a preview/test fixture, so string
# literals inside it are allowed under the Rule 12 allowlist.
FIXTURE_NAME = re.compile(r"(sample|preview|fixture|fake|stub|dummy)", re.IGNORECASE)

# Rule 12 detection: named UI arguments and the positional first argument of the
# two X-components that take text positionally.
R12_NAMED = re.compile(
    r"\b(text|label|placeholder|contentDescription)\s*=\s*\"((?:[^\"\\]|\\.)*)\""
)
R12_POSITIONAL = re.compile(r"\bX(?:Text|Button)\s*\(\s*\"((?:[^\"\\]|\\.)*)\"")
SENTINEL = re.compile(r"(?:[=!]=\s*\"((?:[^\"\\]|\\.)*)\"|\"((?:[^\"\\]|\\.)*)\"\s*[=!]=)")
# `$foo` / `${foo.bar()}` inside a Kotlin template is repository-supplied data, not
# display copy — stripped before the "does this literal contain words" test.
INTERPOLATION = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_]\w*")

# The Compose animation APIs take a `label = "…"` debug tag. It is never user-facing,
# so `label` arguments inside one of these calls are exempt from Rule 12.
ANIMATION_API = re.compile(
    r"\b(animate\w*AsState|animate(?:Float|Color|Dp|Value|Int|Offset|Rect|Size)\b"
    r"|updateTransition|rememberTransition|rememberInfiniteTransition"
    r"|createChildTransition|AnimatedContent|AnimatedVisibility|Crossfade)\s*\("
)

# A plain `navigationBarsPadding()` is correct on a full-bleed scroll list and wrong
# on a sticky bottom bar. These markers identify the bottom-bar case.
BOTTOM_BAR_MARKER = re.compile(r"\bbottomBar\b|Bottom(?:Bar|Cta)|StickyBar|ActionBar")

R13_INSETS = re.compile(
    r"\b(contentWindowInsets|consumeWindowInsets|safeDrawing|statusBarsPadding|imePadding)\b"
)

DECL = re.compile(
    r"^\s*(?:(?:public|private|internal|protected|expect|actual|abstract|open|sealed|data|value|inline|suspend|override)\s+)*"
    r"(fun|class|object|interface|val|var)\s+([A-Za-z_]\w*)"
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def blank_noncode(text: str) -> str:
    """Replace comment bodies and string-literal contents with spaces, preserving
    length and newlines, so brace depth and declaration matching are not confused
    by braces or keywords inside strings/comments."""
    out: list[str] = []
    i, n = 0, len(text)
    state: str | None = None
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state is None:
            if c == "/" and nxt == "/":
                state, i = "line", i + 2
                out.append("  ")
            elif c == "/" and nxt == "*":
                state, i = "block", i + 2
                out.append("  ")
            elif text.startswith('"""', i):
                state, i = "raw", i + 3
                out.append("   ")
            elif c == '"':
                state, i = "str", i + 1
                out.append(" ")
            elif c == "'":
                state, i = "char", i + 1
                out.append(" ")
            else:
                out.append(c)
                i += 1
            continue
        if state == "line":
            if c == "\n":
                state = None
                out.append("\n")
            else:
                out.append(" ")
            i += 1
        elif state == "block":
            if c == "*" and nxt == "/":
                state, i = None, i + 2
                out.append("  ")
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
        elif state == "raw":
            if text.startswith('"""', i):
                state, i = None, i + 3
                out.append("   ")
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
        elif state == "str":
            if c == "\\":
                out.append("  ")
                i += 2
            elif c == '"':
                state, i = None, i + 1
                out.append(" ")
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
        else:  # char
            if c == "\\":
                out.append("  ")
                i += 2
            elif c == "'":
                state, i = None, i + 1
                out.append(" ")
            else:
                out.append(" ")
                i += 1
    return "".join(out)


class Source:
    """One Kotlin file, with a code-only view for structural matching."""

    def __init__(self, path: Path, rel: str):
        self.path = path
        self.rel = rel
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.code = blank_noncode(self.text)
        self.lines = self.text.splitlines()
        self.code_lines = self.code.splitlines()
        self._decls: list[dict] | None = None
        self._fixture_ranges: list[tuple[int, int]] | None = None
        self._anim_ranges: list[tuple[int, int]] | None = None

    # -- structure -----------------------------------------------------------

    def _line_depths(self) -> list[int]:
        """Brace depth at the START of each line (0 == file scope)."""
        depths, depth = [], 0
        for line in self.code_lines:
            depths.append(depth)
            depth += line.count("{") - line.count("}")
        return depths

    @property
    def declarations(self) -> list[dict]:
        """Top-level (file-scope) declarations with their annotations."""
        if self._decls is not None:
            return self._decls
        depths = self._line_depths()
        decls: list[dict] = []
        pending: list[str] = []
        pending_start: int | None = None
        for idx, code_line in enumerate(self.code_lines):
            stripped = code_line.strip()
            if depths[idx] != 0:
                continue
            if stripped.startswith("@"):
                if pending_start is None:
                    pending_start = idx
                pending.append(self.lines[idx].strip())
                continue
            if not stripped:
                continue
            m = DECL.match(code_line)
            if m:
                decls.append(
                    {
                        "kind": m.group(1),
                        "name": m.group(2),
                        "line": idx + 1,
                        "annotations": " ".join(pending),
                        "start": (pending_start if pending_start is not None else idx) + 1,
                        "end": self._decl_end(idx) + 1,
                    }
                )
            pending, pending_start = [], None
        self._decls = decls
        return decls

    def _decl_end(self, start_idx: int) -> int:
        """Last line index of the declaration body starting at start_idx."""
        depth = 0
        seen_brace = False
        for idx in range(start_idx, len(self.code_lines)):
            line = self.code_lines[idx]
            if "{" in line:
                seen_brace = True
            depth += line.count("{") - line.count("}")
            if seen_brace and depth <= 0:
                return idx
            if not seen_brace and idx > start_idx and self.code_lines[idx].strip() == "":
                return idx - 1  # expression body / property, no block
        return len(self.code_lines) - 1

    @property
    def fixture_ranges(self) -> list[tuple[int, int]]:
        """1-based line ranges whose string literals are exempt from Rule 12:
        `@Preview` functions, `PreviewParameterProvider`s, and top-level sample /
        fixture declarations."""
        if self._fixture_ranges is not None:
            return self._fixture_ranges
        ranges: list[tuple[int, int]] = []
        for d in self.declarations:
            annotated = "@Preview" in d["annotations"]
            named = bool(FIXTURE_NAME.search(d["name"]))
            provider = d["kind"] in ("class", "object") and "PreviewParameterProvider" in "\n".join(
                self.lines[d["line"] - 1 : d["end"]]
            )
            if annotated or named or provider:
                ranges.append((d["start"], d["end"]))
        self._fixture_ranges = ranges
        return ranges

    def in_fixture(self, line: int) -> bool:
        return any(lo <= line <= hi for lo, hi in self.fixture_ranges)

    def call_ranges(self, pattern: re.Pattern) -> list[tuple[int, int]]:
        """1-based line ranges spanning the balanced argument list of every call
        matching `pattern` (which must end at the opening paren)."""
        code, ranges = self.code, []
        for m in pattern.finditer(code):
            start = m.end() - 1
            depth, i = 0, start
            while i < len(code):
                if code[i] == "(":
                    depth += 1
                elif code[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            ranges.append((code.count("\n", 0, start) + 1, code.count("\n", 0, i) + 1))
        return ranges

    @property
    def animation_ranges(self) -> list[tuple[int, int]]:
        if self._anim_ranges is None:
            self._anim_ranges = self.call_ranges(ANIMATION_API)
        return self._anim_ranges

    def in_animation_call(self, line: int) -> bool:
        return any(lo <= line <= hi for lo, hi in self.animation_ranges)

    # -- scanning ------------------------------------------------------------

    def scan(self, pattern: re.Pattern, code_only: bool = True):
        """Yield (line_no, match) for every match, line by line."""
        haystack = self.code_lines if code_only else self.lines
        for idx, line in enumerate(haystack):
            for m in pattern.finditer(line):
                yield idx + 1, m


def violation(feature, rule, severity, file, line, message, advisory=False) -> dict:
    row = {
        "feature": feature,
        "rule": rule,
        "severity": severity,
        "file": file,
        "line": line,
        "message": message,
    }
    if advisory:
        # Reported for visibility, but not work: a finding with no fix available in this
        # repo, because the thing it describes is a legitimate choice rather than a
        # defect. Consumers that decide "is this finished" — the migration's
        # `verify_step`, its promotion, its rewrite passes — must skip these, or they
        # hold a feature at a bar nothing can clear. Advisory rows are warnings by
        # construction, so the build never failed on one; the key is omitted entirely
        # when false, keeping the row shape unchanged for every other finding.
        row["advisory"] = True
    return row


def actionable(violations: list[dict]) -> list[dict]:
    """The findings that represent work. The one place the advisory rule is spelled."""
    return [v for v in violations if not v.get("advisory")]


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ─── Discovery ───────────────────────────────────────────────────────────────


def resolve_pkg_prefix(root: Path) -> str:
    """Everything before the `common` segment of a `package` declaration in
    :core:common — the same resolution `create-feature/phases/phase-0-context.md`
    uses. Cutting at `common` rather than stripping one segment matters because
    :core:common legitimately holds sub-packages (`…common.di`, `…common.util`):
    stripping the last segment off `norm.common.di` yields `norm.common`, and every
    prefix-dependent check then silently matches nothing. Files vote, so one odd
    package cannot skew the answer."""
    from collections import Counter

    votes: Counter[str] = Counter()
    for path in (root / "core/common/src").rglob("*.kt"):
        if "/build/" in path.as_posix():
            continue
        m = re.search(r"^package\s+([\w.]+)", read(path), re.MULTILINE)
        if not m:
            continue
        segments = m.group(1).split(".")
        if "common" in segments[1:]:
            votes[".".join(segments[: segments.index("common", 1)])] += 1
        elif len(segments) > 1:
            votes[".".join(segments[:-1])] += 1
    return votes.most_common(1)[0][0] if votes else ""


def discover_features(root: Path) -> list[str]:
    feature_dir = root / "feature"
    if not feature_dir.is_dir():
        return []
    return sorted(
        p.parent.name
        for p in feature_dir.glob("*/build.gradle.kts")
        if p.parent.name != "build"
    )


def collect_sources(root: Path, module_dir: Path) -> list[Source]:
    """All production Kotlin files of a module (test source sets and Gradle
    build output excluded)."""
    src = module_dir / "src"
    if not src.is_dir():
        return []
    sources: list[Source] = []
    for path in sorted(src.rglob("*.kt")):
        parts = path.relative_to(src).parts
        if "build" in parts:
            continue
        if parts and TEST_SOURCESET.search(parts[0]):
            continue
        sources.append(Source(path, path.relative_to(root).as_posix()))
    return sources


# ─── Feature-scoped checks ───────────────────────────────────────────────────


def under(src: Source, *segments: str) -> bool:
    return any(f"/{seg}/" in src.rel for seg in segments)


def check_r3(feature, sources, ctx):
    """R3 — setState: never assign `_uiModel.value` / `_uiState.value` directly."""
    pattern = re.compile(r"_(uiModel|uiState)\s*\.\s*value\s*=(?!=)")
    out = []
    for src in sources:
        if not under(src, "presentation"):
            continue
        for line, m in src.scan(pattern):
            out.append(
                violation(
                    feature, "R3", "error", src.rel, line,
                    f"direct state assignment `_{m.group(1)}.value =` — use "
                    "`_uiModel.setState { copy(...) }`",
                )
            )
    return out


def check_r5(feature, sources, ctx):
    """R5 — X-components: no Material3 component imports."""
    forbidden = re.compile(
        r"^import\s+androidx\.compose\.material3\.(\w+|\*)\s*$"
    )
    names = set(FORBIDDEN_M3)
    out = []
    for src in sources:
        for line, m in src.scan(forbidden):
            name = m.group(1)
            if name == "*":
                out.append(
                    violation(
                        feature, "R5", "error", src.rel, line,
                        "wildcard Material3 import pulls in forbidden components — "
                        "import the specific X-component instead",
                    )
                )
            elif name in names:
                out.append(
                    violation(
                        feature, "R5", "error", src.rel, line,
                        f"Material3 component import `material3.{name}` — use the "
                        f"X-component (`X{name}`) from :core:designsystem",
                    )
                )
            # Anything else (`ButtonDefaults`, `SliderDefaults`, `LocalContentColor`,
            # `ExperimentalMaterial3Api`, the four theme accessors in ALLOWED_M3) is
            # not a component and is not flagged — the forbidden list is the contract.
    return out


def check_r7(feature, sources, ctx):
    """R7 — lowercase package segments."""
    pattern = re.compile(r"^package\s+([\w.\-]+)")
    out = []
    for src in sources:
        for line, m in src.scan(pattern):
            bad = [s for s in m.group(1).split(".") if not re.fullmatch(r"[a-z][a-z0-9]*", s)]
            if bad:
                out.append(
                    violation(
                        feature, "R7", "error", src.rel, line,
                        f"package segment(s) {', '.join(repr(b) for b in bad)} are not "
                        "lowercase-only (no hyphens, underscores or camelCase)",
                    )
                )
    return out


def check_r8(feature, sources, ctx):
    """R8 — DI: a public top-level `val {featurename}Module`, interfaces bound."""
    di = [s for s in sources if under(s, "di")]
    out = []
    if not di:
        return [
            violation(
                feature, "R8", "error", f"feature/{feature}", 0,
                f"no `di/` package — a feature must expose `val {feature}Module`",
            )
        ]
    module_decl = re.compile(rf"^\s*(?:internal\s+|public\s+)?val\s+{re.escape(feature)}Module\b")
    binding = re.compile(r"(singleOf|factoryOf|single|factory)\s*[({][^\n]*")
    bind_call = re.compile(r"\.bind<")
    has_module = any(any(src.scan(module_decl)) for src in di)
    has_bind = any(any(src.scan(bind_call)) for src in di)
    has_provider = any(any(src.scan(binding)) for src in di)
    anchor = di[0].rel
    if not has_module:
        out.append(
            violation(
                feature, "R8", "error", anchor, 1,
                f"no top-level `val {feature}Module` — the feature's DI aggregate must "
                f"be named `{feature}Module` and listed in initKoin's `modules(...)`",
            )
        )
    if has_provider and not has_bind:
        out.append(
            violation(
                feature, "R8", "error", anchor, 1,
                "no `.bind<Interface>()` in the DI module — interface+impl pairs must "
                "be bound (`singleOf(::Impl).bind<Interface>()`)",
            )
        )
    return out


def check_r9(feature, sources, ctx):
    """R9 — no UseCase layer."""
    pattern = re.compile(r"\b(class|interface|object)\s+(\w*UseCase)\b")
    out = []
    for src in sources:
        for line, m in src.scan(pattern):
            out.append(
                violation(
                    feature, "R9", "error", src.rel, line,
                    f"`{m.group(2)}` — no UseCase layer; ViewModels invoke repositories "
                    "directly",
                )
            )
    return out


def check_r11a(feature, sources, ctx):
    """R11a — no `*UiState.kt` under presentation/."""
    return [
        violation(
            feature, "R11a", "error", src.rel, 1,
            f"`{Path(src.rel).name}` — `*UiModel` is the only presentation state "
            "container; fold these into `UiState<DTO>` slots on the UiModel",
        )
        for src in sources
        if under(src, "presentation") and src.path.name.endswith("UiState.kt")
    ]


def check_r11b(feature, sources, ctx):
    """R11b — exactly one `presentation/*UiModel.kt`."""
    models = [
        s
        for s in sources
        if s.path.name.endswith("UiModel.kt") and s.path.parent.name == "presentation"
    ]
    if len(models) == 1:
        return []
    if not models:
        return [
            violation(
                feature, "R11b", "error", f"feature/{feature}", 0,
                "no `presentation/*UiModel.kt` — every feature has exactly one UiModel",
            )
        ]
    return [
        violation(
            feature, "R11b", "error", m.rel, 1,
            f"{len(models)} UiModel files in `presentation/` "
            f"({', '.join(sorted(Path(x.rel).name for x in models))}) — expected exactly 1",
        )
        for m in models
    ]


def check_r11c(feature, sources, ctx):
    """R11c — no data → presentation import."""
    pattern = re.compile(r"^import\s+[\w.]*\.presentation\.")
    return [
        violation(
            feature, "R11c", "error", src.rel, line,
            "data layer imports from `presentation` — the data layer never depends on "
            "presentation (Rule 11)",
        )
        for src in sources
        if under(src, "data")
        for line, _ in src.scan(pattern)
    ]


def check_r12(feature, sources, ctx):
    """R12 — no hardcoded user-facing strings in presentation/ui."""
    ui = [s for s in sources if under(s, "ui")]
    sentinels = set()
    for src in sources:
        for _, m in src.scan(SENTINEL, code_only=False):
            sentinels.add(m.group(1) if m.group(1) is not None else m.group(2))
    out = []
    for src in ui:
        for pattern, group in ((R12_NAMED, 2), (R12_POSITIONAL, 1)):
            for line, m in src.scan(pattern, code_only=False):
                value = m.group(group)
                arg = m.group(1) if pattern is R12_NAMED else None
                if not value or len(value) <= 1:
                    continue  # single-glyph symbol ($, ₿, %, ✓) or empty
                if value in sentinels:
                    continue  # control sentinel parsed in logic
                if not re.search(r"[A-Za-z]{2}", INTERPOLATION.sub("", value)):
                    continue  # interpolated data + glyphs/digits only — not display copy
                if arg == "label" and src.in_animation_call(line):
                    continue  # Compose animation debug label, never rendered
                if src.in_fixture(line):
                    continue  # @Preview fixture
                out.append(
                    violation(
                        feature, "R12", "error", src.rel, line,
                        f'hardcoded user-facing string "{value}" — resolve it from '
                        "`stringResource(Res.string.*)` / `DesignSystemResources` / `UiText`",
                    )
                )
    return out


def check_r12res(feature, sources, ctx):
    """R12 (resources) — a feature that renders text ships a strings.xml."""
    renders = any(
        under(s, "ui") and re.search(r"\bX?Text\s*\(|stringResource\s*\(", s.code)
        for s in sources
    )
    if not renders:
        return []
    strings = ctx["root"] / f"feature/{feature}/src/commonMain/composeResources/values/strings.xml"
    if strings.is_file():
        return []
    return [
        violation(
            feature, "R12", "error",
            f"feature/{feature}/src/commonMain/composeResources/values/strings.xml", 0,
            "feature renders text but has no `composeResources/values/strings.xml`",
        )
    ]


def check_s5(feature, sources, ctx):
    """S5 — a module that declares `@Serializable` applies the serialization plugin.

    The annotation is only an annotation: without the compiler plugin on THAT module
    no serializer is generated, and nothing says so. The module compiles, `archTest`
    passes, every target links — and the app dies the moment the type is serialized.
    It is the crash a migration is most likely to introduce, because Integration
    Point 4 hands each feature a type-safe `@Serializable` nav route it did not have
    before, while the plugin list is inherited from whatever the module was before
    the rewrite. Found on a real repo run: a migrated feature launched straight into
    `SerializationException: Serializer for class 'SearchRoute' is not found`.

    Scoped to the `plugins { }` block on purpose — `implementation(libs.kotlinx.
    serialization.core)` is the runtime library, not the compiler plugin, and reading
    it as one would report the crash as fixed.
    """
    declares = [
        (src, line)
        for src in sources
        for line, _m in src.scan(re.compile(r"@Serializable\b"))
    ]
    if not declares:
        return []
    gradle = read(ctx["root"] / f"feature/{feature}/build.gradle.kts")
    block = re.search(r"\bplugins\s*\{(.*?)\}", gradle, re.DOTALL)
    if block and re.search(r"serialization", block.group(1), re.IGNORECASE):
        return []
    src, line = declares[0]
    return [
        violation(
            feature, "S5", "error", f"feature/{feature}/build.gradle.kts", 0,
            f"`@Serializable` at {src.rel}:{line} but the module's `plugins {{ }}` block "
            "does not apply the kotlinx.serialization plugin — no serializer is generated "
            "and the app throws SerializationException at runtime",
        )
    ]


def check_r13(feature, sources, ctx):
    """R13 — single app-shell Scaffold; features own no insets but their own
    bottom nav-bar inset."""
    scaffold = re.compile(r"\b(X?Scaffold)\b")
    navbar_pad = re.compile(r"\bnavigationBarsPadding\s*\(\s*\)")
    out = []
    for src in sources:
        if not under(src, "ui"):
            continue
        for line, m in src.scan(scaffold):
            out.append(
                violation(
                    feature, "R13", "error", src.rel, line,
                    f"`{m.group(1)}` in a feature screen — the one Scaffold lives in the "
                    "app shell; use `XScreen(topBar = …, bottomBar = …)`",
                )
            )
        for line, m in src.scan(R13_INSETS):
            out.append(
                violation(
                    feature, "R13", "error", src.rel, line,
                    f"`{m.group(1)}` in a feature screen — the app shell owns the "
                    "status/cutout/ime frame",
                )
            )
        # Plain `navigationBarsPadding()` is *correct* on a full-bleed scroll list;
        # it is wrong only on a sticky bottom action bar. Only flag files that look
        # like the bottom-bar case (approximation, hence warning-only).
        if not BOTTOM_BAR_MARKER.search(src.code) and not BOTTOM_BAR_MARKER.search(src.path.name):
            continue
        for line, _ in src.scan(navbar_pad):
            out.append(
                violation(
                    feature, "R13", "warning", src.rel, line,
                    "plain `navigationBarsPadding()` on a sticky bottom bar — prefer "
                    "`windowInsetsPadding(WindowInsets.navigationBars.exclude(WindowInsets.ime))` "
                    "so the pad collapses when the keyboard lifts the screen",
                )
            )
    return out


def check_s1(feature, sources, ctx):
    """S1 — `*Screen.kt` allowlist, enforced per file (secondary screens carry
    their own)."""
    out = []
    for src in sources:
        if src.path.parent.name != "ui" or not src.path.name.endswith("Screen.kt"):
            continue
        base = src.path.name[: -len(".kt")]
        allowed = {base, f"{base}Root", "EmptyContent"}
        for d in src.declarations:
            if d["kind"] != "fun" or "@Composable" not in d["annotations"]:
                continue
            if "@Preview" in d["annotations"] or d["name"] in allowed:
                continue
            if d["name"] in ("LoadingContent", "FailedContent"):
                out.append(
                    violation(
                        feature, "S1", "warning", src.rel, d["line"],
                        f"`{d['name']}` — Loading/Failed route to the shared "
                        "`AppLoadingState`/`AppErrorState`, never a private shell",
                    )
                )
            else:
                out.append(
                    violation(
                        feature, "S1", "warning", src.rel, d["line"],
                        f"`{d['name']}` is outside the {base}.kt allowlist "
                        f"({', '.join(sorted(allowed))}) — move it to "
                        "`presentation/ui/components/`, one file per component",
                    )
                )
    return out


def check_s6(feature, sources, ctx):
    """S6 — the screen lives at `presentation/ui/*Screen.kt`.

    Every path-gated rule in this file keys on that layout: R3 and R11a only look
    `under(src, "presentation")`; R12, R13 and S1 only look `under(src, "ui")`. A
    feature whose sources sit flat in the package root therefore has **none** of them
    evaluated — they do not pass, they never run.

    That is harmless for a feature KMPilot generated, which is born in the right shape.
    It is not harmless for a **migrated** one. A project being migrated arrives flat, and
    the migration's completion bar is "zero actionable findings": a feature could satisfy
    R11b (add `presentation/*UiModel.kt`), R8 (add `di/`), R5 and I3, report **0**, and be
    promoted to `managedFeatures` — while its screen still assigns `_uiState.value =`,
    hardcodes every string, and keeps the `*UiState.kt` Rule 11 forbids. The migration
    would sign off a feature it never migrated, in a file the user did not write.

    So this is the rule that makes the other rules reachable. It fires only when the
    module actually has a screen to place, and says where it goes.
    """
    return [
        violation(
            feature, "S6", "error", src.rel, 1,
            f"`{src.path.name}` is not under `presentation/ui/` — move the feature into "
            "the documented layout (`presentation/ui/`, `presentation/ui/components/`, "
            "`data/`, `di/`). Until it is, R3, R12, R13 and S1 cannot be evaluated on it "
            "at all, so a zero-findings result would not mean the feature conforms",
        )
        # Per screen, not "any screen is in the right place": a feature may own a
        # secondary screen (the documented `kind: screen` case), and those live under
        # `presentation/ui/` too. Checking only that *one* is placed correctly would let
        # the second sit anywhere and take R3/R12/R13/S1 out of scope with it.
        for src in sources
        if src.path.name.endswith("Screen.kt") and src.path.parent.name != "ui"
    ]


def check_s2(feature, sources, ctx):
    """S2 — `components/` holds composables only."""
    return [
        violation(
            feature, "S2", "warning", src.rel, 1,
            "no `@Composable` in a file under `components/` — pure helpers belong in "
            f"`presentation/ui/{feature.capitalize()}Utils.kt`",
        )
        for src in sources
        if under(src, "components") and "@Composable" not in src.code
    ]


def check_s4(feature, sources, ctx):
    """S4 — the deprecated Compose preview import."""
    pattern = re.compile(r"^import\s+org\.jetbrains\.compose\.ui\.tooling\.preview\.")
    return [
        violation(
            feature, "S4", "warning", src.rel, line,
            "deprecated preview import — use "
            "`androidx.compose.ui.tooling.preview.Preview` (CMP 1.11.0+)",
        )
        for src in sources
        for line, _ in src.scan(pattern)
    ]


# ─── Integration points ──────────────────────────────────────────────────────


def check_integration(feature, sources, ctx) -> list[dict]:
    root, out = ctx["root"], []

    settings = read(root / SETTINGS_GRADLE)
    if not re.search(rf'include\s*\(\s*"\s*:feature:{re.escape(feature)}\s*"', settings):
        out.append(
            violation(
                feature, "I1", "error", SETTINGS_GRADLE.as_posix(), 0,
                f'missing `include(":feature:{feature}")`',
            )
        )

    app_gradle = ctx["app_gradle"]
    compose_app = read(root / app_gradle)
    if not re.search(rf'project\s*\(\s*"\s*:feature:{re.escape(feature)}\s*"', compose_app):
        out.append(
            violation(
                feature, "I2", "error", app_gradle.as_posix(), 0,
                f'missing `implementation(project(":feature:{feature}"))`',
            )
        )

    init_koin = ctx["init_koin"]
    if init_koin is None:
        out.append(
            violation(feature, "I3", "error", ctx["app_module"], 0, "initKoin.kt not found")
        )
    else:
        block = re.search(r"modules\s*\((.*?)\)\s*\}", read(init_koin), re.DOTALL)
        listed = bool(block) and bool(re.search(rf"\b{re.escape(feature)}Module\b", block.group(1)))
        if block and not listed:
            # One hop of indirection, because adopt's own Koin bootstrap needs it.
            # When a project has no `startKoin`, `install.sh --adopt` writes one that
            # reads `modules(kmpilotModules)`, with the list built in a *sibling* file:
            #
            #     val kmpilotModules: List<Module> = listOf(commonModule, dataModule, …)
            #
            # A feature added to that list is registered — the app starts, Koin resolves
            # it — but a check that only reads the `modules(...)` call site sees nothing
            # and reports it missing. Since it is a single-file textual match, no edit to
            # the feature can ever clear it: the step cannot complete, `--force` follows,
            # and promotion then refuses the forced sign-off. Exactly the unreachable-bar
            # failure finding 1 already cost this phase once, on the scaffold adopt itself
            # writes.
            #
            # One hop only, and only to a `val` defined in the app module: enough for the
            # aggregation shape everyone actually writes, and short of pretending to
            # resolve arbitrary Kotlin.
            app_src = root / ctx["app_module"] / "src"
            for name in re.findall(r"\b([A-Za-z_]\w*)\b", block.group(1)):
                if name == f"{feature}Module":
                    continue
                for kt in app_src.rglob("*.kt"):
                    body = read(kt)
                    decl = re.search(
                        rf"\bval\s+{re.escape(name)}\b[^=]*=(.*?)(?=\n\s*(?:val|fun|class|object)\b|\Z)",
                        body, re.DOTALL,
                    )
                    if decl and re.search(rf"\b{re.escape(feature)}Module\b", decl.group(1)):
                        listed = True
                        break
                if listed:
                    break
        if not listed:
            out.append(
                violation(
                    feature, "I3", "error", init_koin.relative_to(root).as_posix(), 0,
                    f"`{feature}Module` not listed in `startKoin {{ modules(...) }}`",
                )
            )

    nav_host = ctx["nav_host"]
    if nav_host is None:
        # A template project always ships a NavHost, so its absence is a defect.
        # An ADOPTED project may navigate with Voyager, Decompose, or plain state
        # hoisting — no NavHost is a valid architecture there, not a violation, and
        # failing the build over it would punish the host for its own design.
        if ctx.get("install_mode") == "adopt":
            # Advisory, not work: there is no edit to this feature that clears it. The
            # subject is the project's navigation, the same row is emitted once per
            # feature, and the answer may well be "correct as is". A migration that
            # treated it as work would hold every feature in the repo short of done.
            out.append(
                violation(
                    feature, "I4", "warning", ctx["app_module"], 0,
                    "no NavHost in this project — check the feature is reachable from "
                    "whatever navigation it does use",
                    advisory=True,
                )
            )
        else:
            out.append(
                violation(feature, "I4", "error", ctx["app_module"], 0, "the NavHost file was not found")
            )
    else:
        nav_code = blank_noncode(read(nav_host))
        if not re.search(rf"\b{re.escape(feature)}\s*\(", nav_code):
            out.append(
                violation(
                    feature, "I4", "error", nav_host.relative_to(root).as_posix(), 0,
                    f"`{feature}(...)` not registered in the NavHost",
                )
            )
    return out


# ─── Repo-scoped checks ──────────────────────────────────────────────────────


def check_s3(ctx) -> list[dict]:
    """S3 — generic core code must not import its module's `.app` tier."""
    root, prefix = ctx["root"], ctx["pkg_prefix"]
    if not prefix:
        return []
    app_import = re.compile(
        rf"^import\s+{re.escape(prefix)}\.({'|'.join(APP_TIERS)})\.app\b"
    )
    out = []
    core = root / "core"
    if not core.is_dir():
        return []
    for path in sorted(core.rglob("*.kt")):
        rel = path.relative_to(root).as_posix()
        parts = path.relative_to(core).parts
        if "build" in parts:
            continue
        if len(parts) > 2 and TEST_SOURCESET.search(parts[2]):
            continue
        if path.name == "DataModules.kt":
            continue  # sanctioned exception: references appDataModule, the strip seam
        text = read(path)
        pkg = re.search(r"^package\s+([\w.]+)", text, re.MULTILINE)
        if pkg and re.search(r"\.app(\.|$)", pkg.group(1)):
            continue  # the `app` tier may import itself
        for idx, line in enumerate(text.splitlines()):
            m = app_import.match(line)
            if m:
                out.append(
                    violation(
                        "-", "S3", "error", rel, idx + 1,
                        f"generic core file imports the `{m.group(1)}.app` tier — that "
                        "tier is stripped by install.sh, so this breaks downstream builds",
                    )
                )
    return out


def check_s7(ctx) -> list[dict]:
    """S7 — the app shell has to provide the safe area, because nothing else does.

    Rule 13 is a contract between two halves. `XScreen` and `XTopAppBar` deliberately
    add **no** insets, and the one app-shell `Scaffold` pads the content with the top +
    horizontal safe area. Every feature this pipeline writes relies on that. But
    `check_r13` iterates a *feature's* sources, so it only ever enforces the half that
    says a feature must not nest a Scaffold; the half that says the shell must provide
    the insets was never checked, on any project.

    A migration is where that costs something. Three features were rewritten to the
    contract, promoted to `managedFeatures` and graded strictly in a project whose shell
    is `MaterialTheme { Surface { Column { … } } }` — no Scaffold, no insets. The tab row
    drew under the status bar and the top of every button was untappable, with
    `assembleDebug`, strict `archTest`, `kmpilot_check --all` and the iOS + desktop
    compiles all green (step 9 finding 23).

    It detects the **absence of both mechanisms**, and never grades the correctness of
    one. Of the four shells surveyed, three conform in three different ways —
    `XScaffold` + `windowInsetsPadding`, `Scaffold` + `contentWindowInsets(0,0,0,0)` +
    `safeDrawing`, and a bare `Scaffold` leaning on its default `systemBars` (which runs
    fine) — and only the fourth has neither. Grading correctness would fail the third,
    and wrongly failing an already-working project is the failure this phase has paid
    for twice (findings 1 and 18).

    Repo-scoped for the same reason S3 is. Attached per feature it becomes finding 1
    verbatim: a project-level fact repeated N times that no edit to any feature can
    clear, making every feature uncompletable, forcing `--force`, and then having
    promotion refuse the forced sign-off. Severity `warning`.
    """
    root, app_module = ctx["root"], ctx["app_module"]
    src = root / app_module / "src"
    if not src.is_dir():
        return []
    sources = [
        p
        for p in sorted(src.rglob("*.kt"))
        if "build" not in p.relative_to(src).parts
        and not TEST_SOURCESET.search(p.relative_to(src).parts[0])
    ]
    if not sources:
        return []  # nothing to judge — no Kotlin in the app module at all
    shell_file = None
    for path in sources:
        code = blank_noncode(read(path))
        if SHELL_SCAFFOLD.search(code) or SHELL_INSETS.search(code):
            return []
        if shell_file is None and SHELL_COMPOSABLE.search(code):
            shell_file = path
    # No shell file identified either: point at the app module, which is where the
    # work goes. A path that exists beats a guess at a file name that may not.
    where = (shell_file.relative_to(root).as_posix() if shell_file else app_module)
    return [
        violation(
            "-", "S7", "warning", where, 0,
            f"nothing in `{app_module}` provides the safe area — no `Scaffold`/"
            "`XScaffold` and no window-insets call anywhere in the app module, while "
            "feature screens use `XScreen`, which adds none (Rule 13). Content draws "
            "under the status bar and the top edge of the first row is untappable. Wire "
            "the shell per patterns.md → \"Single App-Shell Scaffold\": Case A (no bottom "
            "nav bar) or Case B (with one)",
        )
    ]


# ─── Driver ──────────────────────────────────────────────────────────────────

FEATURE_CHECKS = [
    ("R3", check_r3),
    ("R5", check_r5),
    ("R7", check_r7),
    ("R8", check_r8),
    ("R9", check_r9),
    ("R11a", check_r11a),
    ("R11b", check_r11b),
    ("R11c", check_r11c),
    ("R12", check_r12),
    ("R12res", check_r12res),
    ("R13", check_r13),
    ("S1", check_s1),
    ("S2", check_s2),
    ("S4", check_s4),
    ("S5", check_s5),
    ("S6", check_s6),
    ("I", check_integration),  # I1-I4
]

# Repo-scoped: one verdict for the project, reported outside the per-feature loop.
# A registry rather than two direct calls, so the report's `RULE_WAS` table can be
# derived from it — S5 and S6 both shipped with no blurb and printed "see
# kmpilot_check.py" to the reader (step 9 finding 22), and that test could only
# derive the feature-scoped half.
REPO_CHECKS = [
    ("S3", check_s3),
    ("S7", check_s7),
]

# 16 feature-scoped checks + 4 integration points + the repo-scoped checks.
CHECK_COUNT = (len(FEATURE_CHECKS) - 1) + 4 + len(REPO_CHECKS)

SEVERITY_ORDER = {"error": 0, "warning": 1}

# ─── Output ──────────────────────────────────────────────────────────────────

# `feature/send/src/commonMain/kotlin/thisissadeghi/send/presentation/ui/X.kt` says
# almost nothing that `presentation/ui/X.kt` does not. The source set and package
# path are constant per feature, so they are elided for display; the JSON report
# always keeps the full repo-relative path.
SOURCE_SET_PREFIX = re.compile(r"^feature/[^/]+/src/[^/]+/kotlin/")


def short_path(rel: str, feature: str, pkg_prefix: str) -> str:
    trimmed = SOURCE_SET_PREFIX.sub("", rel)
    if trimmed == rel:
        return rel  # not a feature source file (gradle file, core/, composeApp/)
    package_dir = f"{pkg_prefix.replace('.', '/')}/{feature}/"
    return trimmed[len(package_dir):] if trimmed.startswith(package_dir) else trimmed


class Palette:
    """ANSI colors, disabled when piped or when NO_COLOR is set."""

    def __init__(self, enabled: bool):
        self.error = "\033[31m" if enabled else ""
        self.warning = "\033[33m" if enabled else ""
        self.dim = "\033[2m" if enabled else ""
        self.bold = "\033[1m" if enabled else ""
        self.off = "\033[0m" if enabled else ""


def print_compact(violations: list[dict]) -> None:
    """One line per violation — greppable, stable, for CI logs and pipes."""
    for v in violations:
        location = f"{v['file']}:{v['line']}" if v["line"] else v["file"]
        print(f"{location}  {v['severity']}  {v['rule']}  {v['message']}")


def print_grouped(violations: list[dict], pkg_prefix: str, color: Palette) -> None:
    """Grouped by feature, paths shortened, messages wrapped — for a terminal."""
    width = min(shutil.get_terminal_size((100, 24)).columns, 100)
    by_feature: dict[str, list[dict]] = {}
    for v in violations:
        by_feature.setdefault(v["feature"], []).append(v)

    for feature in sorted(by_feature, key=lambda f: (f == "-", f)):
        label = "repo-wide" if feature == "-" else f"feature/{feature}"
        print(f"\n{color.bold}{label}{color.off}")
        for v in by_feature[feature]:
            tint = color.error if v["severity"] == "error" else color.warning
            tag = "error" if v["severity"] == "error" else "warn"
            path = short_path(v["file"], v["feature"], pkg_prefix)
            location = f"{path}:{v['line']}" if v["line"] else path
            print(f"  {tint}{tag:<5}{color.off} {v['rule']:<5} {color.dim}{location}{color.off}")
            for line in textwrap.wrap(v["message"], width=max(width - 8, 40)):
                print(f"        {line}")


def find_first(root: Path, pattern: str, *, exclude_build: bool = True) -> Path | None:
    for path in sorted(root.glob(pattern)):
        if exclude_build and "/build/" in path.as_posix():
            continue
        return path
    return None


def find_first_containing(root: Path, pattern: str, needle: str) -> Path | None:
    """First non-build file matching the glob whose CODE matches `needle`.
    Filename conventions are KMPilot's; an adopted project keeps its own.

    Comments and string literals are blanked first. Without that, a file earns a
    role by merely mentioning it in prose: an integrator comment reading "this app
    has no global startKoin{}" made a file register as the Koin entry point, and a
    comment about a `NavHostController` stand-in nearly did the same for the nav
    host. Matching prose is worse than matching nothing — it passes checks that
    should fail."""
    rx = re.compile(needle)
    for path in sorted(root.glob(pattern)):
        if "/build/" in path.as_posix():
            continue
        if rx.search(blank_noncode(read(path))):
            return path
    return None


def resolve_managed_features(root: Path) -> list[str] | None:
    """Features KMPilot generated, from `.kmpilot.json`. `None` when the key is
    absent — a template project, or this repo, where every feature is KMPilot's
    and strict grading is right.

    An ADOPTED project usually has features that predate the pipeline. Holding
    those to rules their author never agreed to means the first `archTest` after
    adoption fails on working, shipped code, with nothing saying the violations
    are pre-existing. So they are graded like `--baseline`: reported in full,
    as warnings, never failing the build. `/create-feature` appends each feature
    it generates, and those are enforced strictly."""
    manifest = root / MANIFEST
    if not manifest.is_file():
        return None
    m = re.search(r'"managedFeatures"\s*:\s*\[([^\]]*)\]', read(manifest))
    if m is None:
        return None
    return re.findall(r'"([^"]+)"', m.group(1))


def append_managed_features(root: Path, features: list[str]) -> list[str] | None:
    """Add `features` to `.kmpilot.json`'s `managedFeatures`. Returns the names
    actually added — already-present ones are skipped — or `None` when there is no
    manifest or no such key, which is a template project where every feature is
    KMPilot's and grading is already strict.

    Promotion is what turns a feature from *reported* into *enforced*, so it is the
    one edit in the pipeline that can make the next `archTest` fail on code somebody
    signed off. It is therefore append-only and never re-orders what is already
    there: entries written by adopt, by `/create-feature` and by a migration all mean
    the same thing, and the release back-compat contract says a shipped field is not
    rewritten under a user.

    The array is rewritten in the one-line form `install.sh` itself emits and the rest
    of the file is left byte-identical — a `json.dump` round-trip would reflow arrays
    the user never touched into a diff nobody asked for. The result is re-parsed
    before it is saved: a manifest this helper corrupted would take the whole project
    out of adopt mode silently.
    """
    manifest = root / MANIFEST
    if not manifest.is_file():
        return None
    text = read(manifest)
    try:
        current = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{MANIFEST} is not valid JSON ({exc}) — fix it by hand first") from exc
    if not isinstance(current.get("managedFeatures"), list):
        return None

    existing = [str(f) for f in current["managedFeatures"]]
    added = [f for f in features if f not in existing]
    if not added:
        return []

    merged = existing + added
    body = ", ".join(json.dumps(f) for f in merged)
    updated, count = re.subn(
        r'"managedFeatures"\s*:\s*\[[^\]]*\]',
        lambda _m: f'"managedFeatures": [{body}]',
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(
            f"{MANIFEST} parses as JSON but its managedFeatures array could not be located "
            "textually — edit it by hand rather than letting this guess"
        )
    try:
        check_back = json.loads(updated)
    except json.JSONDecodeError as exc:  # pragma: no cover — defensive
        raise ValueError(f"the managedFeatures edit would have broken {MANIFEST}: {exc}") from exc
    if check_back.get("managedFeatures") != merged:
        raise ValueError(f"the managedFeatures edit did not land as written in {MANIFEST}")

    tmp = manifest.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, manifest)
    return added


def resolve_install_mode(root: Path) -> str:
    """`template` or `adopt` (absent manifest ⇒ template). Some integration points
    describe KMPilot's own app shell and cannot be demanded of a host project."""
    manifest = root / MANIFEST
    if manifest.is_file():
        m = re.search(r'"installMode"\s*:\s*"([^"]+)"', read(manifest))
        if m:
            return m.group(1)
    return "template"


def run(root: Path, features: list[str]) -> tuple[list[dict], dict]:
    app_module = resolve_app_module(root)
    ctx = {
        "root": root,
        "install_mode": resolve_install_mode(root),
        "managed_features": resolve_managed_features(root),
        "pkg_prefix": resolve_pkg_prefix(root),
        "app_module": app_module,
        "app_gradle": Path(app_module) / "build.gradle.kts",
        "init_koin": find_first(root, f"{app_module}/src/*/kotlin/**/initKoin.kt")
        or find_first_containing(
            root,
            f"{app_module}/src/*/kotlin/**/*.kt",
            # Koin has several entry points; a Compose app often uses none of the
            # global ones. Matching only `startKoin` picked KMPilot's own unused
            # glue file over the place the host actually registers its modules.
            r"\b(startKoin|KoinApplication|koinConfiguration|KoinMultiplatformApplication)\s*[({]",
        ),
        # KMPilot names it BaseAppNavHost.kt; an adopted project may declare its
        # NavHost anywhere (often App.kt), so fall back to content.
        #
        # The call is matched with a `\w*` prefix, not `\b`, because the host is far
        # more likely to call a WRAPPER than `NavHost` itself — `XNavHost` is the
        # design system's own, and it is what /create-feature and the template
        # generate against. `\bNavHost\s*\(` cannot match `XNavHost(` (no word
        # boundary between two word characters), so every adopted project that
        # navigates through the wrapper was told it had no NavHost at all. That is a
        # WRONG advisory, and the expensive half is not the message: with no nav host
        # found, the real I4 check never runs, so a feature genuinely missing from the
        # nav graph goes unreported. `NavHostController(` still does not match — the
        # `\(` has to follow `NavHost` immediately.
        "nav_host": find_first(root, f"{app_module}/src/*/kotlin/**/*NavHost*.kt")
        or find_first_containing(root, f"{app_module}/src/*/kotlin/**/*.kt", r"\w*NavHost\s*\("),
    }
    violations: list[dict] = []
    for feature in features:
        sources = collect_sources(root, root / "feature" / feature)
        for _id, fn in FEATURE_CHECKS:
            violations.extend(fn(feature, sources, ctx))
    # Repo-scoped checks run whether or not this project has a gradable feature. A
    # single-module project has none, and that is exactly the shape whose shell is most
    # likely to provide nothing (step 9 findings 11 and 23: every module-level fact was
    # gated behind "is this a feature" and so was silent on the repo that needed it).
    for _id, fn in REPO_CHECKS:
        violations.extend(fn(ctx))
    violations.sort(
        key=lambda v: (SEVERITY_ORDER.get(v["severity"], 9), v["file"], v["line"], v["rule"])
    )
    return violations, ctx


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kmpilot_check.py",
        description="Check feature modules against the KMPilot architecture rules.",
    )
    parser.add_argument("features", nargs="*", help="feature names to check")
    parser.add_argument("--all", action="store_true", help="check every feature/ module")
    parser.add_argument("--json-only", action="store_true", help="suppress human output")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="one greppable line per violation (the default when output is piped)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="pre-adoption tier: report errors as warnings and always exit 0",
    )
    parser.add_argument(
        "--report",
        default=str(REPORT_PATH),
        help=f"report path, repo-relative (default: {REPORT_PATH})",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repo root to check (default: the repo this script lives in)",
    )
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root).resolve()
        if not (root / SETTINGS_GRADLE).is_file():
            print(f"error: no settings.gradle.kts at {root}", file=sys.stderr)
            return 2
    else:
        root = REPO_ROOT
        if not (root / SETTINGS_GRADLE).is_file():
            cwd = Path.cwd()
            if (cwd / SETTINGS_GRADLE).is_file():
                root = cwd
            else:
                print(
                    f"error: no settings.gradle.kts at {root} — run from the repo root",
                    file=sys.stderr,
                )
                return 2

    available = discover_features(root)
    if args.all:
        features = available
    else:
        features = args.features
        unknown = [f for f in features if f not in available]
        if unknown:
            print(
                f"error: unknown feature(s): {', '.join(unknown)}\n"
                f"available: {', '.join(available) or '(none)'}",
                file=sys.stderr,
            )
            return 2
    # `--all` on a repo with no feature modules is a legitimate empty result, not
    # a usage error: a freshly installed template and a freshly adopted project
    # both run `./gradlew archTest` before their first feature exists. An empty
    # report is written and the gate passes. Naming no features and no --all is
    # still a usage error.
    if not features and not args.all:
        parser.print_usage(sys.stderr)
        print("error: pass at least one feature name, or --all", file=sys.stderr)
        return 2

    violations, ctx = run(root, features)
    # Features that predate the pipeline are reported, never enforced.
    managed = ctx.get("managed_features")
    pre_existing: set[str] = set()
    if managed is not None:
        for v in violations:
            if v["feature"] and v["feature"] not in managed:
                pre_existing.add(v["feature"])
                if v["severity"] == "error":
                    v["severity"] = "warning"
                    v["strictSeverity"] = "error"
                    v["preExisting"] = True

    if args.baseline:
        for v in violations:
            if v["severity"] == "error":
                v["severity"] = "warning"
                v["strictSeverity"] = "error"
    errors = sum(1 for v in violations if v["severity"] == "error")
    warnings = sum(1 for v in violations if v["severity"] == "warning")

    report = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "baseline" if args.baseline else "strict",
        "pkgPrefix": ctx["pkg_prefix"],
        "features": features,
        "violations": violations,
        "preExistingFeatures": sorted(pre_existing),
        "summary": {"error": errors, "warning": warnings, "checked": CHECK_COUNT},
    }
    # One fixed path so consumers (/review-feature, CI) can hardcode it. Written
    # atomically — a concurrent run must never expose a half-written report to a
    # reader. Scope varies with the invocation, so consumers check `features`
    # rather than assuming the file covers the whole repo.
    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(f".{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, report_path)

    exit_code = 1 if errors else 0

    if not args.json_only:
        # A terminal gets grouped, wrapped, colored output; a pipe or a CI log gets
        # the stable one-line-per-violation form that greps and parses cleanly.
        tty = sys.stdout.isatty()
        compact = args.compact or not tty
        color = Palette(tty and not args.compact and os.environ.get("NO_COLOR") is None)

        if compact:
            print_compact(violations)
        else:
            print_grouped(violations, ctx["pkg_prefix"], color)

        scope = f"{len(features)} feature(s)" if args.all else ", ".join(features)
        verdict = "FAIL" if errors else ("PASS" if not warnings else "PASS WITH WARNINGS")
        tint = color.error if errors else (color.warning if warnings else "")
        if not compact:
            print(f"\n{color.dim}{'─' * min(shutil.get_terminal_size((100, 24)).columns, 100)}{color.off}")
        print(
            f"{CHECK_COUNT} checks · {scope} · {errors} error(s) · {warnings} warning(s)"
            + (" · baseline: errors reported as warnings" if args.baseline else "")
        )
        if pre_existing:
            # Loud on purpose: if a feature KMPilot generated ends up here, the
            # gate is silently not enforcing it.
            print(
                f"{color.warning}note{color.off}: {', '.join(sorted(pre_existing))} "
                f"predate{'s' if len(pre_existing) == 1 else ''} KMPilot "
                f"(not in .kmpilot.json managedFeatures) — reported, not enforced"
            )
        print(f"report: {args.report}")
        print(f"{tint}{color.bold}{verdict}{color.off} — exit {exit_code}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
