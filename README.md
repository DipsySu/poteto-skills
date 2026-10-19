# poteto-skills

Agent skills for Claude Code and Cursor that push their own output up the inverted pyramid. Each skill is prose, but what it leaves behind is a script, a lint rule, a test, a model check, or a type, so the next agent gets a check that fails instead of an instruction it can skip.

Built on [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan ([@poteto](https://x.com/poteto)), MIT licensed. The skills here adapt pstack's ideas in new words and add deterministic helper scripts. See [Credits](#credits).

## The inverted pyramid

From Lauren Tan's [post](https://x.com/poteto/status/2111133080526495985) (S1 in [research/NOTES.md](research/NOTES.md)). Put the most effort into the top layer and the least into the bottom:

1. **Codebase improvements**: architecture, types, invariants, clear boundaries. Agents copy what exists and take the shortest path, so make the easy thing the right thing.
2. **Static analysis**: lint rules, tests, CI, verification (for example TLA+). Cheap, deterministic, run on every change.
3. **Prose**: comments, rules, AGENTS.md, skills. Use it minimally; nothing fails when it is ignored.
4. **Human review**: the hardest layer to scale. Spend it on designing an environment where agents do the right thing by default.

## Skills

| Skill | Use it for | Leaves behind |
|---|---|---|
| [`/create-verification-skill`](skills/create-verification-skill/SKILL.md) | an app agents can't yet launch, drive, and prove | `verify-<app>/` with a feature map and a `control.sh` harness (launch, doctor, drive, evidence, cleanup, prove) |
| [`/maintain-verification-skill`](skills/maintain-verification-skill/SKILL.md) | keeping that map honest, daily | a drift report (exit 1), a live pass, at most one PR |
| [`/correct`](skills/correct/SKILL.md) | mistakes agents repeat | mistake classes from history, `patlint` rules proven on past mistakes, an enforced rule table |
| [`/feedback-to-architecture`](skills/feedback-to-architecture/SKILL.md) | review feedback that keeps coming back | `REFACTOR_PLAN.md` plus lint rules replayed against git history |
| [`/architect`](skills/architect/SKILL.md) | non-trivial design | a checked `SKETCH.md`: two or more distinct candidates, a red-flag screen, a synthesis decision |
| [`/sharpen`](skills/sharpen/SKILL.md) | framing a task before starting (includes in-your-own-words) | a brief with one runnable outside check |
| [`/formal-verification`](skills/formal-verification/SKILL.md) | races, retries, state machines, parsers | a model with a one-command check and mutants that prove it can fail |
| [`/curator`](skills/curator/SKILL.md) | keeping this collection current | dated, sourced proposals and verified local `curator/` branches |

[MAPPING.md](MAPPING.md) maps each skill to its upstream sources and lists every helper script.

## Install

```bash
git clone <this repo> poteto-skills && cd poteto-skills
./install.sh --dry-run            # see what it would do
./install.sh                      # symlink every skill into ~/.claude/skills and ~/.cursor/skills
./install.sh --copy --target claude
./install.sh --project ~/code/app # into ~/code/app/.claude/skills and ~/code/app/.cursor/skills
./install.sh --uninstall          # removes only what install.sh created
```

`install.sh` only creates, updates, or removes entries named after this collection's skills, and it leaves anything it didn't install alone (for example `~/.claude/skills/synced`). Install all skills together: `feedback-to-architecture` reuses `correct`'s scripts, and `maintain-verification-skill` reuses `create-verification-skill`'s.

Requirements: Python 3.11+ (developed on 3.13) using the stdlib only, bash, and git. `gh` is optional, for reading PR comments. The curator also uses `curl` and, for its online steps, the `claude` CLI. TLA+ needs Java and `tla2tools.jar`; without them, `formal-verification` falls back to its stdlib `bounded_check.py`.

## Quick start

```bash
S=~/.claude/skills
python3 $S/correct/scripts/mine_mistakes.py . --since "90 days ago"
python3 $S/create-verification-skill/scripts/scan_repo.py .
python3 $S/create-verification-skill/scripts/scaffold.py . --app myapp
python3 $S/formal-verification/scripts/bounded_check.py $S/formal-verification/references/example_model.py --mutants
```

Then invoke a skill by name in your agent, for example `/correct agents keep writing the data file directly`.

## Layout

```text
skills/<name>/SKILL.md        frontmatter (name, description) + agent-facing body + Sources footer
skills/<name>/scripts/        executable helpers, stdlib Python or bash, all with --help
skills/<name>/references/     templates and worked examples
install.sh                    symlink or copy into Claude Code / Cursor skill dirs
research/                     NOTES.md (sources S1-S23) and verbatim upstream snapshots
examples/todo-app/            example repo the helpers are exercised against
scripts/verify.sh             the collection's self-check; scripts/run-examples.sh runs every helper for real
```

## Credits

The ideas, structure, and much of the vocabulary come from [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, © 2026, MIT license ([copy](research/upstream/PSTACK_LICENSE)). Snapshots of the upstream files these skills adapt are kept verbatim in [research/upstream/](research/upstream/) under that license. Each SKILL.md ends with a Sources footer that links the upstream files it draws on. The helper scripts are new to this collection.

## Reproduce the example and checks

```bash
./examples/build-todo-app.sh           # build ignored examples/todo-app/, including its dated git history
./scripts/run-examples.sh             # fresh scratch build; exercise all eight skills serially
./scripts/verify.sh                   # packaging, syntax, installer, app tests, saved example results
./scripts/verify.sh --run-examples    # regenerate example results first
```

The builder uses committed source plus explicit history edits, fixed dates and identity; repeated builds produce the same commit IDs. It refuses a nonempty destination. The outer repo ignores `examples/todo-app/`; no nested `.git` files or gitlinks are stored. Pass a new destination to the builder to make another copy.

Each run uses `${TMPDIR:-/tmp}/poteto-examples-<run-id>` and removes its scratch directory after copying reports to `logs/examples/`. Use `--keep-scratch` for diagnosis. `summary.json` records every skill's status and assertions; `artifacts/<skill>/` retains the generated verification skill, live evidence when available, plans, model traces, and curator proposals. The curator example uses a local fake upstream with no remote or network access. Live checks require permission to open localhost sockets; denied sockets remain failures in the summary and verification gate.
