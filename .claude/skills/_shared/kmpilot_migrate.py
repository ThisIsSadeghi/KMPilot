#!/usr/bin/env python3
"""
kmpilot_migrate.py — the execution envelope for the *clean* phase of
`/kmp-to-kmpilot` (Phase 6, Stage B, steps 6–7).

Discovery answers *what is in this repo*; the plan answers *what will be done to it*;
this owns **how it is done safely**: the checkpoint branch, a checkpoint commit before
every step, the restore that makes a mid-rewrite refusal leave a feature exactly as it
was found, and the verification that says a step is actually finished.

It does **not** rewrite Kotlin. The rewriting is the layer agents' job, driven by the
skill; everything mechanical around it is here, for the same reason discovery and the
plan are scripts: a migration and a CI run must not disagree, and the undo has to work
the same way every time rather than however a model felt like typing `git` that day.

    python3 kmpilot_migrate.py --root . begin
    python3 kmpilot_migrate.py --root . next
    python3 kmpilot_migrate.py --root . checkpoint migrate-search
    python3 kmpilot_migrate.py --root . verify     migrate-search
    python3 kmpilot_migrate.py --root . complete   migrate-search
    python3 kmpilot_migrate.py --root . refuse     migrate-search --reason "…"

## It never re-runs discovery

Every command reads `.claude/docs/_project/migration-plan.json` as written, mutates it
and writes it back. That is deliberate. The clean phase rewrites source, so a
regeneration mid-run would produce a different step list — a relocated feature stops
needing its `relocate` step — and the plan would drop back to `draft` on its own work,
lapsing the confirmation the whole phase leans on. **The plan the user approved is the
plan that executes.** Regenerate with `kmpilot_plan.py` between runs, not during one.

## Dirty trees are supported, not refused

`begin` cuts `kmpilot/migrate-{project}` and commits whatever is in the tree as a
checkpoint. Adopt mode's clean-tree refusal is not carried over: adopt only ever adds
files, so a bad run is `rm -rf` of known paths, while migration rewrites working source
and needs a real undo — but the undo does not have to be *"refuse until you clean up"*.
Undo the whole run with `git switch -`; the user's uncommitted work is preserved inside
the checkpoint commit.

## Restoring is additive, never destructive

`restore` (and the `refuse` that calls it) commits the work in progress first, then
reverts the range back to the step's checkpoint with a **new** commit. Nothing is
rewritten out of history and no `reset --hard` is issued, so a refusal taken by mistake
is still recoverable from the branch itself rather than only from the reflog.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kmpilot_check as check  # noqa: E402
import kmpilot_plan as plan_mod  # noqa: E402

Palette = check.Palette
SETTINGS_GRADLE = check.SETTINGS_GRADLE
REPO_ROOT = check.REPO_ROOT
PLAN_REL = plan_mod.PLAN_REL

BRANCH_PREFIX = "kmpilot/migrate-"
# Written by kmpilot_report.py (the integrate phase), verified here: the `report` step
# is the last thing a run does, and a step that verifies trivially would let a run be
# called finished with nothing written down.
REPORT_REL = Path("MIGRATION-REPORT.md")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


# ─── git ─────────────────────────────────────────────────────────────────────


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check_rc: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )
    if check_rc and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def is_repo(root: Path) -> bool:
    try:
        return git(root, "rev-parse", "--is-inside-work-tree") == "true"
    except (GitError, FileNotFoundError):
        return False


def head_ref(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD")


def is_dirty(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain"))


def commit_all(root: Path, message: str) -> str | None:
    """Commit the whole tree. Returns the new sha, or None when there was nothing to do."""
    if not is_dirty(root):
        return None
    git(root, "add", "-A")
    # No hooks and no signing: a checkpoint must not fail because the host repo runs a
    # linter on commit. This commit is scaffolding, not a contribution.
    git(root, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "--no-gpg-sign", "-m", message)
    return head_ref(root)


# ─── the ledger ──────────────────────────────────────────────────────────────


def load(root: Path) -> dict | None:
    return plan_mod.load_plan(root)


def save(root: Path, plan: dict) -> Path:
    return plan_mod.write_plan(root, plan)


def step_of(plan: dict, step_id: str) -> dict | None:
    for step in plan["steps"]:
        if step["id"] == step_id:
            return step
    return None


def refresh(plan: dict) -> None:
    """Recompute the derived tail of the ledger after a status changed.

    The same helpers the plan phase uses, so the file stays self-consistent between
    regenerations rather than drifting into a second, subtly different format.
    """
    plan["summary"] = plan_mod.summarize(plan["steps"])
    plan["next"] = plan_mod.next_step(plan["steps"])
    # `refusals` is rebuilt from the steps rather than appended to, so a refusal taken
    # here shows up in `--status` and in MIGRATION-REPORT.md without waiting for a
    # regeneration. Discovery's own entries are left exactly as they were.
    plan["refusals"] = [r for r in plan.get("refusals", []) if r.get("at") != "rewrite"] + [
        {
            "subject": s["subject"],
            "kind": "shared" if s["kind"] in ("hoist", "extract") else "feature",
            "at": "rewrite",
            "step": s["id"],
            "reason": s["statusReason"],
            "evidence": s.get("evidence", []),
            "priorStatus": s.get("refusal", {}).get("priorStatus", "pending"),
        }
        for s in plan["steps"]
        if s.get("refusedAt") == "rewrite"
    ]


def set_progress(plan: dict, step_id: str, status: str, note: str = "") -> None:
    entry: dict = {"status": status, "at": now()}
    if note:
        entry["note"] = note
    plan.setdefault("progress", {})[step_id] = entry
    step = step_of(plan, step_id)
    if step:
        step["status"] = status
        step["statusSource"] = "ledger"
        if note:
            step["statusReason"] = note


# ─── verification ────────────────────────────────────────────────────────────


def verify_step(root: Path, step: dict, plan: dict | None = None) -> tuple[bool, list[str]]:
    """Is this step actually finished? Returns (ok, human-readable lines).

    Static only — no Gradle. A migration that compiles is step 9's exit criterion and
    the user's `./gradlew` to run; what belongs here is the check that decides whether
    a feature may be promoted, and that is the same checker `/review-feature` and CI
    consume. Deriving it a second way is how a migration and a CI run come to disagree.
    """
    kind, subject = step["kind"], step["subject"]

    if kind == "report":
        # The closing step's whole content is its outputs, so its verification is that
        # they exist. Two of them: the report itself, and a promotion consistent with
        # the ledger. A feature marked done but not in `managedFeatures` means the only
        # thing that could have blocked promotion happened — the checker still finds
        # work, i.e. `done` was forced. Saying so here is what stops a run signing
        # itself off as finished while claiming a migration that did not conform.
        problems = []
        if not (root / REPORT_REL).is_file():
            problems.append(f"{REPORT_REL} has not been written — run the integrate phase")
        managed = check.resolve_managed_features(root)
        if plan and managed is not None:
            unpromoted = sorted(
                s["detail"]["feature"]
                for s in plan["steps"]
                if s["kind"] == "migrate"
                and s["status"] == "done"
                and s["detail"]["feature"] not in managed
            )
            if unpromoted:
                problems.append(
                    f"done but not promoted: {', '.join(unpromoted)} — promotion re-runs the "
                    "checker, so a feature it refused is one whose completion was forced"
                )
        return (not problems), (problems or [f"{REPORT_REL} written; every done feature promoted"])

    if kind == "migrate":
        feature = step["detail"]["feature"]
        if not (root / "feature" / feature).is_dir():
            return False, [f"feature/{feature}/ does not exist — the relocate step has not run"]
        violations, _ = check.run(root, [feature])
        # The bar is the work, not the row count. An advisory finding has no fix, so
        # holding the step until it clears is a step that can never complete — and the
        # forced completion that follows is what promotion then refuses, leaving the run
        # unable to close. They are still printed: silently ignoring the checker's own
        # advice is the opposite failure.
        work = check.actionable(violations)
        advice = [
            f"  {v['rule']:<6} {v['file']}:{v['line']}  advisory: {v['message']}"
            for v in violations
            if v.get("advisory")
        ]
        if not work:
            return True, [f"feature/{feature}: 0 actionable findings"] + advice
        lines = [f"feature/{feature}: {len(work)} finding(s) remain"]
        lines += [f"  {v['rule']:<6} {v['file']}:{v['line']}  {v['message']}" for v in work[:20]]
        if len(work) > 20:
            lines.append(f"  … and {len(work) - 20} more")
        return False, lines + advice

    if kind == "relocate":
        src, dst = step["detail"]["from"], step["detail"]["to"]
        problems = []
        if not (root / dst).is_dir():
            problems.append(f"{dst}/ does not exist")
        if (root / src).is_dir():
            problems.append(f"{src}/ is still there — the move left the old module behind")
        if f'include(":{subject.lstrip(":")}")' in (root / SETTINGS_GRADLE).read_text(
            encoding="utf-8", errors="replace"
        ):
            problems.append(f"settings.gradle.kts still includes the old path {subject}")
        return (not problems), (problems or [f"{src}/ → {dst}/"])

    if kind in ("hoist", "extract"):
        # Done means discovery no longer sees it as shared code needing a home. This is
        # the one command that may re-read the repo, and it reads only — the step list
        # is untouched, so no confirmation can lapse from it.
        import kmpilot_discover as discover_mod

        report = discover_mod.discover(root)
        if kind == "hoist":
            still = [row for row in report["shared"] if row["gradlePath"] == subject]
            if still:
                return False, [f"{subject} is still a shared module outside :core:* — not hoisted"]
            return True, [f"{subject} no longer appears as unhoisted shared code"]
        still = [row for row in report["inFeatureShared"] if row["owner"] == subject]
        if still:
            consumers = sorted({row["consumer"] for row in still})
            return False, [
                f"{subject} still exports code to {', '.join(consumers)} — the cross-feature "
                "edge is not removed"
            ]
        return True, [f"nothing reaches into {subject} any more"]

    return True, [f"{kind} step — nothing to verify statically"]


# ─── commands ────────────────────────────────────────────────────────────────


def cmd_begin(root: Path, plan: dict, args, color: Palette) -> int:
    if plan["planStatus"] != "confirmed":
        print(f"error: the plan is {plan['planStatus']}, not confirmed. Nothing is rewritten "
              "before the user has approved the plan — review it and run "
              "`kmpilot_plan.py --confirm`.", file=sys.stderr)
        return 1
    if not is_repo(root):
        print(f"error: {root} is not a git repository. Migration rewrites working source and "
              "its undo is `git switch -`, so it will not start without one. Run `git init` "
              "and commit first.", file=sys.stderr)
        return 2

    migration = plan.get("migration")
    branch = BRANCH_PREFIX + slugify(plan["project"]["rootProjectName"])
    if migration and current_branch(root) == migration["branch"]:
        print(f"{color.bold}already begun{color.off} — on {migration['branch']}, "
              f"checkpoint {migration['checkpointRef'][:9]}")
        print(f"resume at {plan['next'] or 'nothing left'}")
        return 0
    if migration:
        print(f"error: this migration already began on {migration['branch']}, but the repo is on "
              f"{current_branch(root)}. Switch back (`git switch {migration['branch']}`) rather "
              "than starting a second one over the same plan.", file=sys.stderr)
        return 1

    base_branch, base_ref = current_branch(root), head_ref(root)
    if git(root, "rev-parse", "--verify", "--quiet", branch, check_rc=False):
        print(f"error: branch {branch} already exists but this plan has no migration recorded. "
              "Delete it or start from a plan generated on that branch.", file=sys.stderr)
        return 1

    git(root, "switch", "-c", branch)
    checkpoint = commit_all(root, "checkpoint before migration") or base_ref
    plan["migration"] = {
        "branch": branch,
        "baseBranch": base_branch,
        "baseRef": base_ref,
        "checkpointRef": checkpoint,
        "startedAt": now(),
    }
    save(root, plan)
    # The ledger has to be committed too, or the documented undo does not work: with
    # migration-plan.json modified and absent from the base branch, `git switch -`
    # refuses. One extra commit at the start buys an undo that actually runs.
    commit_all(root, "record the migration ledger")

    print(f"{color.bold}migration begun{color.off} on {color.bold}{branch}{color.off}")
    print(f"  checkpoint {checkpoint[:9]}")
    print(f"  undo everything: {color.bold}git switch -{color.off}  "
          f"{color.dim}(back to {base_branch}){color.off}")
    if checkpoint != base_ref:
        # The tree was dirty. `git switch -` restores the pre-migration *committed*
        # state; work that was never committed lives in the checkpoint and has to be
        # asked for by name, so say how rather than leaving the user to work it out.
        print(f"  {color.warning}the tree was dirty{color.off} — that work is inside "
              f"{checkpoint[:9]}, not on {base_branch}.")
        print(f"    get it back after undoing: git restore --source={checkpoint[:9]} -- .")
    print(f"  next: {plan['next'] or 'nothing left'}")
    return 0


def cmd_next(root: Path, plan: dict, args, color: Palette) -> int:
    step_id = plan["next"]
    if not step_id:
        print("nothing left — every step is done, refused, blocked or skipped.")
        return 0
    step = step_of(plan, step_id)
    print(f"{color.bold}{step['id']}{color.off}  {step['kind']}  {step['subject']}  "
          f"{color.dim}{step['status']}{color.off}")
    print(f"  {step['title']}")
    # A feature that has not reached feature/ yet was ungradable when the plan was
    # built, so its work list is empty — and an empty work list reads as "nothing to
    # do" rather than "not known yet". The plan already records why; printing it here
    # is what stops the step being opened, worked as if finished, and then refused by
    # `verify` for findings nobody was shown.
    if step["detail"].get("gradableNote"):
        print(f"  {color.warning}{step['detail']['gradableNote']}{color.off}")
    for rewrite in step["detail"].get("passes", []):
        print(f"  pass {rewrite['cluster']:<14} agent={rewrite['agent'] or 'unrouted':<11} "
              f"{','.join(rewrite['rules'])} ×{rewrite['findingCount']}")
        for finding in rewrite["findings"][:6]:
            print(f"       {color.dim}{finding['file']}:{finding['line']}  "
                  f"{finding['message']}{color.off}")
    return 0


def cmd_checkpoint(root: Path, plan: dict, step: dict, args, color: Palette) -> int:
    if not plan.get("migration"):
        print("error: run `begin` first — a step is not opened without a checkpoint to "
              "restore it to.", file=sys.stderr)
        return 1
    if step["status"] not in ("pending", "in-progress"):
        print(f"error: {step['id']} is {step['status']} — only pending or in-progress work is "
              "opened.", file=sys.stderr)
        return 1

    # The order is the safety property, not a suggestion: nothing that consumes shared
    # code is rewritten before that code reaches :core:*. Opening a step out of order
    # rewrites a feature against imports that are about to move under it.
    waiting = [
        d for d in step["dependsOn"]
        if (step_of(plan, d) or {}).get("status") not in ("done", "skipped")
    ]
    if waiting:
        print(f"error: {step['id']} depends on {', '.join(waiting)}, which is not done yet. "
              "Do them first — the order is what keeps a feature from being rewritten against "
              "code that is about to move.", file=sys.stderr)
        return 1

    ref = commit_all(root, f"checkpoint before {step['id']}") or head_ref(root)
    plan.setdefault("checkpoints", {})[step["id"]] = {"ref": ref, "at": now()}
    set_progress(plan, step["id"], "in-progress")
    refresh(plan)
    save(root, plan)
    print(f"{color.bold}{step['id']}{color.off} open — checkpoint {ref[:9]}")
    print(f"  restore it with: kmpilot_migrate.py restore {step['id']}")
    return 0


def restore_to_checkpoint(root: Path, plan: dict, step: dict, why: str) -> list[str]:
    """Put the tree back to this step's checkpoint, keeping every commit in history."""
    checkpoint = (plan.get("checkpoints") or {}).get(step["id"])
    if not checkpoint:
        raise GitError(
            f"no checkpoint recorded for {step['id']} — it was never opened with `checkpoint`, "
            "so there is no state to restore it to"
        )
    ref = checkpoint["ref"]
    lines = []
    wip = commit_all(root, f"wip on {step['id']} before {why}")
    if wip:
        lines.append(f"work in progress committed as {wip[:9]} — nothing is discarded")
    if git(root, "rev-list", "--count", f"{ref}..HEAD") != "0":
        git(root, "revert", "--no-edit", "--no-commit", f"{ref}..HEAD")
        # The ledger is the one file that must NOT travel backwards: it records that
        # this step was opened and is about to be refused. Rolling it back with the
        # source would drop the checkpoint entry the next restore needs.
        if (root / PLAN_REL).is_file():
            git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--",
                str(PLAN_REL), check_rc=False)
        # `revert --no-commit` leaves the reverted tree staged; the commit below is the
        # restoration itself. An empty diff means the branch already matched.
        if is_dirty(root):
            git(root, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "--no-gpg-sign",
                "-m", f"restore {step['subject']} to its pre-migration state — {why}")
            lines.append(f"reverted back to {ref[:9]} in a new commit")
        else:
            git(root, "revert", "--quit", check_rc=False)
            lines.append(f"already identical to {ref[:9]} — nothing to revert")
    else:
        lines.append(f"already at {ref[:9]} — nothing to revert")
    return lines


