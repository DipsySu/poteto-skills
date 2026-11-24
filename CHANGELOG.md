# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Incompatible with main:** both checkers now enforce **90 s per run / 10 s per call** by default; main enforced neither limit. Previously completing checks can now exit 3 on a run timeout, or on a call timeout exit 3 in `gen_check.py` / exit 2 in `bounded_check.py`. The run budget includes the main search and all mutants. Both checkers add `--timeout S`, `--call-timeout S` and the module-level `CHECK_TIMEOUT` run budget. `--timeout` overrides the module budget; imports initially use the CLI budget or 90 s, and module budgets include elapsed import time. Pass `--timeout 0 --call-timeout 0` to disable both limits, or disable each independently; `CHECK_TIMEOUT=0` disables the run limit after import, leaving the call limit unchanged.
- Deadline enforcement adds measurable overhead relative to main, especially for cheap model calls. On the recorded `bounded_check.py` 100,000-state / 3-invariant workload (Python 3.11.14, median of three runs), main took **0.135 s**, this branch with default limits **0.674 s (5.00×)**, and with both limits disabled **0.144 s (1.07×)**. These are workload-specific observations, not performance gates. Measure representative models and set explicit budgets; disabling both limits bypasses the model-call wrappers. Unix main-thread checks use one-shot SIGALRM deadlines with synchronous return checks; unsupported signal/thread contexts warn that limits are not enforced. Native code may delay signals, so retain an external process deadline.
- Replay migration: `gen_check.py` now uses a separate random stream per property, keyed by seed and property name. The same `--seed` can produce a different sample sequence than earlier versions. Keep the old checker version to replay old evidence exactly; revalidate recorded `--seed N --expect-fail NAME` commands on this version and preserve known counterexamples as pinned `examples` inputs.
- `patlint.py` uses git's tracked and untracked, non-ignored file set inside a checkout, excluding nested checkouts and its existing skipped directories. Baseline adoption uses tracked files only; a new rule with untracked hits stays unadopted so it can be retried after staging. Existing ratchets still refuse increases. **Incompatible exits:** an explicit git directory expanding to an empty file set, or a git lookup failure inside a checkout, now exits 2 instead of silently passing or falling back to disk. Ordinary non-git directories retain filesystem fallback.
- Crashing `bounded_check.py` mutants now report model errors (exit 2), rather than surviving mutants (exit 1). Mutant JSON includes its result, stop reason, detail and explored-state counts.

### Added

- Curator checks cited UTC dates against X status IDs without fetching. `check_proposals.py --digest FILE` also checks each post's stated time to the minute in its named zone, rejects missing times and numbered posts before an ISO window start, and reports the corrected time and fix. Sources: [Snowflake ID](https://en.wikipedia.org/wiki/Snowflake_ID), [misdated post](https://x.com/rachelnabors/status/2124176031011475785).
- Curator `check_proposals.py --fetch` checks quotations in Change and Why against each proposal's own fetched source bodies or collection files, with normalized punctuation and ordered ellipsis matching; backticked proposed text is skipped. Fetching retains up to 5 MB per source, uses GitHub raw file text and FxTwitter JSON string values, and failures name the quote and how to fix it. Offline checks explicitly report that quotes were not checked. Sources: [reframe](https://raw.githubusercontent.com/cursor/plugins/005d8f6e8ec057b738adcd08a08b30db0478402d/pstack/skills/reframe/SKILL.md), [cursor/plugins#1324](https://github.com/cursor/plugins/pull/1324).
- Curator panel mode runs read-only seats from `references/panel.json`, requires mixed vendors unless explicitly overridden, logs each seat's routing and exit code, and checks every returned fragment. H3 proposals with their own Source now receive the same acceptance checks as H2 proposals. Sources: [panel routing post](https://x.com/poteto/status/2123824683862855898), [setup-pstack](https://raw.githubusercontent.com/cursor/plugins/005d8f6e8ec057b738adcd08a08b30db0478402d/pstack/skills/setup-pstack/SKILL.md).
- Incomplete checks retain confirmed failure results and accepted target coverage, including when shrinking or formatting is interrupted; an unexpected confirmed failure still exits 1. A timeout or Ctrl-C is never itself a counterexample; an interrupted `gen_check.py --expect-fail` run cannot succeed. Reports identify incomplete work, and bounded model reports distinguish unfinished mutants from survivors. Sources for these additions and route/file-set changes: `proposals/2026-11-09.md`.
- Proposal 1: Python-only, stdlib `gen_check.py` with seeded generators and shrinkers, pinned examples, repeatable setup/reset, declared repo targets traced only during accepted checks, excessive-rejection failure, JSON reports, and `--expect-fail` for real counterexamples only. Added the todo persistence/title property fixture and scratch-break/regression examples.
- Review consensus: deferred harness mode and the cross-language JSON protocol; chose declared `TARGETS` over accepting any tracked function. Other languages use an installed repo property-testing library with a fixed seed, or a real-code scratch break caught by a test.

