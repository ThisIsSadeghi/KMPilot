#!/usr/bin/env python3
"""Self-test for `.claude/skills/_shared/kmpilot_report.py` — the integrate phase.

Runs a whole migration on the discovery fixture — confirm, begin, work a step,
finish — and asserts what the closing phase is responsible for:

  * **promotion re-runs the checker, it never believes the ledger.** `complete --force`
    exists, so a `done` step proves nothing. A feature promoted without passing turns
    the next `archTest` red on work the migration called finished, in a file the user
    did not write. This is the assertion the whole phase exists for.
  * **promotion is per feature.** One feature the checker still finds work in does not
    hold back the ones that are finished.
  * **`managedFeatures` is appended, never rewritten.** Entries that were already there
    survive in place, and a second run adds nothing — promotion is idempotent.
  * **the report is written even when the run went badly**, and names what a reader
    cannot otherwise know: refusals with their reasons, features with no test source
    set, features whose tests still reference the replaced types, missing specs.
  * **the closing step cannot be ticked with nothing written.** `verify report` fails
    with no MIGRATION-REPORT.md and fails again while a `done` feature is unpromoted —
    a run signing itself off as finished while claiming a migration that did not
    conform is the failure mode.
  * **it writes no specs.** `/audit-spec` is the one spec writer; this names the gap.

    python3 scripts/kmpilot_report_test.py

Exits 0 on success. Runs in ~10s, no network, no Gradle. Needs `git`.
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kmpilot_discover_test import Fixture  # noqa: E402
from kmpilot_migrate_test import Failures, git, init_git  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a shipped `_shared` script by path — the suite asserts against the
    generated surface, not the gitignored source it came from."""
    spec = importlib.util.spec_from_file_location(name, REPO / f".claude/skills/_shared/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_mod = _load("kmpilot_check")
REPORT = REPO / ".claude/skills/_shared/kmpilot_report.py"
MIGRATE = REPO / ".claude/skills/_shared/kmpilot_migrate.py"
PLAN = REPO / ".claude/skills/_shared/kmpilot_plan.py"
PLAN_REL = ".claude/docs/_project/migration-plan.json"


def run(script: Path, root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--root", str(root), *args],
        capture_output=True, text=True,
    )


def rep(root: Path, *args: str) -> subprocess.CompletedProcess:
    return run(REPORT, root, *args)


def mig(root: Path, *args: str) -> subprocess.CompletedProcess:
    return run(MIGRATE, root, *args)


def plan_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return run(PLAN, root, "--compact", *args)


def ledger(root: Path) -> dict:
    return json.loads((root / PLAN_REL).read_text())


def managed(root: Path) -> list[str]:
    return json.loads((root / ".kmpilot.json").read_text())["managedFeatures"]


def step_status(root: Path, step_id: str) -> str:
    return {s["id"]: s for s in ledger(root)["steps"]}.get(step_id, {}).get("status", "?")


def rule_blurb_coverage(f: "Failures") -> None:
    """Every rule the checker can emit needs a one-line "what it was" for the report.

    A rule with no entry renders as the literal string `see kmpilot_check.py` in the
    per-rule table — a report telling its reader to go read the source. S5 shipped that
    way with finding 8 and nobody noticed until S6 did the same thing two findings
    later, which is what makes this worth a check rather than a habit: the omission is
    invisible unless a migration happens to clear that exact rule.
    """
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "kc", root / ".claude/skills/_shared/kmpilot_check.py")
    kc = importlib.util.module_from_spec(spec)
    sys.modules["kc"] = kc
    spec.loader.exec_module(kc)
    spec2 = importlib.util.spec_from_file_location(
        "kr", root / ".claude/skills/_shared/kmpilot_report.py")
    kr = importlib.util.module_from_spec(spec2)
    sys.modules["kr"] = kr
    spec2.loader.exec_module(kr)

    emitted = {rule for rule, _fn in kc.FEATURE_CHECKS if rule != "I"}
    # Derived from the repo-scoped registry too, not a hand-kept `{"S3"}`. That literal
    # was why the gap this assertion exists to close stayed open on the repo-scoped side:
    # a new project-level rule could ship with no blurb and nothing would say so, which is
    # exactly how S5 and S6 shipped printing "see kmpilot_check.py" at the reader.
    emitted |= {rule for rule, _fn in kc.REPO_CHECKS}
    emitted |= {"I1", "I2", "I3", "I4"}
    missing = sorted(emitted - set(kr.RULE_WAS))
    if missing:
        f.add(f"no RULE_WAS blurb for {', '.join(missing)} — the report would print "
              "'see kmpilot_check.py' at the reader instead of saying what the rule was")


