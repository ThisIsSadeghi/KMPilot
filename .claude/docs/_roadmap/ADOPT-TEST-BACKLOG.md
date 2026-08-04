# Adopt mode — test backlog

Shapes of real KMP projects that `install.sh --adopt` has **not** been tried against yet,
in the order most likely to pay off.

**Why this file exists.** Phase 2 found 13 defects. Three came from reading the code; the
other ten came from pointing adopt mode at project shapes it had never seen. Not one came
from re-running the happy path. The fixture is a single point in a large space, so the
backlog *is* the test plan.

**The bar.** A shape passes if adopt either works, or **refuses with a reason a stranger
could act on**. Silently breaking a build is the failure under test; wrongly telling a real
KMP project it is not one is the second.

---

## Already covered

`scripts/adopt-matrix.sh` — 18 variants, seconds to run, gates every PR:

```bash
bash scripts/adopt-matrix.sh            # all
bash scripts/adopt-matrix.sh iosx64     # one, by substring
KEEP=1 bash scripts/adopt-matrix.sh     # keep the generated fixtures to poke at
```

baseline · `iosX64` · unsupported targets (`wasmJs`) · convention plugins · no catalog ·
no Koin · Koin's Compose bootstrap · Arrow · rival design system · library-only `core/*` ·
no trailing newline · stale `build/` dirs · `core/*` name collision · single sub-package ·
Groovy DSL · non-KMP · dirty tree · already adopted.

Don't spend manual time on these — spend it below.

---

## Untested, highest value first

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

### 2. Two plausible app modules

Detection returns the **first** module that starts Koin, then the first with a nav host,
then the first the Android app depends on. A project with `shared` **and** `mobile`, both
bootstrapping Koin, gets whichever sorts first — possibly the wrong one, silently.

- **Build:** two `commonMain` modules, both with `startKoin`.
- **Check:** the reported app module. Does it ask, or just pick?
- **Likely fix:** when more than one strong candidate exists, prompt rather than take the
  first — `APP_MODULE_CONFIDENCE` already has the machinery.

### 3. No code at the app module's root package

Package-prefix detection is the longest package shared by the app module's sources. If
every file sits in a sub-package (`…app.ui`, `…app.di`, nothing in `…app`), the prefix
still resolves — but nothing proves it. This is the code path that produced
`com.example.bookshelf.` with a newline glued on.

- **Build:** app module with `ui/`, `di/`, `nav/` and no root-package file.
- **Check:** `packagePrefix` in `.kmpilot.json` is exactly the shared prefix.

### 4. A package prefix unrelated to `rootProject.name`

`rootProject.name = "Bookshelf"` with `com.acme.internal.reader` sources. The
generated-resources package comes from the root name, the Kotlin package from sources —
they are independent, and the rename touches both.

- **Check:** vendored core imports `bookshelf.core.designsystem.generated.resources` while
  its Kotlin package is `com.acme.internal.reader.designsystem`. Then build.

### 5. A monorepo where the KMP app is not at the repo root

`--adopt` runs in the current directory and assumes it is the Gradle root. A repo with
`android/`, `ios/`, `backend/` and the KMP project one level down is common in company
codebases.

- **Check:** does it refuse clearly when run from the wrong level?

### 6. Windows / Git Bash

Every path here is bash + `sed`/`find`/`awk`. The matrix runs on macOS and Linux CI; the
README tells Windows users to use Git Bash, and nobody has ever tried it.

### 7. A project already using `kmpilotLibs` or `core/kmpilot*`

Someone who hand-vendored KMPilot before adopt mode existed. Re-adoption should recognise
the state rather than duplicating or colliding.

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
