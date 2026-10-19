# Research notes: Lauren Tan (@poteto) — inverted pyramid + pstack

Researched on 2026-10-19 (Asia/Taipei). Every claim below has a source. **Status**: `[fetched]` = I fetched and read the page or file myself; `[secondary]` = third-party summary that I fetched (attributed to Lauren but not her own text); `[unverified]` = cited by a secondary source but I did not fetch the original.

Upstream snapshot used: `cursor/plugins` @ `6b78e9f4e46c75d1697d837e51f07c2692e60b24` (commit time 2026-10-19 09:51 Asia/Taipei), pstack `0.15.27`, MIT license (copy at `research/upstream/PSTACK_LICENSE`). The verbatim upstream files I based skills on are in `research/upstream/`.

## Sources

| ID | Source | Status |
|---|---|---|
| S1 | Original inverted pyramid post: https://x.com/poteto/status/2111133080526495985 (2026-10-17 00:32 Asia/Taipei), full note text fetched through the X API | [fetched] |
| S2 | /create-formal-verification-skill launch post: https://x.com/poteto/status/2111926622278521253 (2026-10-19 05:05 Asia/Taipei) | [fetched] |
| S3 | pstack README: https://github.com/cursor/plugins/blob/main/pstack/README.md | [fetched] |
| S4 | create-verification-skill: https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md (+ `references/feature-map-example/`) | [fetched] |
| S5 | maintain-verification-skill: https://github.com/cursor/plugins/blob/main/pstack/skills/maintain-verification-skill/SKILL.md | [fetched] |
| S6 | create-formal-verification-skill: https://github.com/cursor/plugins/blob/main/pstack/skills/create-formal-verification-skill/SKILL.md | [fetched] |
| S7 | correct: https://github.com/cursor/plugins/blob/main/pstack/skills/correct/SKILL.md | [fetched] |
| S8 | architect: https://github.com/cursor/plugins/blob/main/pstack/skills/architect/SKILL.md | [fetched] |
| S9 | sharpen: https://github.com/cursor/plugins/blob/main/pstack/skills/sharpen/SKILL.md | [fetched] |
| S10 | in-your-own-words: https://github.com/cursor/plugins/blob/main/pstack/skills/in-your-own-words/SKILL.md | [fetched] |
| S11 | recall: https://github.com/cursor/plugins/blob/main/pstack/skills/recall/SKILL.md | [fetched] |
| S12 | poteto-mode: https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md | [fetched] |
| S13 | Guide 06, verify and ship: https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md | [fetched] |
| S14 | Guide 08, principles: https://github.com/cursor/plugins/blob/main/pstack/docs/guide/08-principles.md | [fetched] |
| S15 | Guide 09, make it yours: https://github.com/cursor/plugins/blob/main/pstack/docs/guide/09-make-it-yours.md | [fetched] |
| S16 | principle-encode-lessons-in-structure: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-encode-lessons-in-structure/SKILL.md | [fetched] |
| S17 | principle-prove-it-works: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-prove-it-works/SKILL.md | [fetched] |
| S18 | principle-build-the-lever: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-build-the-lever/SKILL.md | [fetched] |
| S19 | Claude Code adapter: https://github.com/cursor/plugins/blob/main/pstack/docs/harness/claude-code.md | [fetched] |
| S20 | Barnaby Robson, "On pstack" (2026-10-05): https://barnabyrobson.org/on-pstack/ | [secondary] |
| S21 | Meng Shao's summary of S1: https://badinfluence.news/items/vz3iwsfefptsq3pc5c26imxyg | [unverified] (search snippet only; full summary not fetched) |
| S22 | "The Complete Guide to pstack" Part 1 (2026-08-31) and Part 2 (2026-09-10), "Loops You Can Trust" (2026-06-24), the 2026-08-20 "gardener" post, and the 2026-08-16 "order of value" post | [unverified] cited by S20; I did not fetch the originals |
| S23 | Matt Pocock interview "Poteto on shipping 1,000 PRs a month" (2026-10-03) | [unverified] cited by S20 |

## 1. The inverted pyramid (S1)

Effort goes mostly to the top layer and least to the bottom one. Layers in priority order:

1. **Codebase improvements**: better architecture, stronger types and invariants, clear boundaries. "agents love to copy what exists and take the shortest path, so make the easy thing the right thing. categorically eliminate problems through code and you won't need to think about it again." — S1
2. **Static analysis**: lint rules, tests, CI, verification (for example TLA+). "very token efficient ways to catch issues ahead of time." — S1
3. **Prose**: comments, rules, AGENTS.md, skills, meaning anything that can't be enforced and depends on the agent's judgment. "use it minimally, but do use /create-verification-skill." — S1
4. **Human review**: the hardest layer to scale. Spend it on "designing an environment where agents do the right thing by default". — S1
- Why it is inverted: codebase improvements are deterministic, so they are more token-efficient and "lets you sleep more soundly at night". — S1
- The post's suggested project prompt: mine chat and review feedback, `/recall` past transcripts, then `/poteto-mode use /correct and /architect on past commits` to find common pitfalls and plan an agent-friendly re-architecture. Answer open questions by prototyping and back the plan with real data. Before starting, `/sharpen` + `/in-your-own-words`. — S1

