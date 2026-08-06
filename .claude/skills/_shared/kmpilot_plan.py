#!/usr/bin/env python3
"""
kmpilot_plan.py — turn the discovery inventory into a confirmable migration plan,
and keep that plan on disk as the resume ledger.

Step 2 of `/kmp-to-kmpilot` (Phase 6, Stage B). Discovery answers *what is in this
repo*; this answers *what will be done to it, in what order, and what will not be
touched* — as an artifact a human approves **before** a single source file is
written. It is the gate the whole phase leans on, so like discovery it is a
deterministic script rather than model prose: a plan a model re-improvises on the
next invocation is not a plan, and cannot be resumed.

    python3 .claude/skills/_shared/kmpilot_plan.py --root ~/code/theirapp
    python3 .claude/skills/_shared/kmpilot_plan.py --root . --set-tier hoist-core-model=common
    python3 .claude/skills/_shared/kmpilot_plan.py --root . --confirm
    python3 .claude/skills/_shared/kmpilot_plan.py --root . --status
    python3 .claude/skills/_shared/kmpilot_plan.py --root . --mark migrate-search=done

## What it writes

Exactly one file: `.claude/docs/_project/migration-plan.json` inside the target repo,
beside `stitch-project.json` and `check-report.json`. **No source file is ever
touched here** — the rewriting phases do that, one feature at a time, against this
ledger. `--dry-run` writes nothing at all.

## Plan status, and why confirmation can lapse

A plan is `draft` until `--confirm`. Confirmation records the exact set of step ids
the user approved; if a later regeneration produces a different set — a feature
appeared, shared code moved, a refusal cleared — the plan drops back to `draft` with
a `plan-reconfirm-required` note. The alternative is a confirmed plan that quietly
grew work the user never saw, which is the failure the gate exists to prevent.

## Derived facts beat remembered ones

Each step's status is the merge of two sources. **Derived** statuses come from the
current discovery pass and always win: a refused subject stays refused, a feature in
`managedFeatures` stays done, a consumer of unhoistable shared code stays blocked.
**Ledger** statuses (`in-progress`, `done`, `skipped`) are what the rewriting phases
and the user wrote, and are preserved across regeneration — that is what makes a
long run resumable, and what keeps a promoted feature from ever being migrated twice.

## Refusing mid-rewrite

Some blockers only surface once a rewrite pass opens the feature — discovery's
classifiers read imports and structure, and cannot see everything. That refusal is
recorded with `--refuse`, and it is deliberately **not** a `--mark` status:

    python3 kmpilot_plan.py --root . --refuse migrate-legacy \\
        --reason "custom DI graph with no Koin equivalent" --evidence Wiring.kt:44

`--mark` still rejects `refused`. If it did not, the remembered status would outlive
the blocker: `merge_progress` only applies a remembered status where the derived one
is `pending`, so a `refused` written into progress could never be cleared by fixing
the source — a permanent refusal nobody can lift, which is the wrong-refusal failure
this phase exists to avoid. Discovery cannot re-derive it either; the premise is that
its classifiers missed it.

So it is remembered as an **input to derivation**, beside `decisions` — a declaration
carrying a reason and evidence, which `build_steps` turns into a `refused` status the
same way discovery's refusals are turned into one. `--unrefuse` withdraws it and the
step returns to `pending`. The status stays derived; only the input is remembered.

**Revert first, refuse second.** A refused feature must be left exactly as it was
found. The mechanism is the per-feature checkpoint commit; `--refuse` records the
status the step held so a refusal taken after work began is called out for exactly
that reason.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import textwrap
from datetime import datetime, timezone
from heapq import heappop, heappush
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kmpilot_check as check  # noqa: E402
import kmpilot_discover as discover_mod  # noqa: E402

Palette = check.Palette
SETTINGS_GRADLE = check.SETTINGS_GRADLE
REPO_ROOT = check.REPO_ROOT
TIER_LABELS = discover_mod.TIER_LABELS

PLAN_REL = Path(".claude/docs/_project/migration-plan.json")
# 2 adds `refusedAtRewrite` (mid-rewrite refusals) and an `at` field on every row of
# `refusals`. Both are additive, and a schema-1 file on disk still merges — the
# missing key reads as "none recorded" — so no minimum is enforced on load.
PLAN_SCHEMA_VERSION = 2

# `findingRows` — the per-feature work list with file:line — arrived in discovery
# schema 2. A plan built from schema 1 would silently carry empty rewrite passes.
MIN_DISCOVERY_SCHEMA = 2

# ─── Rewrite passes ──────────────────────────────────────────────────────────

# Which checker rules travel together, and which existing agent already writes that
# layer. Migration adds no agents: a cluster is a work order for `data-layer`,
# `ui-layer`, `platform` or `integrator`. Order is the order the passes run in —
# data shape first, because the UI state slots are typed by the DTOs it settles.
CLUSTERS = [
    (
        "data",
        ("R9", "R11c"),
        "data-layer",
        "interface+impl pairs returning Either<T>, ViewModel → repository directly, "
        "no data → presentation import",
    ),
    (
        "state",
        ("R3", "R11a", "R11b"),
        "ui-layer",
        "one *UiModel holding UiState<DTO> slots, updated only through setState { copy() }",
    ),
    (
        "designsystem",
        ("R5", "R13", "S4"),
        "ui-layer",
        "X-components instead of Material3; XScreen(topBar, bottomBar), never a nested Scaffold",
    ),
    (
        "strings",
        ("R12", "R12res"),
        "ui-layer",
        "composeResources/values/strings.xml, UiText on the UiModel, no English literals",
    ),
    (
        "structure",
        ("R7", "S1", "S2"),
        "ui-layer",
        "lowercase package, the Screen.kt allowlist, one composable per file under components/",
    ),
    (
        "di",
        ("R8",),
        "integrator",
        "a public top-level val {featurename}Module with its interfaces bound",
    ),
    (
        "integration",
        ("I1", "I2", "I3", "I4"),
        "integrator",
        "the 4 integration points: settings.gradle.kts, the app dependency, initKoin, the NavHost",
    ),
]
RULE_CLUSTER = {rule: name for name, rules, _agent, _goal in CLUSTERS for rule in rules}
CLUSTER_META = {name: (agent, goal) for name, _rules, agent, goal in CLUSTERS}

# A rule the map does not know about is still work; it is just work nobody has
# routed yet. Reporting it under a named cluster with no agent keeps it visible
# instead of dropping it out of the plan.
OTHER_CLUSTER = ("other", None, "unrouted rule findings — assign a layer by hand")

STEP_KIND_RANK = {"hoist": 0, "extract": 0, "relocate": 1, "migrate": 2, "report": 3}

VALID_TIERS = ("common", "data", "designsystem", "split")
MARKABLE = ("pending", "in-progress", "done", "skipped")
DERIVED_STATUSES = ("refused", "blocked")

# A rewrite-time refusal is only meaningful for work that has not finished. Refusing
# a `done` step would claim a migration that already happened did not; refusing a
# `blocked` one hides the dependency that is the actual problem.
REFUSABLE_FROM = ("pending", "in-progress")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Step ids ────────────────────────────────────────────────────────────────


def slug(gradle_path: str) -> str:
    """`:feature:search` → `search`, `:core:model` → `core-model`, `:oldscreen` → `oldscreen`.

    Ids have to be stable across regeneration — they are what carries progress from
    one session to the next — and typeable, because the user marks and re-tiers steps
    by id on the command line.
    """
    parts = [p for p in gradle_path.split(":") if p]
    if parts and parts[0] == "feature" and len(parts) > 1:
        parts = parts[1:]
    return "-".join(parts) or "root"


def unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    taken.add(f"{base}-{n}")
    return f"{base}-{n}"


# ─── Building the steps ──────────────────────────────────────────────────────


def passes_for(finding_rows: list[dict]) -> list[dict]:
    """Group a feature's checker findings into the rewrite passes that fix them."""
    grouped: dict[str, list[dict]] = {}
    for row in finding_rows:
        grouped.setdefault(RULE_CLUSTER.get(row["rule"], "other"), []).append(row)

    ordered = [name for name, *_ in CLUSTERS if name in grouped]
    if "other" in grouped:
        ordered.append("other")

    out = []
    for name in ordered:
        rows = grouped[name]
        agent, goal = CLUSTER_META.get(name, OTHER_CLUSTER[1:])
        out.append(
            {
                "cluster": name,
                "agent": agent,
                "goal": goal,
                "rules": sorted({r["rule"] for r in rows}),
                "findingCount": len(rows),
                "files": sorted({r["file"] for r in rows}),
                "findings": [
                    {"rule": r["rule"], "file": r["file"], "line": r["line"], "message": r["message"]}
                    for r in rows
                ],
            }
        )
    return out


