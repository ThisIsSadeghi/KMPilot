# Phase 3 — Claude Code plugin packaging

**Goal:** make KMPilot installable as a Claude Code plugin — `/plugin marketplace add ThisIsSadeghi/KMPilot` → `/plugin install kmpilot` → the pipeline works in any repo, with no clone and no rename.

**Why now:** ~9 unique visitors per fortnight open `.claude/` to read the skills and agents, and none of them can run any of it. A plugin is exactly the artifact they came for. It is also permanent presence in the **Claude Code pool**, which produced +3 stars from a single Discord post while newsletters produced +3 from two forms — that pool converts, and KMPilot currently has no standing surface in it. Anthropic runs a reviewed community marketplace, and third-party directories scrape marketplaces automatically.

**Why after Phase 2:** a plugin installed into a repo with no `core/common`, no `XButton`, and no Koin wiring generates code importing types that do not exist. Shipping A before B means the debut on a permanent channel is a broken first run — and marketplace first impressions do not get a second take.

**Branch:** `phase-3-plugin-packaging`

> **Corrections — verified 2026-08-04 against a running plugin (Claude Code 2.1.220).**
> Five assumptions below were written against docs, not against an installed plugin, and are wrong.
> The implementation follows this list, not the prose further down:
>
> 1. **Plugin `agents/` is flat-only.** Nested `agents/{code-quality,feature-development,feature-testing}/`
>    loads **zero** agents. The manifest `agents` field does not rescue it — with explicit file paths it
>    still loads zero, and validates clean while doing so. Ship `agents/*.md` flat with **no** `agents` field.
> 2. **`@relative.md` imports silently do not resolve in plugin skills** (they do in project skills).
>    Every import needs `${CLAUDE_PLUGIN_ROOT}/…` on the plugin surface — so *paths* change too, not
>    only invocation strings.
> 3. **`${CLAUDE_PLUGIN_ROOT}` does substitute in skill and agent content**, so the model receives a
>    real absolute path.
> 4. **`commands/` needs no conversion** — a plugin loads flat `.md` files there as skills as-is.
>    Step 1's `disable-model-invocation` migration is unnecessary.
> 5. **Plugins cannot ship `CLAUDE.md`**, so the 14 rules never enter context in plugin mode. A
>    `SessionStart` hook injects them instead.
>
> Two consequences the phase file misses: the plugin **copies nothing into the user's project**, so a
> repo that has not run `install.sh --adopt` has no `core/` and cannot compile generated code (hence an
> adoption preflight + `/kmpilot:adopt`); and `agents/_base/common.md` must leave `agents/` to pass
> `validate --strict`.
>
> **Status — built, parked, not published (2026-08-04).**
> The plugin is **finished and validating** (`claude plugin validate --strict` clean; 12 skills,
> 11 agents, 2 hooks, 1 MCP server; proven end to end in a scratch repo that is not KMPilot).
> It is **deliberately not on GitHub and not installable.**
>
> **Why parked:** the plugin's reason to exist is making *migration* easy. Until
> `migrate-feature` ([PARKED.md](PARKED.md)) lands, publishing it would ship a namespaced copy
> of what `install.sh` already does — and a marketplace debut does not get a second take.
>
> **Where it lives:** on the maintainer's machine only, gitignored —
> `pipeline/src` (authored, shared), `pipeline/plugin-src` (authored, plugin-only),
> `pipeline/dist` (the built plugin), `scripts/gen-surfaces.py` (the generator).
> `.claude/{skills,agents,commands,hooks}` is **generated from `pipeline/src`** and committed as
> the published artifact; edit the source, never `.claude/`. There is no CI drift check —
> syncing is local. Full workflow: `pipeline/README.md` and `.claude/rules/pipeline-source.md`
> (both gitignored).
>
> **What did ship publicly** from this phase: the initialization gate (the feature-file hook
> refuses `feature/` writes in a repo with neither `.kmpilot.json` nor `core/common`, and an
> active skill cannot bypass it) and `scripts/release.sh --dry-run`.
>
> **To publish**, when migration is ready: add `.claude-plugin/marketplace.json` at the repo
> root with `"source": "./pipeline/dist"`, un-gitignore the tree, document both doors in
> `README.md`, then submit to `anthropics/claude-plugins-community` **after** merge —
> submissions pin a commit SHA. Steps in `pipeline/README.md`.
>
> **Before you start:** read [`README.md`](README.md) → *Branch and PR conventions* and *`update.sh` delivery tiers*. Phase 2 must be merged (see *Why after Phase 2* above).
>
> **Prerequisites — verified 2026-07-30, no action needed:**
> - `claude plugin validate <path>` exists in Claude Code **2.1.220**. Re-check with `claude plugin --help` if the CLI has moved on.
> - Submission is **manual and post-merge** (submissions pin a commit SHA): Console form at `platform.claude.com/plugins/submit` for individual authors.
> - A Stitch API key is **not** needed to build or validate — `.mcp.json` references an env var, never a literal key.

