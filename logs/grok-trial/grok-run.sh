#!/usr/bin/env bash
# usage: grok-run.sh NAME PROMPT_FILE   (run from the target repo)
set -u
L=/home/box/developer/poteto-skills/logs/grok-trial
exec /home/box/.local/bin/grok --trust -m grok-4.7 --reasoning-effort xhigh --always-approve \
  --deny 'Bash(git push*)' --deny 'Bash(*git push*)' --deny 'Bash(docker*)' --deny 'Bash(*pay-module*)' \
  --deny 'Bash(pkill*)' --deny 'Bash(killall*)' --deny 'Bash(rm -rf /*)' --deny 'Bash(gh *)' \
  --output-format streaming-messages-json --debug-file "$L/$1.debug.log" --prompt-file "$2" \
  < /dev/null > "$L/$1.jsonl" 2> "$L/$1.err"