def apply_rewrite_refusals(steps: list[dict], recorded: dict) -> list[str]:
    """Turn recorded mid-rewrite refusals into `refused` statuses. Returns the stale ids.

    Runs before the blocked pass, so a refused `hoist` / `extract` / `relocate` blocks
    its dependents through the machinery that already exists rather than a second one.

    A record only applies where the derived status is `pending`. Discovery's own
    refusal is more specific and wins; a `done` that came from `managedFeatures` says
    the feature conforms *now*, which outranks a record of an older attempt. Those
    records are reported as stale rather than silently obeyed or silently dropped.
    """
    stale: list[str] = []
    by_id = {s["id"]: s for s in steps}
    for step_id, entry in sorted(recorded.items()):
        step = by_id.get(step_id)
        if step is None or step["status"] != "pending":
            stale.append(step_id)
            continue
        step["status"] = "refused"
        step["refusedAt"] = "rewrite"
        step["statusReason"] = entry["reason"]
        step["evidence"] = list(entry.get("evidence") or [])
        step["refusal"] = {
            "reason": entry["reason"],
            "at": entry.get("at"),
            "by": entry.get("by", "rewrite"),
            "priorStatus": entry.get("priorStatus", "pending"),
        }
        # A refusal is a pass, not a smaller job — same reasoning as a discovery
        # refusal, and the reason a work list never survives one.
        if step["kind"] == "migrate":
            step["detail"]["passes"] = []
            step["detail"]["passesNote"] = (
                "no rewrite passes — this feature was refused mid-rewrite; its rule findings "
                "stay in the checker report and are reported in MIGRATION-REPORT.md"
            )
    return stale


