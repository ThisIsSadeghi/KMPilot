# Adopt mode — test backlog

Shapes of real KMP projects to point `install.sh --adopt` at, in the order most likely to
pay off. Every shape listed here is now closed; the file stays open for the next one.

**Why this file exists.** Phase 2 found 13 defects. Three came from reading the code; the
other ten came from pointing adopt mode at project shapes it had never seen. Not one came
from re-running the happy path. The fixture is a single point in a large space, so the
backlog *is* the test plan.

**The bar.** A shape passes if adopt either works, or **refuses with a reason a stranger
could act on**. Silently breaking a build is the failure under test; wrongly telling a real
KMP project it is not one is the second.

---

## Already covered

`scripts/adopt-matrix.sh` — 25 variants, seconds to run, gates every PR:

```bash
bash scripts/adopt-matrix.sh            # all
bash scripts/adopt-matrix.sh iosx64     # one, by substring
KEEP=1 bash scripts/adopt-matrix.sh     # keep the generated fixtures to poke at
```

baseline · `iosX64` · unsupported targets (`wasmJs`) · convention plugins · no catalog ·
no Koin · Koin's Compose bootstrap · Arrow · rival design system · library-only `core/*` ·
no trailing newline · stale `build/` dirs · `core/*` name collision · single sub-package ·
Groovy DSL · non-KMP · dirty tree · already adopted · two app modules · sub-packages only ·
unrelated root name · monorepo · pre-vendored · namespace mismatch · below version floor.

Don't spend manual time on these — spend it below.

---

## Worked through, highest value first

Every shape on this list is now closed and has a variant. The file stays living: when a new
shape turns out to matter, add it here and follow the recipe at the bottom.

### 1. ~~A repo that already has a `feature/` directory~~ — DONE 2026-08-04

Confirmed against `~/KMPProjects/bookshelf-featuredir` (bookshelf with `search`/`favorites`
moved under `feature/`): the first `archTest` after adoption reported **10 errors** about
pre-existing code — 6× R5 Material3 imports, 2× R8 missing `di/`, 2× R11b missing UiModel.
All correct as rules, all a hostile first run.

Fixed by `managedFeatures` in `.kmpilot.json`: features KMPilot did not generate are graded
like `--baseline`. Same fixture now reports 0 errors / 12 warnings and passes, while a
feature listed as managed still fails strictly.

<details><summary>Original entry</summary>

**The one gap knowingly left open.** The checker globs `feature/*/build.gradle.kts` and
holds every match to all 19 rules. A project that already organises features that way gets
its **pre-existing, pre-KMPilot code graded** — so the first `./gradlew archTest` after
adoption fails with a wall of violations about code the user did not write with these rules
in mind.

- **Build:** any adoptable project with two or three modules under `feature/`.
- **Check:** `./gradlew archTest` immediately after adopting.
- **Likely fix:** record KMPilot-managed features in `.kmpilot.json` and check only those,
  or grade pre-existing ones at `--baseline` severity.

</details>

### 2. ~~Two plausible app modules~~ — DONE 2026-08-04

Detection returned the **first** module matching a signal and labelled it `strong`, so a
second candidate was never seen. It now collects every match in the winning tier: one match
behaves as before, more than one names them all and asks, and a non-interactive run refuses
with the candidates and `--app-module=`. The confirmation also moved ahead of
`detect_pkg_prefix`, which had been reading the prefix off the module the user was about to
reject.

Variant: `two-app-modules` — runs *without* `--dry-run`, because the claim under test is
that a real run stops before writing.

### 3. ~~No code at the app module's root package~~ — DONE 2026-08-04

The longest-common-prefix walk was already correct on this shape; nothing confirmed it.
Added `android_pkg_identities` + `confirm_pkg_prefix`: the inferred prefix is cross-checked
against the `namespace` / `applicationId` the Android build declares, and a disagreement
shows both and offers the declared one. What that catches is the walk swallowing a
sub-package (`com.acme.app.ui`) — well-formed and completely wrong. Silent on all 25
variants and all three real repos.

Variant: `subpackages-only`. Its mutation moves every root-package file across **all**
source sets — leaving `iosMain` behind made the fixture not the shape under test.

### 4. ~~A package prefix unrelated to `rootProject.name`~~ — DONE 2026-08-04

No code change: the two values were already independent. Pure regression cover, because
every fixture until now had the root name and the package sharing a token, so a rename that
conflated them would still have looked correct.

Variant: `unrelated-root-name` (`Paper-Trail` over `com.acme.notes`), asserting the
resources package lands as `paper_trail.core.*` while the Kotlin package stays
`com.acme.notes.*`. The hyphen also gives the sanitization rule the variant `ADOPTING.md`
already claimed it had. Confirmed beyond the matrix with a real adoption, `assembleDebug`
and `archTest`.

### 5. ~~A monorepo where the KMP app is not at the repo root~~ — DONE 2026-08-04

Two bad refusals, not one. No `settings.gradle.kts` at the top gave generic advice; a
monorepo top that *is* a Gradle root gave "this does not look like a Kotlin Multiplatform
project" plus a pointer to `migrate-feature`, about a repo that plainly contains one. Both
now look one level down via `die_wrong_level` and name the build they find. A sibling Gradle
root that is not KMP is deliberately never named — pointing someone at the wrong directory
is worse than pointing at none.

Variant: `monorepo`, with a `backend/` decoy. Asserting that decoy is *absent* from the
output is why `post_` hooks now receive the run output as well as the fixture directory.

### 6. ~~A project already using `kmpilotLibs` or `core/kmpilot*`~~ — DONE 2026-08-04

`.kmpilot.json` was the only signal that a repo already carried KMPilot. `kmpilot_artefacts`
now detects six artefacts independently of it and reports them as one inventory, refusing
unless `--force`. Replaces the narrower "a previous adoption was removed" refusal, which
only ever listed core modules.

Found while testing it: that refusal told the reader to pass `--force` and refused again
when they did — it was gated on the manifest, the very file missing in this shape.
Pre-existing and shipped. The collision refusal now keys on `theirs`, since a forced run
past the inventory leaves `clash` set to KMPilot's own modules.

Variant: `pre-vendored`.

---

## When a shape turns out to matter

Add it to the matrix rather than leaving it as a note — a variant costs about ten lines and
runs in a second:

1. `mut_<name>()` in `scripts/adopt-matrix.sh` — bend the generated fixture into that shape.
2. A row in `VARIANTS`: `name|mutation|adopts\|warns\|refuses\|applies|expected text|flags`.
3. Optional `post_<name>()` to assert on what landed on disk — use it whenever the
   interesting outcome is a file's contents rather than a line of output.
4. **Prove the test can fail**: break the fix, watch it go red, restore it. A variant that
   cannot fail is worse than no variant.
5. If the shape is user-visible, add a row to [`ADOPTING.md`](../../../ADOPTING.md) so the
   published compatibility table keeps matching the tests.