def cmd_restore(root: Path, plan: dict, step: dict, args, color: Palette) -> int:
    try:
        lines = restore_to_checkpoint(root, plan, step, "restored")
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    set_progress(plan, step["id"], "pending", "restored to its checkpoint")
    refresh(plan)
    save(root, plan)
    commit_all(root, f"record the restore of {step['id']}")
    print(f"{color.bold}{step['id']}{color.off} restored")
    for line in lines:
        print(f"  {line}")
    return 0


def cmd_verify(root: Path, plan: dict, step: dict, args, color: Palette) -> int:
    ok, lines = verify_step(root, step, plan)
    mark = f"{color.bold}PASS{color.off}" if ok else f"{color.error}INCOMPLETE{color.off}"
    print(f"{mark}  {step['id']}  {color.dim}{step['kind']}  {step['subject']}{color.off}")
    for line in lines:
        print(f"  {line}")
    if not ok and step["kind"] == "migrate":
        print(f"  {color.dim}these are the remaining rewrite passes — the step is done at 0"
              f"{color.off}")
    return 0 if ok else 1


def cmd_complete(root: Path, plan: dict, step: dict, args, color: Palette) -> int:
    if step["status"] in plan_mod.DERIVED_STATUSES:
        print(f"error: {step['id']} is {step['status']} — that is a fact about the repo, not "
              "something completing it can overrule.", file=sys.stderr)
        return 1

    ok, lines = verify_step(root, step, plan)
    if not ok and not args.force:
        print(f"{color.error}not complete{color.off} — {step['id']} still has work:",
              file=sys.stderr)
        for line in lines:
            print(f"  {line}", file=sys.stderr)
        print("  finish the passes, or record a human sign-off with --force, or refuse it.",
              file=sys.stderr)
        return 1

    note = "verified: " + lines[0] if ok else f"completed with --force: {lines[0]}"
    set_progress(plan, step["id"], "done", note)
    refresh(plan)
    # Ledger first, then one commit carrying both. Committing before writing it leaves
    # the finished step with a dirty tree, and the next restore would revert a ledger
    # that no commit ever captured.
    save(root, plan)
    committed = commit_all(root, f"migrate {step['subject']} to KMPilot shape ({step['id']})")

    print(f"{color.bold}{step['id']} done{color.off} — {lines[0]}")
    if committed:
        print(f"  committed {committed[:9]}")
    print(f"  next: {plan['next'] or 'nothing left'}")
    return 0