### Fixed

- `scan_repo.py` keeps declared routes named like filesystem roots, including `/home` and SCIM's `/Users`, while filtering filesystem and environment checks. Request-path aliases respect exclusive branches and uncertain assignments, including exceptional paths, optional loop bodies, ordered `with` managers and short-circuit expressions; query-stripping aliases and Go router registrations remain discoverable without repeatedly scanning whole functions or file prefixes.
- CLI inventories record whether each option takes a value, so `drift_check.py` can distinguish flags from options before a subcommand. This includes argparse's explicit `nargs=0`, registered action aliases such as `BooleanOptionalAction` without explicit `nargs`, and click flags. Registrations follow static parser bindings through scopes, groups, subclasses and helpers; helpers, methods and direct subclass constructors bind explicit arguments and capture positional/keyword-only defaults at definition time. Unresolved custom aliases use the custom-class bool-default heuristic. Ordinary store options still consume a value with `default=False`.
- Both checkers save confirmed exception failures, coverage and minimal diagnostics before rendering user-defined exception messages. Interrupted exception formatting retains property failures (exit 1), bounded model or mutant errors (exit 2), and import or property infrastructure errors (exit 2).
- `patlint.py` resolves the repository from `--root` or the current directory and uses that index for file and directory arguments, independent of inherited `GIT_DIR` / `GIT_WORK_TREE`. Directory ignores and baseline eligibility remain tied to that repository, including in linked worktrees and when the repository or a directory argument uses alternate casing on a case-insensitive filesystem.
- `gen_check.py` preserves a found failure when generated inputs exceed the rejection threshold.

- Proposal 3: patlint `--record RULE --commit FIX_SHA` retains raw diff cases after whole-file history proof; `--test` replays every rule's cases with the lint's flags and scope, requires a matching bad case, and validates explicit `fix` references (Python symbols via AST). Ordinary lint and ratchets keep their behavior.
- Review conflict: chose an explicit `fix` field over scanning every name in the message, so mentioning `json.dump` as the mistake never creates a false fix failure.
- The planner saves executable cases only from successful proofs. Updated the correct skill, example owner APIs, helper mapping, deterministic builder fix SHA, and example regressions for recording, scope drift, renamed fixes, and mistake mentions.

- Proposal 5: `scripts/check_docs.py`, wired through `verify.docs()`, checks first-party helper paths and command/subcommand flags, local Markdown links and Unicode/duplicate heading anchors, whole-set counts, and every source citation against NOTES.md. Every skill still needs a citation; new source rows require no checker edit. Updated README's whole-history example and removed its hardcoded source range.
- Review conflict: chose explicit skill-declared README flag policies over banning every flag absent from a skill example. This catches the retired `--since` default while allowing other supported README-only flags. Bare `scripts/` means the collection root; generated harness references now name `verify-<app>/scripts/control.sh`. Generated/placeholder and vendored paths are excluded; cached help calls are bounded and documentation commands are never executed.
- Curator run_examples assertions cover clean docs and regressions in flags, recommendation policy, path ownership, links/titles/anchors, counts, and citation inventory/attribution, including adding S24.