---

## In scope

### Plugin manifest and layout

```
.claude-plugin/
  plugin.json          # name, version (from VERSION), description, author, homepage, repository, license
  marketplace.json     # makes this repo itself an installable marketplace
skills/                # ← plugin root, NOT inside .claude-plugin/
agents/
hooks/
  hooks.json
  protect-feature-files.sh
  reinject-on-compact.sh
.mcp.json              # Stitch MCP preconfigured
```

**The documented common mistake:** `commands/`, `agents/`, `skills/`, `hooks/` must sit at the **plugin root**, never inside `.claude-plugin/`. Only `plugin.json` goes in there.

### Hook migration

`.claude/settings.json` currently registers both hooks with `"$CLAUDE_PROJECT_DIR"/.claude/hooks/…`. In plugin form they move to `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}/hooks/…`. The hook bodies are unchanged, including the `/tmp/.claude-kmpilot-skill-active` marker contract.

### `.mcp.json` — free win

A plugin can ship MCP server configuration. Today the user hand-runs:

```bash
claude mcp add stitch --transport http https://stitch.googleapis.com/mcp \
  --header "X-Goog-Api-Key: YOUR_API_KEY" -s user
```

Shipping `.mcp.json` at the plugin root means **installing the plugin configures Stitch**. The API key still comes from the user's environment — never commit a key, and document the env-var reference in the README.

### Namespacing — the real work

Plugin skills are namespaced: `/create-feature` becomes `/kmpilot:create-feature`. Measured invocation-site counts (path references excluded — those must **not** change):

| Skill | Invocation sites |
|---|---|
| `create-feature` | 48 |
| `design-ui` | 42 |
| `modify-feature` | 37 |
| `verify-ui` | 20 |
| `bridge-swift` | 11 |
| `review-feature` | 6 |
| `audit-spec` | 5 |
| `test-feature` | 4 |

Recount before editing:

```bash
for n in create-feature modify-feature design-ui verify-ui test-feature review-feature audit-spec bridge-swift; do
  echo "$n: $(grep -rn "/$n" .claude | grep -v "skills/$n\|commands/$n\|agents/" | wc -l)"
done
```

**Critical distinction:** rewrite only *invocation* references (`/create-feature`, "Invoke with `/create-feature`"). Directory paths (`.claude/skills/create-feature/architecture/ui.md`) stay exactly as they are.

### One authored copy, two published surfaces

Both published articles, the 25-minute YouTube walkthrough, and the wiki show short names. **The video cannot be re-cut.** So both surfaces ship:

| Surface | Names | Source |
|---|---|---|
| Template `.claude/` | `/create-feature` | **generated** |
| Plugin `skills/` | `/kmpilot:create-feature` | **authored** |

`scripts/release.sh` gains a generation step: copy the plugin tree to `.claude/`, strip the `kmpilot:` prefix from invocation references, and fail the release if the generated output differs from what is committed (so drift is impossible to merge).

### Submission

1. `claude plugin validate .` locally — the review pipeline runs the same check.
2. Submit via the Console form (`platform.claude.com/plugins/submit`) — the individual-author path.
3. Approved plugins are pinned to a commit SHA in `anthropics/claude-plugins-community`, and CI bumps the pin as new commits land. The public catalog syncs nightly, so expect a delay between approval and installability.
4. Then: confirm propagation to the scraper directories, and unpark the `awesome-claude-code` PR (the plugin form makes it eligible).

---

## Out of scope

- Changing any skill's behaviour. This phase is packaging only.
- Adding new skills.
- Submitting to the **official** marketplace (`claude-plugins-official`) — curated at Anthropic's discretion, no application process.
- README repositioning — that follows Phase 2, tracked there.