def cmd_refuse(root: Path, plan: dict, step: dict, args, color: Palette) -> int:
    """Refuse a blocker found once a pass had opened the feature, and undo the pass.

    Revert first, refuse second — a refused feature is left exactly as it was found.
    Doing it in one command is the point: a refusal recorded without the restore is
    the half-migration the refusal exists to prevent.
    """
    reason = (args.reason or "").strip()
    if not reason:
        print("error: refusing needs --reason — a refusal with no reason cannot be written "
              "into MIGRATION-REPORT.md and is indistinguishable from giving up.", file=sys.stderr)
        return 2
    if step["status"] not in plan_mod.REFUSABLE_FROM:
        print(f"error: cannot refuse {step['id']} — it is {step['status']}.", file=sys.stderr)
        return 1

    prior = step["status"]
    lines = []
    if (plan.get("checkpoints") or {}).get(step["id"]):
        try:
            lines = restore_to_checkpoint(root, plan, step, "refused mid-rewrite")
        except GitError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    elif prior == "in-progress" or is_dirty(root):
        # `pending` is not proof nothing was written. `checkpoint` is skippable — nothing
        # forces it, and going straight from `next` to editing is one command away — so a
        # step can be rewritten while still `pending`, and this branch used to key on the
        # status alone and record the refusal over a modified tree. That leaves exactly the
        # half-rewritten feature the refusal exists to prevent, silently. Ask the tree, not
        # only the ledger.
        why = ("is in progress" if prior == "in-progress"
               else "has uncommitted changes")
        print(f"error: {step['id']} {why} but has no checkpoint, so it cannot be put "
              "back to how it was found. Commit or discard the work (or "
              f"`checkpoint {step['id']}` first), then refuse.", file=sys.stderr)
        return 1

    plan.setdefault("refusedAtRewrite", {})[step["id"]] = {
        "reason": reason,
        "evidence": list(args.evidence or []),
        "at": now(),
        "by": "rewrite",
        "priorStatus": prior,
    }
    (plan.get("progress") or {}).pop(step["id"], None)
    # Re-derive the status through the plan phase's own logic rather than writing
    # `refused` in by hand: one definition of what a recorded refusal means.
    step["status"] = "pending"
    step.pop("refusedAt", None)
    plan_mod.apply_rewrite_refusals(plan["steps"], {step["id"]: plan["refusedAtRewrite"][step["id"]]})
    refresh(plan)
    save(root, plan)
    commit_all(root, f"record the refusal of {step['id']}")

    print(f"{color.error}{step['id']} refused{color.off} — {reason}")
    for line in lines:
        print(f"  {line}")
    print(f"  the feature is as it was found; a refusal is a pass, not a half-migration")
    print(f"  withdraw with: kmpilot_plan.py --unrefuse {step['id']}")
    print(f"  next: {plan['next'] or 'nothing left'}")
    return 0


