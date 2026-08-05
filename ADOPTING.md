# Adopting KMPilot into an existing project

`install.sh --adopt` installs the pipeline into a Kotlin Multiplatform repo you already
have, instead of generating a new one. Run it from your repo's root:

```bash
cd /path/to/your-kmp-app
curl -fsSL https://github.com/ThisIsSadeghi/KMPilot/releases/latest/download/install.sh | bash -s -- --adopt --dry-run
curl -fsSL https://github.com/ThisIsSadeghi/KMPilot/releases/latest/download/install.sh | bash -s -- --adopt
```

This page is the detail: what adopt mode does, what it refuses, and why.

## What it does

1. **Detects** your project's shape — root name, app module, package prefix, Kotlin/AGP/
   Compose versions, and whether you already use Koin, Ktor, Compose Multiplatform,
   DataStore, a design system, or your own `Either`.
2. **Reports** what it found and prints the exact list of files it would create or edit.
   `--dry-run` stops here and writes nothing.
3. **Vendors** `core/common`, `core/data` and `core/designsystem` into your repo, renamed
   to your own package prefix, with the example content stripped.
4. **Wires** them into `settings.gradle.kts` and your app module, and registers the
   `archTest` gate.
5. **Installs** `.claude/` and `CLAUDE.md`, so `/create-feature` works exactly as it does
   in a generated project.

Everything lands as one reviewable `git diff`.

## What it will not do

- **Touch your Kotlin.** The Koin wiring is left to `/create-feature`, which reads your
  files rather than guessing with a regex.
- **Touch your version catalog.** KMPilot's dependencies live in a separate
  `gradle/kmpilot.versions.toml`, exposed as `kmpilotLibs`, so no alias of yours can
  collide or be overwritten. Your Kotlin / AGP / Compose versions are read and reused,
  never overridden.
- **Overwrite anything.** An existing path is reported and skipped. Your `CLAUDE.md` or
  `.claude/settings.json` is kept and KMPilot's dropped beside it as `*.kmpilot.*`.
- **Run on a dirty tree**, or twice without `--force`. Re-running duplicates nothing.

## Flags

| Flag | Effect |
|---|---|
| `--dry-run` | Print the compatibility report and file plan; write nothing |
| `--force` | Re-run over an existing adoption (idempotent) |
| `--app-module=<module>` | Name the app module explicitly instead of letting detection ask |

| Env var | Effect |
|---|---|
| `KMPILOT_NONINTERACTIVE=1` | Never prompt, even from a terminal. Anything that would be asked becomes a refusal |

## Will it work on my repo?

Every row below is a variant in [`scripts/adopt-matrix.sh`](scripts/adopt-matrix.sh), asserted
on each change — so this table cannot drift from the behaviour.

**Adopts cleanly**

| Your project | Notes |
|---|---|
| Any module naming | The app module is detected, not assumed — `composeApp`, `shared`, `app`, anything |
| Any package depth | Prefix is the longest package shared by your app module's sources |
| Nothing at the app module's root package | All sources under `…app.ui` / `…app.di` / `…app.nav` still resolve to `…app` |
| Convention plugins (`buildSrc` / `build-logic` / included builds) | The KMP plugin is found there too, and a module with `src/commonMain` counts as proof on its own |
| Koin started any way you like | `startKoin`, or Compose's `KoinApplication` / `koinConfiguration` — all recognised |
| No `NavHost` (Voyager, Decompose, hoisted state) | Valid navigation; `archTest` warns rather than failing |
| No version catalog | KMPilot writes its own `kmpilot.versions.toml`; nothing of yours is required |
| `iosX64` in your targets | Added to the vendored modules automatically — it folds into the same `iosMain` |
| Hyphenated `rootProject.name` | Sanitized the way Compose sanitizes it (`acme-notes` → `acme_notes`) |
| A `rootProject.name` unrelated to your package | Independent values: the resources package follows the root name, the Kotlin package follows your sources |

**Adopts, with a warning you should read**

| Your project | What you're told |
|---|---|
| No Koin | Koin arrives as a new dependency — KMPilot's DI (Rule 8) is Koin-based |
| Arrow (`arrow.core.Either`) | You'll have two distinct types named `Either`; any file importing both must alias one |
| Your own design system | KMPilot's `X*` components are vendored alongside yours; the rules only enforce KMPilot's |
| Kotlin / AGP / Compose below KMPilot's tested floor | Your versions are kept regardless — never overridden — but core may not compile |
| A package prefix unrelated to your Android `namespace` / `applicationId` | Both are shown and you're offered the declared one. The prefix is inferred from your sources; your Android build states it outright, so a disagreement is worth a look |

**Refused, with the reason**

| Your project | Why |
|---|---|
| Two modules that both look like the app shell | Both named, and you're asked which. Everything adoption writes hangs off that answer, so it is never guessed — pass `--app-module=` on a non-interactive run |
| Not Kotlin Multiplatform | Adopt mode installs a KMP pipeline. Android→KMP is a different job (`migrate-feature`, upstream) |
| Targets we can't serve (`wasmJs`, `js`, `macos*`, `watchos*`, `linux*`) | Vendored core ships `androidMain` / `iosMain` / `desktopMain` actuals only — adopting would leave that target without a variant |
| Groovy DSL (`settings.gradle`) | Not supported **yet** — tractable, unscheduled. [Open an issue](https://github.com/ThisIsSadeghi/KMPilot/issues) if you want it; that's what decides |
| You already own a module at `core/common`, `core/data` or `core/designsystem` | The names collide. Yours is never overwritten, but features could not resolve `Either`/`UiState` — so adoption stops instead |
| Dirty working tree | Adoption should land as one reviewable diff |
| Already adopted | Re-run with `--force` |

A refusal is never a broken edit — adopt mode stops before writing anything. If it refuses a
repo it shouldn't, that's a bug worth reporting: paste the message into an issue.

> **The design system is the honest caveat.** Adopt mode vendors KMPilot's `X*` components
> rather than teaching the rules to speak your component library. If your team already owns
> one, expect that to be the friction point — and please
> [say so in an issue](https://github.com/ThisIsSadeghi/KMPilot/issues), because that
> feedback is what decides whether the pipeline learns to enforce the rules against *your*
> types instead of vendoring its own.

<br />

## Afterwards

`./update.sh` understands adopted projects (`installMode: "adopt"` in `.kmpilot.json`) and
pulls newer KMPilot releases without touching your code. `KMPILOT-NEXT-STEPS.md`, written
at adoption, lists anything left for you — delete it when done.
