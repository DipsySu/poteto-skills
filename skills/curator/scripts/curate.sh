#!/usr/bin/env bash
# The curator's cron entry: upstream diff -> proposals -> (weekly) apply on a local branch.
# Never pushes. Commits only after scripts/verify.sh passes.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: curate.sh [--propose | --apply] [--offline] [--no-fetch] [--date YYYY-MM-DD] [--repo DIR]
       curate.sh --panel PROMPT.md [--panel-config FILE] [--allow-single-vendor] [--date YYYY-MM-DD] [--repo DIR]

  --propose   (default) run upstream_diff.sh, then write proposals/DATE.md:
              online: headless `claude -p --model opus --effort max` with references/curator-prompt.md
              (web research; every proposal needs a fetched source URL, a pyramid level, a change)
              --offline: proposals derived from the upstream diff only, no LLM
              The file must pass check_proposals.py (online runs also re-fetch every URL).
  --apply     weekly: make the change on local branch curator/DATE in a separate git worktree
              (your checkout is untouched), append a CHANGELOG entry, run scripts/verify.sh, and
              commit only if it passes. Never pushes.
              online: claude implements the best proposals; --offline: sync changed upstream files
              into research/upstream/ (a deterministic, sourced change)
  --panel F   send F on stdin to each read-only seat in references/panel.json; no upstream diff.
              Save DATE.<seat>-x-propose.md and .err; check every fragment with --fragment --fetch.
              Exit 1 names a failed/empty/invalid seat; exit 2 rejects invalid configuration
              or a single-vendor panel before running seats. No proposals. is a valid fragment.
  --panel-config F       use this JSON array of {name, vendor, argv} seats instead
  --allow-single-vendor  allow all panel seats to use one vendor (panel mode only)
  --offline   skip network fetches and every LLM step; requires a populated local cache
  --no-fetch  don't fetch upstream; diff against the cached clone
  --date D    run date (default: today)
  --repo DIR  poteto-skills checkout (default: derived from this script)

logs: logs/curator/DATE.log plus DATE.upstream.json, DATE.claude-*.json, DATE.verify.log
      panel logs each seat's name, vendor, resolved argv, process and checker exit codes to DATE.log
env:  POTETO_CACHE (clone and worktrees, default ~/.cache/poteto-skills), POTETO_UPSTREAM_URL,
      CURATOR_MODEL (propose/apply: opus; panel: configured Claude model), CURATOR_EFFORT (max),
      CURATOR_TIMEOUT (3600 s per seat); panel mode requires network for --fetch
cron: 17 6 * * *  /path/to/poteto-skills/skills/curator/scripts/curate.sh --propose
      47 6 * * 1  /path/to/poteto-skills/skills/curator/scripts/curate.sh --apply
EOF
}

mode=propose offline=0 nofetch=0 date_="$(date +%F)" repo="${POTETO_SKILLS_REPO:-}"
panel_prompt="" panel_config="" allow_single_vendor=0 explicit_mode=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --propose) mode=propose; explicit_mode=1; shift ;;
    --apply) mode=apply; explicit_mode=1; shift ;;
    --panel)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo 'curate: pass --panel PROMPT.md' >&2; exit 2; }
      panel_prompt="$2"; shift 2 ;;
    --panel-config)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo 'curate: pass --panel-config FILE' >&2; exit 2; }
      panel_config="$2"; shift 2 ;;
    --allow-single-vendor) allow_single_vendor=1; shift ;;
    --offline) offline=1; shift ;;
    --no-fetch) nofetch=1; shift ;;
    --date) date_="${2:?--date needs YYYY-MM-DD}"; shift 2 ;;
    --repo) repo="${2:?--repo needs a directory}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "$date_" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "curate: --date must be YYYY-MM-DD" >&2; exit 2; }
if [[ -n "$panel_prompt" ]]; then
  [[ "$explicit_mode" == 0 && "$offline" == 0 && "$nofetch" == 0 ]] || {
    echo 'curate: use --panel without --propose, --apply, --offline or --no-fetch; every panel source must be fetched' >&2; exit 2;
  }
  mode=panel
elif [[ -n "$panel_config" || "$allow_single_vendor" == 1 ]]; then
  echo 'curate: use --panel PROMPT.md with --panel-config or --allow-single-vendor' >&2; exit 2
fi

