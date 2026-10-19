#!/usr/bin/env bash
# Report whether TLA+ model checking can run here: java on PATH plus a tla2tools.jar.
# Also lists other verifiers it finds (apalache, kani, cbmc, z3, dafny, lean, coq).
# Exit 0 when java and tla2tools.jar are both present, 1 otherwise. Never downloads anything.
set -uo pipefail

usage() {
  cat <<'EOF'
usage: tla_available.sh [--json] [--quiet]

Reports whether TLA+ (TLC) can run on this machine: java on PATH and a tla2tools.jar.
Looks for the jar in $TLA2TOOLS_JAR, $TLA_HOME, ./, ~/.tla, ~/tla, ~/.local/share/tla,
/usr/share/java, /usr/local/lib, /opt/tla*, and a `tlc` wrapper on PATH.

  --json    machine-readable report
  --quiet   no output; exit status only

exit: 0 java + tla2tools.jar present, 1 missing (fall back to bounded_check.py), 2 usage error
EOF
}

json=0 quiet=0
for arg in "$@"; do
  case "$arg" in
    --json) json=1 ;;
    --quiet|-q) quiet=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

java_path="$(command -v java 2>/dev/null || true)"
java_version=""
if [[ -n "$java_path" ]]; then
  java_version="$("$java_path" -version 2>&1 | head -n 1 | tr -d '"')"
fi

jar=""
candidates=(
  "${TLA2TOOLS_JAR:-}"
  "${TLA_HOME:+$TLA_HOME/tla2tools.jar}"
  "./tla2tools.jar"
  "$HOME/.tla/tla2tools.jar"
  "$HOME/tla/tla2tools.jar"
  "$HOME/.local/share/tla/tla2tools.jar"
  "/usr/share/java/tla2tools.jar"
  "/usr/local/lib/tla2tools.jar"
)
for d in /opt/tla* /opt/TLA*; do
  [[ -d "$d" ]] && candidates+=("$d/tla2tools.jar" "$d/toolbox/tla2tools.jar")
done
for c in "${candidates[@]}"; do
  if [[ -n "$c" && -f "$c" ]]; then jar="$c"; break; fi
done
tlc_wrapper="$(command -v tlc 2>/dev/null || command -v tlc2 2>/dev/null || true)"

others=()
for tool in apalache-mc cargo-kani kani cbmc z3 dafny lean coqc; do
  p="$(command -v "$tool" 2>/dev/null || true)"
  [[ -n "$p" ]] && others+=("$tool=$p")
done

available=0
if [[ -n "$java_path" && ( -n "$jar" || -n "$tlc_wrapper" ) ]]; then available=1; fi
run_cmd=""
if [[ -n "$jar" ]]; then
  run_cmd="java -XX:+UseParallelGC -cp $jar tlc2.TLC -workers auto -deadlock Spec.tla"
elif [[ -n "$tlc_wrapper" ]]; then
  run_cmd="$tlc_wrapper -workers auto -deadlock Spec.tla"
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$quiet" == 1 ]]; then
  [[ "$available" == 1 ]]; exit $?
fi
if [[ "$json" == 1 ]]; then
  python3 - "$available" "$java_path" "$java_version" "$jar" "$tlc_wrapper" "$run_cmd" "$here" "${others[@]}" <<'PY'
import json, sys
available, java, version, jar, wrapper, run, here, *others = sys.argv[1:]
print(json.dumps({
    "available": available == "1",
    "java": java or None, "java_version": version or None,
    "tla2tools_jar": jar or None, "tlc_wrapper": wrapper or None,
    "run": run or None,
    "fallback": f"python3 {here}/bounded_check.py MODEL.py",
    "get_tla2tools": "https://github.com/tlaplus/tlaplus/releases (tla2tools.jar), plus a JDK 11+",
    "other_verifiers": dict(o.split("=", 1) for o in others),
}, indent=2))
PY
else
  echo "java:          ${java_path:-missing}${java_version:+ ($java_version)}"
  echo "tla2tools.jar: ${jar:-missing}${tlc_wrapper:+ (tlc wrapper: $tlc_wrapper)}"
  if [[ "$available" == 1 ]]; then
    echo "TLA+ available. Run: $run_cmd"
  else
    echo "TLA+ not available. Install a JDK 11+ and tla2tools.jar (https://github.com/tlaplus/tlaplus/releases),"
    echo "or check a Python model of the same design with: python3 $here/bounded_check.py MODEL.py"
  fi
  if [[ ${#others[@]} -gt 0 ]]; then echo "other verifiers: ${others[*]}"; else echo "other verifiers: none found"; fi
fi
[[ "$available" == 1 ]]
