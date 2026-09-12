#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf 'Usage: %s [--open|--close|--help]\n  (default) Rebuild and restart Shop, Guard Room and Console\n  --open   Start Docker services using existing images; rebuild and restart Console\n  --close  Stop all three components, preserving data\n' "$0"
}
if (( $# > 1 )); then usage >&2; exit 2; fi
action="${1:---restart}"
case "$action" in
  --restart|--open|--close) ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$repo_dir"
for dependency in docker python3 curl lsof; do
  command -v "$dependency" >/dev/null || { printf 'Missing dependency: %s\n' "$dependency" >&2; exit 1; }
done
export PROJECT_NAME="${PROJECT_NAME:-nightwatch-shop-web}"
export GUARDROOM_PROJECT_NAME="${GUARDROOM_PROJECT_NAME:-nightwatch-guardroom}"
console_port="${CONSOLE_PORT:-4173}"
if [[ ! "$console_port" =~ ^[0-9]+$ ]] || (( 10#$console_port < 1 || 10#$console_port > 65535 )); then
  printf 'Invalid CONSOLE_PORT: %s\n' "$console_port" >&2
  exit 2
fi
console_port="$((10#$console_port))"

shop_compose() {
  docker compose -p "$PROJECT_NAME" -f "$repo_dir/shop-web/compose.yaml" "$@"
}
guardroom_compose() {
  docker compose --project-directory "$repo_dir/guardroom" -p "$GUARDROOM_PROJECT_NAME" -f "$repo_dir/guardroom/compose.yaml" "$@"
}
docker compose version >/dev/null
shop_compose config --quiet
guardroom_compose config --quiet

run_dir="$repo_dir/.run"
mkdir -p "$run_dir"
lock_dir="$run_dir/restart.lock"
mkdir "$lock_dir" 2>/dev/null || { printf 'Another restart is running (lock: %s).\n' "$lock_dir" >&2; exit 1; }
trap 'rmdir "$lock_dir"' EXIT
trap 'printf "Restart failed; inspect Docker logs or %s/console.log.\n" "$run_dir" >&2' ERR
pid_file="$run_dir/console.pid"
console_log="$run_dir/console.log"

# Accept only this checkout's Console, including an earlier manual launch.
# A listener belonging to another app or checkout must never be terminated.
is_our_console() {
  local process_command process_cwd
  process_command="$(ps -p "$1" -o command=)" || return 1
  if [[ " $process_command " == *" $repo_dir/console/serve.py "* ]]; then return 0; fi
  process_cwd="$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  [[ "$process_cwd" == "$repo_dir" && " $process_command " == *" console/serve.py "* ]] ||
    [[ "$process_cwd" == "$repo_dir/console" && " $process_command " == *" serve.py "* ]]
}
console_pids="$(
  if [[ -f "$pid_file" ]]; then
    saved_pid="$(<"$pid_file")"
    if [[ "$saved_pid" =~ ^[0-9]+$ ]] && (( saved_pid > 1 )) && kill -0 "$saved_pid" 2>/dev/null; then
      printf '%s\n' "$saved_pid"
    fi
  fi
  lsof -nP -t -iTCP:"$console_port" -sTCP:LISTEN 2>/dev/null || true
)"
console_pids="$(printf '%s\n' "$console_pids" | sort -u)"
for process_id in $console_pids; do
  if ! is_our_console "$process_id"; then
    printf 'PID %s is not this checkout\047s Console; refusing to stop it. Choose another CONSOLE_PORT.\n' "$process_id" >&2
    exit 1
  fi
done

stop_console() {
  local process_id attempt
  for process_id in $console_pids; do
    kill -0 "$process_id" 2>/dev/null || continue
    is_our_console "$process_id" || { printf 'Console PID ownership changed; refusing to stop it.\n' >&2; return 1; }
    kill "$process_id"
    for ((attempt=0; attempt<50; attempt++)); do
      kill -0 "$process_id" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$process_id" 2>/dev/null; then
      printf 'Console PID %s did not stop; no force kill was sent.\n' "$process_id" >&2
      return 1
    fi
  done
  if [[ -f "$pid_file" ]]; then unlink "$pid_file"; fi
}

if [[ "$action" == "--close" ]]; then
  stop_console
  "$repo_dir/guardroom/restart.sh" --close
  "$repo_dir/shop-web/restart.sh" --close
  printf 'All services stopped; Docker volumes and monitor logs preserved.\n'
  exit 0
fi

# Keep an existing Guard Room host port unless PORT is explicitly supplied.
# Shop's own restart script preserves its existing host ports in the same way.
guardroom_id="$(guardroom_compose ps -aq guardroom)"
if [[ -z "${PORT:-}" && -n "$guardroom_id" ]]; then
  existing_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "9999/tcp"}}{{(index . 0).HostPort}}{{end}}' "$guardroom_id")"
  if [[ -n "$existing_port" ]]; then export PORT="$existing_port"; fi
fi

python3 -B "$repo_dir/console/build.py"
"$repo_dir/shop-web/restart.sh" "$action"
"$repo_dir/guardroom/restart.sh" "$action"
shop_frontend="$(shop_compose port frontend 80)"
shop_backend="$(shop_compose port backend 8000)"
guardroom="$(guardroom_compose port guardroom 9999)"

stop_console
# Detach from the launcher's process group so Console survives terminal/tool exit.
# Shell opens the log; Python only launches the child and returns its PID.
console_pid="$(python3 -B - "$repo_dir/console/serve.py" "$console_port" "http://$guardroom" 3>>"$console_log" <<'PY'
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
printf '%s\n' "$console_pid" >"$pid_file"
console_ready=false
for ((attempt=0; attempt<50; attempt++)); do
  kill -0 "$console_pid" 2>/dev/null || break
  if curl --fail --silent --max-time 1 "http://127.0.0.1:$console_port/__console/config" >/dev/null; then
    console_ready=true
    break
  fi
  sleep 0.2
done
if [[ "$console_ready" != true ]]; then
  if kill -0 "$console_pid" 2>/dev/null && is_our_console "$console_pid"; then kill "$console_pid"; fi
  printf 'Console startup failed; inspect %s.\n' "$console_log" >&2
  exit 1
fi
curl --fail --silent --show-error --max-time 8 "http://127.0.0.1:$console_port/api/investigations/state" >/dev/null

printf '\nAll services ready:\n'
printf 'Shop:                  http://%s/\n' "$shop_frontend"
printf 'Shop fault controls:   http://%s/#/events\n' "$shop_frontend"
printf 'Console live graph:    http://127.0.0.1:%s/?source=live#topology\n' "$console_port"
printf 'Investigation history: http://127.0.0.1:%s/?source=live#investigations\n' "$console_port"
printf 'Shop API docs:         http://%s/docs\n' "$shop_backend"
printf 'Guard Room API docs:   http://%s/docs\n' "$guardroom"
printf 'Graph JSON:            http://%s/api/graph\n' "$guardroom"
printf 'Console log: %s (PID %s)\n' "$console_log" "$console_pid"
guardroom_compose exec -T guardroom python -c 'import os; print("AI key: configured (not validated)" if os.getenv("NIGHTWATCH_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") else "AI key: missing; monitoring works, but AI investigation needs a key in guardroom/.env")'
