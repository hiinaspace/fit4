#!/bin/sh
# Usage: run_shard.sh SHARD_FILE [run_window.py options]   (one window id per line; # comments allowed)
# Runs the windows one after another; a failing window leaves ID.error.json and the shard carries on.
here=$(cd "$(dirname "$0")" && pwd)
shard=$1; shift
out=/videos-out/cluster
ok=0; bad=0
for id in $(grep -v '^#' "$shard"); do
  echo "=== $id $(date -u +%FT%TZ)"
  if python3 "$here/run_window.py" "$id" "$@"; then ok=$((ok+1)); else
    bad=$((bad+1))
    [ -e "$out/$id.error.json" ] || printf '{"window": {"id": "%s"}, "error": "run_window.py died"}\n' "$id" > "$out/$id.error.json"
  fi
done
rm -rf /w/scratch
echo "shard done: $ok ok, $bad failed"