def build_steps(
    report: dict,
    decisions: dict,
    rewrite_refusals: dict | None = None,
    stale_out: list[str] | None = None,
) -> list[dict]:
    """One step per unit of work, unordered. Statuses here are the *derived* ones."""
    steps: list[dict] = []
    taken: set[str] = set()
    refusals = {r["subject"]: r for r in report["refusals"]}
    cycle_members = {m for cycle in report["graph"]["cycles"] for m in cycle}
    hoist_ids: dict[str, str] = {}  # shared gradlePath → the step that hoists it

    def decided_tier(step_id: str, proposed: str) -> tuple[str, str]:
        override = decisions.get(step_id, {}).get("tier")
        if override and override != proposed:
            return override, "user"
        return proposed, "proposal"

    # ── shared code → :core:* ───────────────────────────────────────────────
    for row in report["shared"]:
        path = row["gradlePath"]
        step_id = unique(f"hoist-{slug(path)}", taken)
        hoist_ids[path] = step_id
        tier, decided_by = decided_tier(step_id, row["proposedTier"])
        target = TIER_LABELS.get(tier, tier)
        step = {
            "id": step_id,
            "kind": "hoist",
            "subject": path,
            "title": f"hoist {path} → {target}",
            "dependsOn": [],
            "blockedBy": [],
            # `blocked` is not a decision the user can make — discovery deliberately
            # proposes no tier for a package that cannot move, because the Rule-14 fix
            # that unblocks it usually changes which tier it lands in anyway.
            "needsDecision": tier == "split",
            "detail": {
                "dir": row["dir"],
                "package": row["package"],
                "tier": tier,
                "target": target,
                "proposedTier": row["proposedTier"],
                "decidedBy": decided_by,
                "reason": row["reason"],
                "filesByTier": row["filesByTier"],
                "consumers": row["consumers"],
                "featureConsumers": row["featureConsumers"],
            },
        }
        if not row["hoistable"]:
            refusal = refusals.get(path, {})
            step["status"] = "refused"
            step["refusedAt"] = "discovery"
            step["statusReason"] = refusal.get("reason", "shared code cannot be hoisted")
            step["evidence"] = refusal.get("evidence", [])
            step["detail"]["blocks"] = refusal.get("blocks", row["consumers"])
        else:
            step["status"] = "pending"
            step["statusReason"] = ""
        steps.append(step)

    # ── shared code living inside a feature ─────────────────────────────────
    # Discovery reports one row per (consumer, owner) edge; the work is per owner —
    # the code leaves that module once, however many features reach into it.
    by_owner: dict[str, list[dict]] = {}
    for row in report["inFeatureShared"]:
        by_owner.setdefault(row["owner"], []).append(row)
    extract_steps: dict[str, str] = {}
    for owner, rows in sorted(by_owner.items()):
        step_id = unique(f"extract-{slug(owner)}", taken)
        extract_steps[owner] = step_id
        proposed = rows[0]["proposedTier"]
        tier, decided_by = decided_tier(step_id, proposed)
        target = TIER_LABELS.get(tier, tier)
        consumers = sorted({r["consumer"] for r in rows})
        steps.append(
            {
                "id": step_id,
                "kind": "extract",
                "subject": owner,
                "title": f"extract shared code out of {owner} → {target}",
                "status": "pending",
                "statusReason": "",
                "dependsOn": [],
                "blockedBy": [],
                # `blocked` is not a decision the user can make — discovery deliberately
            # proposes no tier for a package that cannot move, because the Rule-14 fix
            # that unblocks it usually changes which tier it lands in anyway.
            "needsDecision": tier == "split",
                "detail": {
                    "owner": owner,
                    "consumers": consumers,
                    "tier": tier,
                    "target": target,
                    "proposedTier": proposed,
                    "decidedBy": decided_by,
                    "reason": rows[0]["reason"],
                    "symbols": sorted({s for r in rows for s in r["symbols"]}),
                    "packages": sorted({p for r in rows for p in r["packages"]}),
                    "declaredIn": sorted({f for r in rows for f in r["declaredIn"]}),
                    "evidence": [e for r in rows for e in r["evidence"]][:8],
                },
            }
        )

    # ── features ────────────────────────────────────────────────────────────
    migrate_ids: list[str] = []
    for feature in report["features"]:
        path = feature["gradlePath"]
        name = feature["name"]

        relocate_id = None
        if feature["location"] == "root" and feature["verdict"] not in ("conforming", "owned"):
            relocate_id = unique(f"relocate-{slug(path)}", taken)
            steps.append(
                {
                    "id": relocate_id,
                    "kind": "relocate",
                    "subject": path,
                    "title": f"move {feature['dir']}/ → feature/{name}/",
                    "status": "pending",
                    "statusReason": "",
                    "dependsOn": [],
                    "blockedBy": [],
                    "needsDecision": False,
                    "detail": {
                        "from": feature["dir"],
                        "to": f"feature/{name}",
                        "gradlePath": path,
                        "newGradlePath": f":feature:{name}",
                        "why": "kmpilot_check.py only grades feature/* — until it moves, this "
                        "feature has no rule findings and cannot be verified",
                    },
                }
            )

        step_id = unique(f"migrate-{slug(path)}", taken)
        migrate_ids.append(step_id)
        gradable = feature["location"] == "featuredir"
        # A feature depends on the code it consumes reaching :core:* — never on
        # another feature's *migration*. The cross-feature edge is what the extract
        # step removes, so hanging this on the owner's rewrite would make one refused
        # feature block every feature that happens to import it today.
        deps = [hoist_ids[c] for c in feature["consumes"] if c in hoist_ids]
        deps += [extract_steps[c] for c in feature["consumes"] if c in extract_steps]
        if relocate_id:
            deps.append(relocate_id)
        if path in extract_steps:
            deps.append(extract_steps[path])

        step = {
            "id": step_id,
            "kind": "migrate",
            "subject": path,
            "title": f"migrate {name} to zero checker findings",
            "dependsOn": sorted(set(deps)),
            "blockedBy": [],
            "needsDecision": False,
            "detail": {
                "feature": name,
                "dir": feature["dir"],
                "location": feature["location"],
                "entryPoint": feature["entryPoint"],
                "verdict": feature["verdict"],
                "gradable": gradable,
                "findingCount": feature["findingCount"],
                "findings": feature["findings"],
                "passes": passes_for(feature.get("findingRows", [])),
                "consumes": feature["consumes"],
                "promoteTo": "managedFeatures",
            },
        }
        if not gradable:
            step["detail"]["gradableNote"] = (
                "findings unknown until the module sits under feature/ — re-grade after the "
                "relocate step, then this step's work list is the checker's output"
            )

        refusal = refusals.get(path)
        if feature["verdict"] in ("conforming", "owned"):
            step["status"] = "done"
            step["statusReason"] = (
                "already in managedFeatures — never re-migrated"
                if feature["inManagedFeatures"]
                else (
                    "KMPilot wrote this feature; its findings are /modify-feature work"
                    if feature["verdict"] == "owned"
                    else "zero checker findings under feature/ — nothing to migrate"
                )
            )
        elif refusal:
            step["status"] = "refused"
            step["refusedAt"] = "discovery"
            step["statusReason"] = refusal["reason"]
            step["evidence"] = refusal.get("evidence", [])
            # A refusal is a pass, not a smaller job. Leaving the rewrite passes on a
            # refused feature reads as a work list somebody is expected to work
            # through, which is exactly the half-rewrite the refusal exists to avoid.
            step["detail"]["passes"] = []
            step["detail"]["passesNote"] = (
                "no rewrite passes — this feature is refused; its rule findings stay in the "
                "checker report and are reported in MIGRATION-REPORT.md"
            )
        else:
            step["status"] = "pending"
            step["statusReason"] = ""
        steps.append(step)

    # ── refused once a rewrite pass opened it ───────────────────────────────
    # Before the blocked pass on purpose: a hoist, extract or relocate refused here
    # blocks its dependents through the machinery that already exists.
    stale = apply_rewrite_refusals(steps, rewrite_refusals or {})
    if stale_out is not None:
        stale_out.extend(stale)

    # ── blocked: a step whose own dependency cannot be done ─────────────────
    refused_ids = {s["id"] for s in steps if s["status"] == "refused"}
    blocking_subjects = {
        s["subject"]: s["id"] for s in steps if s["status"] == "refused" and s["kind"] == "hoist"
    }
    for step in steps:
        if step["status"] != "pending":
            continue
        blocked_by = [d for d in step["dependsOn"] if d in refused_ids]
        if step["kind"] == "migrate":
            blocked_by += [
                blocking_subjects[c]
                for c in step["detail"]["consumes"]
                if c in blocking_subjects
            ]
        if blocked_by:
            step["status"] = "blocked"
            step["blockedBy"] = sorted(set(blocked_by))
            step["statusReason"] = (
                "depends on shared code that cannot be hoisted — resolve that refusal first"
            )
        elif step["subject"] in cycle_members:
            step["status"] = "blocked"
            step["blockedBy"] = []
            step["statusReason"] = (
                "in a dependency cycle — there is no order that puts its dependencies first; "
                "hoisting the shared code out is what breaks it"
            )

    # ── the closing step ────────────────────────────────────────────────────
    # It depends on the migrations that can actually finish. A refused feature never
    # completes, so hanging the report off it would leave the run with no last step
    # — and the report is precisely where refusals get written down.
    steps.append(
        {
            "id": unique("report", taken),
            "kind": "report",
            "subject": report["project"]["rootProjectName"],
            "title": "MIGRATION-REPORT.md, a spec per migrated feature, managedFeatures promotion",
            "status": "pending",
            "statusReason": "",
            "dependsOn": sorted(i for i in migrate_ids if i not in refused_ids),
            "blockedBy": [],
            "needsDecision": False,
            "detail": {
                "outputs": [
                    "MIGRATION-REPORT.md — what changed per rule, what was refused and why",
                    ".claude/docs/{feature}/spec.md — via the /audit-spec generation path",
                    ".kmpilot.json managedFeatures — appended per migrated feature",
                ],
                "riskNote": "features with no test source set carry the most behavioural risk "
                "and are named in the report; regenerate with /test-feature",
            },
        }
    )
    return steps


