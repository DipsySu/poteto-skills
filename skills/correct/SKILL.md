---
name: correct
description: "Find the mistakes agents keep repeating in a repo and make each one impossible: architecture first, then types, then a lint whose message names the fix, then a test, docs last. Mines git history and review feedback into mistake classes, proves every new check fails on a real past mistake, and keeps the AGENTS.md rule table honest. Use for /correct, when an operator corrects the same mistake twice, or after a revert."
---

# Correct

A correction you have to give twice is a defect in the repo, not in the agent. Change the repo so the next agent can't make the mistake.

Design for the worst-case contributor: an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. A change that looks right from one file must be right for the whole repo.

`$SKILL_DIR` is the directory holding this file. All scripts: Python 3 stdlib, `--help`, `--json`.

## 1. Mine the classes

```bash
gh api repos/OWNER/REPO/pulls/comments --paginate > review-comments.json   # optional, read-only
python3 $SKILL_DIR/scripts/mine_mistakes.py . --since "90 days ago" --feedback review-comments.json chat-notes.md
```

- Reads reverts and the commits they revert, fix commits (message + changed identifiers), HACK/FIXME/WORKAROUND comments, review comments, and AGENTS.md/CLAUDE.md rules.
- A class counts once it happened twice (`--min-count 2`; a revert and its target are one occurrence). Leave "seen once" items alone.
- `REPEAT` = a rule already exists and nothing that runs enforces it. Those go first, fixed higher up, in the same change.
- The clustering is a start, not a verdict. Read the evidence; split or merge classes and say why.

## 2. Pick the highest level that works

1. **Architecture.** Make the mistake unwritable: one owner per piece of state, one supported way per task, internals hidden so the wrong import fails, one source of truth instead of hand-synced lists. Delete old ways and dead copies an agent would imitate.
2. **Types.** The bad state can't be constructed: enums, newtypes, validated constructors at the boundary, no stringly-typed status.
3. **Lint.** Bad code still compiles: add a patlint rule whose message names the file, function, or type to use instead. Already common? Ratchet: fail only when a change adds more.
4. **Test.** The behavior through the public entry point. Fix or delete tests that pass when the behavior breaks or fail when only internals change.
5. **Docs and agent rules.** Last, for judgment calls only. Nothing fails when an agent skips them.

Human review is not a level. A reviewer who must catch the same mistake on every PR is the problem.

## 3. Fix and prove, one commit per class

Most frequent class first. Rules file format: [`references/lint-rules.example.json`](references/lint-rules.example.json) (`id`, `regex`, `include`, `exclude`, `message`; patlint rejects a message that doesn't name the fix).

```bash
P="python3 $SKILL_DIR/scripts/patlint.py --rules lint-rules.json"
$P --rev <fix-sha>^ <files>     # exit 1: fires on the real past mistake
$P --rev <fix-sha>  <files>     # exit 0: quiet on the fix
$P --baseline .patlint-baseline.json --update-baseline .   # adopt existing hits once (ratchet)
$P --baseline .patlint-baseline.json .                     # the command CI runs
```

- Every new check must fail on a real past mistake: a reverted commit, the parent of a fix commit, or the line a reviewer flagged. A check you never saw fail proves nothing.
- Types and architecture: show the old code no longer type-checks, imports, or passes its test.
- Run the same command locally and in CI. CI has no skills dir, so vendor the linter (one stdlib file): `cp $SKILL_DIR/scripts/patlint.py tools/patlint.py`, commit `lint-rules.json`, and call `python3 tools/patlint.py ...` from the existing test/CI entry point.
- Exceptions live on the offending line (or a comment line directly above), with a reason, an expiry, and a human approver. Expired or incomplete ones fail:
  `# patlint: allow <rule-id> reason=<why> expires=YYYY-MM-DD approved-by=<human>`

## 4. Keep the rule table

The agent instruction file pairs each rule with what enforces it.

```bash
python3 $SKILL_DIR/scripts/rule_table.py add AGENTS.md --rule "Write todos only via store.save()" \
  --enforced-by '`python3 tools/patlint.py --rules lint-rules.json .`'
python3 $SKILL_DIR/scripts/rule_table.py check AGENTS.md   # exit 1: prose-only rows, or enforcer files that don't exist
```

- When the operator corrects you: fix the mistake and add the row in the same change.
- A prose-only row that gets broken again is a repeat. Move it up the pyramid now.
- Drop a row once its mistake can't happen anymore.

## Reply

Per class: the evidence (shas, comment URLs, file:line), the level you picked, why a higher level didn't work, the proof (the command, exit 1 on the past mistake, exit 0 now), and the rule-table row.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan; see `research/upstream/PSTACK_LICENSE` in poteto-skills).

- S7 pstack `correct`: https://github.com/cursor/plugins/blob/main/pstack/skills/correct/SKILL.md
- S15 Guide 09, make it yours: https://github.com/cursor/plugins/blob/main/pstack/docs/guide/09-make-it-yours.md
- S16 principle-encode-lessons-in-structure: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-encode-lessons-in-structure/SKILL.md
- S1 inverted pyramid post: https://x.com/poteto/status/2111133080526495985
