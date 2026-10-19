# poteto-skills build spec (shared by all CLIs working on this repo)

Read `research/NOTES.md` first, and the verbatim upstream skills in `research/upstream/`. Every skill here must trace back to a source ID (S1..S23) in NOTES.md.

## Ground rules
- Python 3.13 **stdlib only** and bash. No pip installs and no network in skill scripts, except the curator, which may use `curl`. Java/TLA+ is NOT installed on this box.
- Do NOT touch anything outside `/home/box/developer/poteto-skills`. Never touch `/home/box/developer/pay-module`, Docker, `~/.claude`, or `~/.cursor` (install.sh writes there only when a human runs it).
- Do not push and do not add git remotes.
- The box is shared and short on memory (16G total, about 4G free, no swap). Run nothing heavy and nothing in parallel. Kill any server you start.
- Skills are prose, which is layer 3 of the pyramid. Each skill's job is to push its *output* up the pyramid: generate scripts, lints, tests, and types, not more prose.
- Write agent-facing prose: short, imperative, no filler, real commands. English.

## Layout
```
skills/<name>/SKILL.md        # YAML frontmatter: name (== dir name), description (what + when, incl. "/<name>"), then body
skills/<name>/scripts/*       # executable (chmod +x), shebang, `--help` works and exits 0, JSON output where useful
skills/<name>/references/*    # optional templates
install.sh                    # installs to ~/.claude/skills/<name> and ~/.cursor/skills/<name> (symlink by default, --copy option, --target claude|cursor|both, --project DIR to install into DIR/.claude/skills + DIR/.cursor/skills, --uninstall, --dry-run). Must not touch other existing dirs (e.g. ~/.claude/skills/synced).
README.md, MAPPING.md (skill -> source table), CHANGELOG.md (Keep a Changelog style)
examples/todo-app/            # example repo (its own nested git repo, history included)
scripts/run-examples.sh       # runs every skill's helper(s) for real against a scratch copy of examples/todo-app; logs to logs/examples/<skill>.log, writes logs/examples/summary.json
scripts/verify.sh             # self-check, see below
```

## Skills and the script contracts they need

