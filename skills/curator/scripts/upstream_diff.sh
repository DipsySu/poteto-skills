#!/usr/bin/env bash
# Refresh a cached sparse clone of cursor/plugins (pstack/ only) and diff it against the
# snapshot in research/upstream/. Lists changed files and new/removed upstream skills with URLs.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: upstream_diff.sh [--repo DIR] [--offline] [--json]

Keeps a sparse, blob-filtered clone of cursor/plugins (pstack/ only) in $POTETO_CACHE
(default ~/.cache/poteto-skills/cursor-plugins), compares it with <repo>/research/upstream/,
and reports:
  changed           snapshot files whose upstream copy differs (with GitHub URLs)
  missing_upstream  snapshot files upstream no longer has at the expected path
  new_skills        upstream skills absent from research/upstream/ that the previous run had not
                    seen (on the first run: all of them)
  removed_skills    upstream skills gone since the previous run
  untracked_skills  upstream skills this collection has no snapshot of (informational)
  commits_since     upstream commits touching pstack/ since the sha pinned in research/NOTES.md

  --repo DIR   poteto-skills checkout (default: $POTETO_SKILLS_REPO, else derived from this script)
  --offline    don't fetch; diff against the cached clone as it is
  --json       machine-readable report

env: POTETO_CACHE, POTETO_UPSTREAM_URL (default https://github.com/cursor/plugins.git),
     POTETO_UPSTREAM_BRANCH (default main). Tests: point POTETO_CACHE at a scratch dir and
     POTETO_UPSTREAM_URL at a local git repo to stay off the network.
exit: 0 report printed, 2 usage error, 3 no usable clone (first fetch failed)
EOF
}

repo="${POTETO_SKILLS_REPO:-}" offline=0 json=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) repo="${2:?--repo needs a directory}"; shift 2 ;;
    --offline) offline=1; shift ;;
    --json) json=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

script_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")"
if [[ -z "$repo" ]]; then
  repo="$(cd "$(dirname "$script_real")/../../.." && pwd)"
fi
[[ -d "$repo/research/upstream" ]] || { echo "upstream_diff: $repo has no research/upstream/; pass --repo <poteto-skills checkout>" >&2; exit 2; }

cache="${POTETO_CACHE:-$HOME/.cache/poteto-skills}"
url="${POTETO_UPSTREAM_URL:-https://github.com/cursor/plugins.git}"
branch="${POTETO_UPSTREAM_BRANCH:-main}"
clone="$cache/cursor-plugins"
fetch="skipped"
mkdir -p "$cache"

if [[ "$offline" == 0 ]]; then
  if [[ ! -d "$clone/.git" ]]; then
    rm -rf "$clone"
    if git clone --quiet --filter=blob:none --sparse --branch "$branch" "$url" "$clone" 2>"$cache/fetch.err" \
       && git -C "$clone" sparse-checkout set pstack 2>>"$cache/fetch.err"; then
      fetch="cloned"
    else
      fetch="failed"
      rm -rf "$clone"
    fi
  else
    if git -C "$clone" fetch --quiet origin "$branch" 2>"$cache/fetch.err" \
       && git -C "$clone" reset --quiet --hard FETCH_HEAD 2>>"$cache/fetch.err"; then
      fetch="updated"
    else
      fetch="failed"
    fi
  fi
  if [[ "$fetch" == failed ]]; then
    echo "upstream_diff: fetch from $url failed: $(tail -n 1 "$cache/fetch.err" 2>/dev/null)" >&2
  fi
fi
if [[ ! -d "$clone/.git" ]]; then
  echo "upstream_diff: no cached clone at $clone and nothing fetched; run once with network access" >&2
  exit 3
fi

python3 - "$repo" "$clone" "$url" "$branch" "$fetch" "$json" "$cache/state.json" <<'PY'
import datetime as dt, json, os, re, subprocess, sys
from pathlib import Path

repo, clone, url, branch, fetch, as_json, state_file = sys.argv[1:8]
repo, clone, state_file = Path(repo), Path(clone), Path(state_file)
snap, up = repo / "research" / "upstream", clone / "pstack"

def git(*args):
    r = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""

def gh(path: Path) -> str:
    return f"https://github.com/cursor/plugins/blob/{branch}/{path.relative_to(clone).as_posix()}"

