---
name: feedback-to-architecture
description: "Turn accumulated PR review comments, chat complaints, and fix/revert history into an agent-friendly re-architecture plan: themes with evidence, the pyramid level for each, a draft target design, and patlint rules already proven against the repo's own past mistakes. Use for /feedback-to-architecture, when the same review feedback keeps coming back, or before planning a refactor meant to stop a class of agent mistakes."
---

# Feedback to architecture

Repeated feedback is a design signal. Mine it, cluster it into themes, and push each theme as high up the pyramid as it goes: architecture > types > lint whose message names the fix > test > docs. The output is a plan backed by data plus lint rules that already fail on real past mistakes, not a list of new instructions.

Needs the `correct` skill installed next to this one (shared evidence readers and `patlint.py`). `$SKILL_DIR` is the directory holding this file.

## 0. Sharpen the goal

Run /sharpen first, in your own words: which mistakes should agents stop making, and what outside check proves they stopped (for example, "patlint exits 0 on HEAD and 1 on each listed commit")?

## 1. Gather feedback (read-only)

```bash
gh api repos/OWNER/REPO/pulls/comments --paginate > review-comments.json   # all inline review comments
gh api repos/OWNER/REPO/pulls/123/comments > pr-123-comments.json          # one PR
gh pr list --state merged --limit 50 --json number,title,url               # find PRs worth reading
```

- `gh` stays read-only here: never post, resolve, or edit comments.
- Chat and meeting complaints go in a markdown file, one complaint per bullet. Recurring corrections from past agent sessions go in the same file (use /recall if you have it).

```bash
python3 $SKILL_DIR/scripts/collect_feedback.py --repo . \
  --pr-comments review-comments.json --chat chat-feedback.md --out feedback.json
```

Every item keeps its source and ref (commit sha, comment URL, file:line). Spot-check three items against the originals before you plan from them.

## 2. Plan

```bash
python3 $SKILL_DIR/scripts/plan.py feedback.json --out-dir refactor/
```

Writes `refactor/REFACTOR_PLAN.md`, `refactor/lint-rules.json` (patlint format), and `refactor/plan.json`. Each theme gets evidence, a level with why-not-higher, a draft target design, steps, and proof. Then do the part the script can't:

- Merge or split themes the clustering got wrong, and drop noise. Say what you changed.
- Run /architect on every architecture or types theme: two structurally distinct sketches, a red-flag screen, a synthesis. The chosen sketch replaces the draft target design.
- Settle open questions by prototyping on a scratch branch. Back each claim with data (patlint hit counts on HEAD, the commit list), not opinion.

## 3. Prove each rule on the real past mistake

`plan.py` already replays every rule against history. `proven` means more hits at the mistake revision than at the fix. Re-run by hand after you edit a rule:

```bash
P="python3 $SKILL_DIR/../correct/scripts/patlint.py --rules refactor/lint-rules.json"
$P --rev <fix-sha>^ <files-the-fix-touched>   # exit 1: fires on the mistake
$P --rev <fix-sha>  <files-the-fix-touched>   # exit 0: quiet on the fix
$P .                                           # hits on HEAD: fix them, or ratchet with --baseline FILE --update-baseline
```

A rule that never fires on history is unproven. Tighten it, or prove it on a scratch copy of the line a reviewer flagged. Never loosen a rule until HEAD passes. Use a ratchet baseline instead.

## 4. Hand off

- Execute the plan theme by theme with /correct: one commit per theme, a rule-table row per rule.
- Reply with: each theme (occurrences, level, why not higher), its rule and the replay result, the open questions with the prototype or data that answered them, and the first commit to make.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan).

- S1 inverted pyramid post and its project prompt (mine feedback, /correct + /architect on past commits, prototype, back the plan with data): https://x.com/poteto/status/2111133080526495985
- S7 pstack `correct`: https://github.com/cursor/plugins/blob/main/pstack/skills/correct/SKILL.md
- S8 pstack `architect`: https://github.com/cursor/plugins/blob/main/pstack/skills/architect/SKILL.md
