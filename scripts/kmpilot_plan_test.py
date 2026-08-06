#!/usr/bin/env python3
"""Self-test for `.claude/skills/_shared/kmpilot_plan.py`.

Reuses the discovery fixture — an *adopted*, deliberately non-conforming KMP repo
holding one of everything — and asserts what the plan phase is responsible for:

  * **every step kind and status fires**: hoist, extract, relocate, migrate, report;
    pending, done (a feature already in KMPilot shape), refused (Android-locked, no
    entry point, unhoistable shared code) and blocked (a consumer of that shared
    code, and a dependency cycle).
  * **the order is a real order** — nothing is scheduled before the code it
    consumes reaches `:core:*`, and no feature waits on another feature's rewrite:
    the cross-feature edge is what the extract step removes.
  * **a refusal carries no work list.** A refused feature with rewrite passes reads
    as a job somebody is meant to work through, which is the half-migration the
    refusal exists to prevent.
  * **the gate holds**: a fresh plan is a draft, `--confirm` needs a plan the user
    has actually seen, and confirmation lapses the moment the project changes
    underneath it.
  * **the ledger resumes rather than restarts**: a step marked done stays done across
    regeneration, a tier the user overruled survives, and neither can resurrect a
    refused step or re-migrate a feature already in `managedFeatures`.
  * **it writes exactly one file** — `.claude/docs/_project/migration-plan.json` —
    and `--dry-run` / `--status` write nothing at all. No source file is ever touched
    by the plan phase.

    python3 scripts/kmpilot_plan_test.py

Exits 0 on success. Runs in ~2s, no network, no Gradle.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # importing a sibling must not litter scripts/ with __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kmpilot_discover_test import Fixture, file_snapshot  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / ".claude/skills/_shared/kmpilot_plan.py"
PLAN_REL = ".claude/docs/_project/migration-plan.json"


class Failures(list):
    def want(self, condition: bool, message: str) -> None:
        if not condition:
            self.append(message)


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PLAN), "--root", str(root), *args],
        capture_output=True,
        text=True,
    )


# A run that exits non-zero prints no JSON. Handing back a shaped empty plan makes
# that surface as failing assertions; handing back `{}` makes the next lookup raise,
# which aborts the suite and hides every failure collected before it.
EMPTY_PLAN: dict = {
    "steps": [], "planNotes": [], "refusals": [], "notes": [],
    "progress": {}, "decisions": {}, "refusedAtRewrite": {},
    "planStatus": None, "confirmedSteps": [], "confirmedAt": None,
    "next": None, "summary": {},
}


def plan_json(root: Path, *args: str) -> tuple[dict, subprocess.CompletedProcess]:
    proc = run(root, "--json-only", *args)
    try:
        return json.loads(proc.stdout), proc
    except json.JSONDecodeError:
        return json.loads(json.dumps(EMPTY_PLAN)), proc


def main() -> int:
    f = Failures()
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Fixture(Path(tmp))
        fixture.build()
        root = fixture.root

        # ── generation, and the one file it is allowed to write ─────────────
        before = file_snapshot(root)
        report, proc = plan_json(root)
        after = file_snapshot(root)
        if not report:
            print(f"plan exited {proc.returncode} without JSON:\n{proc.stdout[:1500]}\n{proc.stderr}")
            return 1

        written = sorted(set(after) - set(before))
        changed = sorted(k for k in before if k in after and before[k] != after[k])
        f.want(written == [PLAN_REL], f"the plan phase wrote {written}, expected only [{PLAN_REL!r}]")
        f.want(not changed, f"the plan phase modified existing files: {changed}")
        f.want(
            (root / PLAN_REL).is_file() and json.loads((root / PLAN_REL).read_text()),
            "the ledger on disk is missing or not valid JSON",
        )

        steps = {s["id"]: s for s in report["steps"]}
        order = [s["id"] for s in report["steps"]]
        position = {step_id: i for i, step_id in enumerate(order)}

        # ── every step kind fires ───────────────────────────────────────────
        kinds = {s["kind"] for s in report["steps"]}
        f.want(
            kinds == {"hoist", "extract", "relocate", "migrate", "report"},
            f"step kinds {sorted(kinds)} — every kind must fire on this fixture",
        )
        for step_id in (
            "hoist-core-model",
            "hoist-core-netcall",
            "hoist-core-widgets",
            "hoist-core-mixed",
            "hoist-core-androidutil",
            "extract-conforming",
            "relocate-oldscreen",
            "migrate-messy",
            "migrate-oldscreen",
            "report",
        ):
            f.want(step_id in steps, f"no step {step_id!r} in the plan: {order}")

        # ── statuses, all five of them ──────────────────────────────────────
        f.want(
            steps.get("migrate-legacy", {}).get("status") == "refused",
            f"the Android-locked feature is {steps.get('migrate-legacy', {}).get('status')!r}, "
            "expected refused",
        )
        f.want(
            steps.get("migrate-headless", {}).get("status") == "refused",
            "the feature with no entry point must be refused",
        )
        f.want(
            steps.get("hoist-core-androidutil", {}).get("status") == "refused",
            "unhoistable shared code must be refused",
        )
        f.want(
            steps.get("migrate-conforming", {}).get("status") == "done",
            f"a feature already in KMPilot shape is {steps.get('migrate-conforming', {}).get('status')!r}, "
            "expected done — migration must never rewrite it",
        )
        f.want(
            steps.get("migrate-messy", {}).get("status") == "blocked"
            and "hoist-core-androidutil" in steps.get("migrate-messy", {}).get("blockedBy", []),
            "a feature consuming unhoistable shared code must be blocked, naming what blocks it: "
            f"{steps.get('migrate-messy', {}).get('status')!r} "
            f"{steps.get('migrate-messy', {}).get('blockedBy')}",
        )
        cycle_steps = [steps.get("migrate-cyclea", {}), steps.get("migrate-cycleb", {})]
        f.want(
            all(s.get("status") == "blocked" for s in cycle_steps),
            f"cycle members must be blocked, not scheduled: {[s.get('status') for s in cycle_steps]}",
        )

        # ── a refusal is a pass, not a smaller job ──────────────────────────
        for refused in ("migrate-legacy", "migrate-headless"):
            f.want(
                steps.get(refused, {}).get("detail", {}).get("passes") == [],
                f"{refused} is refused but still carries rewrite passes",
            )
        f.want(
            bool(steps.get("migrate-legacy", {}).get("evidence")),
            "a refused step must carry the file:line evidence for its refusal",
        )

        # ── the work list, clustered and routed to existing agents ──────────
        messy = steps.get("migrate-messy", {}).get("detail", {})
        passes = messy.get("passes", [])
        f.want(bool(passes), "the non-conforming feature has no rewrite passes")
        agents = {p["agent"] for p in passes}
        f.want(
            agents <= {"data-layer", "ui-layer", "platform", "integrator"},
            f"a pass is routed to an agent that does not exist: {agents}",
        )
        f.want(
            sum(p["findingCount"] for p in passes) == messy.get("findingCount"),
            f"the passes cover {sum(p['findingCount'] for p in passes)} findings, the feature has "
            f"{messy.get('findingCount')} — every finding must land in exactly one pass",
        )
        f.want(
            all(p["findings"] and p["files"] for p in passes),
            "a pass must carry the file:line findings it fixes, not just a count",
        )
        f.want(
            not any(p["cluster"] == "other" for p in passes),
            f"a rule reached the plan unrouted: {[p['rules'] for p in passes if p['cluster'] == 'other']}",
        )

        # ── order ───────────────────────────────────────────────────────────
        for dependency, consumer in (
            ("hoist-core-model", "migrate-messy"),
            ("hoist-core-netcall", "migrate-messy"),
            ("extract-conforming", "migrate-messy"),
            ("relocate-oldscreen", "migrate-oldscreen"),
            ("hoist-core-model", "migrate-oldscreen"),
        ):
            f.want(
                dependency in position
                and consumer in position
                and position[dependency] < position[consumer],
                f"{dependency} must come before {consumer}: {order}",
            )
            f.want(
                dependency in steps.get(consumer, {}).get("dependsOn", []),
                f"{consumer} does not declare {dependency} as a dependency: "
                f"{steps.get(consumer, {}).get('dependsOn')}",
            )
        f.want(order[-1] == "report", f"the report step must be last: {order}")
        f.want(
            all(
                not any(d.startswith("migrate-") for d in steps[s]["dependsOn"])
                for s in steps
                if steps[s]["kind"] == "migrate"
            ),
            "a feature must never wait on another feature's migration — the cross-feature edge "
            "is what the extract step removes",
        )
        f.want(
            "migrate-legacy" not in steps["report"]["dependsOn"],
            "the closing step must not wait on a refused feature — it never completes",
        )
        f.want(
            report["next"] == order[0]
            or steps[report["next"]]["status"] in ("pending", "in-progress"),
            f"next points at {report['next']!r}, which is not the first actionable step",
        )

        # ── the gate: draft until confirmed ─────────────────────────────────
        f.want(report["planStatus"] == "draft", "a freshly generated plan must be a draft")
        f.want(report["confirmedAt"] is None, "an unconfirmed plan must have no confirmedAt")

        confirmed, _ = plan_json(root, "--confirm")
        f.want(confirmed.get("planStatus") == "confirmed", "--confirm did not confirm the plan")
        f.want(
            confirmed.get("confirmedSteps") == order,
            "confirmation must record the exact step list the user approved",
        )

        # ── resume: progress and decisions survive regeneration ─────────────
        marked, _ = plan_json(root, "--mark", "hoist-core-model=done", "--note", "hoisted by hand")
        f.want(
            marked["steps"][position["hoist-core-model"]]["status"] == "done",
            "--mark did not record progress",
        )
        again, _ = plan_json(root)
        by_id = {s["id"]: s for s in again["steps"]}
        f.want(
            by_id["hoist-core-model"]["status"] == "done"
            and by_id["hoist-core-model"]["statusSource"] == "ledger",
            "a step marked done must stay done across regeneration — that is what resume means",
        )
        f.want(
            by_id["hoist-core-model"]["statusReason"] == "hoisted by hand",
            "the note attached to a mark must survive",
        )
        f.want(
            again["next"] != "hoist-core-model",
            "a completed step must not be handed back as the next thing to do",
        )

        refuse_mark = run(root, "--mark", "migrate-legacy=done", "--compact")
        f.want(
            refuse_mark.returncode != 0 and "refused" in refuse_mark.stderr,
            "marking a refused step done must be rejected — a refusal is a fact about the repo",
        )
        bad_status = run(root, "--mark", "hoist-core-model=finished", "--compact")
        f.want(bad_status.returncode != 0, "an unknown status must be rejected")
        bad_id = run(root, "--mark", "migrate-nosuchthing=done", "--compact")
        f.want(bad_id.returncode != 0, "marking a step that does not exist must be rejected")

        # ── decisions: the tier proposal is the user's to overrule ──────────
        f.want(
            steps.get("hoist-core-mixed", {}).get("needsDecision") is True,
            "a shared module whose files disagree must be flagged as needing a decision",
        )
        f.want(
            steps.get("hoist-core-androidutil", {}).get("needsDecision") is False,
            "an unhoistable package must NOT ask for a tier — the fix that unblocks it usually "
            "changes the tier anyway",
        )
        retiered, _ = plan_json(root, "--set-tier", "hoist-core-mixed=common")
        mixed = {s["id"]: s for s in retiered["steps"]}["hoist-core-mixed"]
        f.want(
            mixed["detail"]["tier"] == "common" and mixed["detail"]["decidedBy"] == "user",
            f"--set-tier did not take: {mixed['detail'].get('tier')} / "
            f"{mixed['detail'].get('decidedBy')}",
        )
        f.want(
            mixed["needsDecision"] is False,
            "a tier the user settled must stop asking for a decision",
        )
        f.want(
            retiered["planStatus"] == "draft",
            "changing a tier must lapse confirmation — it is not the plan that was approved",
        )
        after_retier, _ = plan_json(root)
        f.want(
            {s["id"]: s for s in after_retier["steps"]}["hoist-core-mixed"]["detail"]["tier"]
            == "common",
            "a user's tier decision must survive regeneration",
        )
        f.want(run(root, "--set-tier", "hoist-core-mixed=nowhere").returncode != 0,
               "an unknown tier must be rejected")
        f.want(run(root, "--set-tier", "nosuchstep=common").returncode != 0,
               "a tier for a step that does not exist must be rejected")

        # ── refusing once a rewrite pass has opened the feature ─────────────
        # Every gradable feature in the base fixture is refused, blocked or already
        # conforming, so the *ordinary* case — a healthy feature carrying a work list,
        # ready to be migrated — has nothing to stand on. Without one here, "a refusal
        # carries no work list" passes against a step that never had one.
        fixture.w("settings.gradle.kts",
                  (root / "settings.gradle.kts").read_text() + '\ninclude(":feature:plain")\n')
        fixture.w("feature/plain/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
    sourceSets { commonMain.dependencies { implementation(project(":core:model")) } }
}
""")
        # Material3 (R5) and no di/ (R8) — portable, hoistable deps, nothing refusable.
        fixture.kt("feature/plain", "plain", "PlainScreen.kt", """
package @PKG@.plain

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

@Composable
fun PlainScreen() {
    Text(text = "Hardcoded label")
}
""")
        seeded, _ = plan_json(root)
        seed = {s["id"]: s for s in seeded["steps"]}.get("migrate-plain", {})
        f.want(
            seed.get("status") == "pending" and seed.get("detail", {}).get("passes"),
            "the refusal fixture must be a pending feature that carries a work list, or "
            f"'a refusal carries no work list' proves nothing: {seed.get('status')}",
        )

        # The status stays *derived*; what is remembered is the declaration behind it.
        # A remembered `refused` status would outlive the blocker being fixed, and
        # nothing could then clear it — a permanent wrong refusal.
        as_progress = run(root, "--mark", "migrate-plain=refused", "--compact")
        f.want(
            as_progress.returncode != 0 and "--refuse" in as_progress.stderr,
            "`--mark ID=refused` must be rejected and must point at --refuse: "
            f"exit {as_progress.returncode} {as_progress.stderr[:200]}",
        )
        f.want(
            run(root, "--refuse", "migrate-plain", "--compact").returncode != 0,
            "a refusal with no --reason must be rejected — it cannot be written into the report",
        )

        plan_json(root, "--confirm")
        plan_json(root, "--mark", "migrate-plain=in-progress")
        refused, _ = plan_json(
            root,
            "--refuse", "migrate-plain",
            "--reason", "custom threading model with no KMP equivalent",
            "--evidence", "Sync.kt:44",
        )
        step = {s["id"]: s for s in refused["steps"]}.get("migrate-plain", {})
        f.want(step.get("status") == "refused",
               f"--refuse left the step {step.get('status')}")
        f.want(
            step.get("statusSource") == "derived" and step.get("refusedAt") == "rewrite",
            "a mid-rewrite refusal must still be a *derived* status, tagged as found at rewrite: "
            f"{step.get('statusSource')} / {step.get('refusedAt')}",
        )
        # `.get` throughout: a step that was not refused carries neither key, and an
        # exception here would abort the run and hide every assertion after it.
        f.want(
            step.get("detail", {}).get("passes") == [] and step.get("evidence") == ["Sync.kt:44"],
            "a refusal is a pass, not a smaller job: no work list, and the evidence is kept",
        )
        f.want(
            step.get("refusal", {}).get("priorStatus") == "in-progress",
            "the status the step held must be recorded — it is what says a revert was owed",
        )
        f.want(
            "migrate-plain" not in refused["progress"],
            "the in-progress entry must be dropped: after the revert no work is under way, and "
            "it would resurrect on --unrefuse claiming work that was undone",
        )
        f.want(
            any(r.get("at") == "rewrite" and r["step"] == "migrate-plain"
                for r in refused["refusals"]),
            "a mid-rewrite refusal must join refusals[] — the report names every refusal",
        )
        f.want(
            refused["planStatus"] == "confirmed",
            "a refusal must not lapse confirmation — a refusal is a pass and the run continues",
        )
        f.want(
            not any(s["status"] == "blocked" and "migrate-plain" in s["blockedBy"]
                    for s in refused["steps"]),
            "refusing one feature must never block another — features do not depend on features",
        )
        persisted, _ = plan_json(root)
        f.want(
            {s["id"]: s for s in persisted["steps"]}.get("migrate-plain", {}).get("status")
            == "refused",
            "a mid-rewrite refusal must survive regeneration — discovery cannot re-derive it",
        )

        for step_id, why in (
            ("migrate-legacy", "already refused by discovery"),
            ("migrate-cyclea", "blocked, so no pass has opened it"),
            ("hoist-core-model", "done"),
            ("migrate-plain", "already refused mid-rewrite"),
        ):
            proc = run(root, "--refuse", step_id, "--reason", "x", "--compact")
            f.want(proc.returncode != 0, f"refusing {step_id} must be rejected — it is {why}")
        f.want(
            run(root, "--refuse", "migrate-nosuchthing", "--reason", "x").returncode != 0,
            "refusing a step that does not exist must be rejected",
        )

        # A hoist refused mid-rewrite blocks its consumers through the machinery that
        # already exists, and says so loudly: the plan was confirmed to do that work.
        blocked_plan, _ = plan_json(
            root, "--refuse", "hoist-core-netcall", "--reason", "no KMP port for the codegen"
        )
        f.want(
            any(s["status"] == "blocked" and "hoist-core-netcall" in s["blockedBy"]
                for s in blocked_plan["steps"]),
            "a hoist refused mid-rewrite must block the features that consume it",
        )
        f.want(
            any(n["id"] == "rewrite-refusal-blocks-work" for n in blocked_plan["planNotes"]),
            f"blocking work the user approved must be called out: {blocked_plan['planNotes']}",
        )

        # ── a record the repo has since overruled is kept, not obeyed ───────
        # Derived facts still win. A record naming a step discovery refuses on its own
        # must not restate it as a rewrite refusal — that would relabel the reason and
        # the evidence a user is relying on, and it is how a record for a step that is
        # now `done` would half-migrate a promoted feature.
        ledger = root / PLAN_REL
        on_disk = json.loads(ledger.read_text())
        on_disk["refusedAtRewrite"]["migrate-legacy"] = {
            "reason": "a record the repo disagrees with",
            "evidence": [], "at": "2026-01-01T00:00:00Z", "by": "rewrite", "priorStatus": "pending",
        }
        ledger.write_text(json.dumps(on_disk, indent=2))
        stale_plan, _ = plan_json(root)
        legacy = {s["id"]: s for s in stale_plan["steps"]}.get("migrate-legacy", {})
        f.want(
            legacy.get("refusedAt") == "discovery"
            and "a record the repo disagrees with" not in legacy.get("statusReason", ""),
            "a stale record must not overwrite a refusal discovery derives: "
            f"{legacy.get('refusedAt')} / {legacy.get('statusReason', '')[:60]}",
        )
        f.want(
            any(n["id"] == "stale-rewrite-refusal" for n in stale_plan["planNotes"]),
            f"an overruled record must be reported, not silently dropped: "
            f"{[n['id'] for n in stale_plan['planNotes']]}",
        )
        run(root, "--unrefuse", "migrate-legacy")

        # ── withdrawing one: the step and its work list come back ───────────
        f.want(
            run(root, "--unrefuse", "migrate-legacy").returncode != 0,
            "a discovery refusal is cleared by fixing the source, not withdrawn from the ledger",
        )
        withdrawn, _ = plan_json(
            root, "--unrefuse", "migrate-plain", "--unrefuse", "hoist-core-netcall"
        )
        restored = {s["id"]: s for s in withdrawn["steps"]}.get("migrate-plain", {})
        f.want(
            restored.get("status") == "pending" and restored.get("refusedAt") is None,
            f"--unrefuse must return the step to pending: {restored.get('status')}",
        )
        f.want(
            withdrawn["refusedAtRewrite"] == {}
            and not any(r.get("at") == "rewrite" for r in withdrawn["refusals"]),
            "a withdrawn refusal must leave nothing behind — otherwise it is not revocable",
        )
        f.want(
            not any(s["status"] == "blocked" and "hoist-core-netcall" in s["blockedBy"]
                    for s in withdrawn["steps"]),
            "withdrawing a hoist refusal must unblock its consumers",
        )

        # ── --status and --dry-run write nothing ────────────────────────────
        quiet_before = file_snapshot(root)
        status = run(root, "--status", "--compact")
        run(root, "--dry-run", "--compact")
        quiet_after = file_snapshot(root)
        f.want(
            quiet_before == quiet_after,
            "--status / --dry-run wrote to the repo: "
            f"{sorted(set(quiet_after) - set(quiet_before))}",
        )
        f.want(
            status.returncode == 0 and "plan  " in status.stdout,
            f"--status did not print the ledger: {status.stdout[:300]}{status.stderr[:300]}",
        )

        # ── confirmation lapses when the project changes ────────────────────
        run(root, "--confirm", "--compact")
        fixture.w("settings.gradle.kts",
                  (root / "settings.gradle.kts").read_text() + '\ninclude(":feature:latecomer")\n')
        fixture.w("feature/latecomer/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
    sourceSets { commonMain.dependencies { implementation(project(":core:model")) } }
}
""")
        fixture.kt("feature/latecomer", "latecomer", "LatecomerScreen.kt", """