- Proposal 2: generated drives support JSON dot-path/root, text, absence, and expected CLI exit assertions. Evidence records each assertion and overall execution success; the proof gate opens actual records and rejects missing, failed, or assertion-free evidence.
- HTTP and CLI write recipes require explicit `--write KEY` / asserted `--read-back KEY` pairs; HTTP reads must address the written resource. CLI reads declare their semantics. This extends the proposal's HTTP-only gate to match its “every write” rule. For the proof shortcut, chose the simpler, safer read-only scope with a documented multi-command mutation sequence rather than adding another recipe executor.
- Updated CLI drift parsing, placeholder scanning (literal assertion payloads may contain HTML), scaffold guidance, all four fixtures, the worked feature example, and live/example regressions. Older harnesses receive a regenerate-control.sh hint.

### Fixed (found in the Grok CLI trial on pixel-agent-garden, see `logs/grok-trial/`)

- `scan_repo.py`: Rust repos produced 0 features. It now reads clap `#[derive(Subcommand)]` enums (kebab-case variants, `#[command(name = ...)]`) from every crate's bins and invokes them as `cargo run -q -p PKG --bin BIN --`. CLI verbs with no resource behind them are titled ``Run `bin verb` `` (previously "Doctor a --").
- `scaffold.py`: the generated SKILL.md hardcoded `~/.claude/skills/...` helper paths. They now come from the scaffold's own install location (repo-relative for a project install, absolute otherwise).
- Generated `control.sh evidence --copy-data`: a dangling or relative symlink in the data dir crashed the manifest. Symlinks are now recorded as `{"path", "symlink"}` and not opened.
- `scaffold.py` now writes `FEATURE_MAP.md`, the entry point that links every feature file.
- `check_verify_skill.py --require-proof EVIDENCE_ROOT`: fails until a proof run has left a manifest with a drive record. The structural checks alone passed on a skill that had never run. SKILL.md step 5 uses it.
- `scripts/run_examples.py`: regression assertions for all of the above.
- `mine_mistakes.py` (from the Grok `/correct` run): on pixel-agent-garden it reported 11 classes, 6 of them false `repeat`s. It now reports 8 classes and 0 false repeats, and the top class is the same 6 commits Grok picked by hand. Changes: a typed non-fix subject (`feat:`, `test:` and similar) counts as a fix only when the subject names a fix, and body words no longer turn a `feat` into a fix. A bullet in AGENTS.md or CLAUDE.md counts as a rule only when it is normative ("must", "never", and so on). A shared file name ties a class to a rule-table row, never to a prose bullet. Before/after output is in `logs/grok-trial/mine_mistakes.*.json`, and the new run_examples assertions fail on the old script (`logs/grok-trial/regression-fails-on-old-mine_mistakes.out`).
- `correct/SKILL.md`: mine the whole history by default. The old `--since "90 days ago"` example dropped the top class on a repo whose mistakes are four months old. The skill also notes that clustering is lexical, and that a test is the level to use when a regex cannot express the invariant.

### Known, not changed

- `rule_table.py add` appends a `## Rules` section at the end of the file. In bilingual AGENTS.md files, move it into the right language section by hand.
- `patlint.py` is regex-only by design. Geometric and pixel invariants go to a test, as Grok did.

## [0.1.0] - 2026-10-19

### Added