def order_steps(steps: list[dict], order: list[str]) -> list[dict]:
    """Sort so every step follows the steps it depends on.

    The preference order — discovery's own topological module order, then hoists and
    extracts before relocations before migrations — decides ties. Dependencies are a
    hard constraint on top of it; a step in a cycle has no position in the module
    order and sorts last, which is also where its `blocked` status wants it.
    """
    position = {node: i for i, node in enumerate(order)}
    rank = {
        s["id"]: (
            position.get(s["subject"], len(order)),
            STEP_KIND_RANK.get(s["kind"], 9),
            s["id"],
        )
        for s in steps
    }
    by_id = {s["id"]: s for s in steps}
    remaining = {s["id"]: {d for d in s["dependsOn"] if d in by_id} for s in steps}

    ready: list[tuple] = []
    for step_id, deps in remaining.items():
        if not deps:
            heappush(ready, (rank[step_id], step_id))

    out: list[dict] = []
    while ready:
        _, step_id = heappop(ready)
        out.append(by_id[step_id])
        for other, deps in remaining.items():
            if step_id in deps:
                deps.discard(step_id)
                if not deps and all(o["id"] != other for o in out):
                    heappush(ready, (rank[other], other))

    # A dependency cycle among steps cannot happen (steps only depend on earlier
    # module kinds), but a plan that silently dropped work would be worse than one
    # that ordered it oddly.
    placed = {s["id"] for s in out}
    out.extend(sorted((s for s in steps if s["id"] not in placed), key=lambda s: rank[s["id"]]))
    for i, step in enumerate(out, 1):
        step["order"] = i
    return out


# ─── The ledger ──────────────────────────────────────────────────────────────


def load_plan(root: Path) -> dict | None:
    path = root / PLAN_REL
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_plan(root: Path, plan: dict) -> Path:
    path = root / PLAN_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def apply_remembered(step: dict, entry: dict) -> None:
    """Write one remembered status onto a step — the writing only, never the decision.

    Whether a remembered status *may* be applied is a separate question with two
    different answers (`merge_progress` derives it, `--mark` is told it), and having
    them write the result two different ways is how the ledger on disk comes to
    disagree with what the next regeneration derives from it.
    """
    step["status"] = entry["status"]
    step["statusSource"] = "ledger"
    note = entry.get("note")
    if note:
        step["statusReason"] = note
    elif entry["status"] == "skipped":
        step["statusReason"] = "skipped by the user — out of scope for this migration"


def merge_progress(steps: list[dict], progress: dict) -> None:
    """Apply remembered statuses where the derived one leaves room for them.

    `refused`, `blocked` and a `done` that came from `managedFeatures` are facts about
    the repo as it is now and are re-derived every run. `in-progress`, `done` and
    `skipped` are what the rewriting phases and the user wrote, and are the reason a
    half-finished migration can be resumed instead of restarted.

    Call this on **freshly derived** steps, once. It reads `step["status"]` as the
    derived answer, so a second call over already-merged steps would see a remembered
    status where it expects a derived one and refuse to touch it.
    """
    for step in steps:
        step["statusSource"] = "derived"
        remembered = progress.get(step["id"], {}).get("status")
        if not remembered or remembered == "pending":
            continue
        if step["status"] != "pending":
            # `refused`, `blocked` and a `done` that came from managedFeatures are
            # facts about the repo as it is now. A stale ledger entry does not get
            # to overrule one — that is how a refused feature gets half-migrated.
            continue
        apply_remembered(step, progress[step["id"]])


