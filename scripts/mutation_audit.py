#!/usr/bin/env python3
"""Prove the guards can fail.

A green test suite is evidence of nothing until you have watched it go red. Step 9
found a negative control in `migrate-matrix.sh` that had been passing since the day
it landed and could **never** have failed — its regex could not match whether the bug
was present or not. Step 8 found six more of the same shape. Both were caught only by
deliberately breaking the code and checking that the guard noticed.

This is that check, written down so it is repeatable rather than a thing someone did
once. Each entry breaks one behaviour on purpose and names the guard that must go red.
A mutation that SURVIVES is the finding: the guard is decorative.

    python3 scripts/mutation_audit.py              # run every registered mutation
    python3 scripts/mutation_audit.py --only S5    # just the ones whose id matches
    python3 scripts/mutation_audit.py --coverage   # which matrix variants have NO mutation

`--coverage` is the honest status of the suite. Registering a mutation per variant is
ongoing work, not a finished job: as of 2026-08-06 only the step-9 fixes are covered,
so most variants are green for reasons nobody has verified.

Safety: every mutated file is restored in a `finally`, and `gen-surfaces.py` is re-run
so `.claude/` matches the authored source again. If this is ever killed mid-run, check
`git status` on `pipeline/src` before trusting anything.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "pipeline/src/skills/_shared"
MATRIX = REPO / "scripts/migrate-matrix.sh"


# Each mutation: what it breaks, and the guard that must notice.
#   file   — under pipeline/src/skills/_shared (the AUTHORED source, never .claude/)
#   old    — exact text to replace, once
#   new    — the break
#   guard  — ("matrix", variant) or ("test", script name under scripts/)
MUTATIONS: list[dict] = [
    # ── a step cannot be completed unopened (finding 22) ───────────────────
    {
        "id": "complete-without-checkpoint",
        "why": "a step completes with no restore point and no before-counts, and the "
               "report prints `? → 0` for the feature that needed the most work",
        "file": "kmpilot_migrate.py",
        "old": 'if not (plan.get("checkpoints") or {}).get(step["id"]) and not args.force:',
        "new": "if False:",
        "guard": ("test", "kmpilot_migrate_test.py"),
    },
    # ── I3 sees through one hop of indirection (finding 18) ────────────────
    {
        "id": "i3-call-site-only",
        "why": "a feature registered through adopt's own `kmpilotModules` list reads as "
               "unregistered, and no edit to the feature can ever clear it",
        "file": "kmpilot_check.py",
        "old": "        if block and not listed:",
        "new": "        if False and not listed:",
        "guard": ("test", "kmpilot_check_test.py"),
    },
    {
        "id": "i3-indirection-unbounded",
        "why": "any mention of the module name anywhere in the app module counts as "
               "registration, so an unregistered feature passes I3",
        "file": "kmpilot_check.py",
        "old": '                    if decl and re.search(rf"\\b{re.escape(feature)}Module\\b", decl.group(1)):',
        "new": '                    if re.search(rf"\\b{re.escape(feature)}Module\\b", body):',
        "guard": ("test", "kmpilot_check_test.py"),
    },
    # ── S6: the rule that makes the other rules reachable (finding 17) ──────
    {
        "id": "s6-never-fires",
        "why": "a migrated feature reaches zero findings with its screen still flat, so "
               "R3/R12/R13/S1 never run and the migration signs off work it never did",
        "file": "kmpilot_check.py",
        "old": 'if src.path.name.endswith("Screen.kt") and src.path.parent.name != "ui"',
        "new": 'if False and src.path.name.endswith("Screen.kt")',
        "guard": ("test", "kmpilot_check_test.py"),
    },
    {
        "id": "s6-fires-on-conforming",
        "why": "a correctly laid-out feature is told to move its screen — every KMPilot "
               "feature and every already-migrated repo would turn red",
        "file": "kmpilot_check.py",
        "old": 'if src.path.name.endswith("Screen.kt") and src.path.parent.name != "ui"',
        "new": 'if src.path.name.endswith("Screen.kt")',
        "guard": ("test", "kmpilot_check_test.py"),
    },
    # ── carve: features that are packages, not modules (step 9, finding 10) ─
    {
        "id": "carve-never-fires",
        "why": "the app module is never searched for screen packages, so a monolith "
               "inventories as zero features and plans a single report step",
        "file": "kmpilot_discover.py",
        "old": 'CARVE_HOST_KINDS = ("app", "app-android")',
        "new": 'CARVE_HOST_KINDS = ()',
        "guard": ("matrix", "carve-monolith"),
    },
    {
        "id": "carve-host-unrestricted",
        "why": "every module kind becomes a carve host, so :core:designsystem's own "
               "XScreen carves the vendored design system apart",
        "file": "kmpilot_discover.py",
        "old": "    if module.kind not in CARVE_HOST_KINDS:\n        return []",
        "new": "    if False:\n        return []",
        "guard": ("matrix", "control-carve-core-designsystem"),
    },
    {
        "id": "carve-counts-plain-composables",
        "why": "any top-level @Composable counts as a screen root, so every app "
               "module's own App() reads as a feature to carve out",
        "file": "kmpilot_discover.py",
        "old": "        and COMPOSABLE_SCREEN.match(decl[\"name\"])\n        and package_of(src)",
        "new": "        and package_of(src)",
        "guard": ("matrix", "control-carve-app-shell-only"),
    },
    {
        "id": "carve-ignores-existing-home",
        "why": "a stray screen in an existing feature's package carves a SECOND module "
               "for it, giving one feature two rows and two migrate steps",
        "file": "kmpilot_discover.py",
        "old": "                if home:",
        "new": "                if False:",
        "guard": ("matrix", "carve-stray-screen"),
    },
    {
        "id": "carve-ignores-collision",
        "why": "carving onto an occupied feature/{name} is planned silently instead of "
               "refused, merging two unrelated features into one directory",
        "file": "kmpilot_discover.py",
        "old": "                if collision:\n                    refusals.append(",
        "new": "                if False:\n                    refusals.append(",
        "guard": ("matrix", "carve-name-collision"),
    },
    {
        "id": "carve-no-step",
        "why": "discovery finds the in-module feature but the plan builds no carve step, "
               "so the migrate step waits on a module nobody creates",
        "file": "kmpilot_plan.py",
        "old": 'if feature["location"] == "in-module" and feature["verdict"] not in (',
        "new": 'if False and feature["location"] == "in-module" and feature["verdict"] not in (',
        "guard": ("matrix", "carve-monolith"),
    },
    # ── build settings are module facts, not feature facts (finding 11) ────
    {
        "id": "jvm-target-feature-gated",
        "why": "the JVM-target note only fires on features again, so a monolith hears "
               "nothing about the inline failure its migration is about to cause",
        "file": "kmpilot_discover.py",
        "old": 'if module.kind in ("core-kmpilot", "other") or not module.sources:',
        "new": 'if module.kind != "feature":',
        "guard": ("matrix", "jvm-target-app-module"),
    },
    {
        "id": "jvm-target-bar-from-any-module",
        "why": "the bar is taken from the whole repo instead of :core:*, so the core "
               "reports itself as below itself",
        "file": "kmpilot_discover.py",
        "old": 'if m.kind == "core-kmpilot" and m.jvm_target is not None',
        "new": "if m.jvm_target is not None",
        "guard": ("matrix", "control-jvm-target-core"),
    },
    {
        "id": "android-resources-hits-application",
        "why": "an AGP application module is told to add an androidResources block that "
               "only the KMP androidLibrary DSL has",
        "file": "kmpilot_discover.py",
        "old": '                and "androidApplication" not in module.plugins \\\n',
        "new": "",
        "guard": ("matrix", "control-android-resources-application"),
    },
    # ── S7 + the shell step: the other half of Rule 13 (finding 23) ────────
    {
        "id": "s7-never-fires",
        "why": "a shell providing no safe area is never reported, so features are "
               "rewritten to XScreen and promoted against it — the app's top edge is "
               "untappable with every static gate green",
        "file": "kmpilot_check.py",
        "old": '        if SHELL_SCAFFOLD.search(code) or SHELL_INSETS.search(code):\n'
               '            return []',
        "new": "        if True:\n            return []",
        "guard": ("matrix", "shell-no-safe-area"),
    },
    {
        "id": "s7-demands-a-scaffold",
        "why": "a shell that pads for the system bars itself is told it provides no safe "
               "area — every Voyager/Decompose shell without a Scaffold turns red",
        "file": "kmpilot_check.py",
        "old": "        if SHELL_SCAFFOLD.search(code) or SHELL_INSETS.search(code):",
        "new": "        if SHELL_SCAFFOLD.search(code):",
        "guard": ("matrix", "control-shell-insets-no-scaffold"),
    },
    {
        "id": "s7-scaffold-needs-parens",
        "why": "`Scaffold { … }` — the trailing-lambda form — is invisible, so a working "
               "shell is told to add the Scaffold it already has (finding 4's near-miss)",
        "file": "kmpilot_check.py",
        "old": r'SHELL_SCAFFOLD = re.compile(r"\w*Scaffold\s*[({]")',
        "new": r'SHELL_SCAFFOLD = re.compile(r"\w*Scaffold\s*\(")',
        "guard": ("matrix", "control-shell-scaffold-default"),
    },
    {
        "id": "s7-attached-per-feature",
        "why": "the project-level row is counted against each feature, so no edit to any "
               "feature can complete it: `complete` refuses, `--force` follows, and "
               "promotion refuses the forced sign-off (finding 1's failure)",
        "file": "kmpilot_migrate.py",
        "old": '        violations = [v for v in all_rows if v.get("feature") == feature]',
        "new": "        violations = all_rows",
        "guard": ("test", "kmpilot_migrate_test.py"),
    },
    {
        "id": "shell-step-after-migrates",
        "why": "the shell step sorts by the app module's topological position, which is "
               "LAST, so the features are rewritten and promoted before it is fixed",
        "file": "kmpilot_plan.py",
        "old": '        if step["kind"] == "shell":\n            return (-1, STEP_KIND_RANK["shell"], step["id"])',
        "new": "        if False:\n            return ()",
        "guard": ("test", "kmpilot_plan_test.py"),
    },
    {
        "id": "shell-step-always-planned",
        "why": "a project whose shell already conforms is handed work that is already "
               "done — KMPilot itself and bookshelf would both grow a step",
        "file": "kmpilot_plan.py",
        "old": '    rows = [v for v in report.get("projectFindings", []) if v["rule"] == "S7"]\n'
               "    if not rows:\n        return []",
        "new": '    rows = [v for v in report.get("projectFindings", []) if v["rule"] == "S7"]\n'
               '    rows = rows or [{"rule": "S7", "severity": "warning", "file": "-", '
               '"line": 0, "message": "-"}]\n    if False:\n        return []',
        "guard": ("matrix", "control-shell-scaffold-default"),
    },
    {
        "id": "shell-verified-by-nothing",
        "why": "`verify shell` passes while the checker still reports S7, so the step is "
               "completable without the shell ever being wired",
        "file": "kmpilot_migrate.py",
        "old": '        rows = [v for v in violations if v["rule"] == "S7"]',
        "new": "        rows = []",
        "guard": ("test", "kmpilot_migrate_test.py"),
    },
    {
        "id": "project-findings-dropped",
        "why": "discovery keeps bucketing repo-scoped rows under a feature named `-` that "
               "nobody reads, so S3 and S7 vanish from every report and no shell step is "
               "ever planned",
        "file": "kmpilot_discover.py",
        "old": '        if v["feature"] and v["feature"] != "-":',
        "new": '        if v["feature"]:',
        "guard": ("matrix", "shell-no-safe-area"),
    },
    {
        "id": "repo-checks-need-a-feature",
        "why": "the checker is only run when a gradable feature exists, so a single-module "
               "project — the shape most likely to have no shell insets — hears nothing "
               "(finding 11 again)",
        "file": "kmpilot_discover.py",
        "old": "    violations, _ = check.run(root, gradable)",
        "new": "    violations = [] if not gradable else check.run(root, gradable)[0]",
        "guard": ("matrix", "shell-no-feature-modules"),
    },
    # ── I4: the nav host is usually a wrapper (step 9, finding 6) ──────────
    {
        "id": "i4-wrapper-blind",
        "why": "the nav-host match cannot see XNavHost, so the real I4 never runs",
        "file": "kmpilot_check.py",
        "old": r'r"\w*NavHost\s*\("',
        "new": r'r"\bNavHost\s*\("',
        "guard": ("matrix", "nav-host-wrapper"),
    },
    {
        "id": "i4-wrapper-overbroad",
        "why": "a file that merely NAMES NavHostController is graded as the nav host",
        "file": "kmpilot_check.py",
        "old": r'r"\w*NavHost\s*\("',
        "new": r'r"NavHost"',
        "guard": ("matrix", "control-navhost-mention-only"),
    },

    # ── S5: @Serializable without the compiler plugin (step 9, finding 8) ──
    {
        "id": "s5-never-fires",
        "why": "the crash a launch finds is never reported statically",
        "file": "kmpilot_check.py",
        "old": "    if not declares:\n        return []",
        "new": "    if declares:\n        return []",
        "guard": ("matrix", "serialization-plugin-missing"),
    },
    {
        "id": "s5-always-fires",
        "why": "a module that already applies the plugin is told to add it",
        "file": "kmpilot_check.py",
        "old": '    if block and re.search(r"serialization", block.group(1), re.IGNORECASE):\n'
               '        return []',
        "new": "    if False:\n        return []",
        "guard": ("matrix", "control-serialization-plugin-present"),
    },
    {
        "id": "s5-reads-whole-file",
        "why": "the runtime library is mistaken for the compiler plugin, so the crash "
               "is reported as already fixed",
        "file": "kmpilot_check.py",
        "old": '    block = re.search(r"\\bplugins\\s*\\{(.*?)\\}", gradle, re.DOTALL)\n'
               '    if block and re.search(r"serialization", block.group(1), re.IGNORECASE):\n'
               '        return []',
        "new": '    if re.search(r"serialization", gradle, re.IGNORECASE):\n'
               '        return []',
        "guard": ("matrix", "control-serialization-lib-only"),
    },

    # ── refuse over a dirty tree with no checkpoint (step 9, finding 5) ────
    {
        "id": "refuse-ignores-tree",
        "why": "a refusal is recorded over a half-rewritten feature and leaves it there",
        "file": "kmpilot_migrate.py",
        "old": '    elif prior == "in-progress" or is_dirty(root):',
        "new": '    elif prior == "in-progress":',
        "guard": ("test", "kmpilot_migrate_test.py"),
    },

    # ── the dry run must not predict an edit (step 9, finding 7) ───────────
    {
        "id": "plan-overpromises",
        "why": "the dry run announces promotions the real command never makes",
        "file": "kmpilot_report.py",
        "old": '    would = [] if state["managed"] is None else state["toPromote"]',
        "new": '    would = state["toPromote"]',
        "guard": ("test", "kmpilot_report_test.py"),
    },

    # ── a relocated feature's "before" (step 9, open item 2) ───────────────
    {
        "id": "regrade-never-captured",
        "why": "the only 'before' a relocated feature ever has is thrown away, so the "
               "features that needed the most work report as `? → 0`",
        "file": "kmpilot_migrate.py",
        "old": '    if step["kind"] != "migrate" or step["detail"].get("gradable", True):\n'
               "        return None",
        "new": "    if True:\n        return None",
        "guard": ("test", "kmpilot_report_test.py"),
    },
    {
        "id": "regrade-overwrites-gradable",
        "why": "a step the plan already graded is re-graded on open, silently redefining "
               "'before' as 'after the hoists and extracts'",
        "file": "kmpilot_migrate.py",
        "old": '    if step["kind"] != "migrate" or step["detail"].get("gradable", True):\n'
               "        return None",
        "new": '    if step["kind"] != "migrate":\n        return None',
        "guard": ("test", "kmpilot_report_test.py"),
    },
    {
        "id": "regrade-lost-on-regen",
        "why": "a regeneration drops the capture, and it cannot be re-derived — the "
               "rewrite has already removed the findings it records",
        "file": "kmpilot_plan.py",
        "old": '        "regrades": dict(previous.get("regrades") or {}),',
        "new": '        "regrades": {},',
        "guard": ("test", "kmpilot_report_test.py"),
    },
    {
        "id": "regrade-ignored-by-report",
        "why": "the capture is written but the report keeps reading the empty plan-time "
               "counts, so the fix exists and changes nothing a reader sees",
        "file": "kmpilot_report.py",
        "old": '        if not source.get("gradable", True) and step["id"] in regrades:',
        "new": "        if False:",
        "guard": ("test", "kmpilot_report_test.py"),
    },

    # ── the report must not count its own step outstanding (open item 5) ───
    {
        "id": "report-counts-own-step",
        "why": "a run that finished completely reports outstanding work, contradicting "
               "`status` in the same artifact",
        "file": "kmpilot_report.py",
        "old": "    s = step_counts(plan)",
        "new": '    s = plan["summary"]',
        "guard": ("test", "kmpilot_report_test.py"),
    },
]


def regenerate() -> None:
    subprocess.run([sys.executable, "scripts/gen-surfaces.py"], cwd=REPO,
                   capture_output=True, check=True)


def guard_caught(kind: str, target: str) -> bool:
    """True when the guard noticed — i.e. it failed, which is what we want here."""
    if kind == "matrix":
        cmd = ["bash", "scripts/migrate-matrix.sh", target]
    else:
        cmd = [sys.executable, f"scripts/{target}"]
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True).returncode != 0


def matrix_variants() -> list[str]:
    return re.findall(r"^if matches ([a-z0-9-]+); then", MATRIX.read_text(), re.MULTILINE)


def coverage() -> int:
    covered = {m["guard"][1] for m in MUTATIONS if m["guard"][0] == "matrix"}
    variants = matrix_variants()
    uncovered = [v for v in variants if v not in covered]
    print(f"{len(variants)} matrix variants · {len(covered)} with a registered mutation "
          f"· {len(uncovered)} unverified\n")
    print("UNVERIFIED — green, but nobody has watched these go red:")
    for name in uncovered:
        print(f"  {name}")
    print("\nA variant here is not known to be broken. It is known to be unproven, which is "
          "\nthe state the one fake-green control was in for weeks before step 9 found it.")
    return 0


def run(only: str | None) -> int:
    backups = {name: (SRC / name).read_text() for name in {m["file"] for m in MUTATIONS}}
    caught = survived = invalid = 0
    green_guards: dict[tuple[str, str], bool] = {}

    def guard_is_green(guard: tuple[str, str]) -> bool:
        """Is the guard passing on UNMUTATED code?

        Without this the audit proves nothing about an already-failing guard: a red
        guard stays red under mutation, `guard_caught` sees a non-zero exit, and the
        mutation is reported `caught` on the strength of a failure that was there
        before. Found the hard way while adding the carve controls — a control with a
        typo'd assertion registered its mutation as caught on the first run.

        Cached: several mutations share one guard and re-running the matrix per
        mutation is the expensive part of this script.
        """
        if guard not in green_guards:
            green_guards[guard] = not guard_caught(*guard)
        return green_guards[guard]

    try:
        for mut in MUTATIONS:
            if only and only not in mut["id"]:
                continue
            path = SRC / mut["file"]
            original = backups[mut["file"]]
            if mut["old"] not in original:
                print(f"STALE    {mut['id']}  — anchor no longer in {mut['file']}; "
                      "the code moved and this mutation now proves nothing")
                survived += 1
                continue
            if not guard_is_green(tuple(mut["guard"])):
                print(f"INVALID  {mut['id']}  ({mut['guard'][1]})")
                print("         the guard is ALREADY failing on unmutated code, so watching "
                      "it fail again proves nothing. Fix the guard first.")
                invalid += 1
                continue
            path.write_text(original.replace(mut["old"], mut["new"], 1))
            regenerate()
            hit = guard_caught(*mut["guard"])
            path.write_text(original)
            regenerate()
            if hit:
                caught += 1
                print(f"caught   {mut['id']}  ({mut['guard'][1]})")
            else:
                survived += 1
                print(f"SURVIVED {mut['id']}  ({mut['guard'][1]})")
                print(f"         the guard cannot fail — {mut['why']}")
    finally:
        for name, text in backups.items():
            (SRC / name).write_text(text)
        regenerate()

    tail = f" · {invalid} invalid" if invalid else ""
    print(f"\n{caught} caught · {survived} survived{tail}")
    return 1 if (survived or invalid) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="run mutations whose id contains this")
    ap.add_argument("--coverage", action="store_true",
                    help="list matrix variants with no registered mutation")
    args = ap.parse_args()
    return coverage() if args.coverage else run(args.only)


if __name__ == "__main__":
    sys.exit(main())