1. **create-verification-skill** (S4, S13)
   - `scripts/scan_repo.py REPO [--json]`: inventory of language, build/test commands, launch candidates (servers/CLIs with ports), HTTP routes, CLI subcommands, existing skill dirs. Heuristics for Python, Node, Go, and Make are fine.
   - `scripts/scaffold.py REPO --app NAME [--skills-dir .claude/skills]`: writes `verify-NAME/SKILL.md` (frontmatter + Launch/Doctor/Drive/Evidence/Cleanup/Helpers sections), `verify-NAME/features/README.md` and one file per detected feature, using the 4 H2s in order (`Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, `Gotchas`), plus `verify-NAME/scripts/control.sh` (subcommands launch|doctor|drive|evidence|cleanup; pid file; kill only its own pid; evidence under `artifacts/verify/<run-id>/`). Unknown values become `TODO(verify):` markers.
   - `scripts/check_verify_skill.py SKILL_DIR`: fails when frontmatter is missing, a section is missing, a feature file has the wrong H2 order, the index links are dead, a `TODO(verify)` remains, or a script isn't executable.
   - SKILL.md body: interview the repo → scaffold → replace every TODO with real commands → **prove end to end once** (launch → doctor → drive one feature → evidence → cleanup) → check_verify_skill must pass.
2. **maintain-verification-skill** (S5)
   - `scripts/drift_check.py SKILL_DIR --repo REPO [--json]`: index hygiene (missing/extra/dead entries), H2 contract, routes/commands present in source but absent from the map (missing) and present in the map but absent from source (dead). Exit 1 on drift.
   - Body: outcomes clean|changed|blocked, edit scope, a live pass that is required even when the source looks clean, and triage (doc drift / harness gap / product gap).
3. **correct** (S7, S15, S16)
   - `scripts/mine_mistakes.py REPO [--since DATE] [--feedback FILE...] [--json]`: reads git log (reverts, fix commits, messages), optional review-comment JSON/markdown, and AGENTS.md. Groups items into classes by keyword/path signatures, keeps classes with count >= 2, and prints the evidence (commit shas, comment ids).
   - `scripts/patlint.py --rules RULES.json [--baseline BASELINE.json] [--update-baseline] PATHS...`: a generic deterministic lint. Each rule has id, regex, include globs, and a message that **names the fix**. Ratchet mode fails only when a change adds new violations. Inline exceptions use `patlint: allow <id> reason=... expires=YYYY-MM-DD approved-by=...`, and expired exceptions fail.
   - `scripts/rule_table.py check AGENTS.md`: parses the `| Rule | Enforced by |` table and flags rows enforced only by prose/docs.
   - Body: the pyramid levels (architecture > types > lint naming the fix > test > docs last), prove each check fails on a real past mistake, keep the rule table, and the reply format.
4. **feedback-to-architecture** (S1 project prompt, S7, S8)
   - `scripts/collect_feedback.py --repo REPO [--pr-comments JSON] [--chat MD] [--out feedback.json]`: normalizes PR review comments (the `gh api` JSON shape: body/path/line/user), commit-history signals, and chat markdown into one list with a source for every item.
   - `scripts/plan.py feedback.json --out-dir DIR`: clusters items into themes and writes `REFACTOR_PLAN.md` (per theme: evidence, pyramid level chosen, target design, steps, proof) and `lint-rules.json` (patlint format) for themes that can be linted.
   - Body: how to fetch PR comments with `gh` (read-only), and how to run patlint with the generated rules to prove the rule fires on the real past mistake.
5. **architect** (S8): body only, plus `scripts/sketch_check.py SKETCH.md`, which checks for >= 2 structurally distinct candidates, a red-flags screen, and a synthesis decision.
6. **sharpen** (S9, S10; also covers in-your-own-words): body only, plus `scripts/brief.py "task"`, which writes a brief template (in-your-own-words restatement, expert approach, over/under-spec, outside check).
7. **formal-verification** (S2, S6): body covers target choice, tool choice (TLA+ default for orderings; Kani for Rust; generated inputs when no verifier), able-to-fail, and findings-stay-findings. Ships `scripts/bounded_check.py MODEL.py`, a stdlib explicit-state model checker. MODEL.py defines `init()`, `actions(state) -> [(name, next_state)]`, and `invariants = {name: fn}`. It does BFS to depth N and prints a counterexample trace on violation. `scripts/tla_available.sh` reports whether java + tla2tools are present.
8. **curator** (manager). Researches the latest practice on coding-agent reliability, pstack, the inverted pyramid, verification skills, feature maps, formal verification, and agent-friendly architecture, then iterates this collection.
   - `scripts/upstream_diff.sh`: sparse-clones/pulls cursor/plugins pstack into a cache dir (`$POTETO_CACHE`, default `~/.cache/poteto-skills`), diffs it against `research/upstream/`, and lists new/changed upstream skills with GitHub URLs.
   - `scripts/curate.sh [--propose|--apply] [--offline]`: the cron entry. It (1) runs upstream_diff, (2) optionally runs headless `claude -p --model opus --effort max` with `references/curator-prompt.md` to web-search X/GitHub/blogs/papers/docs and write `proposals/YYYY-MM-DD.md` (every item with a source URL, a pyramid level, and a proposed change), (3) in `--apply` mode makes the change on a local branch `curator/YYYY-MM-DD`, runs `scripts/verify.sh`, appends to CHANGELOG.md, and commits only if verify passes. It never pushes. It logs to `logs/curator/`. `--offline` skips the LLM step (used in tests).
   - `references/curator-prompt.md`: a paragraph that works as a scheduled-routine prompt.
   - Body: the cadence (daily propose, weekly apply), the source list, and the acceptance bar (a real fetched URL; a change that improves determinism beats more prose).

## Example repo `examples/todo-app`
A small Python stdlib app with an HTTP JSON API (`python -m todo serve --port N --data DIR`, routes GET /health, GET/POST /todos, POST /todos/<id>/done, DELETE /todos/<id>), a CLI (`python -m todo add|list|done|rm`), JSON-file storage, a state machine for todo status (open -> done -> archived), and unittest tests (`python -m unittest`). Its own git history should contain realistic, repeated mistakes for /correct and feedback-to-architecture to find: at least 2 commits that write the JSON file directly instead of through `store.py`, a revert, and "fix:" commits. Add `review-comments.json` (gh api shape) and `chat-feedback.md` complaining about the same classes. AGENTS.md has a rule table with one rule enforced only by prose.

## scripts/verify.sh
Checks: every `skills/*/SKILL.md` exists with valid frontmatter (name == dir, description non-empty, contains "Use"), each script is executable with a shebang and `--help` exits 0, `python3 -m py_compile` passes for all .py, `bash -n` passes for all .sh, README/MAPPING/CHANGELOG/install.sh exist, MAPPING lists every skill, install.sh `--dry-run` into a temp HOME works, the example app's tests pass, and `logs/examples/summary.json` exists with every skill `pass` (or `--run-examples` regenerates it). Print a PASS/FAIL table and exit non-zero on any FAIL.