def summarize(steps: list[dict]) -> dict:
    counts = {s: 0 for s in ("pending", "in-progress", "done", "refused", "blocked", "skipped")}
    for step in steps:
        counts[step["status"]] = counts.get(step["status"], 0) + 1
    return {
        "steps": len(steps),
        **counts,
        "featuresToMigrate": sum(
            1 for s in steps if s["kind"] == "migrate" and s["status"] in ("pending", "in-progress")
        ),
        "refusedAtRewrite": sum(1 for s in steps if s.get("refusedAt") == "rewrite"),
        "hoists": sum(1 for s in steps if s["kind"] in ("hoist", "extract")),
        "findings": sum(s["detail"].get("findingCount", 0) for s in steps if s["kind"] == "migrate"),
        "needsDecision": sum(1 for s in steps if s["needsDecision"]),
    }


def next_step(steps: list[dict]) -> str | None:
    for step in steps:
        if step["status"] == "in-progress":
            return step["id"]
    for step in steps:
        if step["status"] == "pending":
            return step["id"]
    return None


def build_plan(root: Path, report: dict, previous: dict | None) -> dict:
    previous = previous or {}
    decisions = dict(previous.get("decisions") or {})
    progress = dict(previous.get("progress") or {})
    rewrite_refusals = dict(previous.get("refusedAtRewrite") or {})

    stale_refusals: list[str] = []
    steps = order_steps(
        build_steps(report, decisions, rewrite_refusals, stale_refusals),
        report["graph"]["order"],
    )
    merge_progress(steps, progress)

    plan_notes: list[dict] = []
    step_ids = [s["id"] for s in steps]
    if stale_refusals:
        plan_notes.append(
            {
                "id": "stale-rewrite-refusal",
                "message": "a mid-rewrite refusal is recorded for "
                f"{', '.join(stale_refusals)}, but the repo says otherwise now — the step is "
                "refused by discovery, already done, or gone. The record is kept, not obeyed; "
                "withdraw it with --unrefuse if it no longer applies.",
            }
        )
    blocking_refusals = sorted(
        {
            b
            for s in steps
            if s["status"] == "blocked"
            for b in s["blockedBy"]
            if any(o["id"] == b and o.get("refusedAt") == "rewrite" for o in steps)
        }
    )
    if blocking_refusals:
        plan_notes.append(
            {
                "id": "rewrite-refusal-blocks-work",
                "message": "a refusal taken mid-rewrite now blocks other steps "
                f"({', '.join(blocking_refusals)}) — work the plan was confirmed to do will not "
                "run. Review it with the user before continuing.",
            }
        )
    plan_status = previous.get("planStatus", "draft")
    confirmed_at = previous.get("confirmedAt")
    confirmed_steps = previous.get("confirmedSteps") or []
    if plan_status == "confirmed" and confirmed_steps != step_ids:
        plan_status = "draft"
        confirmed_at = None
        plan_notes.append(
            {
                "id": "plan-reconfirm-required",
                "message": "the project changed since this plan was confirmed — the step list is "
                "no longer the one that was approved, so the plan is back to draft. Review it and "
                "confirm again.",
            }
        )

    # Decisions and progress that no longer name a step are kept rather than dropped:
    # they usually mean the subject is temporarily absent (a module renamed mid-run),
    # and silently discarding a user's decision is not recoverable.
    stale = sorted(set(decisions) - set(step_ids)) + sorted(set(progress) - set(step_ids))
    if stale:
        plan_notes.append(
            {
                "id": "stale-ledger-entries",
                "message": "the ledger holds entries for steps this plan does not contain "
                f"({', '.join(stale)}) — kept, not discarded, in case the subject reappears.",
            }
        )

    p = report["project"]
    return {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "generatedAt": now(),
        "planStatus": plan_status,
        "confirmedAt": confirmed_at,
        "confirmedSteps": confirmed_steps,
        "project": {
            "root": str(root),
            "rootProjectName": p["rootProjectName"],
            "packagePrefix": p["packagePrefix"],
            "role": p["role"],
            "appModule": p["appModule"],
            "catalogAccessor": p["catalogAccessor"],
            "managedFeatures": p["managedFeatures"],
        },
        "discovery": {
            "schemaVersion": report["schemaVersion"],
            "generatedAt": report["generatedAt"],
            "summary": report["summary"],
        },
        "decisions": decisions,
        "progress": progress,
        "refusedAtRewrite": rewrite_refusals,
        # Written by kmpilot_migrate.py, carried here untouched. The branch and the
        # per-step checkpoints are what a refusal restores to, so losing them on a
        # regeneration would strand a half-open step with no way back.
        "migration": previous.get("migration"),
        "checkpoints": dict(previous.get("checkpoints") or {}),
        "steps": steps,
        # Discovery's refusals plus the ones a rewrite pass hit, in one list, because
        # MIGRATION-REPORT.md has to name every refusal regardless of when it was found.
        "refusals": [dict(r, at="discovery") for r in report["refusals"]]
        + [
            {
                "subject": s["subject"],
                "kind": "shared" if s["kind"] in ("hoist", "extract") else "feature",
                "at": "rewrite",
                "step": s["id"],
                "reason": s["statusReason"],
                "evidence": s.get("evidence", []),
                "priorStatus": s["refusal"]["priorStatus"],
            }
            for s in steps
            if s.get("refusedAt") == "rewrite"
        ],
        "notes": report["notes"],
        "planNotes": plan_notes,
        "next": next_step(steps),
        "summary": summarize(steps),
    }


# ─── Output ──────────────────────────────────────────────────────────────────

STATUS_MARK = {
    "pending": " ",
    "in-progress": "~",
    "done": "✓",
    "refused": "✗",
    "blocked": "■",
    "skipped": "-",
}


