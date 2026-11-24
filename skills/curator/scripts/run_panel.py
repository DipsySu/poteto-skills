#!/usr/bin/env python3
"""Run curator panel seats from one routing file and validate every fragment.

The config is a nonempty JSON array of {"name": ..., "vendor": ..., "argv": [...]}.
Use trusted read-only commands that read the prompt on stdin and print only the
proposal fragment on stdout. Commands run directly, without a shell, in --repo.
Only whole argv tokens ${CURATOR_MODEL:-default} and ${CURATOR_EFFORT:-default}
expand environment overrides; all other arguments are literal.

All configuration is checked before running seats. At least two distinct vendors
are required unless --allow-single-vendor is passed. Every seat runs even if an
earlier seat fails. Each output is checked with check_proposals.py --fragment
--fetch --skills-dir REPO/skills; panel mode always fetches sources. No proposals.
is valid. CURATOR_TIMEOUT bounds each seat (default 3600 seconds).

Writes logs/curator/DATE.<seat>-x-propose.md and .err, plus seat name, vendor,
resolved argv, exit_code and check_exit_code in DATE.log. A repeated date replaces
seat artifacts and appends to the log. Exit 0: all pass; 1: seat/check failure;
2: invalid configuration (fix the named field before rerunning).
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
OVERRIDE = re.compile(r"\$\{(CURATOR_MODEL|CURATOR_EFFORT):-([^{}]+)\}")


def load_seats(path: Path, allow_single_vendor: bool) -> list[dict]:
    seats = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(seats, list) or not seats:
        raise ValueError(f"{path}: set a nonempty JSON array of seats with name, vendor and argv")
    names = set()
    for index, seat in enumerate(seats, 1):
        where = f"{path}: seat #{index}"
        if not isinstance(seat, dict) or set(seat) != {"name", "vendor", "argv"}:
            raise ValueError(f"{where}: provide exactly name, vendor and argv fields")
        name, vendor, argv = seat["name"], seat["vendor"], seat["argv"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ValueError(f"{where}: set name to letters, digits, underscores or hyphens, starting with a letter or digit")
        if name.casefold() in names:
            raise ValueError(f"{where}: rename duplicate seat {name!r}; names must be unique ignoring case")
        names.add(name.casefold())
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError(f"{where} ({name}): set vendor to a nonempty provider name")
        seat["vendor"] = vendor.strip().casefold()
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
                or not argv[0].strip()):
            raise ValueError(f"{where} ({name}): set argv to a nonempty string array beginning with a read-only executable")
        seat["argv"] = [os.environ.get(match[1]) or match[2] if (match := OVERRIDE.fullmatch(arg)) else arg
                        for arg in argv]
    if not allow_single_vendor and len({seat["vendor"] for seat in seats}) < 2:
        raise ValueError("all seats use the same vendor; add a seat from another vendor or pass --allow-single-vendor")
    return seats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", type=Path, help="UTF-8 prompt file sent unchanged to each seat on stdin")
    parser.add_argument("--repo", type=Path, default=SCRIPT_DIR.parents[2], help="collection checkout for seat cwd and logs")
    parser.add_argument("--date", default=date.today().isoformat(), help="log date, YYYY-MM-DD (default: today)")
    parser.add_argument("--panel-config", type=Path, default=SCRIPT_DIR.parent / "references/panel.json",
                        help="routing JSON file (default: curator references/panel.json)")
    parser.add_argument("--allow-single-vendor", action="store_true", help="explicitly allow seats from just one vendor")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args.date):
            raise ValueError("pass --date YYYY-MM-DD")
        date.fromisoformat(args.date)
        repo = args.repo.resolve()
        if not (repo / "skills").is_dir() or not (repo / "research/upstream").is_dir():
            raise ValueError(f"{repo}: pass --repo pointing to a poteto-skills checkout")
        prompt = args.prompt.read_bytes()
        if not prompt.strip():
            raise ValueError(f"{args.prompt}: write a nonempty prompt before running --panel")
        timeout = float(os.environ.get("CURATOR_TIMEOUT", "3600"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("set CURATOR_TIMEOUT to a positive finite number of seconds")
        seats = load_seats(args.panel_config, args.allow_single_vendor)
    except (OSError, ValueError) as error:
        print(f"panel: {error}; fix the prompt, routing file or options and rerun --panel", file=sys.stderr)
        return 2

    log_dir = repo / "logs/curator"
    log_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    with (log_dir / f"{args.date}.log").open("a", encoding="utf-8") as log:
        def report(message: str) -> None:
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        for seat in seats:
            name = seat["name"]
            fragment = log_dir / f"{args.date}.{name}-x-propose.md"
            stderr = fragment.with_suffix(".err")
            report(f"panel: running {name} (vendor={seat['vendor']}) argv={json.dumps(seat['argv'])}")
            with fragment.open("wb") as out, stderr.open("wb") as err:
                try:
                    with subprocess.Popen(seat["argv"], stdin=subprocess.PIPE, stdout=out, stderr=err,
                                          cwd=repo, start_new_session=True) as process:
                        try:
                            process.communicate(input=prompt, timeout=timeout)
                            code = process.returncode
                        except subprocess.TimeoutExpired:
                            code = 124
                            err.write(b"Seat timed out; shorten the prompt or increase CURATOR_TIMEOUT.\n")
                        finally:
                            # A CLI's children share the artifact descriptors. Stop the whole
                            # group before checking, even if the direct process has exited.
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.communicate()
                except OSError as error:
                    code = 127
                    err.write(f"Cannot run seat: {error}; fix its argv executable in the routing file.\n".encode())
            checked = subprocess.run([sys.executable, str(SCRIPT_DIR / "check_proposals.py"), str(fragment),
                                      "--fragment", "--fetch", "--skills-dir", str(repo / "skills")],
                                     capture_output=True, text=True, errors="replace", cwd=repo)
            if checked.stdout:
                report(checked.stdout.rstrip())
            if checked.stderr:
                report(checked.stderr.rstrip())
            report(json.dumps({**seat, "exit_code": code, "check_exit_code": checked.returncode}))
            if code or checked.returncode or not fragment.read_bytes().strip():
                failed = True
                reason = f"command exited {code}" if code else (
                    "empty fragment" if not fragment.read_bytes().strip() else "fragment failed validation")
                report(f"panel: FAIL seat {name}: {reason}; fix its command or proposal fields/sources "
                       f"(or return No proposals.), then rerun --panel; see {fragment} and {stderr}")
            else:
                report(f"panel: PASS seat {name}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
