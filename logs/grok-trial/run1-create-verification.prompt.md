/create-verification-skill

Run this skill for real, end to end, on this repository (pixel-agent-garden, local branch `poteto-skills-trial`). Follow `.claude/skills/create-verification-skill/SKILL.md` exactly, including its helper scripts (`scan_repo.py`, `scaffold.py`, `check_verify_skill.py`). Put the generated project-local skill at `.claude/skills/verify-pixel-agent-garden/`. It needs the SKILL.md (Launch/Doctor/Drive/Evidence/Cleanup/Helpers), the `features/` map (README index plus one file per top user-facing feature, each with the 4 H2s), and a `FEATURE_MAP.md` at the skill root that points at, or summarizes, the features index. Fill every TODO(verify) from the real code. Then do the end-to-end proof against the REAL app (the `agent-garden` CLI, and the static `web/` frontend if it can be driven over HTTP): launch → doctor → drive at least one feature → capture evidence → cleanup. The final `check_verify_skill.py` must pass.

Box constraints (hard rules):
- The machine is shared: 16 GB RAM, no swap, about 4 GB free. Build ONLY the core and CLI crates, with `CARGO_BUILD_JOBS=2` (for example `CARGO_BUILD_JOBS=2 cargo build -p <cli package>`). Never build `crates/tauri-app`; its system libs (webkit) are not installed. Use `cargo test -p` for core and CLI only. Run nothing in parallel.
- Privacy: the app scans local agent directories. Drive it ONLY against a disposable fake HOME or a fixture data dir that you create under /tmp, never the real ~/.claude, ~/.codex or ~/.cursor of this machine. Put fixture creation in the verify skill's Launch section so it is reproducible.
- Kill only processes you started (record their PIDs). Never kill by name.
- Do not touch /home/box/developer/pay-module or docker. Do not `git push`. Do not commit yet.
- Keep evidence under `artifacts/verify/<run-id>/` in this repo, which is gitignored if possible; otherwise say where it is.

When done, reply with: the files you created, the exact commands of the proof run and their results (exit codes and key output), the check_verify_skill.py result, and any problem you hit with the skill's instructions or scripts. For each problem, say whether it is a bug in the poteto skill, a Grok incompatibility, or a property of this repo.