def print_compact(plan: dict) -> None:
    """One greppable line per row — what the matrix and CI assert against."""
    s, p = plan["summary"], plan["project"]
    print(f"plan  {p['rootProjectName']}  status={plan['planStatus']}  steps={s['steps']}  "
          f"pending={s['pending']}  done={s['done']}  refused={s['refused']}  "
          f"refused-rewrite={s.get('refusedAtRewrite', 0)}  "
          f"blocked={s['blocked']}  skipped={s['skipped']}  next={plan['next'] or '-'}")
    for step in plan["steps"]:
        target = step["detail"].get("target", "")
        arrow = f"  -> {target}" if target else ""
        deps = ",".join(step["dependsOn"]) or "-"
        print(f"step  {step['order']:02d}  {step['id']}  {step['kind']}  {step['subject']}  "
              f"{step['status']}{arrow}  depends={deps}  source={step['statusSource']}")
        if step["kind"] == "migrate":
            for rewrite in step["detail"]["passes"]:
                print(f"pass  {step['id']}  {rewrite['cluster']}  agent={rewrite['agent'] or '-'}  "
                      f"rules={','.join(rewrite['rules'])}  findings={rewrite['findingCount']}")
    for step_id, decision in sorted(plan["decisions"].items()):
        print(f"decision  {step_id}  tier={decision['tier']}  at={decision.get('at', '-')}")
    for note in plan["planNotes"]:
        print(f"plannote  {note['id']}  {note['message']}")
    # Two line kinds, not one column added to one: a refusal found by reading the repo
    # and a refusal taken with a pass already open are different facts, and the second
    # is the one that needs its prior status read.
    for refusal in plan["refusals"]:
        if refusal.get("at") == "rewrite":
            print(f"refusal-rewrite  {refusal['step']}  {refusal['subject']}  "
                  f"prior={refusal['priorStatus']}  {refusal['reason']}")
        else:
            print(f"refusal  {refusal['subject']}  {refusal['kind']}  {refusal['reason']}")


def print_grouped(plan: dict, color: Palette, path: Path | None) -> None:
    width = min(shutil.get_terminal_size((100, 24)).columns, 100)

    def wrap(text: str, indent: str) -> None:
        for line in textwrap.wrap(text, width=max(width - len(indent), 40)):
            print(f"{indent}{line}")

    p, s = plan["project"], plan["summary"]
    tint = color.warning if plan["planStatus"] == "draft" else color.bold
    print(f"\n{color.bold}{p['rootProjectName']}{color.off} {color.dim}— migration plan · "
          f"{s['steps']} steps · app module {p['appModule']}{color.off}")
    print(f"{tint}{plan['planStatus'].upper()}{color.off}"
          + (f" {color.dim}confirmed {plan['confirmedAt']}{color.off}" if plan["confirmedAt"] else ""))

    print(f"\n{color.bold}PLAN{color.off}")
    for step in plan["steps"]:
        shade = {
            "refused": color.error,
            "blocked": color.error,
            "done": color.dim,
            "skipped": color.dim,
        }.get(step["status"], "")
        print(f"  {step['order']:>2}. {shade}{STATUS_MARK[step['status']]} "
              f"{step['status']:<12}{color.off}{color.bold}{step['id']:<24}{color.off}"
              f"{color.dim}{step['title']}{color.off}")
        if step["statusReason"]:
            wrap(step["statusReason"], "         ")
        if step["blockedBy"]:
            wrap("blocked by: " + ", ".join(step["blockedBy"]), "         ")
        if step["kind"] in ("hoist", "extract") and step["status"] != "refused":
            wrap(f"why {step['detail']['target']}: {step['detail']['reason']}", "         ")
            if step["needsDecision"]:
                files = step["detail"].get("filesByTier") or {}
                for tier, names in sorted(files.items()):
                    wrap(f"{tier}: {', '.join(Path(f).name for f in names)}", "           ")
        if step["kind"] == "migrate" and step["status"] in ("pending", "in-progress"):
            for rewrite in step["detail"]["passes"]:
                agent = rewrite["agent"] or "unrouted"
                wrap(f"{rewrite['cluster']} ({agent}) — {','.join(rewrite['rules'])} "
                     f"×{rewrite['findingCount']}: {rewrite['goal']}", "         ")
            if not step["detail"]["gradable"]:
                wrap(step["detail"]["gradableNote"], "         ")
        for line in step.get("evidence", [])[:3]:
            print(f"         {color.dim}{line}{color.off}")

    decisions = [s for s in plan["steps"] if s["needsDecision"]]
    if decisions:
        print(f"\n{color.bold}NEEDS A DECISION ({len(decisions)}){color.off}")
        for step in decisions:
            print(f"  {color.warning}{step['id']}{color.off} {color.dim}"
                  f"{step['detail'].get('target', '')}{color.off}")
            wrap("set it with: --set-tier " + step["id"] + "={common|data|designsystem}", "    ")

    if plan["refusals"]:
        print(f"\n{color.bold}REFUSALS ({len(plan['refusals'])}){color.off}")
        for refusal in plan["refusals"]:
            rewrite = refusal.get("at") == "rewrite"
            tag = "rewrite" if rewrite else refusal["kind"]
            print(f"  {color.error}{tag:<8}{color.off}{color.bold}"
                  f"{refusal['subject']}{color.off}")
            wrap(refusal["reason"], "    ")
            if rewrite:
                wrap(f"found once a pass had opened it ({refusal['step']}), not by discovery — "
                     f"withdraw with --unrefuse {refusal['step']}", "    ")
                if refusal["priorStatus"] == "in-progress":
                    wrap("refused with the rewrite already under way: confirm the feature was "
                         "reverted to its pre-pass state before this run continues.", "    ")

    grouped: dict[str, list[str]] = {}
    for note in plan["notes"]:
        grouped.setdefault(note["id"], []).append(note["subject"])
    if grouped or plan["planNotes"]:
        print(f"\n{color.bold}NOTES ({len(grouped) + len(plan['planNotes'])}){color.off}")
        for note in plan["planNotes"]:
            print(f"  {color.warning}{note['id']}{color.off}")
            wrap(note["message"], "    ")
        for note_id, subjects in grouped.items():
            print(f"  {color.warning}{note_id}{color.off} {color.dim}{', '.join(subjects)}{color.off}")

    print(f"\n{color.dim}{'─' * width}{color.off}")
    print(f"{s['featuresToMigrate']} feature(s) to migrate · {s['hoists']} hoist step(s) · "
          f"{s['findings']} rule finding(s) · {s['done']} done · {s['refused']} refused · "
          f"{s['blocked']} blocked · {s['skipped']} skipped")
    if path:
        print(f"{color.dim}plan: {path}{color.off}")
    if plan["planStatus"] == "draft":
        print(f"{color.bold}DRAFT — no source file has been touched.{color.off} Confirm with "
              f"`--confirm` before any rewrite begins.")
    else:
        print(f"{color.bold}CONFIRMED{color.off} — resume at {plan['next'] or 'nothing left'}")


