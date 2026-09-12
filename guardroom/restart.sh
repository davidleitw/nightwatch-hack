#!/usr/bin/env bash
set -Eeuo pipefail

guardroom_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
server_dir="$(dirname -- "$guardroom_dir")/control/server"
run_dir="$guardroom_dir/.run"
port="${PORT:-8001}"
export GUARDROOM_CONFIG="${GUARDROOM_CONFIG:-$guardroom_dir/shop-web.config.json}"
if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  printf 'Invalid PORT: %s\n' "$port" >&2
  exit 1
fi
for dependency in uv curl lsof; do
  command -v "$dependency" >/dev/null || { printf 'Missing dependency: %s\n' "$dependency" >&2; exit 1; }
done
mkdir -p "$run_dir"
pid_file="$run_dir/server-$port.pid"
log_file="$run_dir/server-$port.log"
lock_dir="$run_dir/restart-$port.lock"
mkdir "$lock_dir" 2>/dev/null || { printf 'Restart already running: %s\n' "$lock_dir" >&2; exit 1; }
trap 'rmdir "$lock_dir"' EXIT

# Install before stopping a working server.
uv sync --project "$server_dir" --locked
# Reject a bad topology before stopping the working server.
"$server_dir/.venv/bin/python" -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from graph_state import GraphConfig; GraphConfig.model_validate_json(Path(sys.argv[2]).read_text())' "$server_dir" "$GUARDROOM_CONFIG"
if [[ -f "$pid_file" ]]; then
  old_pid="$(<"$pid_file")"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    process_command="$(ps -p "$old_pid" -o command=)"
    if [[ "$process_command" != *"-m uvicorn main:app --app-dir $server_dir --host 127.0.0.1 --port $port"* ]]; then
      printf 'PID %s no longer belongs to this server; refusing to stop it.\n' "$old_pid" >&2
      exit 1
    fi
    kill "$old_pid"
    for ((attempt=0; attempt<50; attempt++)); do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 "$old_pid" 2>/dev/null; then
      printf 'Server did not stop within 10 seconds.\n' >&2
      exit 1
    fi
  fi
fi
if lsof -nP -iTCP:"$port" -sTCP:LISTEN; then
  printf 'Port %s is occupied; choose another PORT.\n' "$port" >&2
  exit 1
fi
nohup "$server_dir/.venv/bin/python" -m uvicorn main:app --app-dir "$server_dir" --host 127.0.0.1 --port "$port" >"$log_file" 2>&1 < /dev/null &
server_pid=$!
printf '%s\n' "$server_pid" >"$pid_file"
for ((attempt=0; attempt<50; attempt++)); do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    printf 'Server failed to start. See %s\n' "$log_file" >&2
    exit 1
  fi
  if curl --fail --silent --max-time 1 "http://127.0.0.1:$port/api/graph" >/dev/null; then
    printf 'Server ready (PID %s)\nGraph: http://127.0.0.1:%s/api/graph\nConfig: %s\nLog: %s\n' "$server_pid" "$port" "$GUARDROOM_CONFIG" "$log_file"
    exit 0
  fi
  sleep 0.2
done
kill "$server_pid" 2>/dev/null || true
printf 'Startup timed out. See %s\n' "$log_file" >&2
exit 1