## 2. Repeated mistakes: fix at the highest level (S7, S15, S16)

- Order: (1) eliminate the problem with architecture: one owner per piece of state, one supported way per task, hide internals, a single source of truth, delete old ways. (2) Use types so the bad state can't be written. If bad code still compiles, add a lint/CI check **whose error names the fix**. If the pattern is already common, fail only when a change adds more (ratchet). (3) Test the behavior. (4) Docs and agent rules come last and only for judgment calls. — S7
- A mistake class counts once it has happened **twice**. Prove that each new check fails on a real past mistake. Run the same command locally and in CI. Exceptions go inline with a reason, an expiry date, and a human's approval. — S7
- Keep a **rule table** in the agent instruction file that pairs each rule with what enforces it. A rule with no enforcement that gets broken again is a repeat, so fix it at a higher level. Drop a rule once its mistake can't happen. — S7
- Human review isn't on the list. "A reviewer who must catch the same mistake on every PR is the problem this fixes." — S15
- Encode lessons in structure: "If the fix is structural, only use the structural fix. The instruction is the symptom." — S16
- Design assumption: every contributor is an agent that "sees only the files it opened, copies the nearest example, and takes the shortest path that compiles". — S7, S8

## 3. Verification as infrastructure (S4, S5, S13, S17, S18, S20)

- Every serious project needs a scripted way to **launch the real app, drive it the way a user would, and capture evidence**. — S4
- The generated `verify-<app>` skill has these sections: **Launch** (exact command, a usable-ready signal, starts from an empty data dir, teardown), **Doctor** (one read-only check that the instance is worth driving), **Drive** (real selectors and commands, stable handles), **Evidence** (the real user path, the action plus the resulting state, side effects, mocks only at production boundaries, dry-run claims checked by observation), **Cleanup** (kill only what you started, keep the evidence), **Helpers** (scripts are executable and their invocation is shown). — S4
- **Feature map**: `features/README.md` plus one file per user-facing feature (start with the top 3–5), with exactly four H2s in order: `Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, `Gotchas`. — S4, example in `research/upstream/create-verification-skill/feature-map-example/`
- Prove the generated skill end to end once before handing it over (launch → doctor → drive one feature → evidence → cleanup). "If that proof fails, don't use the output." — S4, S13
- Commit the skill so every person and agent drives the app the same way. When agents keep writing throwaway scripts, build a small control CLI: composable commands, `--dry-run`, rich `--help`, JSON output, errors that say what to do. — S13, S18
- Maintenance: outcomes are `clean | changed | blocked`. Edit only the verify skill's own directory and never product code. Pass: index hygiene → per-feature source readers → reconcile → **live pass is required even when the source looks clean** → triage (doc drift / harness gap / product gap) → at most one PR. — S5
- Lauren calls verification "the single most important skill", meaning it gives agents "hands and eyes". She recommends a daily `/maintain-verification-skill` routine. — S20 (attributing S22/S23)
- Prove it works: verify against the real artifact, not a proxy. Prefer a deterministic script whose output is kept as an artifact. — S17

## 4. Formal verification (S2, S6)

- `/create-formal-verification-skill` (beta, pstack 0.15.27) is a meta-skill. It picks the target where a bug costs the most and tests reach the least, picks the tool that fits the question (TLA+ for orderings and concurrency, Kani for Rust UB, a proof assistant where bounds aren't enough, generated-input tests when no verifier exists), and leaves a project-local skill behind. — S2, S6
- Quality bar: the model is true to the code (each part points at real code), it is **able to fail** (show each property failing on a broken variant), and findings stay findings (never weaken the property to make it pass). Prove it by running the generated skill on a scratch change that breaks the property. — S6

## 5. Other pstack skills referenced in the brief

- `/architect`: sketch types, signatures, and modules with `not implemented` bodies. Ground → Sketch (at least two structurally distinct candidates, screened against design red flags) → Agree (opt-in) → Implement against the sketch → Scrap when friction becomes a pattern. — S8
- `/sharpen`: before starting, ask how a top expert would approach the task, what the user is really after, and whether the task is over- or under-specified. Always keep one outside check that the result is right. — S9
- `/in-your-own-words`: restate the user's goals and the problem. — S10
- `/recall`: rebuild recent working context from transcripts and the shared record. Lock the scope and output a capsule, threads, problems, and the next move. — S11
- `/poteto-mode`: a router that picks a playbook, names each applied principle and the decision it changed, and verifies the work. — S12, S14

## 6. Implications for this collection (my synthesis, not a quote)

- Skills are prose (layer 3), so each skill here should push its **output** up the pyramid: generate scripts, lint rules, and tests instead of more prose.
- Every skill ships a deterministic helper script, so "running the skill" leaves artefacts that can be re-run (S17, S18).
- The curator must not auto-apply unsourced changes. It writes proposals with source URLs, and every applied change goes through `scripts/verify.sh` and a CHANGELOG entry.