def cmd_status(root: Path, plan: dict, args, color: Palette) -> int:
    migration = plan.get("migration")
    p, s = plan["project"], plan["summary"]
    print(f"{color.bold}{p['rootProjectName']}{color.off} {color.dim}— {plan['planStatus']} · "
          f"{s['steps']} steps{color.off}")
    if migration:
        print(f"  branch {migration['branch']} {color.dim}(from {migration['baseBranch']} "
              f"@ {migration['baseRef'][:9]}){color.off}")
    else:
        print(f"  {color.warning}not begun{color.off} — run `begin` to cut the checkpoint branch")
    for step in plan["steps"]:
        checkpoint = (plan.get("checkpoints") or {}).get(step["id"])
        tail = f"  {color.dim}checkpoint {checkpoint['ref'][:9]}{color.off}" if checkpoint else ""
        print(f"  {plan_mod.STATUS_MARK[step['status']]} {step['status']:<12}"
              f"{color.bold}{step['id']:<26}{color.off}{color.dim}{step['kind']}{color.off}{tail}")
    print(f"\n{s['done']} done · {s['pending']} pending · {s['refused']} refused · "
          f"{s['blocked']} blocked · {s['skipped']} skipped")
    print(f"next: {plan['next'] or 'nothing left'}")
    return 0


