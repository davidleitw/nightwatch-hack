#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# )); then
  printf 'Usage: bash console/restart.sh\nSet CONSOLE_PORT or NIGHTWATCH_CONTROL_URL to override the connection.\n' >&2
  exit 2
fi
console_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repo_dir="$(dirname -- "$console_dir")"
port="${CONSOLE_PORT:-4173}"
if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
  printf 'Invalid CONSOLE_PORT: %s\n' "$port" >&2
  exit 2
fi
port="$((10#$port))"
for dependency in python3 curl lsof ps; do
  command -v "$dependency" >/dev/null || { printf 'Missing dependency: %s\n' "$dependency" >&2; exit 1; }
done
run_dir="$console_dir/.codex/run"
mkdir -p "$run_dir"
lock_dir="$run_dir/restart-$port.lock"
mkdir "$lock_dir" 2>/dev/null || { printf 'Restart already running: %s\n' "$lock_dir" >&2; exit 1; }
trap 'rmdir "$lock_dir"' EXIT
pid_file="$run_dir/console-$port.pid"
log_file="$run_dir/console-$port.log"

is_our_console() {
  local process_command process_cwd
  process_command="$(ps -p "$1" -o command=)" || return 1
  if [[ " $process_command " == *" $console_dir/serve.py "* ]]; then return 0; fi
  process_cwd="$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  [[ "$process_cwd" == "$repo_dir" && " $process_command " == *" console/serve.py "* ]] ||
    [[ "$process_cwd" == "$console_dir" && " $process_command " == *" serve.py "* ]]
}
old_pids="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
for process_id in $old_pids; do
  if ! is_our_console "$process_id"; then
    printf 'PID %s is not this checkout\047s Console; refusing to stop it.\n' "$process_id" >&2
    exit 1
  fi
done

upstream="${NIGHTWATCH_CONTROL_URL:-}"
if [[ -z "$upstream" && -n "$old_pids" ]]; then
  config="$(curl --noproxy '*' --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/__console/config")"
  upstream="$(python3 -B -c 'import json, sys; value = json.load(sys.stdin); sys.exit("Existing Console is in mock mode; set NIGHTWATCH_CONTROL_URL explicitly.") if value.get("mode") != "live" or not value.get("upstream") else print(value["upstream"])' <<< "$config")"
fi
upstream="${upstream:-http://127.0.0.1:9999}"
# Validate before stopping the existing service, using serve.py's URL rules.
python3 -B - "$upstream" <<'PY'
import sys
from urllib.parse import urlsplit
value = sys.argv[1]
try:
    target = urlsplit(value)
    if (target.scheme not in ('http', 'https') or not target.hostname
            or target.username is not None or target.password is not None
            or target.query or target.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)):
        raise ValueError('use an HTTP(S) URL without credentials, query or fragment')
    target.port
except ValueError as exc:
    sys.exit(f'Invalid NIGHTWATCH_CONTROL_URL: {exc}')
PY
python3 -B "$console_dir/build.py"

for process_id in $old_pids; do
  kill -0 "$process_id" 2>/dev/null || continue
  is_our_console "$process_id" || { printf 'Console PID ownership changed; refusing to stop it.\n' >&2; exit 1; }
  kill "$process_id"
  for ((attempt=0; attempt<50; attempt++)); do
    kill -0 "$process_id" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$process_id" 2>/dev/null; then
    printf 'Console PID %s did not stop within 5 seconds.\n' "$process_id" >&2
    exit 1
  fi
done
if lsof -nP -iTCP:"$port" -sTCP:LISTEN; then
  printf 'Port %s is occupied; refusing to start.\n' "$port" >&2
  exit 1
fi
server_pid="$(python3 -B - "$console_dir/serve.py" "$port" "$upstream" 3>>"$log_file" <<'PY'
import subprocess
import sys
process = subprocess.Popen(
    [sys.executable, '-B', sys.argv[1], '--port', sys.argv[2], '--control-url', sys.argv[3]],
    stdin=subprocess.DEVNULL, stdout=3, stderr=subprocess.STDOUT,
    start_new_session=True,
)
print(process.pid)
PY
)"
printf '%s\n' "$server_pid" >"$pid_file"
ready=false
for ((attempt=0; attempt<50; attempt++)); do
  kill -0 "$server_pid" 2>/dev/null || break
  if curl --noproxy '*' --fail --silent --max-time 1 "http://127.0.0.1:$port/__console/config" >/dev/null; then
    ready=true
    break
  fi
  sleep 0.1
done
if [[ "$ready" != true ]]; then
  if kill -0 "$server_pid" 2>/dev/null && is_our_console "$server_pid"; then kill "$server_pid"; fi
  printf 'Console failed to start; inspect %s.\n' "$log_file" >&2
  exit 1
fi
printf 'Console ready (PID %s): http://127.0.0.1:%s/?source=live#topology\nUpstream: %s\nLog: %s\n' "$server_pid" "$port" "$upstream" "$log_file"
curl --noproxy '*' --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/" >/dev/null
if ! curl --noproxy '*' --fail --silent --show-error --max-time 8 "http://127.0.0.1:$port/api/investigations/state" >/dev/null; then
  printf 'Console is running, but the upstream API check failed. Inspect %s.\n' "$log_file" >&2
  exit 1
fi
printf 'Homepage and investigation API: OK\n'
