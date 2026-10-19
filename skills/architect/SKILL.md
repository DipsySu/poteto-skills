---
name: architect
description: "Sketch types, signatures, and module boundaries before writing code: ground in the real code, design at least two structurally distinct candidates, screen them against design red flags, record a synthesis decision that sketch_check.py verifies, then implement against the sketch and scrap it when friction becomes a pattern. Use for /architect, \"design this\", or non-trivial work where jumping straight to code would lock in the wrong shape."
---

# Architect

Decide the shape before the code. Sketch types, signatures, and module boundaries with stub bodies, compare whole-shape alternatives, then implement against the chosen sketch.

`$SKILL_DIR` is the directory holding this file. Open a todo list with the five phases: Ground, Sketch, Agree, Implement, Scrap.

## 1. Ground

Build a real model of every system the change touches. Read the code paths end to end; naming a file isn't grounding. For each system, write down who owns the state, the entry points, the invariants, and the constraint it puts on the design. If the change moves ownership or layering, find out why the current shape exists (`git log -S`, blame, the PR that introduced it), so the rationale becomes a constraint instead of a guess. Skip this only for true greenfield work.

## 2. Sketch: design it twice

```bash
python3 $SKILL_DIR/scripts/sketch_check.py --template > SKETCH.md
```

- Write the usage first (the caller's code), then derive the types from it.
- Draft at least two structurally distinct candidates: different ownership, module boundaries, or data flow. Point fixes inside one shape don't count. When you can, have a second model or subagent draft one candidate so the alternatives don't share your blind spots.
- Stub every body (`raise NotImplementedError`, `...`, `todo!()`).
- Screen each candidate against [`references/design-red-flags.md`](references/design-red-flags.md), for a contributor that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. Prefer the design where a change that looks right from one file is right for the whole repo.
- Compare interface depth. Prefer the design that hides more complexity behind a smaller public surface.
- Write the synthesis decision: the chosen candidate, what it borrows from the others, and what you rejected and why.

```bash
python3 $SKILL_DIR/scripts/sketch_check.py SKETCH.md    # must exit 0 before you implement
```

## 3. Agree (opt-in)

By default, proceed to implementation. Stop for sign-off only when asked ("with checkpoint", "show me before implementing"). The sketch can ship as its own commit (scaffold first). Treat any pushback on the shape as new grounding: return to phases 1 and 2 before writing more code.

## 4. Implement against the sketch

Replace stubs with code. The sketch is the contract. If a function needs a parameter or type the sketch didn't anticipate, surface it as a signal: the sketch was wrong, a requirement was missed, or the implementation is overreaching. Don't absorb it silently. Put each rule the design relies on into structure: a type, a patlint rule (/correct), or a test.

## 5. Scrap when the shape is wrong

Scrap on a pattern, not on one instance:

- the same workaround in unrelated places
- several unrelated edge cases that each need a special branch
- types that need escape hatches (`any`, casts, optional fields that are always set)
- a lock showing up where the sketch said the state wasn't shared
- callers that must know the abstraction's internal rules
- two or more independent deviations of the same shape during implementation

To scrap: re-ground on what has been built, then redesign as if the new constraints had been there from day one. Make the new sketch smaller than the old one before it grows. Then rerun phase 2.

**Reply:** the chosen candidate, the rejected ones and why, the `sketch_check.py` result, and every deviation you surfaced during implementation.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan). `sketch_check.py`, the template, and the red-flag list are new to poteto-skills.

- S8 pstack `architect`: https://github.com/cursor/plugins/blob/main/pstack/skills/architect/SKILL.md
- S14 Guide 08, principles (exhaust the design space, subtract before you add, redesign from first principles): https://github.com/cursor/plugins/blob/main/pstack/docs/guide/08-principles.md
- S1 inverted pyramid post (/architect on past commits to plan an agent-friendly re-architecture): https://x.com/poteto/status/2111133080526495985
