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
    caught = survived = 0
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

    print(f"\n{caught} caught · {survived} survived")
    return 1 if survived else 0


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