---

## Files touched

| Path | Change | `update.sh` tier |
|---|---|---|
| `.claude-plugin/plugin.json` | new | not delivered |
| `.claude-plugin/marketplace.json` | new | not delivered |
| `skills/`, `agents/`, `hooks/` (plugin root) | new — authored source | not delivered |
| `.mcp.json` | new | not delivered |
| `.claude/skills`, `.claude/agents`, `.claude/commands`, `.claude/hooks` | become **generated** | OVERRIDE |
| `scripts/release.sh` | generation + drift check | stripped on install |
| `.claude/settings.json` | hooks now come from the plugin in plugin mode | TIER1 |
| `README.md` | plugin install path alongside `install.sh` | — |
| `CHANGELOG.md` | `[Tooling]` entry | — |

---

## Steps

1. `git mv` (or copy) `.claude/skills`, `.claude/agents`, `.claude/hooks` to the plugin root. Decide `commands/` vs `skills/` — the docs recommend `skills/` for new plugins; the four files in `.claude/commands/` are slash-only utilities and can become `skills/<name>/SKILL.md` with `disable-model-invocation: true`.
2. Write `plugin.json` + `marketplace.json`. Version comes from `VERSION` — wire it in `release.sh` so it cannot drift.
3. Write `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}` paths.
4. Write `.mcp.json` for the Stitch HTTP server, with the API key sourced from the environment.
5. Rewrite invocation sites in the plugin tree to `kmpilot:` form. Use the recount command above as the checklist; verify no path reference was touched (`git diff` should show zero changes to lines containing `skills/<name>/`).
6. `claude plugin validate .` until clean.
7. Add the `release.sh` generation + drift check. Run it; confirm the regenerated `.claude/` matches the committed one byte for byte.
8. Test with `claude --plugin-dir .` locally, then in a scratch repo adopted via Phase 2.
9. README: document both entry paths.
10. Open the PR. **Submit to the community marketplace only after merge**, since submissions pin a commit SHA.

---

## Exit criteria

- [ ] `claude plugin validate .` passes.
- [ ] `claude --plugin-dir .` loads; `/kmpilot:create-feature` appears in `/help` under the plugin namespace.
- [ ] `/plugin marketplace add ThisIsSadeghi/KMPilot` → `/plugin install kmpilot` works from a clean machine.
- [ ] The pipeline runs end to end in a scratch repo adopted via Phase 2.
- [ ] Template `.claude/` still shows short names; both articles' commands still valid.
- [ ] `release.sh` regenerates `.claude/` with zero drift, and fails loudly if drift exists.
- [ ] Both hooks fire in plugin mode (test: try to `Edit` a `feature/` file without a marker — expect the block).
- [ ] Submitted to `anthropics/claude-plugins-community` (post-merge).

---

## Verification

```bash
claude plugin validate .
claude --plugin-dir .          # then run /help → Custom commands tab
# in a Phase-2-adopted scratch repo:
#   /plugin marketplace add ThisIsSadeghi/KMPilot
#   /plugin install kmpilot
#   /kmpilot:create-feature a simple settings screen
bash scripts/release.sh --dry-run   # regenerate .claude/, expect no diff
git diff --stat .claude/            # expect empty
```

Hook check: with the plugin enabled, ask Claude to edit a file under `feature/` with no marker present — the edit must be blocked with *"Blocked: Cannot edit feature source files directly."*

---

## Risks

- **Doc churn is the accepted cost.** The wiki needs a pass (it mirrors `patterns.md`); the articles and video keep working because the template retains short names. Do not attempt to update the published articles.
- **Two surfaces can drift.** The `release.sh` drift check is what prevents it — make it a hard failure, not a warning.
- **Marketplace submission is one-shot in reputation terms.** Do not submit until the plugin has been proven in a repo that is not KMPilot.
- **`.mcp.json` and secrets.** Ship a config that *references* an env var. Never a key. Review the diff for accidental key inclusion before pushing.
- **`skills/` vs `commands/` migration** may change how the four utility commands are invoked. Verify each still runs after conversion.

---

## Downstream delivery

The plugin tree and manifests are new upstream paths, not delivered by `update.sh` — downstream users get the plugin by *installing* it, which is the point. Template `.claude/` stays in **OVERRIDE**, so existing template-mode projects continue to receive skill updates exactly as before, with unchanged short names.
