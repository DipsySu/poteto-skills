# Refactor plan: make repeated feedback impossible

Generated 2026-10-19 by `plan.py` from `/tmp/poteto-examples-1b66nnil/feedback.json` (13 items: 3 chat, 6 commit, 4 review). Repo: `/tmp/poteto-examples-1b66nnil/todo-app`.

Each theme is a cluster of feedback seen at least 2 times. Its level is the highest that can work: architecture > types > lint whose message names the fix > test > docs. Target designs are drafts; refine them with /architect before implementing.

## Summary

| Theme | Items | Level | Lint rule | Proof on history |
|---|---|---|---|---|
| T1 store.py / bypassing / direct | 7 | architecture | `no-rows-append-outside-store` | proven on 2fd7988731eb^, 97a3f3000122^, 570c1e91c258 |
| T2 done / status / transition | 5 | types | none | - |

## T1: store.py / bypassing / direct

Occurrences: 7 (chat×2, commit×4, review×2). Signature: store.py, bypassing, direct, json, store, writes.

### Evidence

- review   review-comments.json#102 @reviewer todo/api.py:1 "Direct JSON writes bypassing store.py repeat in the HTTP handler. Use Store.add; only sto…"
- review   review-comments.json#101 @reviewer todo/__main__.py:1 "Direct JSON writes bypassing store.py again: store.path.write_text(json.dumps(rows)) lose…"
- fix      2fd7988731 2026-09-18 fix: remove direct JSON writes bypassing store.py; use Store.add atomic transac…  [todo/api.py]
- fix      dbd4400868 2026-09-15 fix: persist API additions with direct JSON writes bypassing store.py  [todo/api.py]
- revert   97a3f30001 2026-09-09 Revert "fix: persist CLI additions with direct JSON writes bypassing store.py"  [todo/__main__.py]
- reverted 570c1e91c2 2026-09-07 fix: persist CLI additions with direct JSON writes bypassing store.py  [todo/__main__.py]
- chat     chat-feedback.md:4 "Direct JSON writes bypassing store.py lose concurrent updates; centralize persistence and…"
- chat     chat-feedback.md:3 "Direct JSON writes bypassing store.py happened in the CLI and HTTP handler. Use Store.add…"

### Level

**architecture**: the evidence describes bypassing an owner (8 owner/bypass signals) and names the supported way `Store.add`.
Why not higher: already the highest level; keep the lint as a ratchet while call sites migrate.
Named in evidence: bad `json.dumps`, `rows.append`, `store.list_all()`; fix `Store.add`.

### Target design (draft)

Make `Store` the single owner: callers use `Store.add`, nothing else touches what `json.dumps`, `rows.append`, `store.list_all()` touched. Hide or delete the second path (move the internals behind `Store`, delete copies an agent could imitate), so a change that looks right from one file is right for the repo. Keep `no-rows-append-outside-store` as a ratchet until no call site is left.

### Steps

1. Run /architect on the owner boundary: sketch the owner API (Store.add) and the module layout, two candidates.
2. Route every call site through the owner; list them with the lint: `python3 /home/box/developer/poteto-skills/skills/correct/scripts/patlint.py --rules /tmp/poteto-examples-1b66nnil/plan/lint-rules.json --root /tmp/poteto-examples-1b66nnil/todo-app /tmp/poteto-examples-1b66nnil/todo-app`.
3. Delete the old path and any helper an agent could copy.
4. Keep the lint with a baseline while migrating; drop the AGENTS.md rule once the lint makes the mistake unmergeable.

### Proof