- `create-verification-skill`: `scan_repo.py` (Python AST, Node, Go, and Make heuristics for routes, CLI subcommands, ports, build/test commands), `scaffold.py` (writes `verify-<app>` with SKILL.md, a feature map using the four H2s, and a `control.sh` harness: launch, doctor, drive, evidence, cleanup, status, prove), and `check_verify_skill.py` (contract gate).
- `maintain-verification-skill`: `drift_check.py`, covering index hygiene, the H2 contract, and routes and commands missing from or dead in the map.
- `correct`: `mine_mistakes.py` (mistake classes from reverts, fix commits and their diffs, review comments, chat notes, and workaround comments), `patlint.py` (rule-file lint with a ratchet baseline, expiring inline exceptions, and `--rev` to replay history), and `rule_table.py` (flags rules enforced only by prose).
- `feedback-to-architecture`: `collect_feedback.py` (normalized `feedback.json`) and `plan.py` (`REFACTOR_PLAN.md`, `lint-rules.json`, and `plan.json`, with each rule replayed against git history).
- `architect`: `sketch_check.py`, plus a sketch template and a design red-flags list.
- `sharpen`: `brief.py` (writes and checks the brief; covers in-your-own-words).
- `formal-verification`: `bounded_check.py` (stdlib explicit-state model checker with counterexample traces, deadlock detection, `--mutants`, and `--expect-violation`), `tla_available.sh`, and an example lost-update model.
- `curator`: `upstream_diff.sh`, `curate.sh` (daily propose, weekly apply on a local `curator/<date>` worktree branch, committed only after `scripts/verify.sh` passes, never pushed), `check_proposals.py`, and `references/curator-prompt.md`.
- `install.sh` with symlink and `--copy` modes, `--target claude|cursor|both`, `--project`, `--uninstall`, `--force`, and `--dry-run`.
- README.md, MAPPING.md, and this changelog.

- Deterministic nested todo repository builder with ten dated commits, repeated persistence and status mistakes, review/chat evidence, seven passing HEAD tests, and a persistence-boundary lint.
- Serial example runner with positive and negative checks for all eight skills, per-skill logs, JSON summary, saved artefacts, and isolated process cleanup.
- Collection verification gate covering frontmatter, script contracts, syntax, installation isolation, fresh app tests, and real example results, with `--run-examples` regeneration.

### Fixed

- Preserve zero-count adopted patlint rules so `--update-baseline` cannot silently re-adopt new violations after the count reaches zero.
- Reject prose-only rule enforcement even when it mentions tests. Detect dead routes and Python CLI commands when the last source entry disappears.
- Exclude generated bytecode from verification-skill checks. Generated cleanup checks the leader's process-start identity (Linux boot id and start ticks) and treats zombies as stopped.
- Resolve generated lint owner exclusions to the actual source file; replay candidate patterns against real mistakes and fixes before promoting them. Failed replay and residual fixed-side matches cannot count as proof.
- Make curator offline mode skip network fetches, fail unsuccessful upstream diffs, report new skills on the first run, reject malformed proposals and inaccessible fetched sources, recheck sources on online apply, retain snapshot-sync provenance, and require the collection's verification command before committing.
- Correct S21's source status to unverified because only a search snippet was read.
- Load dataclass models correctly; reject empty initial-state sets and invalid bounds. Deadlock alone cannot count as killing an invariant mutant.
- Phase C cross-review: generated `control.sh` cleanup signals the process group `launch` created again. A wrapper launch command (`sh -c`, `npm start`, `make run`) no longer leaves its server running while cleanup reports `killed: true`.
- Start identity falls back to `ps` start time where `/proc` is missing, so doctor and cleanup work on macOS.
- `launch` refuses a `VERIFY_RUN_ID` that already has kept evidence instead of merging two runs into one evidence dir.
- Curator `new_skills` reports a skill only if it is absent from the snapshot and new since the previous run (on the first run, every such skill). Untracked upstream skills are no longer re-proposed every day as new.
- `drift_check.py` recovers CLI vocabulary only from recipes' `drive ... cli -- PROG` commands, so an incidental `python3 -m unittest discover` no longer reports drift. Its no-source-inventory note matches the dead-route behaviour.
- `check_proposals.py` documents the required `No proposals` line and the 2xx-only fetch rule.
- The example runner covers wrapper-launch cleanup, the curator's second run, and incidental commands in recipes.

[Unreleased]: #unreleased
[0.1.0]: #010---2026-10-19
