#!/usr/bin/env bash
# Install the poteto-skills collection for Claude Code and/or Cursor.
# Only ever creates, updates, or removes <root>/<skill> entries for this collection's own skills.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: install.sh [--copy] [--target claude|cursor|both] [--project DIR] [--uninstall] [--force] [--dry-run]

Installs every skills/<name> of this checkout into:
  ~/.claude/skills/<name> and ~/.cursor/skills/<name>        (default)
  DIR/.claude/skills/<name> and DIR/.cursor/skills/<name>    (--project DIR)

  --copy        copy the files instead of symlinking (default: symlink, so `git pull` updates them)
  --target T    claude, cursor, or both (default: both)
  --project DIR install into a project instead of $HOME
  --uninstall   remove only entries this script installed (our symlinks, or copies with its marker file)
  --force       replace an existing entry with the same name that this script did not install
  --dry-run     print what would happen; change nothing

Other entries in the skills dirs (for example ~/.claude/skills/synced) are never touched.
Skills that share helpers (feedback-to-architecture uses correct; maintain-verification-skill uses
create-verification-skill) are always installed together.
exit: 0 done, 1 an entry was skipped because of a conflict, 2 usage error
EOF
}

mode=link target=both project="" uninstall=0 force=0 dry=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --copy) mode=copy; shift ;;
    --target) target="${2:-}"; shift 2 || { usage >&2; exit 2; } ;;
    --project) project="${2:-}"; shift 2 || { usage >&2; exit 2; } ;;
    --uninstall) uninstall=1; shift ;;
    --force) force=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$target" in claude|cursor|both) ;; *) echo "install.sh: --target must be claude, cursor, or both" >&2; exit 2 ;; esac

src_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/skills"
[[ -d "$src_root" ]] || { echo "install.sh: $src_root not found" >&2; exit 2; }
if [[ -n "$project" ]]; then
  [[ -d "$project" ]] || { echo "install.sh: --project $project is not a directory" >&2; exit 2; }
  base="$(cd "$project" && pwd -P)"
else
  base="${HOME:?HOME is not set}"
fi
roots=()
[[ "$target" == claude || "$target" == both ]] && roots+=("$base/.claude/skills")
[[ "$target" == cursor || "$target" == both ]] && roots+=("$base/.cursor/skills")

marker=".poteto-skills-install"
skills=()
for d in "$src_root"/*/; do
  if [[ -f "$d/SKILL.md" ]]; then skills+=("$(basename "$d")"); fi
done
[[ ${#skills[@]} -gt 0 ]] || { echo "install.sh: no skills found in $src_root" >&2; exit 2; }

run() {
  if [[ "$dry" == 1 ]]; then echo "[dry-run] $*"; else "$@"; fi
}

is_ours() {  # an entry we installed: a symlink into src_root, or a copy carrying our marker
  local dest="$1"
  if [[ -L "$dest" ]]; then
    local t
    t="$(readlink "$dest")"
    [[ "$t" == "$src_root/"* ]]
  else
    [[ -f "$dest/$marker" ]]
  fi
}

conflicts=0 changed=0 made_root=""
for root in "${roots[@]}"; do
  for name in "${skills[@]}"; do
    src="$src_root/$name"
    dest="$root/$name"
    if [[ "$uninstall" == 1 ]]; then
      if [[ -e "$dest" || -L "$dest" ]]; then
        if is_ours "$dest"; then
          run rm -rf "$dest"; echo "removed $dest"; changed=$((changed + 1))
        else
          echo "skip $dest: not installed by poteto-skills (left alone)"
        fi
      fi
      continue
    fi
    if [[ -L "$dest" && "$mode" == link && "$(readlink "$dest")" == "$src" ]]; then
      echo "ok   $dest -> $src"
      continue
    fi
    if [[ -e "$dest" || -L "$dest" ]]; then
      if is_ours "$dest" || [[ "$force" == 1 ]]; then
        run rm -rf "$dest"
      else
        echo "skip $dest: exists and was not installed by poteto-skills (use --force to replace it)"
        conflicts=$((conflicts + 1))
        continue
      fi
    fi
    if [[ ! -d "$root" && "$made_root" != "$root" ]]; then run mkdir -p "$root"; made_root="$root"; fi
    if [[ "$mode" == link ]]; then
      run ln -s "$src" "$dest"
      echo "link $dest -> $src"
    else
      run cp -R "$src" "$dest"
      if [[ "$dry" == 1 ]]; then
        echo "[dry-run] write $dest/$marker"
      else
        printf 'installed-by: poteto-skills install.sh\nsource: %s\ndate: %s\n' "$src" "$(date -u +%FT%TZ)" > "$dest/$marker"
        find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} +
      fi
      echo "copy $dest <- $src"
    fi
    changed=$((changed + 1))
  done
done

action=$([[ "$uninstall" == 1 ]] && echo "uninstall" || echo "install")
echo "install.sh: $action done: ${#skills[@]} skill(s), ${#roots[@]} target dir(s), $changed change(s), $conflicts conflict(s)$([[ "$dry" == 1 ]] && echo ' (dry run, nothing changed)')"
[[ "$conflicts" == 0 ]]
