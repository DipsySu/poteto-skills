---
name: sharpen
description: "Sharpen a task before starting it: restate the goal in your own words, ask how a top expert would approach it (raw sources, method, prior art), check whether it is over- or under-specified, and commit to one outside check that the result is right. brief.py writes the brief and checks it. Use for /sharpen, /in-your-own-words, or before any task where the request and the real goal could differ."
---

# Sharpen

A few minutes of framing before the work starts decides whether the work is right. Don't narrate the framing to the user. Use it, then do the work.

`$SKILL_DIR` is the directory holding this file.

```bash
python3 $SKILL_DIR/scripts/brief.py "<the task, in the user's words>" --out BRIEF.md   # lists repo files matching the task
python3 $SKILL_DIR/scripts/brief.py --check BRIEF.md                                  # exit 1 while blanks remain
```

1. **In your own words.** Restate what the user is really after and the problem behind the request. If your restatement and the literal request differ, act on the goal and say so in one line. That is the whole of /in-your-own-words.
2. **Expert approach.** How would a top practitioner do this? Their raw sources (start from the files `brief.py` lists, then add the docs, data, or upstream code they would read), their method, and a known-good reference to compare against.
3. **Specification check.** Loosen constraints that don't serve the goal as readily as you add ones that do. Leave choices that belong to the user open; ask only when the work can't proceed without them. Fold in only what makes the work better, not bigger.
4. **Outside check.** Always keep one check outside your own judgment: a command, a reference output, a dataset, or a named person. Write the command and the pass condition. `--check` fails without a runnable command or a URL.

Keep `BRIEF.md` out of commits unless the user asks for it. In the final reply, report the outside check's result, not the brief.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan). `brief.py` is new to poteto-skills.

- S9 pstack `sharpen`: https://github.com/cursor/plugins/blob/main/pstack/skills/sharpen/SKILL.md
- S10 pstack `in-your-own-words`: https://github.com/cursor/plugins/blob/main/pstack/skills/in-your-own-words/SKILL.md
- S1 inverted pyramid post ("Before starting, /sharpen + /in-your-own-words"): https://x.com/poteto/status/2111133080526495985
