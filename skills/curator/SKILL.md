---
name: curator
description: "Keep this skill collection current: diff upstream pstack against the snapshot, research the latest practice on coding-agent reliability (pstack, the inverted pyramid, verification skills, feature maps, formal verification, agent-friendly architecture), write sourced proposals daily, and weekly apply the best ones on a local branch that is committed only after scripts/verify.sh passes. Use for /curator, \"update the skills from upstream\", or as the collection's scheduled routine."
---

# Curator

The skills here are prose, layer 3 of the pyramid. The curator keeps them current without letting them drift into unsourced advice. Every proposal names a fetched source, the pyramid level it lands on, and a concrete change. Every applied change passes `scripts/verify.sh` before it is committed, on a local branch that is never pushed.

`$SKILL_DIR` is the directory holding this file. Paths like `proposals/` are relative to the poteto-skills checkout.

## Cadence

- **Daily: propose.** `$SKILL_DIR/scripts/curate.sh --propose` runs `upstream_diff.sh`, then a headless `claude -p --model opus --effort max` with [`references/curator-prompt.md`](references/curator-prompt.md). That writes `proposals/YYYY-MM-DD.md`, and `check_proposals.py --fetch` must pass (it re-requests every source URL).
- **Panel:** `$SKILL_DIR/scripts/curate.sh --panel PROMPT.md` runs the read-only seats in [`references/panel.json`](references/panel.json), checks each fragment with `check_proposals.py --fragment --fetch`, and records seat output and routing in `logs/curator/`.
- **Weekly: apply.** `$SKILL_DIR/scripts/curate.sh --apply` creates branch `curator/YYYY-MM-DD` in a separate git worktree. There, Claude implements at most three proposals, a CHANGELOG line is appended, and `scripts/verify.sh --run-examples` runs. The commit happens only if verify passes. A human reviews and merges.

```cron
17 6 * * *  /path/to/poteto-skills/skills/curator/scripts/curate.sh --propose
47 6 * * 1  /path/to/poteto-skills/skills/curator/scripts/curate.sh --apply
```

For a scheduled cloud agent instead of cron, use `references/curator-prompt.md` as the routine prompt. It proposes only.

## By hand

```bash
$SKILL_DIR/scripts/upstream_diff.sh                     # changed, new, and removed upstream pstack files and skills
$SKILL_DIR/scripts/curate.sh --propose --offline        # no network or LLM: requires a populated local cache
$SKILL_DIR/scripts/curate.sh --panel PROMPT.md          # seat routing and options: curate.sh --help
python3 $SKILL_DIR/scripts/check_proposals.py proposals/2026-10-19.md --fetch --skills-dir skills
python3 $SKILL_DIR/scripts/check_proposals.py --digest logs/curator/2026-11-24.x-digest.md
$SKILL_DIR/scripts/curate.sh --apply --offline          # deterministic apply: sync changed upstream files into research/upstream/
```

Tests and dry runs should set `POTETO_CACHE` to a scratch dir and `POTETO_UPSTREAM_URL` to a local git repo, which keeps them off the network and out of `~/.cache`.

Offline proposals record cached diff evidence and source URLs; they do not claim fresh web research. Snapshot syncs retain their exact source head and version in `research/snapshot-syncs.json`.

## Sources to watch

- @poteto on X: https://x.com/poteto (S1, S2 in `research/NOTES.md`)
- pstack in cursor/plugins: https://github.com/cursor/plugins/tree/main/pstack, including its README, `skills/`, `docs/guide/`, and commit history (S3 to S19)
- Write-ups about pstack, such as https://barnabyrobson.org/on-pstack/ (S20). Fetch the original posts they cite before relying on them.
- Verification tools: TLA+ https://lamport.azurewebsites.net/tla/tla.html, Kani https://model-checking.github.io/kani/, Hypothesis https://hypothesis.readthedocs.io/
- Papers on coding-agent evaluation, verification, and repo guardrails (cite the abstract page you fetched), and the Claude Code and Cursor skill docs, since frontmatter fields and install paths change.

## Acceptance bar

1. Every proposal has a real URL that was fetched. Search snippets don't count. `check_proposals.py --fetch` checks double-quoted phrases of at least four words in Change and Why against that proposal's sources or collection files. Put proposed messages, commands, and test inputs in backticks.
2. Every proposal has a pyramid level. A change that makes a skill's output more deterministic (a script, lint, test, or type) beats more prose.
3. Every proposal names a concrete change to a named skill (or `new:<name>`, or `collection`).
4. An applied change passes `scripts/verify.sh` (including `scripts/check_docs.py`: helper paths/flags, links, counts, source IDs, and explicit README usage policies), adds a CHANGELOG line, and keeps MAPPING.md and each SKILL.md Sources footer in sync.
5. Upstream text is MIT (© 2026 Lauren Tan). Adapt it with credit; don't paste it wholesale.

Reject unsourced claims, prose that restates an existing rule, and anything that would add network access to a skill script other than the curator's.

## Safety

`curate.sh` never pushes and never touches your working tree: apply mode works in a git worktree under `$POTETO_CACHE/worktrees`. A failed verify deletes the branch and keeps the patch in `logs/curator/DATE.failed.patch`. Everything is logged to `logs/curator/DATE.log`.

## Sources

- research/NOTES.md §6 (curator rule: proposals with source URLs; every applied change goes through verify.sh and the CHANGELOG)
- S3 pstack README: https://github.com/cursor/plugins/blob/main/pstack/README.md
- S18 principle-build-the-lever: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-build-the-lever/SKILL.md
- S20 Barnaby Robson, "On pstack" (scheduled daily routines), secondary: https://barnabyrobson.org/on-pstack/
- Panel routing: [@poteto v0.15.46](https://x.com/poteto/status/2123824683862855898) and [setup-pstack](https://raw.githubusercontent.com/cursor/plugins/005d8f6e8ec057b738adcd08a08b30db0478402d/pstack/skills/setup-pstack/SKILL.md).
- Quote verification: [reframe](https://raw.githubusercontent.com/cursor/plugins/005d8f6e8ec057b738adcd08a08b30db0478402d/pstack/skills/reframe/SKILL.md) and [cursor/plugins#1324](https://github.com/cursor/plugins/pull/1324).
- Post timestamps: [Snowflake ID](https://en.wikipedia.org/wiki/Snowflake_ID) and the [misdated post](https://x.com/rachelnabors/status/2124176031011475785).
