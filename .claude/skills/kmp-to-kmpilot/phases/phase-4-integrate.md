# Phase 4 — Integrate

Close the run: promote what conforms, write down what happened, and say plainly what did not.

This is the only record the migration leaves. Everything before it is contained by a checkpoint commit and undone by `git switch -`; this phase is what a reviewer reads six weeks later, and what turns a rewritten feature into one the build actually enforces.

```bash
python3 .claude/skills/_shared/kmpilot_report.py --root {repo} <command>
```

| Command | What it does |
|---|---|
| `plan` | what promotion and the report would say. Writes nothing, works on a draft |
| `promote` | re-verify each finished feature, then append it to `.kmpilot.json` |
| `write` | `MIGRATION-REPORT.md` |
| `finish` | promote → write → verify the closing step → mark it done → commit |

`plan` is the only one that runs on an unconfirmed or unbegun migration — knowing what promotion would do is a fair question to ask *before* approving the plan that leads to it. The other three are behind the same gate as the clean phase.

## Order: specs first, then `finish`

```
/audit-spec {feature}   (per migrated feature)
        ↓
kmpilot_report.py finish
```

`finish` names the features with no spec but does not block on them — refusing to write the report because a document is missing would withhold the very artifact that records the gap. Generate them first anyway, so the report goes out saying nothing is missing.

**Do not write specs here.** `/audit-spec` is the one spec-generation path in the pipeline; a second one is how two of them come to disagree about what a spec is.

## Promotion re-runs the checker; it never believes the ledger

`complete --force` exists, so a `done` step is a claim, not a pass. Promotion is the edit that flips a feature from *reported* to *enforced* — the moment `archTest` starts failing the build on it — so a feature promoted without passing turns the next CI run red on work the migration called finished, in a file the user did not write.

Every candidate is therefore re-verified through the same function `complete` uses. A feature promoted on a different bar than the one that completed it is a migration disagreeing with itself.

- **Per feature, not all-or-nothing.** One feature the checker still finds work in does not hold back the ones that are done.
- **Append-only.** Entries adopt or `/create-feature` already wrote keep their place; a shipped field is not rewritten under a user. Running it twice adds nothing.
- **No `managedFeatures` key at all** means a template project, where every feature is already graded strictly. That is nothing to do, not an error.
- **Never hand-write an entry.** Promoting a feature the checker has not passed is how a migration starts lying about itself, and it is the one edit here that cannot be undone by reading the report.

## The report is written even when the run went badly

A refused, blocked or half-finished migration is exactly the run whose record matters. Nothing here withholds the report because there is bad news in it, and it is regenerated in full rather than appended to — a report that accretes stale sections reads as current, which is worse than not having one.

What it must contain, and why each part is not optional:

| Section | Why |
|---|---|
| What changed, per rule — before from the confirmed plan, after from a live checker run | the only quantitative answer to "what did this actually do" |
| Every refusal, with its reason and evidence, whether found at discovery or mid-rewrite | a refusal nobody wrote down is indistinguishable from a bug |
| Features with **no test source set** | tests are out of scope, so naming the untested features *is* the mitigation. These carry the most behavioural risk in the run |
| Features whose test source sets outlived the rewrite | they still reference the types that were replaced; expect them not to compile, and regenerate with `/test-feature` |
| Anything not promoted, and why | a `done` that did not promote is a completion that was forced |
| Features with no spec | the gap, named, with the command that closes it |
| The undo, in full | `git switch -` plus the `git restore --source={ref} -- .` for work that was uncommitted when the run began |

## The closing step is earned

`verify report` passes on two conditions: the report exists, and every `done` feature is in `managedFeatures`. The second is what catches a run signing itself off as finished while carrying a completion that was forced — the feature promotion refused is exactly the feature whose `done` was a claim.

`finish --force` closes a run that fails those conditions and records the sign-off **as forced**, for the same reason `complete --force` does: an unverified tick that reads as verified is worse than no tick.

## It adds no integration points

I1–I4 are checker rules in the `integration` rewrite cluster, already routed to `integrator` during the clean phase, and `verify` holds a `migrate` step at zero findings — so a feature cannot reach `done` unforced with one missing. This phase adds no second mechanism; what it adds is the report naming any that a forced completion left behind.

## What is still the user's to run

The migration verifies statically and compiles nothing:

```bash
./gradlew assembleDebug        # android + ios + desktop
./gradlew archTest             # strict now, for every promoted feature
```

Say this out loud when the run ends. A promoted feature is graded strictly from that moment, so the first `archTest` after a migration is the one that matters.
