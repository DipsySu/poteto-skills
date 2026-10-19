#!/usr/bin/env python3
"""Scaffold a project-local verification skill from scan_repo.py's inventory.

  scaffold.py REPO --app NAME [--skills-dir .claude/skills] [--force] [--max-features N] [--json]

Writes REPO/<skills-dir>/verify-NAME/:
  SKILL.md               frontmatter + Launch, Doctor, Drive, Evidence, Cleanup, Helpers
  features/README.md     the map index: preconditions, conventions, proof rules, feature list
  features/<id>.md       one per detected feature, four H2s in order:
                         Sub-features / How to get to it (user POV) / Driving it with control.sh / Gotchas
  scripts/control.sh     launch | doctor | drive | evidence | cleanup | status | prove
                         (pid file, kills only the process group it started, evidence in artifacts/verify/<run-id>/)

Every value the scan could not establish becomes a `TODO(verify): ...` marker.
check_verify_skill.py fails until each one is replaced with a real command or fact.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # importing scan_repo must not leave __pycache__ in the skill dir
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_repo  # noqa: E402

TODO = "TODO(verify)"


def sh(value: str) -> str:
    return shlex.quote(value)


def pick_server(inv: dict) -> dict | None:
    return next((lc for lc in inv["launch_candidates"] if lc["kind"] == "server"), None)


def pick_cli(inv: dict) -> dict | None:
    return next((lc for lc in inv["launch_candidates"] if lc["kind"] == "cli"), None)


def launch_command(lc: dict | None, inv: dict) -> tuple[str, str, int | None, list[str]]:
    """(LAUNCH_CMD, PORT_MODE, DEFAULT_PORT, todos)."""
    todos: list[str] = []
    if lc is None:
        return "", "none", None, todos
    env_names = {e["name"] for e in inv["env_vars"]}
    prog, sub = lc.get("prog"), lc.get("subcommand")
    head = f"{prog} " if prog else ""
    pre, post = [], []
    if lc.get("data_flag"):
        (pre if lc.get("data_flag_global") else post).append(f'{lc["data_flag"]} "$DATA_DIR"')
    if lc.get("port_flag"):
        (pre if lc.get("port_flag_global") else post).append(f'{lc["port_flag"]} "$PORT"')
    if prog and sub:
        cmd = " ".join([prog, *pre, sub, *post])
    else:
        cmd = " ".join([lc["cmd"], *pre, *post]).strip() if not head else lc["cmd"]
    if lc.get("port_flag"):
        mode = "flag"
    elif "PORT" in env_names:
        mode = "env"
    elif lc.get("port"):
        mode = "fixed"
    else:
        mode = "flag"
        todos.append("port")
        cmd += f" {TODO}: pass $PORT so the app listens where control.sh expects"
    if not lc.get("data_flag"):
        data_env = [n for n in sorted(env_names) if re.search(r"DATA|DB|DIR|STORE|STATE|HOME|PATH", n) and n != "PORT"]
        hint = f" (env {', '.join(data_env)}?)" if data_env else ""
        todos.append("data")
        cmd += f" {TODO}: point the app at the empty $DATA_DIR{hint}"
    return cmd, mode, lc.get("port"), todos


def cli_base(cli: dict | None, inv: dict) -> tuple[str, str]:
    """(prog with data flag placed correctly, data_flag_text after subcommand)."""
    if cli is None:
        return "", ""
    prog = cli["cmd"]
    if cli.get("data_flag"):
        if cli.get("data_flag_global"):
            return f"{prog} {cli['data_flag']} {{DATA_DIR}}", ""
        return prog, f" {cli['data_flag']} {{DATA_DIR}}"
    return prog, ""


def sample_value(name: str, fid: str) -> str:
    n = name.lower()
    if n in ("id", "todo_id", "item_id", "pk") or n.endswith("_id"):
        return f"<{name}>"
    if n in ("done", "completed", "archived", "enabled"):
        return "true"
    if n in ("count", "limit", "priority", "n"):
        return "1"
    return f"verify {fid}"


def concrete(path: str) -> str:
    return re.sub(r"<\*>|<([^>]*)>", lambda m: "<" + (m.group(1) or "id") + ">", path)


def feature_md(f: dict, inv: dict, C: str, cli_prefix: str, cli_suffix: str, list_route: dict | None,
               list_cmd: dict | None) -> str:
    title, fid = f["title"], f["id"]
    lines = [f"# {title}", "",
             f"{TODO}: one paragraph, from the user's point of view: what \"{title.lower()}\" does and what the user "
             f"sees afterwards.", "", "## Sub-features", ""]
    for r in f["routes"]:
        lines.append(f"- `{fid}-http-{r['method'].lower()}`: `{r['method']} {concrete(r['path'])}` over HTTP.")
    for c in f["commands"]:
        lines.append(f"- `{fid}-cli-{c['name']}`: `{c['prog']} {c['name']}` in a terminal.")
    lines += ["", "## How to get to it (user POV)", ""]
    for r in f["routes"]:
        lines.append(f"- HTTP API: `{r['method']} {concrete(r['path'])}` on the running server (base URL from `launch`).")
    for c in f["commands"]:
        args = " ".join(f"<{p}>" for p in c.get("positionals", []))
        lines.append(f"- Terminal: `{' '.join(x for x in (c['prog'], c['name'], args) if x)}`.")
    lines += ["", "## Driving it with control.sh", "", "Preconditions:", "",
              f"- `{C} doctor` exits 0."]
    needs_existing = f["action"] not in ("create", "list") or any("<" in r["path"] for r in f["routes"])
    if needs_existing:
        lines.append(f"- At least one {scan_repo.singular(f['resource'])} exists: run the create recipe first. "
                     f"{TODO}: the id it returns on an empty data dir.")
    else:
        lines.append("- Fresh run from `launch` (empty data dir).")
    lines.append("")
    for r in f["routes"]:
        path = concrete(r["path"])
        body = ""
        if r["method"] in ("POST", "PUT", "PATCH") and f["action"] in ("create", "update"):
            keys = r.get("body_keys") or []
            if keys:
                body = " " + sh(json.dumps({k: sample_value(k, fid) for k in keys}))
            else:
                body = f" '{TODO}: JSON body'"
        id_note = f" Replace `<...>` with the real id. " if "<" in path else " "
        lines.append(f"- **HTTP {r['method']}.** Run `{C} drive --feature {fid} http {r['method']} {path}{body}`."
                     f"{id_note}{TODO}: the expected status and body.")
    for c in f["commands"]:
        args = " ".join(sh(sample_value(p, fid)) for p in c.get("positionals", []))
        cmdline = f"{cli_prefix} {c['name']}{cli_suffix} {args}".strip()
        id_note = " Replace `<...>` with the real id." if "<" in args else ""
        lines.append(f"- **CLI {c['name']}.** Run `{C} drive --feature {fid} cli -- {cmdline}`.{id_note} "
                     f"{TODO}: the expected exit code and stdout.")
    proof = []
    if list_route:
        proof.append(f"`{C} drive --feature {fid} http GET {concrete(list_route['path'])}`")
    elif list_cmd:
        proof.append(f"`{C} drive --feature {fid} cli -- {cli_prefix} {list_cmd['name']}{cli_suffix}`".replace("  ", " "))
    proof.append(f"`{C} evidence --copy-data`")
    lines.append(f"- **Proof.** Read it back through a second view: run {' and '.join(proof)}. "
                 f"{TODO}: what the read-back and the data snapshot must show.")
    lines += ["", "## Gotchas", "",
              f"- {TODO}: traps that waste or invalidate a run here (ids, ordering, defaults, validation)."]
    if cli_prefix and "{DATA_DIR}" in (cli_prefix + cli_suffix):
        lines.append("- Every CLI call needs the run's data dir (`{DATA_DIR}`); without it the CLI touches its default "
                     "data location instead of the scratch one.")
        flag = re.search(r"(--[\w-]+) \{DATA_DIR\}", cli_prefix + cli_suffix)
        if flag and flag.group(1) in ("--db", "--database", "--db-path", "--file", "--path", "--store", "--data-path"):
            lines.append(f"- {TODO}: confirm `{flag.group(1)}` takes a directory. If it takes a file, pass "
                         f"`{{DATA_DIR}}/<file>` everywhere instead.")
    elif f["commands"]:
        lines.append(f"- {TODO}: confirm where CLI commands store data; drives must not touch real user data.")
    return "\n".join(lines) + "\n"


def readme_md(app: str, feats: list[dict], C: str, skipped: list[dict]) -> str:
    lines = [f"# {app} verification map", "",
             f"The maintained source for verifying what {app} users can do. Read this index before driving the app, "
             "then follow the matching feature file as the recipe.", "",
             "## Baseline preconditions", "",
             f"- Launch with `{C} launch`: empty data dir, its own port, JSON with run_id, base_url, data_dir.",
             f"- `{C} doctor` exits 0 before the first drive.",
             "- Never drive an instance this run did not start.",
             f"- {TODO}: seed data the journeys need, created through the app's own routes or commands (or \"none\").",
             "", "## Driving conventions", "",
             "- Run every command from the repo root, literally as written.",
             f"- HTTP: `{C} drive --feature <id> http METHOD PATH ['JSON'] [--expect STATUS]`.",
             f"- CLI: `{C} drive --feature <id> cli -- <command>`. `{{DATA_DIR}}`, `{{PORT}}`, `{{BASE_URL}}`, and "
             "`{RUN_ID}` in arguments become this run's values.",
             "- Start each recipe from the state its Preconditions name. Use stable handles: route paths, subcommand "
             "names, JSON fields.",
             "", "## Proof and skip reporting", "",
             "- Each drive records the action and its result in `artifacts/verify/<run-id>/`.",
             f"- A mutation needs a read-only second view (a GET or a list command), then `{C} evidence --copy-data` "
             "for the stored side effect.",
             "- Record the feature id with every artifact (`--feature`).",
             "- Report an unreachable path with the attempted command and the unmet precondition. Never report it as "
             "verified through a different path.",
             "", "## Feature entry contract", "",
             "Each feature file has an H1, one paragraph of user-visible behavior, then exactly these H2s in order: "
             "`Sub-features`, `How to get to it (user POV)`, `Driving it with control.sh`, `Gotchas`. Keep "
             "implementation details out: user paths, stable handles, required state, commands, observable proof.",
             "", "## Features", ""]
    for f in feats:
        entry = [f"`{r['method']} {concrete(r['path'])}`" for r in f["routes"]] + \
                [f"`{c['prog']} {c['name']}`" for c in f["commands"]]
        lines.append(f"- [{f['title']}](./{f['id']}.md): " + ", ".join(entry) + ".")
    if not feats:
        lines.append(f"- {TODO}: no routes or commands were detected. Add one file per user-facing feature.")
    if skipped:
        lines += ["", "## Not mapped yet", ""]
        lines += [f"- {f['title']} (`{f['id']}`): over --max-features; add a file when it matters." for f in skipped]
    return "\n".join(lines) + "\n"


def helper_path(inv: dict, skill: str, script: str) -> str:
    """Where this checkout's poteto helper lives, as the next agent in this repo should type it.

    Uses the unresolved path of this script, so a project install (`.claude/skills/<skill>` symlink or copy)
    yields a repo-relative path; anything else yields an absolute path. Never assume ~/.claude.
    """
    here = Path(__file__).absolute().parent.parent.parent / skill / "scripts" / script
    try:
        return here.relative_to(Path(inv["repo"])).as_posix()
    except ValueError:
        return str(here)


def skill_md(app: str, inv: dict, C: str, skill_rel: str, launch: str, port_mode: str, health: str, read_path: str,
             cli_prog: str, surfaces: list[str], prove_args: str) -> str:
    build = "; ".join(f"`{b['cmd']}`" for b in inv["build_commands"]) or "none (interpreted)"
    tests = "; ".join(f"`{t['cmd']}`" for t in inv["test_commands"]) or f"{TODO}: the repo's test command"
    surf = " and ".join(surfaces) or "surface"
    desc = (f"Launch {app} from an empty data dir, drive its {surf} the way a user does, and keep evidence under "
            f"artifacts/verify/. Use for /verify-{app}, before claiming a change to {app} works, or to reproduce "
            f"a bug in the running app.")
    lines = ["---", f"name: verify-{app}", f"description: {json.dumps(desc)}", "---", "",
             f"# Verify {app}", "",
             f"Prove behavior on the running app, not a proxy. Every step goes through `{C}`, run from the repo root. "
             "Feature recipes live in [features/README.md](features/README.md).", "", "## Launch", ""]
    if launch:
        ready = f"`GET {health}` returns 2xx" if health else "the port accepts connections"
        port_txt = {"flag": "a free port (or `VERIFY_PORT`)", "env": "a free port exported as `PORT` (or `VERIFY_PORT`)",
                    "fixed": "the app's fixed port (only one instance at a time)"}.get(port_mode, "a port")
        lines += [f"- `{C} launch` starts `{launch}` from the repo root on {port_txt}, with an empty data dir under "
                  f"`${{VERIFY_STATE_DIR:-/tmp/verify-{app}-$UID}}/<run-id>/data`.",
                  f"- Ready = {ready}" + (f", then doctor proves it is usable with `GET {read_path}`." if read_path else "."),
                  "- It prints JSON: run_id, pid, port, base_url, data_dir, evidence_dir. The server log is "
                  "`artifacts/verify/<run-id>/launch.log`.",
                  "- It refuses to start while an earlier run is alive. Teardown is `cleanup`."]
    else:
        lines += [f"- No long-lived server: `{C} launch` only creates the run's empty data dir and evidence dir. "
                  "Each CLI drive runs in its own process."]
    if launch:
        checks = ("the recorded pid is alive and is still the process this run started, the port answers, " +
                  (f"`GET {health}` is 2xx, " if health else "") + (f"`GET {read_path}` is 2xx, " if read_path else ""))
    else:
        checks = f"`{cli_prog} --help` exits 0, " if cli_prog else ""
    lines += [f"- Build first: {build}. Tests (they do not replace driving): {tests}.", "",
              "## Doctor", "",
              f"`{C} doctor` is read-only and exits 1 on any failed check: {checks}"
              "the data dir and evidence dir exist. Run it before the first drive and again after any failed drive.",
              "", "## Drive", ""]
    if launch:
        lines.append(f"- HTTP: `{C} drive --feature <id> http METHOD PATH ['JSON'] [--expect STATUS]`.")
    if cli_prog:
        lines.append(f"- CLI: `{C} drive --feature <id> cli -- {cli_prog} <subcommand> ...`. `{{DATA_DIR}}`, `{{PORT}}`, "
                     "`{BASE_URL}`, `{RUN_ID}` in arguments become this run's values.")
    lines += ["- One recipe per feature in `features/`. Drive the public routes and commands only.", "",
              "## Evidence", "",
              "- Every drive writes `artifacts/verify/<run-id>/NNN-<feature>-<http|cli>.json`: the request or argv, "
              "the status or exit code, the response body or stdout/stderr, and timing.",
              f"- `{C} evidence --copy-data` snapshots the data dir (the side effect) and writes `manifest.json` "
              "with a sha256 per file.",
              "- Proof standard: the real user path (no internal setters or test-only endpoints); the action and the "
              "resulting state, read back through a second view; side effects checked, not inferred; mocks only at "
              "production boundaries. If a dry-run or test mode is used, observe what it really skips.", "",
              "## Cleanup", "",
              f"`{C} cleanup` stops only the process group this run started (pid file plus recorded start identity, never "
              "by name), deletes the scratch state and data dir, and keeps `artifacts/verify/<run-id>/`. It prints "
              "the evidence path and fails if the evidence is gone.", "",
              "## Helpers", "",
              f"- `scripts/control.sh`: `launch | doctor | drive | evidence | cleanup | status | prove`. "
              f"`{C} --help` lists the arguments.",
              f"- One-shot proof: `{C} prove --feature <id> {prove_args}` runs launch, doctor, drive, "
              "evidence, and cleanup, then checks the evidence survived.",
              f"- Contract check: `python3 {helper_path(inv, 'create-verification-skill', 'check_verify_skill.py')} {skill_rel}`.",
              f"- Drift check: `python3 {helper_path(inv, 'maintain-verification-skill', 'drift_check.py')} {skill_rel} --repo .`.",
              ""]
    return "\n".join(lines)


CONTROL_SH = r'''#!/usr/bin/env bash
# Verification harness for @@APP@@. Generated by create-verification-skill/scripts/scaffold.py.
# Edit the CONFIG block when the app changes; check_verify_skill.py fails while a TODO marker remains.
set -euo pipefail

# ---- CONFIG ------------------------------------------------------------------
APP=@@APP@@
# Run from the repo root with PORT, DATA_DIR, BASE_URL, RUN_ID exported. Empty = no server (CLI-only app).
LAUNCH_CMD=@@LAUNCH_CMD@@
PORT_MODE=@@PORT_MODE@@        # flag|env: a free port per run; fixed: the app always listens on DEFAULT_PORT
DEFAULT_PORT=@@DEFAULT_PORT@@
HEALTH_PATH=@@HEALTH_PATH@@    # GET path that must return 2xx before the app counts as ready ('' = TCP only)
READ_PATH=@@READ_PATH@@        # read-only GET path doctor uses to prove the app is usable ('' = skip)
CLI_CHECK=@@CLI_CHECK@@        # doctor's health command when there is no server ('' = skip)
READY_TIMEOUT="${VERIFY_READY_TIMEOUT:-30}"
# -------------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/@@REL_TO_REPO@@" && pwd)"
STATE_ROOT="${VERIFY_STATE_DIR:-${TMPDIR:-/tmp}/verify-$APP-$(id -u)}"
ARTIFACTS_ROOT="${VERIFY_ARTIFACTS_DIR:-$REPO_ROOT/artifacts/verify}"
CURRENT="$STATE_ROOT/current"

usage() {
  cat <<EOF
usage: control.sh <command> [args]

Drive $APP for verification. Run from anywhere; paths resolve to the repo root.

  launch                         start the app from an empty data dir, wait until ready, print run JSON
  doctor                         read-only health check of the current run (exit 1 if unhealthy)
  drive [--feature ID] http METHOD PATH [JSON] [--expect STATUS]
                                 HTTP request against the run; evidence saved as NNN-ID-http.json
  drive [--feature ID] cli -- CMD [ARGS...]
                                 run a command in the repo root; {DATA_DIR} {PORT} {BASE_URL} {RUN_ID}
                                 in ARGS become this run's values; evidence saved as NNN-ID-cli.json
  evidence [--copy-data]         write manifest.json (sha256 per file); --copy-data snapshots the data dir
  cleanup                        stop only the process group this run started, delete scratch state, keep evidence
  status                         print the current run's state (read-only)
  prove [--feature ID] http|cli ...
                                 launch, doctor, drive once, evidence --copy-data, cleanup, check evidence survived

env: VERIFY_PORT, VERIFY_RUN_ID, VERIFY_STATE_DIR (default $STATE_ROOT),
     VERIFY_ARTIFACTS_DIR (default $ARTIFACTS_ROOT), VERIFY_READY_TIMEOUT (default 30s)
EOF
}

die() { echo "control.sh: $*" >&2; exit 1; }

case "$LAUNCH_CMD$CLI_CHECK" in
  *TODO\(verify\)*) [[ "${1:-}" =~ ^(-h|--help|help|status)$ ]] || die "CONFIG still has unresolved TODO markers; edit $SCRIPT_DIR/control.sh first" ;;
esac

pid_alive() { [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null && [[ "$(ps -o stat= -p "$1" 2>/dev/null)" != Z* ]]; }
proc_started() {  # start identity: Linux boot id + start ticks; elsewhere (macOS) ps start time
  if [[ ! -r "/proc/${1:-x}/stat" ]]; then ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//' || true; return 0; fi
  python3 - "$1" <<'PYID'
from pathlib import Path
import sys
try:
    stat = Path(f"/proc/{int(sys.argv[1])}/stat").read_text().rsplit(")", 1)[1].split()
    print(Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + stat[19])
except (OSError, ValueError, IndexError):
    pass
PYID
}

free_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

port_open() {
  python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1"
}

http_status() {  # URL -> prints the status code, 000 when unreachable
  python3 - "$1" <<'PY'
import sys, urllib.error, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(urllib.request.Request(sys.argv[1], method="GET"), timeout=3) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("000")
PY
}

load_run() {
  [[ -f "$CURRENT" ]] || die "no active run; start one with: control.sh launch"
  RUN_ID="$(cat "$CURRENT")"
  RUN_STATE="$STATE_ROOT/$RUN_ID"
  [[ -f "$RUN_STATE/env" ]] || die "run $RUN_ID has no state in $RUN_STATE; run control.sh cleanup, then launch"
  # shellcheck disable=SC1091
  source "$RUN_STATE/env"
}

next_seq() {
  local n
  n="$(find "$EVIDENCE_DIR" -maxdepth 1 -name '[0-9][0-9][0-9]-*' | wc -l)"
  printf '%03d' "$((n + 1))"
}

ready_check() {
  if [[ -n "$HEALTH_PATH" ]]; then
    [[ "$(http_status "$BASE_URL$HEALTH_PATH")" == 2* ]]
  else
    port_open "$PORT"
  fi
}

run_json() {  # key value ... -> JSON object (ints, true/false, and "" -> null are typed)
  python3 - "$@" <<'PY'
import json, sys

def typed(v):
    if v == "":
        return None
    if v in ("true", "false"):
        return v == "true"
    return int(v) if v.isdigit() else v

print(json.dumps({k: typed(v) for k, v in zip(sys.argv[1::2], sys.argv[2::2])}, indent=2))
PY
}

cmd_launch() {
  mkdir -p "$STATE_ROOT"
  if [[ -f "$CURRENT" ]]; then
    local old pidf
    old="$(cat "$CURRENT")"
    pidf="$STATE_ROOT/$old/pid"
    if [[ -f "$pidf" ]] && pid_alive "$(cat "$pidf")"; then
      die "run $old is still alive (pid $(cat "$pidf")); drive it, or stop it with: control.sh cleanup"
    fi
  fi
  RUN_ID="${VERIFY_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
  RUN_STATE="$STATE_ROOT/$RUN_ID"
  DATA_DIR="$RUN_STATE/data"
  EVIDENCE_DIR="$ARTIFACTS_ROOT/$RUN_ID"
  [[ ! -e "$RUN_STATE" && ! -e "$EVIDENCE_DIR" ]] || die "run id $RUN_ID already has state or kept evidence; pick a new VERIFY_RUN_ID"
  mkdir -p "$DATA_DIR" "$EVIDENCE_DIR"
  if [[ "$PORT_MODE" == fixed ]]; then PORT="${VERIFY_PORT:-$DEFAULT_PORT}"; else PORT="${VERIFY_PORT:-$(free_port)}"; fi
  BASE_URL="http://127.0.0.1:$PORT"
  {
    printf 'RUN_ID=%q\nPORT=%q\nDATA_DIR=%q\nBASE_URL=%q\nEVIDENCE_DIR=%q\n' \
      "$RUN_ID" "$PORT" "$DATA_DIR" "$BASE_URL" "$EVIDENCE_DIR"
  } > "$RUN_STATE/env"
  echo "$RUN_ID" > "$CURRENT"
  if [[ -z "$LAUNCH_CMD" ]]; then
    run_json run_id "$RUN_ID" pid "" port "" base_url "" data_dir "$DATA_DIR" evidence_dir "$EVIDENCE_DIR"
    return 0
  fi
  if port_open "$PORT"; then
    die "port $PORT is already taken by a process this run did not start; pick another with VERIFY_PORT (never kill it)"
  fi
  # Start the app as the leader of a new process group, so cleanup stops exactly what this run started.
  local starter=() pid
  if command -v setsid >/dev/null 2>&1; then starter=(setsid); else set -m; fi
  (
    cd "$REPO_ROOT"
    export PORT DATA_DIR BASE_URL RUN_ID
    exec "${starter[@]}" bash -c "$LAUNCH_CMD"
  ) > "$EVIDENCE_DIR/launch.log" 2>&1 < /dev/null &
  pid=$!
  set +m
  echo "$pid" > "$RUN_STATE/pid"
  proc_started "$pid" > "$RUN_STATE/started"
  local deadline=$((SECONDS + READY_TIMEOUT))
  until ready_check; do
    if ! pid_alive "$pid"; then
      tail -n 20 "$EVIDENCE_DIR/launch.log" >&2 || true
      die "the app exited during startup; log: $EVIDENCE_DIR/launch.log. Run control.sh cleanup before retrying."
    fi
    if (( SECONDS >= deadline )); then
      tail -n 20 "$EVIDENCE_DIR/launch.log" >&2 || true
      die "not ready after ${READY_TIMEOUT}s (${HEALTH_PATH:-TCP} on port $PORT); log: $EVIDENCE_DIR/launch.log. Run control.sh cleanup."
    fi
    sleep 0.3
  done
  run_json run_id "$RUN_ID" pid "$pid" port "$PORT" base_url "$BASE_URL" data_dir "$DATA_DIR" evidence_dir "$EVIDENCE_DIR" log "$EVIDENCE_DIR/launch.log"
}

cmd_doctor() {
  load_run
  local checks=() ok=1 pid=""
  [[ -f "$RUN_STATE/pid" ]] && pid="$(cat "$RUN_STATE/pid")"
  if [[ -n "$LAUNCH_CMD" ]]; then
    if pid_alive "$pid" && [[ -s "$RUN_STATE/started" ]] && [[ "$(proc_started "$pid")" == "$(cat "$RUN_STATE/started" 2>/dev/null)" ]]; then
      checks+=("process|1|pid $pid alive, started by this run")
    else
      checks+=("process|0|pid ${pid:-none} is not running (or is no longer this run's process)"); ok=0
    fi
    if port_open "$PORT"; then checks+=("port|1|$PORT accepts connections"); else checks+=("port|0|$PORT refuses connections"); ok=0; fi
    local p code
    for p in "$HEALTH_PATH" "$READ_PATH"; do
      [[ -z "$p" ]] && continue
      code="$(http_status "$BASE_URL$p")"
      if [[ "$code" == 2* ]]; then checks+=("GET $p|1|$code"); else checks+=("GET $p|0|$code"); ok=0; fi
    done
  elif [[ -n "$CLI_CHECK" ]]; then
    if (cd "$REPO_ROOT" && bash -c "$CLI_CHECK") >/dev/null 2>&1; then checks+=("cli|1|$CLI_CHECK exits 0"); else checks+=("cli|0|$CLI_CHECK failed"); ok=0; fi
  fi
  if [[ -d "$DATA_DIR" ]]; then checks+=("data dir|1|$DATA_DIR"); else checks+=("data dir|0|$DATA_DIR missing"); ok=0; fi
  if [[ -d "$EVIDENCE_DIR" ]]; then checks+=("evidence dir|1|$EVIDENCE_DIR"); else checks+=("evidence dir|0|$EVIDENCE_DIR missing"); ok=0; fi
  python3 - "$ok" "$RUN_ID" "${checks[@]}" <<'PY'
import json, sys
ok, run_id, rows = sys.argv[1] == "1", sys.argv[2], sys.argv[3:]
checks = [dict(zip(("name", "ok", "detail"), r.split("|", 2))) for r in rows]
for c in checks:
    c["ok"] = c["ok"] == "1"
print(json.dumps({"ok": ok, "run_id": run_id, "checks": checks}, indent=2))
PY
  [[ "$ok" == 1 ]]
}

cmd_drive() {
  load_run
  local feature="adhoc"
  if [[ "${1:-}" == "--feature" ]]; then feature="${2:?--feature needs an id}"; shift 2; fi
  local kind="${1:-}"; shift || true
  local seq; seq="$(next_seq)"
  local out="$EVIDENCE_DIR/$seq-$feature-$kind.json"
  case "$kind" in
    http)
      local method="${1:?usage: drive http METHOD PATH [JSON] [--expect STATUS]}" path="${2:?missing PATH}"
      shift 2
      local body="" expect=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --expect) expect="${2:?--expect needs a status}"; shift 2 ;;
          *) body="$1"; shift ;;
        esac
      done
      path="${path//\{RUN_ID\}/$RUN_ID}"
      [[ -n "$BASE_URL" ]] || die "this run has no server; use: drive cli -- ..."
      python3 - "$method" "$BASE_URL$path" "$body" "$out" "$feature" "$expect" <<'PY'
import json, sys, time, urllib.error, urllib.request
method, url, body, out, feature, expect = sys.argv[1:7]
data = body.encode() if body else None
headers = {"Content-Type": "application/json", "Accept": "application/json"} if body else {"Accept": "application/json"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
started = time.time()
try:
    with opener.open(urllib.request.Request(url, data=data, method=method.upper(), headers=headers), timeout=30) as r:
        status, raw, rh = r.status, r.read(), dict(r.headers)
except urllib.error.HTTPError as e:
    status, raw, rh = e.code, e.read(), dict(e.headers)
except Exception as e:  # connection refused, timeout
    status, raw, rh = 0, str(e).encode(), {}
text = raw.decode("utf-8", "replace")
try:
    parsed = json.loads(text) if text else None
except ValueError:
    parsed = None
try:
    req_body = json.loads(body) if body else None
except ValueError:
    req_body = body
rec = {"feature": feature, "kind": "http", "method": method.upper(), "url": url, "request_body": req_body,
       "status": status, "response_headers": rh, "response_body": parsed if parsed is not None else text,
       "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
       "elapsed_ms": round((time.time() - started) * 1000, 1), "expect": expect or None}
with open(out, "w") as f:
    json.dump(rec, f, indent=2)
print(f"{method.upper()} {url} -> {status}")
if text:
    print(text)
print(f"evidence: {out}", file=sys.stderr)
if expect:
    sys.exit(0 if str(status) == expect else 1)
sys.exit(0 if 200 <= status < 400 else 1)
PY
      ;;
    cli)
      [[ "${1:-}" == "--" ]] && shift
      [[ $# -gt 0 ]] || die "usage: drive [--feature ID] cli -- CMD [ARGS...]"
      local argv=() a
      for a in "$@"; do
        a="${a//\{DATA_DIR\}/$DATA_DIR}"; a="${a//\{PORT\}/$PORT}"
        a="${a//\{BASE_URL\}/$BASE_URL}"; a="${a//\{RUN_ID\}/$RUN_ID}"
        argv+=("$a")
      done
      local so se rc
      so="$(mktemp)"; se="$(mktemp)"
      set +e
      (cd "$REPO_ROOT" && PORT="$PORT" DATA_DIR="$DATA_DIR" BASE_URL="$BASE_URL" RUN_ID="$RUN_ID" "${argv[@]}") >"$so" 2>"$se" </dev/null
      rc=$?
      set -e
      python3 - "$out" "$feature" "$rc" "$so" "$se" "${argv[@]}" <<'PY'
import json, sys, time
out, feature, rc, so, se, *argv = sys.argv[1:]
rec = {"feature": feature, "kind": "cli", "argv": argv, "exit_code": int(rc),
       "stdout": open(so, encoding="utf-8", errors="replace").read(),
       "stderr": open(se, encoding="utf-8", errors="replace").read(),
       "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(out, "w") as f:
    json.dump(rec, f, indent=2)
PY
      cat "$so"; cat "$se" >&2
      rm -f "$so" "$se"
      echo "exit: $rc  evidence: $out" >&2
      return "$rc"
      ;;
    *) die "usage: drive [--feature ID] http METHOD PATH [JSON] | drive [--feature ID] cli -- CMD..." ;;
  esac
}

cmd_evidence() {
  load_run
  local copy=0
  [[ "${1:-}" == "--copy-data" ]] && copy=1
  if [[ "$copy" == 1 && -d "$DATA_DIR" ]]; then
    local snap; snap="$EVIDENCE_DIR/$(next_seq)-data-snapshot"
    mkdir -p "$snap"
    cp -R "$DATA_DIR/." "$snap/"
  fi
  python3 - "$EVIDENCE_DIR" "$RUN_ID" "$APP" <<'PY'
import hashlib, json, os, sys, time
root, run_id, app = sys.argv[1:4]
files = []
for dirpath, _, names in os.walk(root):
    for n in sorted(names):
        p = os.path.join(dirpath, n)
        rel = os.path.relpath(p, root)
        if rel == "manifest.json":
            continue
        if os.path.islink(p):
            # snapshot keeps links as links; a relative link can dangle in the copy, so record it, don't open it
            files.append({"path": rel, "symlink": os.readlink(p)})
            continue
        with open(p, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        files.append({"path": rel, "bytes": os.path.getsize(p), "sha256": digest})
files.sort(key=lambda x: x["path"])
manifest = {"app": app, "run_id": run_id, "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": files}
with open(os.path.join(root, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
print(json.dumps({"evidence_dir": root, "files": len(files), "manifest": os.path.join(root, "manifest.json")}, indent=2))
PY
}

cmd_cleanup() {
  if [[ ! -f "$CURRENT" ]]; then
    echo '{"cleaned": false, "reason": "no active run"}'
    return 0
  fi
  load_run
  local killed=false pid=""
  [[ -f "$RUN_STATE/pid" ]] && pid="$(cat "$RUN_STATE/pid")"
  if pid_alive "$pid"; then
    if [[ -s "$RUN_STATE/started" && "$(proc_started "$pid")" == "$(cat "$RUN_STATE/started" 2>/dev/null)" ]]; then
      # launch made the pid a new process-group leader; signal that group so a wrapper's server child
      # (sh -c, npm, make) stops too. Nothing outside the group is touched.
      local target="$pid" i
      [[ "$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')" == "$pid" ]] && target="-$pid"
      kill -TERM -- "$target" 2>/dev/null || true
      for i in $(seq 1 50); do pid_alive "$pid" || break; sleep 0.1; done
      if pid_alive "$pid"; then kill -KILL -- "$target" 2>/dev/null || true; fi
      killed=true
    else
      echo "control.sh: pid $pid is alive but was not started by run $RUN_ID (start time differs); leaving it alone" >&2
    fi
  fi
  rm -rf "$RUN_STATE"
  rm -f "$CURRENT"
  [[ -d "$EVIDENCE_DIR" ]] || die "evidence dir $EVIDENCE_DIR is missing after cleanup"
  local n; n="$(find "$EVIDENCE_DIR" -type f | wc -l | tr -d ' ')"
  run_json run_id "$RUN_ID" killed "$killed" evidence_dir "$EVIDENCE_DIR" evidence_files "$n"
}

cmd_status() {
  if [[ ! -f "$CURRENT" ]]; then echo '{"active": false}'; return 0; fi
  load_run
  local pid="" alive=false
  [[ -f "$RUN_STATE/pid" ]] && pid="$(cat "$RUN_STATE/pid")"
  pid_alive "$pid" && alive=true
  run_json active true run_id "$RUN_ID" pid "$pid" alive "$alive" port "$PORT" base_url "$BASE_URL" data_dir "$DATA_DIR" evidence_dir "$EVIDENCE_DIR"
}

cmd_prove() {
  [[ $# -gt 0 ]] || die "usage: prove [--feature ID] http METHOD PATH [JSON] | prove [--feature ID] cli -- CMD..."
  local self="$SCRIPT_DIR/control.sh" rc=0
  "$self" launch || { "$self" cleanup >/dev/null 2>&1 || true; die "prove: launch failed"; }
  trap '"$self" cleanup >/dev/null 2>&1 || true' EXIT
  "$self" doctor || { echo "control.sh: prove: doctor failed" >&2; rc=1; }
  if [[ "$rc" == 0 ]]; then "$self" drive "$@" || rc=$?; fi
  load_run
  local ev="$EVIDENCE_DIR"
  "$self" evidence --copy-data >/dev/null
  trap - EXIT
  "$self" cleanup
  local drives; drives="$(find "$ev" -maxdepth 1 -name '[0-9][0-9][0-9]-*-*.json' | wc -l | tr -d ' ')"
  [[ -f "$ev/manifest.json" && "$drives" -ge 1 ]] || die "prove: evidence did not survive cleanup in $ev"
  echo "prove: drive exit $rc; evidence kept in $ev ($drives drive record(s))" >&2
  return "$rc"
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift
case "$cmd" in
  launch) cmd_launch "$@" ;;
  doctor) cmd_doctor "$@" ;;
  drive) cmd_drive "$@" ;;
  evidence) cmd_evidence "$@" ;;
  cleanup) cmd_cleanup "$@" ;;
  status) cmd_status "$@" ;;
  prove) cmd_prove "$@" ;;
  -h|--help|help) usage ;;
  "") usage; exit 2 ;;
  *) usage >&2; die "unknown command: $cmd" ;;
esac
'''


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scaffold.py",
        description="Write a project-local verify-NAME skill (SKILL.md, feature map, scripts/control.sh) from a repo "
                    "scan. Unknown values become TODO(verify) markers; check_verify_skill.py fails until they are gone.",
        epilog="examples:\n"
               "  scaffold.py . --app todo\n"
               "  scaffold.py ../app --app shop --skills-dir .cursor/skills --max-features 5\n"
               "next: replace every TODO(verify), run `control.sh prove ...`, then check_verify_skill.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("repo", type=Path, help="repository root")
    ap.add_argument("--app", required=True, help="short app name; the skill becomes verify-NAME")
    ap.add_argument("--skills-dir", default=".claude/skills", help="skills dir, relative to REPO (default .claude/skills)")
    ap.add_argument("--max-features", type=int, default=0, help="write at most N feature files (default: all detected)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing verify-NAME dir")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.app):
        print("scaffold: --app must be lowercase letters, digits, and dashes", file=sys.stderr)
        return 2
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"scaffold: {repo} is not a directory", file=sys.stderr)
        return 2
    skills_root = Path(args.skills_dir)
    skills_root = skills_root if skills_root.is_absolute() else repo / skills_root
    skill = skills_root / f"verify-{args.app}"
    if skill.exists() and not args.force:
        print(f"scaffold: {skill} exists; maintain it with /maintain-verification-skill, or pass --force to overwrite",
              file=sys.stderr)
        return 1

    inv = scan_repo.scan(repo)
    try:
        skill_rel = skill.relative_to(repo).as_posix()
    except ValueError:
        skill_rel = str(skill)
    C = f"{skill_rel}/scripts/control.sh"
    server = pick_server(inv)
    cli = pick_cli(inv)
    launch, port_mode, default_port, launch_todos = launch_command(server, inv)
    health = next((r["path"] for r in inv["health_routes"] if r["method"] in ("GET", "ANY")), "")
    feats_all = inv["features"]
    feats = feats_all[: args.max_features] if args.max_features > 0 else feats_all
    skipped = feats_all[len(feats):]
    list_routes = {f["resource"]: r for f in feats_all if f["action"] == "list" for r in f["routes"] if r["method"] in ("GET", "ANY")}
    list_cmds = {f["resource"]: c for f in feats_all if f["action"] == "list" for c in f["commands"]}
    read_path = next(iter(list_routes.values()), {}).get("path", "") if server else ""
    cli_prefix, cli_suffix = cli_base(cli, inv)
    cli_prog = cli["cmd"] if cli else ""
    surfaces = (["HTTP API"] if inv["http_routes"] else []) + (["CLI"] if inv["cli_commands"] else [])
    if launch:
        prove_args = f"http GET {read_path or health or '/'}"
    elif cli_prog:
        first_list = next(iter(list_cmds.values()), None)
        sub = first_list["name"] if first_list else "--help"
        prove_args = f"cli -- {cli_prefix} {sub}{cli_suffix if first_list else ''}".replace("  ", " ")
    else:
        prove_args = f"cli -- {TODO}: a read-only command"

    (skill / "features").mkdir(parents=True, exist_ok=True)
    (skill / "scripts").mkdir(parents=True, exist_ok=True)
    written = []
    (skill / "SKILL.md").write_text(skill_md(args.app, inv, C, skill_rel, launch, port_mode, health, read_path,
                                             cli_prog, surfaces, prove_args), encoding="utf-8")
    written.append("SKILL.md")
    (skill / "features" / "README.md").write_text(readme_md(args.app, feats, C, skipped), encoding="utf-8")
    written.append("features/README.md")
    fm = [f"# {args.app} feature map", "",
          "Entry point to the maintained feature map. The per-feature recipes live in "
          "[features/README.md](features/README.md); keep this list in sync with that index.", ""]
    fm += [f"- [{f['title']}](features/{f['id']}.md)" for f in feats] or [f"- {TODO}: no features detected yet"]
    (skill / "FEATURE_MAP.md").write_text("\n".join(fm) + "\n", encoding="utf-8")
    written.append("FEATURE_MAP.md")
    for f in feats:
        text = feature_md(f, inv, C, cli_prefix, cli_suffix, list_routes.get(f["resource"]),
                          list_cmds.get(f["resource"]))
        (skill / "features" / f"{f['id']}.md").write_text(text, encoding="utf-8")
        written.append(f"features/{f['id']}.md")
    cli_check = ""
    if not launch and cli_prog:
        cli_check = f"{cli_prog} --help"
    if not launch and not cli_prog:
        cli_check = f"{TODO}: command that proves the app is usable"
    rel_to_repo = os.path.relpath(repo, skill / "scripts")
    control = CONTROL_SH
    for key, val in {"APP": sh(args.app), "LAUNCH_CMD": sh(launch), "PORT_MODE": port_mode if launch else "none",
                     "DEFAULT_PORT": str(default_port or 0), "HEALTH_PATH": sh(health),
                     "READ_PATH": sh(read_path), "CLI_CHECK": sh(cli_check), "REL_TO_REPO": rel_to_repo}.items():
        control = control.replace(f"@@{key}@@", val)
    ctl = skill / "scripts" / "control.sh"
    ctl.write_text(control, encoding="utf-8")
    ctl.chmod(0o755)
    written.append("scripts/control.sh")

    todos = []
    for p in sorted(skill.rglob("*")):
        if p.is_file():
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if TODO in line:
                    todos.append(f"{p.relative_to(skill).as_posix()}:{n}")
    summary = {"skill_dir": str(skill), "files": written, "features": [f["id"] for f in feats],
               "not_mapped": [f["id"] for f in skipped], "launch": launch or None, "health": health or None,
               "read_path": read_path or None, "cli": cli_prog or None, "todo_markers": len(todos),
               "todo_locations": todos,
               "next": [f"replace every {TODO} (grep -rn 'TODO(verify)' {skill_rel})",
                        f"{C} prove --feature <id> {prove_args}",
                        f"python3 {Path(__file__).resolve().parent / 'check_verify_skill.py'} {skill_rel}"]}
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"scaffold: wrote {skill} ({len(written)} files, {len(feats)} features)")
        print(f"  launch: {launch or '(none: CLI-only)'}")
        print(f"  health: {health or '(none: TCP check)'}  read: {read_path or '-'}  cli: {cli_prog or '-'}")
        print(f"  {len(todos)} TODO(verify) markers to replace, e.g. {', '.join(todos[:4])}")
        for n in summary["next"]:
            print(f"  next: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
