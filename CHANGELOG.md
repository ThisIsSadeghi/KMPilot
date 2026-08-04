# Changelog

All notable changes to KMPilot are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

To pull a release into a project created with `install.sh`, see
[Staying up to date](README.md#staying-up-to-date). Upgrade notes are tagged
**[Tooling]** (auto-applied by `update.sh`), **[Core]** (`update.sh --core`,
may conflict), or **[Breaking]** (manual steps required).

## [Unreleased]

## [0.2.1] — 2026-08-04

### Fixed

- **[Tooling]** **The updater bootstrap no longer blocks itself.** `update.sh` is tracked, so
  the documented `curl … -o update.sh` step modified the working tree — and the very next
  command refused because the tree was not clean. The preflight now ignores a tree whose only
  change is `update.sh`; anything else still refuses. The release notes also lead with the
  precondition, since only projects installed before v0.1.2 need that step at all.

### Changed

- **[Tooling]** **Adopting no longer fails your build over code that predates KMPilot.**
  `.kmpilot.json` gained `managedFeatures` — the features `/create-feature` generated. A
  repo that already organises code under `feature/` used to get its existing, working
  modules graded against all 14 rules on the first `archTest`, with nothing saying the
  violations were pre-existing. Those are now reported in full but never enforced (the
  `--baseline` treatment), while KMPilot-generated features stay strict. The summary names
  every unenforced feature, so a generated one missing from the list is visible rather than
  silently exempt.

## [0.2.0] — 2026-08-04

Two doors instead of one, and a build that fails when the architecture drifts.

### Added

- **[Tooling] Adopt mode.** `install.sh --adopt` installs the pipeline into a KMP repo you
  **already have**, instead of generating a new one. It reads your project, prints a
  compatibility report and the exact file plan, then vendors `core/common`, `core/data` and
  `core/designsystem` renamed to your own package.

  ```bash
  cd /path/to/your-kmp-app
  curl -fsSL .../install.sh | bash -s -- --adopt --dry-run   # report + plan, writes nothing
  curl -fsSL .../install.sh | bash -s -- --adopt
  ```

  It never edits your Kotlin, never touches your version catalog (KMPilot's lives separately in
  `gradle/kmpilot.versions.toml` as `kmpilotLibs`), never overwrites an existing file, and
  refuses with a reason rather than guessing. What works, what warns, and what is refused:
  **[ADOPTING.md](ADOPTING.md)**.

- **[Tooling] `./gradlew archTest`.** 19 deterministic checks covering the greppable half of the
  14 rules. A violation now fails the build instead of depending on a model noticing it.
  `--baseline` reports errors as warnings, for measuring a codebase that has not adopted the
  rules yet; `--root <path>` scans another repo without installing anything into it.

- **[Tooling]** `/review-feature` consumes the checker's report instead of re-deriving it by
  grep, so a review and a CI run can no longer disagree.

### Changed

- **[Tooling]** The pipeline stopped assuming KMPilot's own layout. The app module and the
  catalog accessor come from `.kmpilot.json`, so generated features fit an adopted project as
  well as a template one.
- **[Tooling]** `update.sh` understands `installMode: "adopt"` — absent means `"template"`, so
  every existing manifest stays valid.

### Breaking

- **[Breaking]** *(manual, one block)* **Register the `archTest` task.** `build.gradle.kts` is a
  MANUAL path — `./update.sh` surfaces it but never merges it — so existing installs paste this
  into the **root** `build.gradle.kts`. Skipping it is safe: the checker still runs standalone
  via `python3 .claude/skills/_shared/kmpilot_check.py --all`, and `/review-feature` invokes it
  that way. You only lose `./gradlew archTest`.

  <details>
  <summary>The block</summary>

  ```kotlin
  tasks.register<Exec>("archTest") {
      group = "verification"
      description = "Checks feature modules against the KMPilot architecture rules."
      workingDir = rootDir
      commandLine("python3", ".claude/skills/_shared/kmpilot_check.py", "--all")
      doFirst {
          val onPath =
              System.getenv("PATH")
                  ?.split(File.pathSeparator)
                  ?.any { dir -> File(dir, "python3").canExecute() || File(dir, "python3.exe").canExecute() }
                  ?: false
          if (!onPath) {
              throw GradleException(
                  "archTest needs python3 on PATH (macOS: `brew install python`, " +
                      "Windows: python.org installer + Git Bash). Or run the checker directly: " +
                      "python3 .claude/skills/_shared/kmpilot_check.py --all",
              )
          }
      }
  }
  ```

  `python3` is already a hard requirement of `/design-ui`, so this adds no new dependency.
  </details>

## [0.1.3] — 2026-07-12

### Changed
- **[Tooling]** `install.sh` is now an **interactive, colorized installer**. Run it
  with no arguments (`curl … | bash`) and it walks a guided flow — banner, numbered
  step log, clone spinner, and prompts for the project name / package with inline
  validation. Passing both as arguments stays fully non-interactive (unchanged for
  scripting). New env vars: `NO_COLOR` (disable color) and `KMPILOT_ASSUME_YES=1`
  (skip the confirmation prompt). Template surgery, rename, and manifest logic are
  unchanged.

## [0.1.2] — 2026-07-04

### Added
- **[Tooling]** **Upstream-owned rule layers**: `.claude/skills`, `.claude/agents`,
  `.claude/commands` and `.claude/hooks` are now applied **as-is from the release** —
  no 3-way merge, no `<<<<<<<` conflicts. Downstream projects follow upstream rules:
  local edits to shipped rule files are overridden (`force:` in the log, recoverable
  via git), and upstream deletions/renames apply even over local edits. Files you
  created yourself (paths KMPilot never shipped, e.g. your own skills) are never
  touched. `settings.json`, `CLAUDE.md`, the gradle wrapper and `update.sh` keep the
  conflict-surfacing 3-way merge so your own additions survive.
- **[Tooling]** `update.sh` **re-execs under the target release's updater**: when
  `update.sh` itself changed in the release being pulled, the run transparently
  restarts under the new updater so its merge logic drives *this* update, not just
  the next one. Loop-guarded via `KMPILOT_REEXEC`; the stash/preflight moved after
  the re-exec point so a stashed working tree is always restored. A re-exec'd run
  writes `update.sh` directly (no `update.sh.new` staging — that now only happens
  when the running process is the project's own `update.sh`).
- **[Tooling]** `update.sh` ends every run — including "already on the latest
  release" — with a **stale sweep**: template files the target release no longer
  ships are deleted. In the upstream-owned rule layers any file whose path ever
  shipped in a release is swept (local edits included); on merged paths only
  byte-identical copies are swept. Files at paths KMPilot never shipped are never
  touched. Re-running `./update.sh` after updating with a pre-0.1.2 updater heals
  the stale skill/agent/command copies it left behind — no flags needed.

### Migrating from 0.1.0 / 0.1.1
Old updaters can't benefit from fixes they predate, so update the updater **before**
updating the project:

```bash
curl -fsSL https://github.com/ThisIsSadeghi/KMPilot/releases/latest/download/update.sh -o update.sh
./update.sh
```

(A project installed from 0.1.0 has an updater that never updates itself; 0.1.1's
updates itself only after running its old merge logic once. From 0.1.2 onward this
is automatic — the updater always re-execs under the target release's updater.)

### Fixed
- **[Tooling]** `/design-ui`'s `edit_screens` MCP tool silently no-ops (upstream Stitch
  bug — success reported, edit never applied). Banned; all edit flows now route through
  `generate_variants` (variantCount 1 + REFINE) instead.
- **[Tooling]** Compaction hook re-injected only 11 of the 14 architecture rules,
  dropping Rules 12–14 (string resources, single app-shell Scaffold, platform
  capability) after a `/compact`.
- **[Tooling]** `update.sh` now applies upstream **renames and deletions**. On merged
  paths, renamed files (detected via git rename tracking) carry your local edits to
  the new path and the old copy is removed; files deleted upstream are removed when
  your copy is unmodified, and a locally edited copy is only flagged. (In the
  upstream-owned rule layers, deletions/renames apply unconditionally — see Added.)
  Previously a release that renamed skills left both the old and new skill
  directories on disk, keeping stale skills discoverable.
- **[Tooling]** `update.sh` preserves the executable bit on newly added files (new
  hooks would otherwise land non-executable and silently never fire) and never
  text-merges binary files (e.g. `gradle-wrapper.jar`) — it takes upstream's copy when
  yours is unmodified and flags it for manual reconciliation otherwise.
- **[Tooling]** `update.sh` no longer misleads after a conflicted run: the exit message
  explains that `.kmpilot.json` is already bumped and how to abandon the update
  wholesale, so a partial revert can't silently shift the next update's base.

## [0.1.1] — 2026-07-01

### Changed
- **[Tooling]** `install.sh` is now **release-pinned**. The release workflow stamps
  the published tag into the installer, so a released `install.sh` clones the exact
  tag it shipped with — the installer and the cloned template are always the same
  release. Install from `releases/latest/download/install.sh`; set
  `KMPILOT_TEMPLATE_BRANCH=main` for the bleeding edge.
- **[Tooling]** `install.sh` stamps `.kmpilot.json` `kmpilotVersion` from the resolved
  release **tag** (not the `VERSION` file), so `update.sh`'s baseline can never drift
  from the tag actually installed.
- **[Tooling]** `update.sh` now updates **itself** — when the updater changes upstream it
  swaps the new version in automatically (atomic rename, safe mid-run; tracked in git, so it
  shows in `git diff`). It also warns that your app version (android/iOS) is yours and never
  touched by updates, and points at the upstream changelog for release notes.
- Fresh installs get a **minimal project `README.md` + empty `CHANGELOG.md`** instead of
  inheriting KMPilot's own.

### Added
- **Release automation** — `.github/workflows/release.yml` (publishes assets on a `v*`
  tag, guards that tag == `VERSION` == `libs.versions.toml`) and `scripts/release.sh`
  (bumps the version, rolls the changelog, commits + tags; never pushes).

### Migrating from 0.1.0
Existing projects are **unaffected in place** — nothing here rewrites an already-installed
tree. To move onto the self-updating `update.sh`, re-pull it once:

```bash
curl -fsSL https://raw.githubusercontent.com/ThisIsSadeghi/KMPilot/main/update.sh -o update.sh
```

Then run `./update.sh` as usual. If your `update.sh` reports `base tag not found`, pass an
explicit baseline: `./update.sh --from v0.1.0`.

## [0.1.0] — 2026-06-22

First public release — an AI-driven Spec-Driven Development template for
Kotlin Multiplatform + Compose Multiplatform.

### Added
- **Spec-Driven pipeline** — `/ui-designer` → `/creating-kmp-feature` →
  `/verify-ui` → `/feature-test` → `/feature-review`, coordinated through a
  per-feature living spec at `.claude/docs/{name}/spec.md`.
- **One-command install** — `install.sh` clones the latest release, trims to a
  clean shell, renames packages and identifiers, and re-initializes git.
- **Downstream updater** — `update.sh` (tiered, rename-aware, conflict-safe,
  never commits) with the `.kmpilot.json` manifest that drives updates.
- **Clean Architecture core** — `:core:common`, `:core:data`, and
  `:core:designsystem`, with the X-component design system, light/dark `XTheme`,
  and a runtime locale.
- **Reference feature** — a `dashboard` showcase demonstrating the generated
  feature shape; reference features are stripped on install, so a fresh project
  starts on a Welcome screen.

[Unreleased]: https://github.com/ThisIsSadeghi/KMPilot/compare/v0.2.1...HEAD
[0.1.0]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/v0.1.0
[0.1.1]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/v0.1.1
[0.1.2]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/v0.1.2
[0.1.3]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/v0.1.3
[0.2.0]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/v0.2.0
[0.2.1]: https://github.com/ThisIsSadeghi/KMPilot/releases/tag/v0.2.1
