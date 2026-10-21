# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

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