# ─── main ────────────────────────────────────────────────────────────────────


def parse_assignments(values: list[str], what: str) -> list[tuple[str, str]]:
    out = []
    for raw in values or []:
        if "=" not in raw:
            print(f"error: --{what} expects ID=VALUE, got {raw!r}", file=sys.stderr)
            sys.exit(2)
        key, _, value = raw.partition("=")
        out.append((key.strip(), value.strip()))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kmpilot_plan.py",
        description="Turn the discovery inventory into a confirmable migration plan "
        "and keep it on disk as the resume ledger.",
    )
    parser.add_argument("--root", default=None, help="repo root to plan (default: cwd)")
    parser.add_argument(
        "--discovery",
        default=None,
        help="consume an existing discovery report instead of running the pass again "
        "— use the one the user was shown, so the plan matches the inventory",
    )
    parser.add_argument("--json-only", action="store_true", help="print the plan JSON to stdout")
    parser.add_argument("--compact", action="store_true", help="one greppable line per row")
    parser.add_argument(
        "--status", action="store_true", help="print the plan on disk; run no discovery, write nothing"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="mark the plan confirmed — refuses if the project changed since it was shown",
    )
    parser.add_argument(
        "--set-tier",
        action="append",
        metavar="STEP_ID=TIER",
        help=f"overrule a tier proposal ({'|'.join(VALID_TIERS)}); repeatable",
    )
    parser.add_argument(
        "--mark",
        action="append",
        metavar="STEP_ID=STATUS",
        help=f"record progress ({'|'.join(MARKABLE)}); repeatable",
    )
    parser.add_argument(
        "--note", default=None, help="note attached to the --mark entries written in this run"
    )
    parser.add_argument(
        "--refuse",
        action="append",
        metavar="STEP_ID",
        help="record a blocker a rewrite pass hit — needs --reason; repeatable. Revert the "
        "feature to how it was found first: a refusal is a pass, not a half-migration",
    )
    parser.add_argument(
        "--reason", default=None, help="why the --refuse steps in this run were refused (required)"
    )
    parser.add_argument(
        "--evidence",
        action="append",
        metavar="FILE:LINE",
        help="file:line backing the refusal; repeatable",
    )
    parser.add_argument(
        "--unrefuse",
        action="append",
        metavar="STEP_ID",
        help="withdraw a recorded mid-rewrite refusal; the step returns to pending. Repeatable",
    )
    parser.add_argument("--dry-run", action="store_true", help="generate and print, write nothing")
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

    tty = sys.stdout.isatty()
    color = Palette(os.environ.get("NO_COLOR") is None and tty)
    previous = load_plan(root)

    # ── --status: the ledger as it stands, no discovery, no write ───────────
    if args.status:
        if not previous:
            print(f"error: no plan at {root / PLAN_REL} — run without --status to generate one",
                  file=sys.stderr)
            return 1
        if args.json_only:
            print(json.dumps(previous, indent=2))
        elif args.compact or not tty:
            print_compact(previous)
        else:
            print_grouped(previous, color, root / PLAN_REL)
        return 0

    # ── the discovery inventory this plan is built from ─────────────────────
    if args.discovery:
        report_path = Path(args.discovery).expanduser()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read the discovery report at {report_path}: {exc}", file=sys.stderr)
            return 2
        if report.get("schemaVersion", 0) < MIN_DISCOVERY_SCHEMA:
            print(f"error: discovery report is schema {report.get('schemaVersion')}, need "
                  f"{MIN_DISCOVERY_SCHEMA}+ (it carries no per-feature work list) — regenerate it",
                  file=sys.stderr)
            return 2
        if Path(report["project"]["root"]).resolve() != root:
            print(f"error: the discovery report is for {report['project']['root']}, not {root}",
                  file=sys.stderr)
            return 2
    else:
        report = discover_mod.discover(root)

    if not report["project"]["migrationTarget"]:
        role = report["project"]["role"]
        print(f"{color.warning}no plan written{color.off} — {root.name} is `{role}`, not a "
              f"migration target.")
        for note in report["notes"]:
            if note["id"] in ("template-mode", "pipeline-source", "not-adopted"):
                print(f"  {note['message']}")
        return 1

    # ── decisions the user overruled ────────────────────────────────────────
    if args.set_tier:
        previous = previous or {}
        decisions = dict(previous.get("decisions") or {})
        valid_ids = {s["id"] for s in build_steps(report, decisions)}
        for step_id, tier in parse_assignments(args.set_tier, "set-tier"):
            if tier not in VALID_TIERS:
                print(f"error: tier {tier!r} is not one of {', '.join(VALID_TIERS)}", file=sys.stderr)
                return 2
            if step_id not in valid_ids:
                print(f"error: no step {step_id!r} in this plan", file=sys.stderr)
                return 2
            decisions[step_id] = {"tier": tier, "at": now(), "by": "user"}
        previous["decisions"] = decisions
        # A tier the user changed is not the plan they confirmed.
        previous["planStatus"] = "draft"
        previous["confirmedSteps"] = []
        previous["confirmedAt"] = None

    # ── a refusal withdrawn ─────────────────────────────────────────────────
    # First, so that refusing something in the same run is validated against the
    # statuses the withdrawal produces rather than the ones it replaced.
    if args.unrefuse:
        previous = previous or {}
        recorded = dict(previous.get("refusedAtRewrite") or {})
        for step_id in args.unrefuse:
            if step_id not in recorded:
                print(f"error: no mid-rewrite refusal is recorded for {step_id!r} — "
                      "a refusal that came from discovery is cleared by fixing the source, "
                      "not withdrawn here.", file=sys.stderr)
                return 2
            del recorded[step_id]
        previous["refusedAtRewrite"] = recorded

    # ── a blocker a rewrite pass hit ────────────────────────────────────────
    if args.refuse:
        previous = previous or {}
        recorded = dict(previous.get("refusedAtRewrite") or {})
        progress = dict(previous.get("progress") or {})
        by_id = {s["id"]: s for s in build_plan(root, report, previous)["steps"]}

        reason = (args.reason or "").strip()
        if not reason:
            print("error: --refuse needs --reason — a refusal with no reason cannot be written "
                  "into MIGRATION-REPORT.md and is indistinguishable from giving up.",
                  file=sys.stderr)
            return 2

        for step_id in args.refuse:
            step = by_id.get(step_id)
            if step is None:
                print(f"error: no step {step_id!r} in this plan", file=sys.stderr)
                return 2
            status = step["status"]
            if status not in REFUSABLE_FROM:
                hint = {
                    "refused": (
                        f"it is already refused mid-rewrite; `--unrefuse {step_id}` first if the "
                        "reason has changed"
                        if step.get("refusedAt") == "rewrite"
                        else "it is already refused by discovery — the refusal and its evidence "
                        "are in the plan already"
                    ),
                    "blocked": "it is blocked, so no pass has opened it; resolve the blocker "
                    "instead — refusing here would hide the dependency that is the real problem",
                    "done": "it is done; a finished migration is not refusable. "
                    f"`--mark {step_id}=pending` first if it genuinely has to be redone",
                    "skipped": "it is already out of scope",
                }.get(status, f"its status is {status}")
                print(f"error: cannot refuse {step_id} — {hint}.", file=sys.stderr)
                return 2
            recorded[step_id] = {
                "reason": reason,
                "evidence": list(args.evidence or []),
                "at": now(),
                "by": "rewrite",
                "priorStatus": status,
            }
            # After the revert this step is not in progress any more, and a remembered
            # `in-progress` would resurrect on --unrefuse claiming work that was undone.
            progress.pop(step_id, None)

        previous["refusedAtRewrite"] = recorded
        previous["progress"] = progress

    plan = build_plan(root, report, previous)

    # ── progress ────────────────────────────────────────────────────────────
    if args.mark:
        by_id = {s["id"]: s for s in plan["steps"]}
        for step_id, status in parse_assignments(args.mark, "mark"):
            if status == "refused":
                print(f"error: `{step_id}=refused` is not a progress entry. A remembered refusal "
                      "would outlive the blocker being fixed, and nothing could then clear it. "
                      f"Record it as a declaration instead: --refuse {step_id} --reason '…'",
                      file=sys.stderr)
                return 2
            if status not in MARKABLE:
                print(f"error: status {status!r} is not one of {', '.join(MARKABLE)}",
                      file=sys.stderr)
                return 2
            if step_id not in by_id:
                print(f"error: no step {step_id!r} in this plan", file=sys.stderr)
                return 2
            if by_id[step_id]["status"] in DERIVED_STATUSES:
                print(f"error: {step_id} is {by_id[step_id]['status']} — that is a fact about the "
                      "repo, not a ledger entry. Resolve it in the source, then regenerate.",
                      file=sys.stderr)
                return 2
            # A `done` nobody recorded is `managedFeatures` or a feature that already
            # conformed. Same reason as above: it is a fact about the repo, and the
            # ledger does not get to overrule one.
            if (
                by_id[step_id]["status"] != "pending"
                and by_id[step_id].get("statusSource") != "ledger"
            ):
                print(f"error: {step_id} is already {by_id[step_id]['status']} because the repo "
                      "says so, not because a run recorded it — there is no progress to mark.",
                      file=sys.stderr)
                return 2
            entry = {"status": status, "at": now()}
            if args.note:
                entry["note"] = args.note
            plan["progress"][step_id] = entry
            # Applied here rather than through merge_progress: these steps already carry
            # a merged status, and merge_progress reads that slot as the *derived* one —
            # it would decline to overwrite the very entry this flag just replaced,
            # leaving the file saying one thing and the next regeneration another.
            apply_remembered(by_id[step_id], entry)
        plan["summary"] = summarize(plan["steps"])
        plan["next"] = next_step(plan["steps"])

    # ── confirmation ────────────────────────────────────────────────────────
    if args.confirm:
        step_ids = [s["id"] for s in plan["steps"]]
        approved = (previous or {}).get("confirmedSteps") or []
        if previous and (previous.get("planStatus") == "confirmed") and approved == step_ids:
            plan["planStatus"] = "confirmed"
            plan["confirmedAt"] = previous.get("confirmedAt")
            plan["confirmedSteps"] = step_ids
        elif not previous:
            print(f"error: no plan at {root / PLAN_REL} — generate and review it before "
                  "confirming; confirming a plan nobody has seen is not a gate.", file=sys.stderr)
            return 1
        elif [s["id"] for s in previous.get("steps", [])] != step_ids:
            if not args.dry_run:
                write_plan(root, plan)
            print(f"{color.error}not confirmed{color.off} — the project changed since this plan "
                  "was generated, so the refreshed plan is what would run. Review it and confirm "
                  "again.", file=sys.stderr)
            return 1
        else:
            plan["planStatus"] = "confirmed"
            plan["confirmedAt"] = now()
            plan["confirmedSteps"] = step_ids

    path = None
    if not args.dry_run:
        path = write_plan(root, plan)

    if args.json_only:
        print(json.dumps(plan, indent=2))
    elif args.compact or not tty:
        print_compact(plan)
        if path:
            print(f"plan-file  {path}")
    else:
        print_grouped(plan, color, path)

    # A plan is never a build failure: a refused subject is a finding, and a blocked
    # step is work the user gets to decide about.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