# ─── main ────────────────────────────────────────────────────────────────────


NEEDS_STEP = ("checkpoint", "restore", "verify", "complete", "refuse")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kmpilot_migrate.py",
        description="Run the clean phase of /kmp-to-kmpilot against a confirmed plan: "
        "checkpoint branch, per-step checkpoints, restore, verify, complete, refuse.",
    )
    parser.add_argument("--root", default=None, help="repo root (default: cwd)")
    parser.add_argument(
        "command",
        choices=("begin", "next", "status", *NEEDS_STEP),
        help="begin: cut the checkpoint branch · next: the resume pointer · "
        "checkpoint/restore/verify/complete/refuse: act on one step",
    )
    parser.add_argument("step", nargs="?", help="the step id to act on")
    parser.add_argument("--reason", default=None, help="why a step was refused (required)")
    parser.add_argument(
        "--evidence", action="append", metavar="FILE:LINE", help="backing for a refusal; repeatable"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="complete a step the checker still finds work in — records the sign-off as such",
    )
    parser.add_argument("--json-only", action="store_true", help="print the ledger as JSON")
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

    color = Palette(os.environ.get("NO_COLOR") is None and sys.stdout.isatty())

    plan = load(root)
    if plan is None:
        print(f"error: no migration plan at {root / PLAN_REL}. Run /kmp-to-kmpilot — discover "
              "and plan come first, and the plan is confirmed before anything is rewritten.",
              file=sys.stderr)
        return 1

    step = None
    if args.command in NEEDS_STEP:
        if not args.step:
            print(f"error: `{args.command}` needs a step id — see `next` or `status`.",
                  file=sys.stderr)
            return 2
        step = step_of(plan, args.step)
        if step is None:
            print(f"error: no step {args.step!r} in this plan", file=sys.stderr)
            return 2
        # Every command below this line writes source or history, so the gate applies
        # to all of them, not only to `begin`.
        if plan["planStatus"] != "confirmed":
            print(f"error: the plan is {plan['planStatus']}, not confirmed — nothing is "
                  "rewritten before the user has approved it.", file=sys.stderr)
            return 1
        if not plan.get("migration"):
            print("error: this migration has not begun — run `begin` first so there is a branch "
                  "to undo and a checkpoint to restore to.", file=sys.stderr)
            return 1

    try:
        if args.command == "begin":
            return cmd_begin(root, plan, args, color)
        if args.command == "next":
            return cmd_next(root, plan, args, color)
        if args.command == "status":
            if args.json_only:
                print(json.dumps(plan, indent=2))
                return 0
            return cmd_status(root, plan, args, color)
        handler = {
            "checkpoint": cmd_checkpoint,
            "restore": cmd_restore,
            "verify": cmd_verify,
            "complete": cmd_complete,
            "refuse": cmd_refuse,
        }[args.command]
        return handler(root, plan, step, args, color)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
