/correct

Run this skill for real, end to end, on this repository (pixel-agent-garden, local branch `poteto-skills-trial`). Follow `.claude/skills/correct/SKILL.md` exactly and use its helper scripts (`mine_mistakes.py`, `patlint.py`, `rule_table.py`).

1. Mine the mistake classes from this repo's real git history (all ~200 commits: fix commits, reverts, follow-up fixes), from AGENTS.md/CLAUDE.md, and from comments that explain workarounds. A class counts only once it has happened at least twice. Cite commit shas.
2. Pick the top class and apply the highest pyramid level that works (architecture > types > a lint/CI check whose error names the fix > a test > docs last). Explain why each higher level didn't work. Make it ONE local commit on `poteto-skills-trial` (do not push; the verify-pixel-agent-garden skill files and the .gitignore change already in the working tree may go in a separate earlier commit titled "verify: add verify-pixel-agent-garden skill").
3. Prove the new check FAILS on a real past offending commit (check it out in a `git worktree` under /tmp and run the same check there) and PASSES at HEAD. Run the same command you would put in CI. Remove the worktree afterwards.
4. Update the rule table in AGENTS.md (rule | enforced by) and run `rule_table.py check AGENTS.md`.

Box constraints (hard rules): the machine is shared (16 GB RAM, no swap, about 4 GB free). For any cargo command, use `CARGO_BUILD_JOBS=2` and only `-p` for the core and CLI crates; never build `crates/tauri-app`. Run nothing in parallel. Kill only processes you started. Do not touch /home/box/developer/pay-module or docker. Never `git push`. Don't read the real ~/.claude, ~/.codex or ~/.cursor data.

Reply with: each class with its evidence and the level you picked (and why a higher level didn't work), the commit sha, the exact proof commands with their exit codes and output at the offending commit and at HEAD, the rule_table result, and any problem with the skill's instructions or scripts. For each problem, say whether it is a poteto skill bug, a Grok incompatibility, or a repo property.