def main() -> int:  # noqa: C901 — a linear script; splitting it would hide the sequence
    f = Failures()
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        print("git is not available — this suite needs it")
        return 1
    rule_blurb_coverage(f)

    with tempfile.TemporaryDirectory() as tmp:
        fixture = Fixture(Path(tmp))
        fixture.build()
        root = fixture.root

        # Every gradable feature in the base fixture is refused, blocked or already
        # conforming, so the case this phase is *about* — a feature a run actually
        # worked on — has nothing to stand on. Two of them, because the report has to
        # say two different things: `plain` keeps a test source set the rewrite would
        # have invalidated, `bare` has none at all and is the higher behavioural risk.
        settings = (root / "settings.gradle.kts").read_text()
        fixture.w("settings.gradle.kts",
                  settings + '\ninclude(":feature:plain")\ninclude(":feature:bare")\n')
        for name in ("plain", "bare"):
            fixture.w(f"feature/{name}/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
    sourceSets { commonMain.dependencies { implementation(project(":core:model")) } }
}
""")
            # Material3 (R5) and no di/ (R8) — portable, hoistable deps, nothing
            # refusable. It cannot reach zero findings, which is the point: completing
            # it takes --force, and promotion has to refuse what --force claimed.
            fixture.kt(f"feature/{name}", name, f"{name.capitalize()}Screen.kt", """