script_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")"
script_dir="$(dirname "$script_real")"
skill_dir="$(dirname "$script_dir")"
[[ -n "$repo" ]] || repo="$(cd "$skill_dir/../.." && pwd)"
[[ -d "$repo/research/upstream" && -d "$repo/skills" ]] || { echo "curate: $repo is not a poteto-skills checkout; pass --repo" >&2; exit 2; }
repo="$(cd "$repo" && pwd)"
if [[ "$mode" == panel ]]; then
  panel_args=("$panel_prompt" --repo "$repo" --date "$date_")
  [[ -n "$panel_config" ]] && panel_args+=(--panel-config "$panel_config")
  [[ "$allow_single_vendor" == 1 ]] && panel_args+=(--allow-single-vendor)
  exec python3 "$script_dir/run_panel.py" "${panel_args[@]}"
fi
cache="${POTETO_CACHE:-$HOME/.cache/poteto-skills}"
log_dir="$repo/logs/curator"
mkdir -p "$log_dir"
exec > >(tee -a "$log_dir/$date_.log") 2>&1

say() { echo "[curate $(date +%H:%M:%S)] $*"; }
die() { say "ERROR: $*"; exit 1; }

say "mode=$mode offline=$offline no_fetch=$nofetch repo=$repo cache=$cache"
up_json="$log_dir/$date_.upstream.json"
proposals="$repo/proposals/$date_.md"
timeout_cmd=()
command -v timeout >/dev/null 2>&1 && timeout_cmd=(timeout "${CURATOR_TIMEOUT:-3600}")

# 1. upstream diff ----------------------------------------------------------------
diff_args=(--repo "$repo" --json)
[[ "$nofetch" == 1 || "$offline" == 1 ]] && diff_args+=(--offline)
if "$script_dir/upstream_diff.sh" "${diff_args[@]}" > "$up_json"; then
  say "upstream: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["changes"], "change(s),", len(d["changed"]), "changed file(s), head", d["upstream"]["head"][:12])' "$up_json")"
else
  die "upstream_diff failed (exit $?); no proposal can be grounded in this run"
fi

offline_proposals() {
  python3 - "$up_json" "$date_" <<'PY'
import json, sys
up, date = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(up))
except (OSError, ValueError):
    d = {}
ours = {"create-verification-skill": "create-verification-skill", "maintain-verification-skill": "maintain-verification-skill",
        "correct": "correct", "architect": "architect", "sharpen": "sharpen", "in-your-own-words": "sharpen",
        "create-formal-verification-skill": "formal-verification"}
items = []
for c in d.get("changed", []):
    skill = ours.get(c["file"].split("/")[0], "collection")
    items.append((f"Upstream changed: {c['file']}", c["url"], skill,
                  f"Diff research/upstream/{c['file']} against upstream, port any new rule into {'skills/' + skill if skill != 'collection' else 'the matching skill'} "
                  "as a script, lint, or test where it can be checked, and refresh the snapshot file.",
                  "pstack changed this file after the snapshot pinned in research/NOTES.md."))
for n in d.get("new_skills", []):
    items.append((f"New upstream skill: {n['name']}", n["url"], "collection",
                  f"Read pstack skill {n['name']}; if it pushes output up the pyramid, propose a poteto-skills skill with a "
                  "deterministic helper script, otherwise record it in MAPPING.md as not adopted.",
                  "pstack has a skill with no snapshot in research/upstream/ that no earlier curator run reported."))
for m in d.get("missing_upstream", []):
    items.append((f"Upstream moved or removed: {m['file']}", "https://github.com/cursor/plugins/tree/main/pstack", "collection",
                  f"Find where {m['file']} went upstream (renamed or deleted), update research/upstream and MAPPING.md, "
                  "and drop guidance that upstream retracted.", "The snapshot file no longer exists at its upstream path."))
head = (d.get("upstream") or {}).get("head", "")[:12] or "unknown"
print(f"# Curator proposals: {date}\n")
print(f"Generated offline by curate.sh from the upstream diff (no web research). Upstream cursor/plugins @ {head}.\n")
if not items:
    print("No proposals: upstream pstack matches the snapshot.")
for i, (title, url, skill, change, why) in enumerate(items, 1):
    print(f"## {i}. {title}\n\n- Source: {url}\n- Level: prose\n- Skill: {skill}\n- Change: {change}\n- Why: {why}\n")
PY
}