package @PKG@.latecomer

import androidx.compose.runtime.Composable

@Composable
fun LatecomerScreen() = Unit
""")
        lapsed, _ = plan_json(root)
        f.want(
            lapsed["planStatus"] == "draft",
            "a plan whose step list changed must drop back to draft — otherwise it grew work "
            "the user never approved",
        )
        f.want(
            any(n["id"] == "plan-reconfirm-required" for n in lapsed["planNotes"]),
            f"the lapse must say why: {lapsed['planNotes']}",
        )
        f.want(
            "migrate-latecomer" in {s["id"] for s in lapsed["steps"]},
            "the new feature did not reach the plan",
        )
        f.want(
            {s["id"]: s for s in lapsed["steps"]}["hoist-core-model"]["status"] == "done",
            "progress must survive a lapse — the plan is re-confirmed, not restarted",
        )

        # ── a repo that is not a migration target gets no plan ──────────────
        with tempfile.TemporaryDirectory() as other_tmp:
            other = Fixture(Path(other_tmp))
            other.build()
            manifest = other.root / ".kmpilot.json"
            manifest.write_text(
                manifest.read_text().replace('"installMode": "adopt"', '"installMode": "template"')
            )
            before_other = file_snapshot(other.root)
            proc = run(other.root, "--compact")
            f.want(
                proc.returncode == 1 and "not a migration target" in proc.stdout,
                f"a template project must be refused a plan: exit {proc.returncode} "
                f"{proc.stdout[:200]}",
            )
            f.want(
                file_snapshot(other.root) == before_other,
                "a refused project must not get a plan file written into it",
            )
            no_plan = run(other.root, "--confirm", "--compact")
            f.want(no_plan.returncode != 0, "confirming a plan that was never written must fail")

        # ── managedFeatures is never re-migrated ────────────────────────────
        manifest = root / ".kmpilot.json"
        manifest.write_text(manifest.read_text().replace('"managedFeatures": []',
                                                         '"managedFeatures": ["messy"]'))
        promoted, _ = plan_json(root)
        messy_step = {s["id"]: s for s in promoted["steps"]}["migrate-messy"]
        f.want(
            messy_step["status"] == "done" and messy_step["statusSource"] == "derived",
            f"a promoted feature must be done and stay done: {messy_step['status']} / "
            f"{messy_step['statusSource']}",
        )

    print(
        f"plan: {len(steps)} steps · "
        f"{sum(1 for s in steps.values() if s['status'] == 'refused')} refused · "
        f"{sum(1 for s in steps.values() if s['status'] == 'blocked')} blocked · "
        f"{sum(1 for s in steps.values() if s['status'] == 'done')} done"
    )
    print(f"order: {' → '.join(order)}")

    if f:
        print("\nFAILURES:")
        for failure in f:
            print(f"  x {failure}")
        return 1
    print("\nPASS — every step kind and status fires, the gate holds, the ledger resumes, "
          "and only the plan file was written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