package @PKG@.@NAME@

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun @CAP@Screen() {
    Text(text = "Hardcoded label")
}
""".replace("@NAME@", name).replace("@CAP@", name.capitalize()))
        # A feature the checker CANNOT grade at plan time, because it sits outside
        # `feature/`. Its plan-time work list is empty by construction, so the report
        # printed `? → 0` and "no rule findings were recorded" for precisely the
        # features that needed the most work. The only "before" it ever has is the
        # re-grade taken when its step is opened, after the relocate and before the
        # rewrite — which is why this shape has to be in the fixture to test at all.
        fixture.w("settings.gradle.kts",
                  (root / "settings.gradle.kts").read_text() + '\ninclude(":stray")\n')
        # No shared-code dependency, deliberately: its `migrate` step must hang on its
        # own `relocate` and nothing else, or the run cannot open it and the re-grade
        # this covers never happens.
        fixture.w("stray/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
}
""")
        fixture.kt("stray", "stray", "StrayScreen.kt", """
package @PKG@.stray

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun StrayScreen() {
    Text(text = "Hardcoded label")
}
""")

        # A test source set that outlives the rewrite and still references the types it
        # replaced. Migration does not touch tests, so naming it is the whole mitigation.
        plain_test = root / "feature/plain/src/commonTest/kotlin/PlainScreenTest.kt"
        plain_test.parent.mkdir(parents=True, exist_ok=True)
        plain_test.write_text("class PlainScreenTest\n")
        # The entry adopt or /create-feature would already have left in the manifest.
        # Promotion writes the same field, so it has to append beside this, not over it.
        fixture.w(".kmpilot.json", (root / ".kmpilot.json").read_text().replace(
            '"managedFeatures": []', '"managedFeatures": ["preexisting"]'))

        init_git(root)

        # ── the gate: reporting on a run that was never approved ────────────
        plan_cli(root)
        draft = rep(root, "promote")
        f.want(
            draft.returncode != 0 and "not confirmed" in draft.stderr,
            f"promoting against a draft plan must be refused: {draft.stderr[:200]}",
        )
        # `plan` is read-only, so it stays answerable — knowing what promotion would do
        # is a fair question to ask *before* confirming.
        preview = rep(root, "plan", "--compact")
        f.want(
            preview.returncode == 0 and "integrate  " in preview.stdout,
            f"the dry run must work on a draft plan: {preview.stderr[:200]}",
        )
        f.want(
            not (root / "MIGRATION-REPORT.md").exists(),
            "the dry run must write nothing",
        )

        plan_cli(root, "--confirm")
        unbegun = rep(root, "write")
        f.want(
            unbegun.returncode != 0 and "has not begun" in unbegun.stderr,
            f"there is no run to report on before `begin`: {unbegun.stderr[:200]}",
        )

        # ── a real, partial migration ───────────────────────────────────────
        begun = mig(root, "begin")
        f.want(begun.returncode == 0, f"begin failed: {begun.stderr[:300]}")

        # `conforming` is already at zero findings and is not in managedFeatures — the
        # feature that proves promotion is what makes strict grading real, not a tick.
        f.want(
            "conforming" not in managed(root),
            "fixture drift: `conforming` must start outside managedFeatures",
        )
        # Force completions the checker never passed. This is the whole point of the
        # phase: `done` is a claim, and promotion is where the claim is checked.
        # Their shared-code dependency has to be settled before their steps will open —
        # the order is enforced, not suggested. Skipping it here is what lets the
        # checkpoints below actually run; without it they failed silently and `--force`
        # completed steps that had never been opened.
        plan_cli(root, "--mark", "hoist-core-model=skipped")
        for name in ("plain", "bare"):
            opened_gradable = mig(root, "checkpoint", f"migrate-{name}")
            f.want(
                opened_gradable.returncode == 0,
                f"migrate-{name} must actually open: {opened_gradable.stderr[:200]}",
            )
            # The negative control for the re-grade: this feature was gradable when the
            # plan was built, so re-grading it here would silently redefine "before" as
            # "after the hoists and extracts" — not what the user confirmed.
            f.want(
                f"migrate-{name}" not in (ledger(root).get("regrades") or {}),
                f"a step the plan already graded must NOT be re-graded when opened",
            )
            forced = mig(root, "complete", f"migrate-{name}", "--force")
            f.want(
                forced.returncode == 0,
                f"--force must record a sign-off on {name}: {forced.stderr[:200]}",
            )
            f.want(
                step_status(root, f"migrate-{name}") == "done",
                f"the forced step must read done: migrate-{name} is "
                f"{step_status(root, f'migrate-{name}')}",
            )

        # ── the ungradable feature: relocate it, then open it ───────────────
        # The relocate is what makes the checker able to see it at all. Doing it for
        # real (rather than marking it done) is the point: the re-grade is taken from
        # the tree, so a fake relocate would prove nothing about the counts.
        stray_step = {s["id"]: s for s in ledger(root)["steps"]}["migrate-stray"]
        f.want(
            not stray_step["detail"]["gradable"]
            and not stray_step["detail"].get("passes"),
            "fixture drift: `stray` must be a feature the plan could NOT grade, with an "
            "empty work list — otherwise the bug this covers cannot occur",
        )
        mig(root, "checkpoint", "relocate-stray")
        (root / "feature").mkdir(exist_ok=True)
        git(root, "mv", "stray", "feature/stray")
        settings_now = root / "settings.gradle.kts"
        settings_now.write_text(
            settings_now.read_text().replace('include(":stray")', 'include(":feature:stray")')
        )
        relocated = mig(root, "complete", "relocate-stray")
        f.want(
            relocated.returncode == 0,
            f"the relocate must complete: {relocated.stdout[-300:]}{relocated.stderr[:300]}",
        )
        opened_stray = mig(root, "checkpoint", "migrate-stray")
        f.want(
            opened_stray.returncode == 0,
            f"opening the relocated feature failed: {opened_stray.stderr[:300]}",
        )
        stray_regrade = (ledger(root).get("regrades") or {}).get("migrate-stray")
        f.want(
            stray_regrade is not None and stray_regrade["findingCount"] > 0,
            "opening a step the plan could not grade must capture the findings it has now — "
            f"that is the only 'before' this feature ever has. got {stray_regrade}",
        )
        mig(root, "complete", "migrate-stray", "--force")

        # A `done` step that nobody worked on is `managedFeatures` or a feature that
        # already conformed. Reporting it as migrated claims credit for code this run
        # never touched, which makes every count in the report a little bit false.
        shapes = rep(root, "plan", "--compact")
        f.want(
            "feature  conforming  already-conforming" in shapes.stdout,
            "a feature that was already conforming must not be reported as migrated: "
            f"{shapes.stdout[:400]}",
        )
        f.want(
            "feature  plain  migrated" in shapes.stdout,
            f"…and one this run completed must be: {shapes.stdout[:400]}",
        )

        # ── promotion re-runs the checker ───────────────────────────────────
        promoted = rep(root, "promote")
        f.want(promoted.returncode == 0, f"promote failed: {promoted.stderr[:300]}")
        f.want(
            "plain" not in managed(root) and "bare" not in managed(root),
            "a feature the checker still finds work in must NOT be promoted — a forced "
            f"`done` is a claim, not a pass. managedFeatures={managed(root)}",
        )
        f.want(
            "not promoted" in promoted.stdout and "plain" in promoted.stdout,
            f"and refusing to promote it must be said out loud: {promoted.stdout[:400]}",
        )
        f.want(
            "conforming" in managed(root),
            "a feature that passes the checker must be promoted — that is what makes "
            f"strict grading real. managedFeatures={managed(root)}",
        )

        # ── append-only, and idempotent ─────────────────────────────────────
        # Seeded before this run: the entry adopt or /create-feature would have left.
        # Promotion writes the same field they do, and the back-compat contract says a
        # shipped field is not rewritten under a user — so it must still be there, and
        # still first.
        f.want(
            managed(root)[:1] == ["preexisting"],
            f"an entry that was already there must survive promotion, in place: {managed(root)}",
        )
        before = managed(root)
        again = rep(root, "promote")
        f.want(again.returncode == 0, f"a second promote must be safe: {again.stderr[:200]}")
        f.want(
            managed(root) == before,
            f"promotion must be idempotent — {managed(root)} != {before}",
        )
        manifest = json.loads((root / ".kmpilot.json").read_text())
        f.want(
            manifest.get("installMode") == "adopt" and manifest.get("appModule"),
            "the rest of the manifest must survive the edit untouched",
        )
        # The helper's own contract, exercised directly: the end-to-end path filters
        # already-promoted names out before it is called, so its dedupe and its
        # append-only order would otherwise never be reached by this suite.
        f.want(
            check_mod.append_managed_features(root, ["conforming", "preexisting"]) == [],
            "appending names that are already there must add nothing",
        )
        f.want(
            managed(root) == before,
            f"…and must leave the array untouched: {managed(root)} != {before}",
        )
        f.want(
            check_mod.append_managed_features(root, ["late"]) == ["late"]
            and managed(root) == before + ["late"],
            f"a new name is appended after what was there, never in place of it: {managed(root)}",
        )
        (root / ".kmpilot.json").write_text(
            (root / ".kmpilot.json").read_text().replace(', "late"', "")
        )
        f.want(managed(root) == before, f"test cleanup left the manifest dirty: {managed(root)}")

        # ── no managedFeatures key: the dry run must not predict an edit ────
        #
        # A manifest with no key is a project where nothing was ever graded leniently, so
        # `promote` correctly does nothing. `plan` computed its candidate list without
        # asking that question, so it announced "would promote: a, b, c" for a command
        # that promotes none of them — a dry run mispredicting the real thing, which is
        # the one job a dry run has. Seen on a real adopted repo whose features all sat
        # outside `feature/` at adopt time.
        keyed = (root / ".kmpilot.json").read_text()
        no_key = json.loads(keyed)
        no_key.pop("managedFeatures", None)
        (root / ".kmpilot.json").write_text(json.dumps(no_key, indent=2))
        keyless = rep(root, "plan")
        f.want(
            keyless.returncode == 0 and "would promote: nothing" in keyless.stdout,
            "with no managedFeatures key the dry run must promise nothing, because "
            f"`promote` does nothing: {keyless.stdout[:400]}",
        )
        f.want(
            "template" not in keyless.stdout,
            "an adopted project with no key is not a template — naming it one misdescribes "
            f"the repo the user is standing in: {keyless.stdout[:300]}",
        )
        (root / ".kmpilot.json").write_text(keyed)

        # ── the report ──────────────────────────────────────────────────────
        written = rep(root, "write")
        f.want(written.returncode == 0, f"write failed: {written.stderr[:300]}")
        body = (root / "MIGRATION-REPORT.md").read_text()
        f.want(body.startswith("# Migration report"), "the report must have a title")
        for needle, why in [
            ("## Refusals", "a report with no refusals section cannot record a refusal"),
            ("legacy", "discovery's refusals must be named"),
            ("## Behavioural risk", "untested features are the whole mitigation for tests "
                                    "being out of scope"),
            ("## What changed, per rule", "the report must say what the migration changed"),
            ("git switch -", "the undo must be in the artifact, not only in scrollback"),
        ]:
            f.want(needle in body, f"{why} (missing {needle!r})")

        # Scoped to the risk section on purpose: every feature name and its test source
        # sets also appear in the features table, so an unscoped `"bare" in body` would
        # pass with the risk analysis deleted entirely.
        risk = body.split("## Behavioural risk", 1)[-1].split("## What this run did not do", 1)[0]
        f.want(
            "bare" in risk,
            "a migrated feature with NO test source set must be named as the highest "
            f"behavioural risk in the run, in the risk section: {risk[:400]}",
        )
        f.want(
            "plain" in risk and "commonTest" in risk,
            "a test source set that outlived the rewrite still references the types it "
            f"replaced, and must be named as such: {risk[:400]}",
        )
        f.want(
            "have at least one test source set" not in risk,
            "with an untested feature present, the report must not also print the all-clear",
        )
        f.want(
            "/audit-spec" in body,
            "a migrated feature with no spec must be named and pointed at the one spec writer",
        )
        f.want(
            "`plain`" in body and "**no**" in body,
            "a feature that was not promoted must be visible as such in the report",
        )
        f.want(
            "**none**" in body and "bare" in body,
            "a migrated feature with no test source set at all must be named — it carries "
            "the most behavioural risk in the run",
        )
        # ── the relocated feature's "before" survives into the report ───────
        # Without the capture this row reads `? → 0` and the rule table below it says
        # "No rule findings were recorded for any feature in this migration" — for the
        # feature that needed the most work in the whole run.
        stray_row = next(
            (ln for ln in body.splitlines() if ln.startswith("| `stray`")), ""
        )
        f.want(
            bool(re.search(r"\|\s*[1-9]\d*\s*→", stray_row)),
            f"a relocated feature's findings-before must be a real number, not `?`: {stray_row!r}",
        )
        rules = body.split("## What changed, per rule", 1)[-1].split("##", 1)[0]
        f.want(
            "No rule findings were recorded" not in rules,
            f"…and the rule table must not claim the run changed nothing: {rules[:300]}",
        )
        # `(stray_regrade or {})` on purpose: an earlier failure must not abort the suite
        # here and hide every failure collected before it — a trap this phase has already
        # been caught by once.
        captured_rules = sorted((stray_regrade or {}).get("findings") or {})
        f.want(
            bool(captured_rules) and any(rule in rules for rule in captured_rules),
            f"the captured rules must reach the rule table: {captured_rules} "
            f"not in {rules[:300]}",
        )

        # Regenerated, not appended to: a report that accretes stale sections reads as
        # current and is worse than none.
        rep(root, "write")
        f.want(
            (root / "MIGRATION-REPORT.md").read_text().count("# Migration report") == 1,
            "the report must be regenerated in full, never appended to",
        )

        # ── the closing step is earned ──────────────────────────────────────
        # It verifies only once the report exists AND every `done` feature is promoted.
        # `messy` is done-but-forced and unpromotable, so this must still fail.
        verified = mig(root, "verify", "report")
        f.want(
            verified.returncode != 0 and "not promoted" in verified.stdout,
            f"the report step must not verify while a done feature is unpromoted: "
            f"{verified.stdout[:300]}",
        )
        finished = rep(root, "finish")
        f.want(
            finished.returncode != 0,
            "finish must not close a run whose closing step does not verify",
        )
        f.want(step_status(root, "report") != "done", "and must not have marked it done")

        forced_finish = rep(root, "finish", "--force")
        f.want(forced_finish.returncode == 0, f"--force must close it: {forced_finish.stderr[:300]}")
        f.want(step_status(root, "report") == "done", "the report step must end up done")

        # `finish` is promote → write → verify → complete, because `verify report` needs
        # the file to exist before it can pass. So the report is always rendered while
        # its own step is still open, and a summary copied from the ledger verbatim
        # reports the run that just closed as still carrying outstanding work.
        #
        # Asserted on THIS close, not a later one: by the second `finish` the step is
        # already done, the adjustment is a no-op, and the same assertion passes with
        # the fix deleted. The check is agreement with `status` rather than a fixed
        # total — this fixture has real outstanding work of its own, and a hard-coded
        # number would pass with the count off by one in the other direction.
        steps_row = next(
            (ln for ln in (root / "MIGRATION-REPORT.md").read_text().splitlines()
             if ln.startswith("| Plan steps |")),
            "",
        )
        summary = ledger(root)["summary"]
        f.want(
            f"| {summary['done']} done " in steps_row,
            f"the report must count its own step among the done ones — writing it is how "
            f"that step is done: {steps_row!r} vs ledger done={summary['done']}",
        )
        f.want(
            f"{summary['pending'] + summary['in-progress']} outstanding" in steps_row,
            f"…and must not report as outstanding a run `status` calls closed: {steps_row!r} "
            f"vs ledger pending={summary['pending']} in-progress={summary['in-progress']}",
        )
        f.want(
            "force" in {s["id"]: s for s in ledger(root)["steps"]}["report"].get(
                "statusReason", ""
            ),
            "a forced close must say so — an unverified tick that reads as verified is worse "
            "than no tick",
        )
        f.want(
            not git(root, "status", "--porcelain"),
            "finish must commit its work — the report and the manifest are part of the run",
        )
        f.want(
            "MIGRATION-REPORT.md" in git(root, "show", "--name-only", "--format=", "HEAD"),
            "and the report must be in that commit, not left untracked",
        )

        # ── no specs were invented ──────────────────────────────────────────
        f.want(
            not list((root / ".claude/docs").glob("*/spec.md")),
            "the integrate phase must write no specs — /audit-spec is the one spec writer",
        )

        # ── a clean feature promotes, and the step then verifies ────────────
        # Undo the forced claims so the run can reach a genuinely verifiable close.
        plan_cli(root, "--mark", "migrate-plain=skipped", "--mark", "migrate-bare=skipped",
                 "--mark", "migrate-stray=skipped")
        # That regeneration is the first since the relocate actually moved `stray`, so
        # discovery no longer proposes a `relocate` step and the step list changed —
        # which lapses the confirmation by design. Progress survives it; only the
        # approval does not, so re-approving is what an operator does here.
        f.want(
            ledger(root)["planStatus"] == "draft",
            "a step list that changed under a confirmed plan must lapse its confirmation",
        )
        f.want(
            "migrate-stray" in (ledger(root).get("regrades") or {}),
            "the captured re-grade must survive regeneration — it cannot be re-derived "
            "once the rewrite has removed the findings it records",
        )
        plan_cli(root, "--confirm")
        rerun = rep(root, "finish", "--force")
        f.want(rerun.returncode == 0, f"a re-run must be safe: {rerun.stderr[:300]}")
        ok = mig(root, "verify", "report")
        f.want(
            ok.returncode == 0,
            f"with nothing left claiming a migration it did not do, the closing step must "
            f"verify: {ok.stdout[:300]}",
        )
        # `finish` is promote → write → verify → complete, because `verify report` needs
        # the file to exist first. So the report is always rendered while its own step is
        # still open, and a summary copied from the ledger verbatim reports the run that
        # just closed completely as carrying outstanding work.
        # Nothing was migrated in that state, and "every migrated feature has tests" is
        # true of zero features. A vacuous all-clear in the one artifact a reviewer
        # reads is the same failure as an unverified tick that reads as verified.
        empty_risk = (root / "MIGRATION-REPORT.md").read_text().split(
            "## Behavioural risk", 1
        )[-1].split("## What this run did not do", 1)[0]
        f.want(
            "nothing to assess" in empty_risk
            and "have at least one test source set" not in empty_risk,
            f"a run that migrated nothing must say so, not print an all-clear: {empty_risk[:300]}",
        )
        # The other half of that verification, isolated: with promotion consistent, the
        # report file alone decides. A run must not be able to sign itself off having
        # written nothing down.
        (root / "MIGRATION-REPORT.md").unlink()
        gone = mig(root, "verify", "report")
        f.want(
            gone.returncode != 0 and "MIGRATION-REPORT.md" in gone.stdout,
            f"the closing step must not verify with no report written: {gone.stdout[:300]}",
        )

    if f:
        print("\nFAILURES:")
        for failure in f:
            print(f"  x {failure}")
        return 1
    print("PASS — promotion re-runs the checker, managedFeatures is append-only and idempotent, "
          "the report names refusals/untested/stale tests/missing specs, and the closing step "
          "cannot be ticked with nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