Rule `no-rows-append-outside-store` in `/tmp/poteto-examples-1b66nnil/plan/lint-rules.json`: regex `(?:\brows\.append\b|\bstore\.path\.write_text\b)`, include ['**/*.py'], message: Use `Store.add` instead of `rows.append`, `store.path.write_text` (todo/store.py owns this). Theme T1: store.py / bypassing / direct.
- fix 2fd7988731: 2 hit(s) at `2fd7988731eb^`, 0 at `2fd7988731eb`: fires on the mistake and not on the fix. Rerun: `python3 /home/box/developer/poteto-skills/skills/correct/scripts/patlint.py --rules /tmp/poteto-examples-1b66nnil/plan/lint-rules.json --root /tmp/poteto-examples-1b66nnil/todo-app --rev '2fd7988731eb^' todo/api.py`
- fix dbd4400868: 0 hit(s) at `dbd440086858^`, 2 at `dbd440086858`: does NOT distinguish mistake from fix. Rerun: `python3 /home/box/developer/poteto-skills/skills/correct/scripts/patlint.py --rules /tmp/poteto-examples-1b66nnil/plan/lint-rules.json --root /tmp/poteto-examples-1b66nnil/todo-app --rev 'dbd440086858^' todo/api.py`
- revert 97a3f30001: 2 hit(s) at `97a3f3000122^`, 0 at `97a3f3000122`: fires on the mistake and not on the fix. Rerun: `python3 /home/box/developer/poteto-skills/skills/correct/scripts/patlint.py --rules /tmp/poteto-examples-1b66nnil/plan/lint-rules.json --root /tmp/poteto-examples-1b66nnil/todo-app --rev '97a3f3000122^' todo/__main__.py`
- reverted 570c1e91c2: 2 hit(s) at `570c1e91c258`, 0 at `570c1e91c258^`: fires on the mistake and not on the fix. Rerun: `python3 /home/box/developer/poteto-skills/skills/correct/scripts/patlint.py --rules /tmp/poteto-examples-1b66nnil/plan/lint-rules.json --root /tmp/poteto-examples-1b66nnil/todo-app --rev 570c1e91c258 todo/__main__.py`
- HEAD: 0 hit(s) now. Rerun: `python3 /home/box/developer/poteto-skills/skills/correct/scripts/patlint.py --rules /tmp/poteto-examples-1b66nnil/plan/lint-rules.json --root /tmp/poteto-examples-1b66nnil/todo-app /tmp/poteto-examples-1b66nnil/todo-app`. After the refactor this must be 0, or held by a ratchet baseline.
Record the enforcement: `rule_table.py add AGENTS.md --rule "..." --enforced-by "..."` (from the correct skill).

## T2: done / status / transition

Occurrences: 5 (chat×1, commit×2, review×2). Signature: done, status, transition, machine, reject, state.

### Evidence

- review   review-comments.json#104 @reviewer todo/store.py:1 "Status transition done to done accepted again. Reject repeated completion in the shared s…"
- review   review-comments.json#103 @reviewer todo/store.py:1 "Status transition open to archived skips done. Keep the forward-only state machine."
- fix      8801d95dfc 2026-10-06 fix: reject repeated status transition done to done; enforce forward-only state…  [todo/store.py]
- fix      86baa1827c 2026-09-25 fix: reject status transition open to archived; cannot skip done  [todo/store.py]
- chat     chat-feedback.md:5 "Status transition bugs returned twice: skipping done and accepting done to done. Reject i…"

### Level

**types**: the evidence is about values or states that should not be writable (16 state/type signals).
Why not higher: no single owner or bypass is named, so moving code alone would not stop the bad state being constructed.
Named in evidence: bad -; fix `TRANSITIONS.get`.

### Target design (draft)

Model the values in this theme (done, status, transition, machine, reject) as a type the bad state cannot be written in: an enum or newtype plus one transition function owned by one module, validated once at the boundary. Callers stop passing raw strings.

### Steps

1. Sketch the type (enum/newtype + transition table) with /architect.
2. Convert the boundary (CLI/HTTP/storage parsing) to construct the type once; internal code takes the type.
3. Make illegal transitions raise in one function; delete scattered string comparisons.
4. Add a test that the illegal transition from the evidence is rejected.

### Proof

No lint rule: no bad code pattern named in the evidence.
Prove the change with a test that fails on the mistake revision and passes after it.
Record the enforcement: `rule_table.py add AGENTS.md --rule "..." --enforced-by "..."` (from the correct skill).
