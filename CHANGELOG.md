# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: #unreleased
[0.1.0]: #010---2026-10-19