propose() {
  mkdir -p "$repo/proposals"
  if [[ "$offline" == 1 ]]; then
    offline_proposals > "$proposals"
    say "wrote $proposals (offline, from the upstream diff)"
  else
    command -v claude >/dev/null 2>&1 || die "claude CLI not found; install Claude Code or use --offline"
    local prompt
    prompt="$(cat "$skill_dir/references/curator-prompt.md")

Run context (filled in by curate.sh):
- Today is $date_. The collection is $repo. Write exactly one file: proposals/$date_.md. Change nothing else.
- Upstream pstack diff from upstream_diff.sh (JSON): logs/curator/$date_.upstream.json
- Skills in this collection: $(ls "$repo/skills" | tr '\n' ' ')
- Sources already used: research/NOTES.md. Skill-to-source map: MAPPING.md."
    say "propose: claude -p --model ${CURATOR_MODEL:-opus} --effort ${CURATOR_EFFORT:-max}"
    (cd "$repo" && "${timeout_cmd[@]}" claude -p "$prompt" --model "${CURATOR_MODEL:-opus}" --effort "${CURATOR_EFFORT:-max}" \
        --permission-mode acceptEdits --allowedTools "WebSearch WebFetch Read Grep Glob Write" --output-format json) \
      > "$log_dir/$date_.claude-propose.json" || die "claude propose step failed; see $log_dir/$date_.claude-propose.json"
    [[ -f "$proposals" ]] || die "claude did not write $proposals"
  fi
  local check_args=("$proposals" --skills-dir "$repo/skills")
  [[ "$offline" == 0 ]] && check_args+=(--fetch)
  python3 "$script_dir/check_proposals.py" "${check_args[@]}" || die "$proposals fails the acceptance bar; fix or delete it"
}

