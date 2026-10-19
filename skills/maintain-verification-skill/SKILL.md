---
name: maintain-verification-skill
description: "Keep a project's verify-<app> skill and feature map honest as the app changes: a deterministic drift check (index hygiene, H2 contract, routes and commands missing from or dead in the map), a live pass that drives every feature, and at most one PR of proven corrections. Use for /maintain-verification-skill, \"audit the verify skill\", or as a daily scheduled routine."
---

# Maintain a verification skill

A feature map starts rotting the moment the app changes. This is its upkeep loop. The unit of rigor is the feature: every feature file gets source coverage and a live drive. Not every sentence needs its own terminal run.

`$SKILL_DIR` is the directory holding this file. It needs the `create-verification-skill` skill installed next to it.

## Outcomes

Pick exactly one and say which:

- **clean**: every feature got source and live coverage, and nothing needs to ship. No branch, no PR.
- **changed**: one PR ships proven doc, harness, or map corrections.
- **blocked**: coverage couldn't finish, or a proven fix couldn't ship safely. Name the blocker.

## Edit scope

Edit only the verify skill's own directory: its SKILL.md, `features/`, and `scripts/`. Never edit product code during a run. If the map describes behavior the app no longer has, that is either doc drift (fix the map) or a product regression (report it, and don't hide it with a doc edit).

## Pass

0. **Locate the target**: the project-local skill with Launch/Drive sections and a `features/` map (`.claude/skills/verify-*`, `.cursor/skills/verify-*`). If there are several, ask which. If there is none, stop and point at /create-verification-skill.
1. **Deterministic checks.**
   ```bash
   python3 $SKILL_DIR/scripts/drift_check.py .claude/skills/verify-<app> --repo .
   python3 $SKILL_DIR/../create-verification-skill/scripts/check_verify_skill.py .claude/skills/verify-<app>
   ```
   Fix index entries (missing, dead, duplicate, extra) and H2 contract breaks first. A `missing` route or command with a source path is an unmapped feature, so add a feature file. A `dead` mention is drift: confirm it in source, then fix or delete the recipe.
2. **Source wave.** Use one read-only reader per feature file, in parallel subagents if you have them. Each explains how the feature works now, cites likely drift as `file:line`, and returns one concise live recipe. Readers never drive the app and never edit files. Return shape: summary / source entry points / drift or none / recipe.
3. **Reconcile.** Every feature file must have a summary. Merge overlapping recipes into as few app states as practical. Spot-check cited drift and don't re-prove clean claims. Sweep recent churn (`git log --since="7 days ago" --name-only`) for user-facing surfaces the map lacks. Name a source path before calling one missing.
4. **Live pass, required even when drift_check is clean.** You own all driving and follow the skill's own launch model: one long-lived instance driven serially for servers, or a fresh process per drive for CLIs. Drive every feature at least once. Hold three invariants the whole pass:
   - Run doctor before the first drive, after every failed drive, and on every fresh session. If doctor can't see a wedged state, reset or relaunch instead of hoping.
   - Evidence captured so far survives every cleanup. Check its location; don't assume.
   - Nothing a drive started outlives its usefulness. Clean up failed-iteration residue. On a shared instance, clean the residue and leave the instance.

   A doctor failure caused by skill drift is drift: fix it within scope and retry once, then call the pass blocked. A feature you can't reach is `verified-unreachable` only with the concrete prerequisite (auth, OS, external state) and the route you attempted. A prerequisite the map omits is drift.
5. **Triage.**
   - *Doc drift*: a wrong or missing user-POV description. Fix the map.
   - *Harness gap*: working behavior that `control.sh` can't drive. Fix the harness (executable, invocation shown in SKILL.md), then drive it live again before it ships.
   - *Product gap*: the app itself is broken. Record it for the user with the evidence path, and keep it out of the PR.
6. **Ship or stop.** For `changed`: re-read every changed file, rerun both checks until they exit 0, tear down after the last re-drive, and open one PR. For `clean` or `blocked`: no PR. Report the outcome and the coverage honestly.

Keep run notes (features covered, unreachable prerequisites, confirmed drift, outcome) in a scratch file. Don't commit them.

## Cadence

Run it daily, ideally from a scheduled routine so nobody has to remember. The prompt is just `/maintain-verification-skill`.

**Reply:** the outcome, features covered (source and live), drift fixed, harness gaps fixed, product gaps with evidence paths, unreachable features with their prerequisites, and the PR link or why there is none.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan). `drift_check.py` is new to poteto-skills.

- S5 pstack `maintain-verification-skill`: https://github.com/cursor/plugins/blob/main/pstack/skills/maintain-verification-skill/SKILL.md
- S13 Guide 06, verify and ship ("keep the verification skill honest", run at least daily): https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md
- S20 Barnaby Robson, "On pstack" (verification as the most important skill; daily maintenance), secondary: https://barnabyrobson.org/on-pstack/