head = git("rev-parse", "HEAD")
version = None
for cand in (up / ".cursor-plugin" / "plugin.json", up / "plugin.json", up / "package.json"):
    try:
        version = json.loads(cand.read_text()).get("version")
        if version:
            break
    except (OSError, ValueError):
        pass
notes = (repo / "research" / "NOTES.md")
pinned = None
if notes.is_file():
    m = re.search(r"cursor/plugins`?\s*@\s*`?([0-9a-f]{7,40})", notes.read_text())
    pinned = m.group(1) if m else None
commits = []
if pinned and git("cat-file", "-t", pinned) == "commit":
    commits = [ln for ln in git("log", "--oneline", f"{pinned}..HEAD", "--", "pstack").splitlines()][:50]

def upstream_for(rel: Path):
    parts = rel.parts
    if rel.name == "PSTACK_LICENSE":
        return [up / "LICENSE", clone / "LICENSE"]
    if len(parts) == 1 and re.match(r"\d\d-.*\.md$", rel.name):
        return [up / "docs" / "guide" / rel.name]
    rest = Path(*parts[1:]) if len(parts) > 1 else Path(rel.name)
    return [up / "skills" / parts[0] / rest, up / "skills" / parts[0] / "references" / rest]

changed, missing, same = [], [], 0
for f in sorted(p for p in snap.rglob("*") if p.is_file()):
    rel = f.relative_to(snap)
    cands = upstream_for(rel)
    hit = next((c for c in cands if c.is_file()), None)
    if hit is None:
        missing.append({"file": rel.as_posix(), "looked_for": [c.relative_to(clone).as_posix() for c in cands]})
    elif hit.read_bytes() != f.read_bytes():
        changed.append({"file": rel.as_posix(), "upstream_path": hit.relative_to(clone).as_posix(), "url": gh(hit)})
    else:
        same += 1

skills_dir = up / "skills"
current = sorted(d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file()) if skills_dir.is_dir() else []
tracked = sorted(d.name for d in snap.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())
prev = None
try:
    prev = json.loads(state_file.read_text())
except (OSError, ValueError):
    pass
skill_url = lambda n: f"https://github.com/cursor/plugins/tree/{branch}/pstack/skills/{n}"
seen = set((prev or {}).get("skills", []))  # reported by an earlier run: not new again
new = [{"name": n, "url": skill_url(n)} for n in current if n not in tracked and n not in seen]
removed = [{"name": n} for n in (prev or {}).get("skills", []) if n not in current]
state_file.write_text(json.dumps({"skills": current, "head": head,
                                  "checked": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, indent=2))
report = {
    "upstream": {"url": url, "branch": branch, "head": head, "head_date": git("log", "-1", "--format=%cI"),
                 "version": version, "fetch": fetch, "clone": str(clone)},
    "snapshot": {"dir": str(snap), "pinned_sha": pinned, "files_same": same, "commits_since": commits},
    "changed": changed, "missing_upstream": missing, "new_skills": new, "removed_skills": removed,
    "untracked_skills": [n for n in current if n not in tracked], "first_run": prev is None,
}
report["changes"] = len(changed) + len(missing) + len(new) + len(removed)
if as_json == "1":
    print(json.dumps(report, indent=2))
    sys.exit(0)
u = report["upstream"]
print(f"upstream: {u['url']} @ {u['head'][:12] or '?'} ({u['head_date'] or '?'}) pstack {u['version'] or '?'}; fetch: {u['fetch']}")
print(f"snapshot: research/upstream ({same} files unchanged), pinned {pinned or '?'}; "
      f"{len(commits)} upstream commit(s) touch pstack/ since")
for c in commits[:10]:
    print(f"  {c}")
print(f"changed ({len(changed)}):")
for c in changed:
    print(f"  {c['file']:<55} {c['url']}")
if missing:
    print(f"missing upstream ({len(missing)}):")
    for m in missing:
        print(f"  {m['file']} (looked for {', '.join(m['looked_for'])})")
if report["first_run"]:
    print("new skills absent from snapshot: " + ", ".join(n["name"] for n in new))
else:
    print(f"new skills since last run ({len(new)}):" + "".join(f"\n  {n['name']:<40} {n['url']}" for n in new))
    if removed:
        print(f"removed since last run ({len(removed)}): " + ", ".join(r["name"] for r in removed))
print(f"untracked upstream skills ({len(report['untracked_skills'])}): " + ", ".join(report["untracked_skills"][:40]))
print(f"changes: {report['changes']}")
PY