append_changelog() {  # file date summary sources
  python3 - "$@" <<'PY'
import re, sys
path, date, summary, sources = sys.argv[1:5]
text = open(path, encoding="utf-8").read()
entry = f"- curator {date}: {summary.rstrip('.')}." + (f" Sources: {sources.rstrip('.')}." if sources else "")
m = re.search(r"^## \[Unreleased\][^\n]*\n", text, re.M)
if not m:
    first = re.search(r"^## \[", text, re.M)
    block = "## [Unreleased]\n\n### Changed\n\n" + entry + "\n\n"
    text = text[:first.start()] + block + text[first.start():] if first else text.rstrip() + "\n\n" + block
else:
    nxt = re.search(r"^## \[", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    section = text[m.end():end]
    ch = re.search(r"^### Changed[^\n]*\n", section, re.M)
    if ch:
        after = re.search(r"^### ", section[ch.end():], re.M)
        pos = ch.end() + (after.start() if after else len(section[ch.end():]))
        body = section[:pos].rstrip("\n") + "\n" + entry + "\n\n" + section[pos:].lstrip("\n")
    else:
        body = "\n### Changed\n\n" + entry + "\n\n" + section.lstrip("\n")
    text = text[:m.end()] + body + text[end:]
open(path, "w", encoding="utf-8").write(text)
PY
}

apply_change() {
  [[ -f "$proposals" ]] || propose
  local check_args=("$proposals" --skills-dir "$repo/skills")
  [[ "$offline" == 0 ]] && check_args+=(--fetch)
  python3 "$script_dir/check_proposals.py" "${check_args[@]}" >/dev/null \
    || die "$proposals fails the acceptance bar; not applying"
  local branch="curator/$date_" wt="$cache/worktrees/curator-$date_-$$"
  if git -C "$repo" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
    die "branch $branch already exists; review it or delete it (git branch -D $branch)"
  fi
  mkdir -p "$cache/worktrees"
  git -C "$repo" worktree add --quiet -b "$branch" "$wt" HEAD || die "git worktree add failed"
  drop() {
    git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"
    git -C "$repo" worktree prune
    git -C "$repo" branch -D "$branch" >/dev/null 2>&1 || true
  }
  mkdir -p "$wt/proposals"
  cp "$proposals" "$wt/proposals/$date_.md"
  local summary="" sources=""
  if [[ "$offline" == 1 ]]; then
    local out
    out="$(python3 - "$up_json" "$wt" <<'PY'
import json, shutil, sys
from pathlib import Path
up, wt = sys.argv[1], Path(sys.argv[2])
try:
    d = json.load(open(up))
except (OSError, ValueError):
    d = {}
clone = Path((d.get("upstream") or {}).get("clone", ""))
done, urls = [], []
for c in d.get("changed", []):
    src = clone / c["upstream_path"]
    if src.is_file():
        dst = wt / "research" / "upstream" / c["file"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        done.append(c["file"])
        urls.append(c["url"])
if done:
    provenance = wt / "research" / "snapshot-syncs.json"
    records = json.loads(provenance.read_text()) if provenance.exists() else []
    records.append({"head": d["upstream"]["head"], "version": d["upstream"]["version"], "files": done, "sources": urls})
    provenance.write_text(json.dumps(records, indent=2) + "\n")
    notes = wt / "research" / "NOTES.md"
    text = notes.read_text()
    note = "\nSnapshot updates after the original research: see [snapshot-syncs.json](snapshot-syncs.json) for the exact head, version, files, and URLs.\n"
    if note not in text:
        notes.write_text(text + note)
    print(f"sync research/upstream with pstack ({len(done)} file(s): {', '.join(done)})\t{', '.join(urls)}")
PY
)"
    summary="${out%%$'\t'*}"
    [[ "$out" == *$'\t'* ]] && sources="${out#*$'\t'}"
  else
    command -v claude >/dev/null 2>&1 || { drop; die "claude CLI not found; install Claude Code or use --offline"; }
    local prompt="You are the poteto-skills curator, apply step. The current directory is a fresh git worktree of the collection on branch $branch.
Read proposals/$date_.md and the acceptance bar in skills/curator/references/curator-prompt.md.
Implement at most three proposals that pass the bar, most deterministic first (architecture > types > lint > test > prose).
Rules: edit files only inside this worktree. Do not run git commit or git push; curate.sh commits only after scripts/verify.sh passes.
Keep every SKILL.md frontmatter valid. Scripts stay stdlib Python or bash, executable, with --help. Cite each source URL in the
skill's Sources footer and in MAPPING.md. When done, write one line describing the change to .curator-summary, or exactly
'nothing to apply' if no proposal passes the bar."
    say "apply: claude -p --model ${CURATOR_MODEL:-opus} --effort ${CURATOR_EFFORT:-max} in $wt"
    (cd "$wt" && "${timeout_cmd[@]}" claude -p "$prompt" --model "${CURATOR_MODEL:-opus}" --effort "${CURATOR_EFFORT:-max}" \
        --permission-mode acceptEdits --allowedTools "Read Grep Glob Edit Write WebFetch Bash(python3 *) Bash(bash *) Bash(chmod *)" \
        --output-format json) > "$log_dir/$date_.claude-apply.json" || { drop; die "claude apply step failed; see $log_dir/$date_.claude-apply.json"; }
    summary="$(head -n 1 "$wt/.curator-summary" 2>/dev/null || true)"
    rm -f "$wt/.curator-summary"
    sources="see proposals/$date_.md"
    [[ "$summary" == "nothing to apply" ]] && summary=""
  fi
  if [[ -z "$summary" || -z "$(git -C "$wt" status --porcelain -- . ':!proposals')" ]]; then
    say "nothing to apply; removing $branch"
    drop
    return 0
  fi
  append_changelog "$wt/CHANGELOG.md" "$date_" "$summary" "$sources"
  local verify_cmd="bash scripts/verify.sh --run-examples"
  say "verify: $verify_cmd"
  if ! (cd "$wt" && bash -c "$verify_cmd") > "$log_dir/$date_.verify.log" 2>&1; then
    git -C "$wt" add -A >/dev/null 2>&1 || true
    git -C "$wt" diff --cached > "$log_dir/$date_.failed.patch" 2>/dev/null || true
    drop
    die "verify failed (log: $log_dir/$date_.verify.log); nothing committed; patch kept at $log_dir/$date_.failed.patch"
  fi
  git -C "$wt" add -A
  git -C "$wt" -c user.name="${CURATOR_GIT_NAME:-poteto-curator}" -c user.email="${CURATOR_GIT_EMAIL:-curator@localhost}" \
    commit --quiet -m "curator: $date_ $summary" -m "Proposals: proposals/$date_.md
Verified by: $verify_cmd"
  local sha
  sha="$(git -C "$wt" rev-parse --short HEAD)"
  git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || true
  git -C "$repo" worktree prune
  say "committed $sha on $branch (local only, never pushed). Review: git -C $repo log -p $branch"
}

case "$mode" in
  propose) propose ;;
  apply) apply_change ;;
esac
say "done"
