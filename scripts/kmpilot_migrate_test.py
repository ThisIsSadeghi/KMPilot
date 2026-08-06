#!/usr/bin/env python3
"""Self-test for `.claude/skills/_shared/kmpilot_migrate.py` — the clean phase.

Reuses the discovery fixture, puts it under git, and asserts what the execution
envelope is responsible for. The rewriting itself is the layer agents' job; what is
tested here is everything that has to be true *around* it:

  * **the gate holds everywhere.** Nothing is rewritten before the plan is confirmed —
    not by `begin`, and not by any per-step command either. A gate only `begin`
    checked would be bypassed by starting at step two.
  * **a dirty tree is absorbed, not refused.** The branch is cut, the uncommitted work
    goes into the checkpoint commit, and the run says how to get it back — `git switch -`
    restores the pre-migration *committed* state, which is not the same thing.
  * **the order is enforced.** A step whose dependencies are unfinished cannot be
    opened; rewriting a feature against code that is about to move under it is the
    failure the dependency order exists to prevent.
  * **a refusal leaves the subject exactly as it was found** — byte-identical after a
    pass that modified, added *and* deleted files, which is where a naive restore
    quietly leaks. Nothing is dropped from history: the work in progress is committed
    first and the restore is a new commit, never a `reset --hard`.
  * **completion is earned.** `complete` re-runs the checker rather than believing the
    caller, refuses while findings remain, and records a `--force` sign-off as such.
  * **it never re-runs discovery.** Completing a `relocate` changes what discovery
    would report, and a regenerated plan would drop back to `draft` on its own work —
    lapsing the confirmation the whole phase leans on. The step list must not move.

    python3 scripts/kmpilot_migrate_test.py

Exits 0 on success. Runs in ~5s, no network, no Gradle. Needs `git`.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kmpilot_discover_test import PKG, Fixture  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MIGRATE = REPO / ".claude/skills/_shared/kmpilot_migrate.py"
PLAN = REPO / ".claude/skills/_shared/kmpilot_plan.py"
PLAN_REL = ".claude/docs/_project/migration-plan.json"


class Failures(list):
    def want(self, condition: bool, message: str) -> None:
        if not condition:
            self.append(message)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    ).stdout.strip()


def mig(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MIGRATE), "--root", str(root), *args],
        capture_output=True, text=True,
    )


def plan_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PLAN), "--root", str(root), "--compact", *args],
        capture_output=True, text=True,
    )


def ledger(root: Path) -> dict:
    return json.loads((root / PLAN_REL).read_text())


def cksum(root: Path, rel: str) -> str:
    """A content fingerprint of a subtree, including which files exist."""
    base = root / rel
    return "|".join(
        f"{p.relative_to(base)}:{p.read_bytes().hex()[:32]}:{p.stat().st_size}"
        for p in sorted(base.rglob("*")) if p.is_file()
    )


def init_git(root: Path) -> None:
    git(root, "init", "--quiet", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "kmpilot test")
    git(root, "add", "-A")
    git(root, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-q", "-m", "baseline")


def main() -> int:  # noqa: C901 — a linear script; splitting it would hide the sequence
    f = Failures()
    if not subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        print("git is not available — this suite needs it")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        fixture = Fixture(Path(tmp))
        fixture.build()
        root = fixture.root

        # ── a repo with no git at all is refused, with a reason ─────────────
        plan_cli(root)
        plan_cli(root, "--confirm")
        no_git = mig(root, "begin")
        f.want(
            no_git.returncode != 0 and "git" in no_git.stderr,
            "a repo with no git must be refused — the undo is `git switch -`: "
            f"exit {no_git.returncode} {no_git.stderr[:160]}",
        )

        init_git(root)
        base_branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
        base_ref = git(root, "rev-parse", "HEAD")

        # ── the gate: a draft plan rewrites nothing ─────────────────────────
        plan_cli(root, "--set-tier", "hoist-core-mixed=common")  # lapses confirmation
        f.want(ledger(root)["planStatus"] == "draft", "the fixture setup did not lapse the plan")
        draft_begin = mig(root, "begin")
        f.want(
            draft_begin.returncode != 0 and "not confirmed" in draft_begin.stderr,
            f"`begin` on a draft plan must be refused: {draft_begin.stderr[:160]}",
        )
        # A gate only `begin` enforced would be walked around by starting at step two.
        draft_step = mig(root, "checkpoint", "hoist-core-model")
        f.want(
            draft_step.returncode != 0 and "not confirmed" in draft_step.stderr,
            f"a per-step command on a draft plan must be refused too: {draft_step.stderr[:160]}",
        )
        f.want(
            git(root, "rev-parse", "--abbrev-ref", "HEAD") == base_branch,
            "a refused run must not have cut a branch",
        )

        # ── begin, on a deliberately dirty tree ─────────────────────────────
        (root / "MY-NOTES.txt").write_text("work the user had not committed\n")
        plan_cli(root, "--confirm")
        begun = mig(root, "begin")
        f.want(begun.returncode == 0, f"begin failed: {begun.stderr[:300]}")
        migration = ledger(root).get("migration") or {}
        f.want(
            git(root, "rev-parse", "--abbrev-ref", "HEAD") == migration.get("branch")
            and migration.get("branch", "").startswith("kmpilot/migrate-"),
            f"begin did not put the repo on its own branch: {migration}",
        )
        f.want(
            migration.get("baseBranch") == base_branch and migration.get("baseRef") == base_ref,
            "the way back must be recorded — that is what makes `git switch -` the undo",
        )
        f.want(
            migration.get("checkpointRef") != base_ref
            and "MY-NOTES.txt" in git(root, "show", "--name-only",
                                     migration.get("checkpointRef", "HEAD")),
            "a dirty tree must be absorbed into the checkpoint commit, not refused",
        )
        f.want(
            "git restore --source=" in begun.stdout,
            "a dirty run must say how to get the uncommitted work back — `git switch -` "
            f"restores the committed state only: {begun.stdout[:300]}",
        )
        again = mig(root, "begin")
        f.want(
            again.returncode == 0 and "already begun" in again.stdout,
            f"begin must be idempotent, not a second branch: {again.stdout[:200]}",
        )

        # ── the order is enforced ───────────────────────────────────────────
        early = mig(root, "checkpoint", "migrate-oldscreen")
        f.want(
            early.returncode != 0 and "depends on" in early.stderr,
            "a step whose dependencies are unfinished must not be opened: "
            f"{early.stderr[:200]}",
        )
        blocked = mig(root, "checkpoint", "migrate-cyclea")
        f.want(blocked.returncode != 0, "a blocked step must not be opened")

        # ── open a step ─────────────────────────────────────────────────────
        opened = mig(root, "checkpoint", "hoist-core-model")
        f.want(opened.returncode == 0, f"checkpoint failed: {opened.stderr[:300]}")
        entry = (ledger(root).get("checkpoints") or {}).get("hoist-core-model") or {}
        f.want(bool(entry.get("ref")), "the checkpoint ref must be recorded — it is what restores")
        f.want(
            {s["id"]: s for s in ledger(root)["steps"]}.get("hoist-core-model", {}).get("status")
            == "in-progress",
            "opening a step must mark it in progress — that is where a resumed run picks up",
        )

        # ── a rewrite that modifies, adds AND deletes, then refuses ─────────
        before = cksum(root, "core/model")
        target = next((root / "core/model").rglob("*.kt"))
        target.write_text(target.read_text() + "\n// mangled by a pass\n")
        (target.parent / "AddedByPass.kt").write_text("package added\n")
        (root / "core/model/build.gradle.kts").unlink()

        no_reason = mig(root, "refuse", "hoist-core-model")
        f.want(
            no_reason.returncode != 0 and "reason" in no_reason.stderr,
            "refusing without a reason must be rejected — it cannot go into the report",
        )
        refused = mig(root, "refuse", "hoist-core-model", "--reason", "annotation-processor only",
                      "--evidence", "Note.kt:1")
        f.want(refused.returncode == 0, f"refuse failed: {refused.stderr[:300]}")
        f.want(
            cksum(root, "core/model") == before,
            "a refused subject must be left exactly as it was found — a pass that added or "
            "deleted files is where a naive restore leaks",
        )
        f.want(
            not (target.parent / "AddedByPass.kt").exists()
            and (root / "core/model/build.gradle.kts").exists(),
            "the restore must undo added and deleted files, not only modified ones",
        )
        log = git(root, "log", "--oneline")
        f.want(
            "wip on hoist-core-model" in log and "restore" in log,
            f"the work in progress must survive in history, and the restore must be a new "
            f"commit rather than a reset: {log[:300]}",
        )
        f.want(
            not git(root, "status", "--porcelain", "--", "core/model"),
            "the tree must be clean after a restore",
        )
        # The ledger is the one file that must not travel backwards with the source: it
        # records that this step was opened and is now refused. A restore commit that
        # also rolls it back reads as if the run forgot its own progress.
        restore_commit = git(root, "log", "--format=%H %s").splitlines()
        restore_sha = next(
            (line.split()[0] for line in restore_commit if line.split(" ", 1)[1].startswith("restore ")),
            "",
        )
        f.want(
            bool(restore_sha)
            and PLAN_REL not in git(root, "show", "--name-only", "--format=", restore_sha),
            "the restore commit must revert source only — not the ledger recording the refusal",
        )
        step = {s["id"]: s for s in ledger(root)["steps"]}.get("hoist-core-model", {})
        f.want(
            step.get("status") == "refused" and step.get("refusedAt") == "rewrite",
            f"the step must end up refused, tagged as found at rewrite: {step.get('status')} / "
            f"{step.get('refusedAt')}",
        )
        f.want(
            (ledger(root)["refusedAtRewrite"].get("hoist-core-model") or {}).get("priorStatus")
            == "in-progress",
            "the status the step held must be recorded — it is what says a revert was owed",
        )
        f.want(
            "hoist-core-model" not in ledger(root)["progress"],
            "a refused step must not keep an in-progress entry claiming work that was undone",
        )

        # ── verify is earned, not asserted ──────────────────────────────────
        mig(root, "checkpoint", "relocate-oldscreen")
        unfinished = mig(root, "verify", "relocate-oldscreen")
        f.want(
            unfinished.returncode != 0 and "does not exist" in unfinished.stdout,
            f"verify must fail before the work is done: {unfinished.stdout[:200]}",
        )
        premature = mig(root, "complete", "relocate-oldscreen")
        f.want(
            premature.returncode != 0,
            "complete must not believe the caller over the checker",
        )
        forced = mig(root, "complete", "relocate-oldscreen", "--force")
        f.want(forced.returncode == 0, f"--force must be able to record a sign-off: {forced.stderr[:200]}")
        f.want(
            "force" in {s["id"]: s for s in ledger(root)["steps"]}
            .get("relocate-oldscreen", {}).get("statusReason", ""),
            "a forced completion must say so — an unverified tick that reads as verified is worse "
            "than no tick",
        )

        # ── the real thing: do the work, then verify passes ─────────────────
        plan_cli(root, "--mark", "relocate-oldscreen=in-progress")
        mig(root, "checkpoint", "relocate-oldscreen")
        (root / "feature").mkdir(exist_ok=True)
        git(root, "mv", "oldscreen", "feature/oldscreen")
        settings = root / "settings.gradle.kts"
        settings.write_text(
            settings.read_text().replace('include(":oldscreen")', 'include(":feature:oldscreen")')
        )
        done = mig(root, "complete", "relocate-oldscreen")
        f.want(done.returncode == 0, f"complete failed on finished work: {done.stdout[:300]}{done.stderr[:300]}")
        f.want(
            {s["id"]: s for s in ledger(root)["steps"]}.get("relocate-oldscreen", {})
            .get("status") == "done",
            "a verified step must be recorded done",
        )
        f.want(
            not git(root, "status", "--porcelain"),
            "complete must commit its work — a finished step leaves no dirty tree",
        )

        # ── it never re-runs discovery ──────────────────────────────────────
        # The relocate just changed what discovery would report. A regeneration here
        # would drop the `relocate` step and lapse the confirmation on the run's own
        # progress, which is why the clean phase reads the ledger instead.
        after = ledger(root)
        f.want(
            "relocate-oldscreen" in {s["id"] for s in after["steps"]},
            "the step list must not move under the run — the plan the user approved is the "
            "plan that executes",
        )
        f.want(
            after["planStatus"] == "confirmed",
            f"the clean phase must not lapse its own confirmation: {after['planStatus']}",
        )

        # ── the plan phase carries the migration state across regeneration ──
        plan_cli(root)
        regenerated = ledger(root)
        f.want(
            (regenerated.get("migration") or {}).get("branch") == migration["branch"]
            and "hoist-core-model" in (regenerated.get("checkpoints") or {}),
            "regenerating must not strand a half-open run: the branch and the checkpoints are "
            "what a restore needs",
        )
        f.want(
            {s["id"]: s for s in regenerated["steps"]}.get("hoist-core-model", {})
            .get("status") == "refused",
            "the mid-rewrite refusal must survive regeneration",
        )

        # ── the undo ────────────────────────────────────────────────────────
        git(root, "add", "-A")
        git(root, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-q", "-m", "ledger")
        git(root, "switch", "-")
        f.want(
            git(root, "rev-parse", "HEAD") == base_ref
            and git(root, "rev-parse", "--abbrev-ref", "HEAD") == base_branch,
            "`git switch -` must land exactly on the pre-migration commit",
        )
        f.want(
            not git(root, "status", "--porcelain", "--", "core", "feature"),
            "the undo must leave no migration leftovers in the source tree",
        )
        f.want(
            "MY-NOTES.txt" in git(root, "show", "--name-only",
                                  migration.get("checkpointRef", "HEAD")),
            "and the user's uncommitted work must still be recoverable from the checkpoint",
        )

    # ── advisory findings are reported, but they are not work ───────────────
    # The real-repo shape this comes from: an adopted project that navigates by
    # hoisted state instead of a NavHost. I4 then reports "no NavHost in this
    # project" once per feature — the checker's own comment calls that a valid
    # architecture rather than a violation, and no edit to the feature clears it.
    # Counting it as work made every feature in the repo uncompletable: `complete`
    # refused, `--force` recorded a forced sign-off, promotion re-ran the checker
    # and refused, and the run could not close. A separate fixture because deleting
    # the NavHost is exactly the mutation the suite above must not see.
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Fixture(Path(tmp))
        fixture.build()
        root = fixture.root
        (root / f"shared/src/commonMain/kotlin/{PKG}/app/BaseAppNavHost.kt").unlink()
        init_git(root)
        plan_cli(root)
        plan_cli(root, "--confirm")
        mig(root, "begin")

        clean = mig(root, "verify", "migrate-conforming")
        f.want(
            clean.returncode == 0,
            "a feature whose only remaining finding is advisory must verify — otherwise it "
            f"can never be completed or promoted: exit {clean.returncode} {clean.stdout[:300]}",
        )
        f.want(
            "0 actionable findings" in clean.stdout,
            f"verify must count work, not rows: {clean.stdout[:300]}",
        )
        f.want(
            "advisory:" in clean.stdout and "I4" in clean.stdout,
            "an advisory finding must still be printed — silently dropping the checker's own "
            f"advice is the opposite failure: {clean.stdout[:300]}",
        )

        # NEGATIVE CONTROL. The cost of getting this wrong is a feature signed off with
        # real work outstanding, so a rule that stops blocking must be proven still to
        # block where it should. `messy` has genuine findings and the same advisory row.
        messy = mig(root, "verify", "migrate-messy")
        f.want(
            messy.returncode != 0 and "finding(s) remain" in messy.stdout,
            "advisory must not swallow real findings — a feature with actual work must still "
            f"fail verify: exit {messy.returncode} {messy.stdout[:300]}",
        )

        # And the plan must not hand an agent a work order it cannot fill.
        steps = {s["id"]: s for s in ledger(root)["steps"]}
        passes = steps["migrate-messy"]["detail"]["passes"]
        f.want(
            all("I4" not in p["rules"] for p in passes),
            f"an advisory finding must carry no rewrite pass: {[p['rules'] for p in passes]}",
        )
        f.want(
            steps["migrate-messy"]["detail"]["findingCount"] == sum(p["findingCount"] for p in passes),
            "the step's count must equal the work its passes cover — a total nothing can "
            "reach is a target that reads as failure forever",
        )

    if f:
        print("\nFAILURES:")
        for failure in f:
            print(f"  x {failure}")
        return 1
    print("PASS — the gate holds everywhere, a dirty tree is absorbed, the order is enforced, "
          "a refusal restores exactly, completion is earned, and discovery is never re-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
