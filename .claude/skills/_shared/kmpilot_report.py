#!/usr/bin/env python3
"""
kmpilot_report.py — the *integrate* phase of `/kmp-to-kmpilot` (Phase 6, Stage B,
step 8): the last thing a migration does, and the only record of what it did.

Discovery says *what is in this repo*; the plan says *what will be done to it*;
`kmpilot_migrate.py` owns *how it is done safely*. This owns **what is left behind**:

    MIGRATION-REPORT.md   what changed per rule, what was refused and why, and which
                          features carry behavioural risk because nothing tests them
    .kmpilot.json         `managedFeatures` gains every feature the checker passed —
                          the moment a migrated feature starts being graded strictly
    the `report` step     marked done, verified, committed

    python3 kmpilot_report.py --root . plan       # what it would do; writes nothing
    python3 kmpilot_report.py --root . promote    # re-verify, then append to managedFeatures
    python3 kmpilot_report.py --root . write      # MIGRATION-REPORT.md
    python3 kmpilot_report.py --root . finish     # promote → write → complete → commit

## Promotion re-runs the checker; it never believes the ledger

`complete --force` exists, so a `done` step is not proof of anything. Promotion is the
edit that flips a feature from *reported* to *enforced*, so a feature promoted without
passing turns the next `archTest` red on work the migration called finished — and does
it in a file the user did not write. Every candidate is re-verified through
`kmpilot_migrate.verify_step`, the same function `complete` uses, because a feature
promoted on a different bar than the one that completed it is a migration disagreeing
with itself.

Promotion is per feature, not all-or-nothing: one feature the checker still finds work
in does not hold back the ones that are finished.

## The report is written even when the run went badly

A refused, blocked or half-finished migration is exactly the run whose record matters,
so nothing here withholds the report on the grounds that there is bad news in it. It is
regenerated rather than appended to — a report that accretes stale sections is worse
than no report, because it reads as current.

## What it does not do

It does not write specs. `/audit-spec` is the one spec writer in the pipeline and
inventing a second one is how two of them come to disagree; the report **names** every
migrated feature that has no spec, and the skill drives `/audit-spec` for them.

It does not add integration points either. I1–I4 are checker rules inside the
`integration` rewrite cluster, already routed to `integrator` during the clean phase,
and `verify` holds a `migrate` step at zero findings — so a feature cannot reach `done`
unforced with one missing. What is added here is the report naming any that a forced
completion left behind.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kmpilot_check as check  # noqa: E402
import kmpilot_migrate as migrate_mod  # noqa: E402
import kmpilot_plan as plan_mod  # noqa: E402

Palette = check.Palette
SETTINGS_GRADLE = check.SETTINGS_GRADLE
REPO_ROOT = check.REPO_ROOT
PLAN_REL = plan_mod.PLAN_REL
REPORT_REL = migrate_mod.REPORT_REL
SHARED_STEP_KINDS = ("hoist", "extract", "relocate")

# What each rule was, in the words the report needs — the rewrite passes already carry
# the goal per cluster, so this is only the one-line "what it was" column.
RULE_WAS = {
    "R3": "state assigned directly instead of through `setState { copy() }`",
    "R5": "Material3 used directly instead of the X-components",
    "R7": "a package name that is not lowercase-only",
    "R8": "no top-level `{featurename}Module` for Koin",
    "R9": "a UseCase layer between the ViewModel and the repository",
    "R11a": "more than one presentation state container",
    "R11b": "no `*UiModel`",
    "R11c": "the data layer importing from `presentation`",
    "R12": "user-facing strings hardcoded in composables",
    "R12res": "no `composeResources/values/strings.xml`",
    "R13": "a `Scaffold` nested inside a feature screen",
    "S1": "composables in `Screen.kt` outside the 3-name allowlist",
    "S2": "composables outside `components/`",
    "S3": "generic core code importing its module's `.app` tier",
    "S4": "the deprecated `@Preview` import",
    "I1": "not included in `settings.gradle.kts`",
    "I2": "not a dependency of the app module",
    "I3": "not registered in `initKoin`",
    "I4": "not wired into the NavHost",
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── reading the run ─────────────────────────────────────────────────────────


def feature_dir(root: Path, step: dict) -> Path | None:
    """Where this feature's module is *now*. After a `relocate` that is `feature/{name}`;
    a refused or unfinished one is still wherever discovery found it."""
    for candidate in (Path("feature") / step["detail"]["feature"], Path(step["detail"]["dir"])):
        if (root / candidate).is_dir():
            return candidate
    return None


def test_source_sets(root: Path, rel: Path | None) -> list[str]:
    """The module's test source sets, read off disk rather than out of discovery.

    Migration does not touch tests — that is a locked decision, not an accident — so
    what is on disk now is what was there when discovery ran, and reading it here keeps
    the report working against a plan generated before this phase existed. The answer is
    needed in both directions: a feature with **no** test source set carries the most
    behavioural risk in the whole run, and a feature **with** one has tests still
    referencing the types the rewrite replaced.
    """
    if rel is None:
        return []
    src = root / rel / "src"
    if not src.is_dir():
        return []
    return sorted(
        d.name for d in src.iterdir()
        if d.is_dir() and check.TEST_SOURCESET.search(d.name) and any(d.rglob("*.kt"))
    )


def has_spec(root: Path, feature: str) -> bool:
    return (root / ".claude" / "docs" / feature / "spec.md").is_file()


def classify(step: dict) -> str:
    """What this feature's migrate step *means*, which is not the same as its status.

    A `done` step is either work this run did or a feature that was already conforming
    when discovery met it. Reporting both as "migrated" would claim credit for code
    nobody touched, so the ledger's own `statusSource` decides: `ledger` means a
    command in this run wrote it, `derived` means the repo said so on its own.
    """
    status = step["status"]
    if status == "done":
        return "migrated" if step.get("statusSource") == "ledger" else "already-conforming"
    if status in ("refused", "blocked", "skipped"):
        return status
    return "unfinished"


def assess(root: Path, plan: dict) -> dict:
    """Everything the integrate phase needs, gathered once.

    Deliberately read-only: `plan`, `promote` and `write` all start from the same
    picture, so a dry run cannot describe a promotion different from the one that
    happens next.
    """
    managed = check.resolve_managed_features(root)
    present = []
    features: list[dict] = []

    for step in plan["steps"]:
        if step["kind"] != "migrate":
            continue
        name = step["detail"]["feature"]
        rel = feature_dir(root, step)
        under_featuredir = rel is not None and rel.parts[:1] == ("feature",)
        if under_featuredir:
            present.append(name)
        features.append(
            {
                "name": name,
                "step": step["id"],
                "status": step["status"],
                "kind": classify(step),
                "statusReason": step.get("statusReason", ""),
                "dir": rel.as_posix() if rel else None,
                "gradable": under_featuredir,
                "before": dict(step["detail"].get("findings") or {}),
                "beforeTotal": step["detail"].get("findingCount", 0),
                "beforeKnown": bool(step["detail"].get("gradable", True)),
                "tests": test_source_sets(root, rel),
                "hasSpec": has_spec(root, name),
                "wasManaged": managed is not None and name in managed,
            }
        )

    # One batched checker run for the detail, per-feature `verify_step` for the verdict.
    # The batch is attribution only; nothing decides anything from it.
    after: dict[str, list[dict]] = {}
    if present:
        violations, _ = check.run(root, present)
        for v in violations:
            after.setdefault(v["feature"] or "-", []).append(v)

    for row in features:
        rows = after.get(row["name"], [])
        row["after"] = dict(Counter(v["rule"] for v in rows))
        row["afterTotal"] = len(rows)
        row["afterRows"] = rows[:20]
        step = next(s for s in plan["steps"] if s["id"] == row["step"])
        if row["kind"] in ("migrated", "already-conforming"):
            ok, lines = migrate_mod.verify_step(root, step, plan)
            row["promotable"] = ok
            row["promoteBlock"] = "" if ok else lines[0]
        else:
            row["promotable"] = False
            row["promoteBlock"] = f"not migrated — {row['kind']}"

    shared = [
        {
            "id": s["id"],
            "kind": s["kind"],
            "subject": s["subject"],
            "title": s["title"],
            "status": s["status"],
            "statusReason": s.get("statusReason", ""),
            "target": s["detail"].get("target") or s["detail"].get("to", ""),
        }
        for s in plan["steps"]
        if s["kind"] in SHARED_STEP_KINDS
    ]

    to_promote = [r["name"] for r in features if r["promotable"] and not r["wasManaged"]]
    return {
        "managed": managed,
        "features": features,
        "shared": shared,
        "toPromote": to_promote,
        "blockedFromPromotion": [
            r for r in features
            if r["kind"] in ("migrated", "already-conforming") and not r["promotable"]
        ],
        "missingSpecs": [
            r["name"] for r in features if r["kind"] == "migrated" and not r["hasSpec"]
        ],
        "untested": [r["name"] for r in features if r["kind"] == "migrated" and not r["tests"]],
        "staleTests": [r for r in features if r["kind"] == "migrated" and r["tests"]],
        "projectWide": after.get("-", []),
    }


# ─── the report ──────────────────────────────────────────────────────────────


def rule_table(features: list[dict]) -> list[tuple[str, int, int]]:
    """Per rule: how many findings the plan recorded, and how many are left."""
    before: Counter = Counter()
    after: Counter = Counter()
    for row in features:
        before.update(row["before"])
        after.update(row["after"])
    return [(rule, before.get(rule, 0), after.get(rule, 0)) for rule in sorted(before | after)]


def render(plan: dict, state: dict, promoted: list[str]) -> str:
    p = plan["project"]
    migration = plan.get("migration") or {}
    s = plan["summary"]
    out: list[str] = []
    w = out.append

    w(f"# Migration report — {p['rootProjectName']}")
    w("")
    w(f"`/kmp-to-kmpilot` · {today()} · package prefix `{p['packagePrefix']}` · "
      f"app module `{p['appModule']}`")
    if migration:
        w("")
        w(f"Branch `{migration['branch']}`, cut from `{migration['baseBranch']}` at "
          f"`{migration['baseRef'][:9]}`.")
    w("")

    migrated = [r for r in state["features"] if r["kind"] == "migrated"]
    unfinished = [r for r in state["features"] if r["kind"] == "unfinished"]
    w("## What happened")
    w("")
    w("| | |")
    w("|---|---|")
    w(f"| Features migrated | {len(migrated)} |")
    w(f"| Features promoted to `managedFeatures` | {len(promoted)} "
      "<br>*(includes any that already conformed — see the table below)* |")
    w(f"| Features refused | {sum(1 for r in state['features'] if r['kind'] == 'refused')} |")
    w(f"| Features blocked | {sum(1 for r in state['features'] if r['kind'] == 'blocked')} |")
    w(f"| Features left unfinished | {len(unfinished)} |")
    w(f"| Shared packages hoisted or extracted | "
      f"{sum(1 for r in state['shared'] if r['status'] == 'done')} of {len(state['shared'])} |")
    w(f"| Plan steps | {s['done']} done · {s['refused']} refused · {s['blocked']} blocked · "
      f"{s['skipped']} skipped · {s['pending'] + s['in-progress']} outstanding |")
    w("")

    # ── features ────────────────────────────────────────────────────────────
    w("## Features")
    w("")
    w("| Feature | Outcome | Findings before → after | Promoted | Spec | Tests |")
    w("|---|---|---|---|---|---|")
    for row in state["features"]:
        before = str(row["beforeTotal"]) if row["beforeKnown"] else "?"
        counts = f"{before} → {row['afterTotal']}" if row["gradable"] else f"{before} → n/a"
        promoted_mark = (
            "yes" if row["name"] in promoted
            else ("already" if row["wasManaged"] else "**no**")
        )
        w(f"| `{row['name']}` | {row['kind']} | {counts} | {promoted_mark} | "
          f"{'yes' if row['hasSpec'] else 'missing'} | "
          f"{', '.join(row['tests']) if row['tests'] else '**none**'} |")
    w("")
    if state["blockedFromPromotion"]:
        w("Not promoted despite being marked done — promotion re-runs the checker, so these "
          "are completions that were forced:")
        w("")
        for row in state["blockedFromPromotion"]:
            w(f"- `{row['name']}` — {row['promoteBlock']}")
        w("")

    # ── per rule ────────────────────────────────────────────────────────────
    table = rule_table(state["features"])
    w("## What changed, per rule")
    w("")
    if not table:
        w("No rule findings were recorded for any feature in this migration.")
    else:
        w("*Before* is what the plan recorded when it was confirmed; *after* is the checker "
          "run at the time this report was written.")
        w("")
        w("| Rule | Before | After | What it was |")
        w("|---|---|---|---|")
        for rule, b, a in table:
            w(f"| {rule} | {b} | {a} | {RULE_WAS.get(rule, 'see kmpilot_check.py')} |")
    w("")

    # ── shared code ─────────────────────────────────────────────────────────
    if state["shared"]:
        w("## Shared code")
        w("")
        w("| Module | Step | Outcome | |")
        w("|---|---|---|---|")
        for row in state["shared"]:
            w(f"| `{row['subject']}` | {row['kind']} | {row['status']} | "
              f"{row['target'] or row['statusReason'] or row['title']} |")
        w("")

    # ── refusals ────────────────────────────────────────────────────────────
    w("## Refusals")
    w("")
    refusals = plan.get("refusals") or []
    if not refusals:
        w("Nothing was refused.")
    else:
        w("A refusal is a pass, not a half-migration: each subject below was left exactly as "
          "it was found.")
        w("")
        for r in refusals:
            found = "found by reading the repo" if r.get("at") == "discovery" else (
                "found once a rewrite pass had opened it"
            )
            w(f"### `{r['subject']}` — {r.get('kind', 'feature')}, {found}")
            w("")
            w(r["reason"])
            if r.get("evidence"):
                w("")
                for e in r["evidence"]:
                    w(f"- `{e}`")
            w("")
    w("")

    # ── risk ────────────────────────────────────────────────────────────────
    w("## Behavioural risk")
    w("")
    w("A rewrite that compiles but changes behaviour is the worst failure this migration can "
      "produce, because it is silent. Pre-existing tests are out of scope — nothing here ran "
      "or ported them — so the two things worth knowing are named instead.")
    w("")
    if not migrated:
        # Vacuous reassurance is the failure mode here: "every migrated feature has
        # tests" is true of zero features and reads as an all-clear.
        w("No feature was migrated in this run, so there is nothing to assess.")
    elif state["untested"]:
        w(f"**No test source set at all** — {', '.join(f'`{n}`' for n in state['untested'])}. "
          "Nothing in the repo would have caught a behavioural change in these. They carry the "
          "most risk in this run; generate coverage with `/test-feature`.")
    else:
        w(f"All {len(migrated)} migrated feature(s) have at least one test source set.")
    w("")
    if state["staleTests"]:
        w("**Tests written against the pre-migration types** — these source sets were not "
          "touched and still reference what the rewrite replaced, so expect them to fail to "
          "compile until they are regenerated:")
        w("")
        for row in state["staleTests"]:
            w(f"- `{row['name']}` — {', '.join(row['tests'])}")
        w("")

    # ── what has not been done ──────────────────────────────────────────────
    w("## What this run did not do")
    w("")
    w("- **Nothing was compiled.** Verification here is static and uses the same checker CI "
      "consumes. Run `./gradlew assembleDebug` and `./gradlew archTest` before calling the "
      "migration finished.")
    w("- **Tests were not ported or run.**")
    if state["missingSpecs"]:
        w(f"- **No spec yet** for {', '.join(f'`{n}`' for n in state['missingSpecs'])} — "
          "generate them with `/audit-spec`.")
    if unfinished:
        names = ", ".join(f"`{r['name']}`" for r in unfinished)
        w(f"- **Left unfinished**: {names}. Re-invoke `/kmp-to-kmpilot` to resume — it never "
          "re-migrates a finished feature.")
    if state["projectWide"]:
        w(f"- **{len(state['projectWide'])} project-wide finding(s)** outside any one feature "
          "remain (S3, the `.app`-tier boundary).")
    w("")

    # ── undo ────────────────────────────────────────────────────────────────
    if migration:
        w("## Undoing this")
        w("")
        w("```bash")
        w(f"git switch -   # back to {migration['baseBranch']}")
        w("```")
        w("")
        w(f"That restores the pre-migration **committed** state. Work that was uncommitted when "
          f"the run started lives inside the checkpoint commit `{migration['checkpointRef'][:9]}` "
          "and has to be asked for by name:")
        w("")
        w("```bash")
        w(f"git restore --source={migration['checkpointRef'][:9]} -- .")
        w("```")
        w("")
    w(f"*Generated by `kmpilot_report.py` at {now()}. Regenerated in full on every run — do "
      "not hand-edit.*")
    return "\n".join(out) + "\n"


# ─── commands ────────────────────────────────────────────────────────────────


def print_state(state: dict, plan: dict, color: Palette, header: str) -> None:
    print(f"{color.bold}{header}{color.off} — {plan['project']['rootProjectName']}")
    for row in state["features"]:
        mark = "+" if row["promotable"] and not row["wasManaged"] else (
            "=" if row["wasManaged"] else " "
        )
        tail = f"  {color.dim}{row['promoteBlock']}{color.off}" if row["promoteBlock"] else ""
        print(f"  {mark} feature  {row['name']:<20} {row['kind']:<18} "
              f"findings={row['afterTotal'] if row['gradable'] else 'n/a'}"
              f"  tests={','.join(row['tests']) or 'none'}"
              f"  spec={'yes' if row['hasSpec'] else 'missing'}{tail}")
    if state["managed"] is None:
        print(f"  {color.warning}no managedFeatures key{color.off} — a template project; every "
              "feature is already graded strictly and nothing is promoted")


def cmd_plan(root: Path, plan: dict, state: dict, args, color: Palette) -> int:
    print_state(state, plan, color, "integrate — dry run, nothing written")
    print(f"  would promote: {', '.join(state['toPromote']) or 'nothing'}")
    print(f"  would write:   {REPORT_REL}")
    if state["missingSpecs"]:
        print(f"  {color.warning}specs missing{color.off}: {', '.join(state['missingSpecs'])} — "
              "generate with /audit-spec before finishing")
    if state["untested"]:
        print(f"  {color.warning}no test source set{color.off}: {', '.join(state['untested'])} — "
              "named in the report as the highest behavioural risk")
    return 0


def cmd_promote(root: Path, plan: dict, state: dict, args, color: Palette) -> list[str] | None:
    """Append every re-verified feature to `managedFeatures`. Returns what was added,
    or None when the manifest has no such key."""
    if state["managed"] is None:
        print(f"{color.warning}nothing to promote{color.off} — {check.MANIFEST} has no "
              "managedFeatures key, so every feature here is already graded strictly")
        return None
    try:
        added = check.append_managed_features(root, state["toPromote"])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
    added = added or []
    if added:
        print(f"{color.bold}promoted{color.off} {', '.join(added)} → managedFeatures "
              f"{color.dim}(graded strictly from now on){color.off}")
    else:
        print("promoted nothing — every verified feature was already in managedFeatures")
    for row in state["blockedFromPromotion"]:
        print(f"  {color.error}not promoted{color.off} {row['name']}: {row['promoteBlock']}")
    return added


def cmd_write(root: Path, plan: dict, state: dict, promoted: list[str], color: Palette) -> int:
    path = root / REPORT_REL
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(render(plan, state, promoted), encoding="utf-8")
    os.replace(tmp, path)
    print(f"{color.bold}wrote{color.off} {REPORT_REL}")
    return 0


def cmd_finish(root: Path, plan: dict, state: dict, args, color: Palette) -> int:
    step = next((s for s in plan["steps"] if s["kind"] == "report"), None)
    if step is None:
        print("error: this plan has no report step — regenerate it with kmpilot_plan.py",
              file=sys.stderr)
        return 1
    if step["status"] == "done" and not args.force:
        print(f"{color.bold}already finished{color.off} — the report step is done. Re-run "
              "`write` to regenerate the report, or pass --force to record it again.")
        return 0

    try:
        promoted = cmd_promote(root, plan, state, args, color) or []
    except ValueError:
        return 1
    # Promotion changes what the checker enforces, so the picture the report describes
    # is the one *after* it — re-read rather than reporting the state we came in with.
    state = assess(root, plan)
    cmd_write(root, plan, state, promoted, color)

    ok, lines = migrate_mod.verify_step(root, step, plan)
    if not ok and not args.force:
        print(f"{color.error}not finished{color.off} — the report step does not verify:",
              file=sys.stderr)
        for line in lines:
            print(f"  {line}", file=sys.stderr)
        print("  fix it, or record a human sign-off with --force.", file=sys.stderr)
        return 1

    note = "verified: " + lines[0] if ok else f"completed with --force: {lines[0]}"
    migrate_mod.set_progress(plan, step["id"], "done", note)
    migrate_mod.refresh(plan)
    migrate_mod.save(root, plan)
    committed = None
    if migrate_mod.is_repo(root):
        committed = migrate_mod.commit_all(root, "write MIGRATION-REPORT.md and promote the "
                                                 "migrated features (report)")

    print(f"{color.bold}migration finished{color.off} — {lines[0]}")
    if committed:
        print(f"  committed {committed[:9]}")
    print(f"  next: {plan['next'] or 'nothing left'}")
    print(f"  {color.dim}not done here: ./gradlew assembleDebug · ./gradlew archTest · "
          f"/test-feature · /audit-spec{color.off}")
    return 0


def print_compact(state: dict, plan: dict, promoted: list[str] | None) -> None:
    """One greppable line per row — what the matrix and CI assert against."""
    print(f"integrate  {plan['project']['rootProjectName']}  "
          f"promoted={','.join(promoted) if promoted else '-'}  "
          f"promotable={','.join(state['toPromote']) or '-'}  "
          f"untested={','.join(state['untested']) or '-'}  "
          f"nospec={','.join(state['missingSpecs']) or '-'}  "
          f"report={'yes' if (Path(plan['project']['root']) / REPORT_REL).is_file() else 'no'}")
    for row in state["features"]:
        print(f"feature  {row['name']}  {row['kind']}  before={row['beforeTotal']}  "
              f"after={row['afterTotal'] if row['gradable'] else '-'}  "
              f"promotable={'yes' if row['promotable'] else 'no'}  "
              f"managed={'yes' if row['wasManaged'] else 'no'}  "
              f"tests={','.join(row['tests']) or '-'}  "
              f"spec={'yes' if row['hasSpec'] else 'no'}"
              + (f"  block={row['promoteBlock']}" if row["promoteBlock"] else ""))


# ─── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kmpilot_report.py",
        description="Run the integrate phase of /kmp-to-kmpilot against a migrated repo: "
        "MIGRATION-REPORT.md, managedFeatures promotion, and the closing step.",
    )
    parser.add_argument("--root", default=None, help="repo root (default: cwd)")
    parser.add_argument(
        "command",
        choices=("plan", "promote", "write", "finish"),
        help="plan: what it would do, writes nothing · promote: re-verify then append to "
        "managedFeatures · write: MIGRATION-REPORT.md · finish: all of it, then close the run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="finish a run whose report step does not verify — records the sign-off as forced",
    )
    parser.add_argument("--compact", action="store_true", help="one greppable line per row")
    parser.add_argument("--json-only", action="store_true", help="print the assessment as JSON")
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

    color = Palette(os.environ.get("NO_COLOR") is None and sys.stdout.isatty() and not args.compact)

    plan = plan_mod.load_plan(root)
    if plan is None:
        print(f"error: no migration plan at {root / PLAN_REL}. The integrate phase reports on a "
              "migration; run /kmp-to-kmpilot first.", file=sys.stderr)
        return 1
    # `plan` is read-only, so it stays available on a draft — reviewing what promotion
    # would do is exactly the kind of question worth answering before confirming.
    # Everything else writes, so it is behind the same gate as the clean phase.
    if args.command != "plan":
        if plan["planStatus"] != "confirmed":
            print(f"error: the plan is {plan['planStatus']}, not confirmed — a migration that "
                  "was never approved has nothing to promote or report.", file=sys.stderr)
            return 1
        if not plan.get("migration"):
            print("error: this migration has not begun — there is no run to report on. Run "
                  "`kmpilot_migrate.py begin` and work the steps first.", file=sys.stderr)
            return 1

    state = assess(root, plan)
    if args.json_only:
        print(json.dumps(state, indent=2))
        return 0

    if args.command == "plan":
        if args.compact:
            print_compact(state, plan, None)
            return 0
        return cmd_plan(root, plan, state, args, color)
    if args.command == "promote":
        try:
            promoted = cmd_promote(root, plan, state, args, color)
        except ValueError:
            return 1
        if args.compact:
            print_compact(assess(root, plan), plan, promoted)
        return 0
    if args.command == "write":
        already = [r["name"] for r in state["features"] if r["wasManaged"]]
        cmd_write(root, plan, state, already, color)
        if args.compact:
            print_compact(state, plan, already)
        return 0

    code = cmd_finish(root, plan, state, args, color)
    if args.compact:
        after = plan_mod.load_plan(root) or plan
        print_compact(assess(root, after), after, None)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
