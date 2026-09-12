#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf 'Usage: %s [--close|--open|--help]\n  (default) Rebuild and restart services\n  --close   Stop services, preserving containers and data\n  --open    Start services without rebuilding\n' "$0"
}
if (( $# > 1 )); then
  usage >&2
  exit 2
fi
action="${1:---restart}"
case "$action" in
  --close|--open|--restart) ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

shop_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PROJECT_NAME="${PROJECT_NAME:-nightwatch-shop-web}"

compose() {
  docker compose -p "$PROJECT_NAME" -f "$shop_dir/compose.yaml" "$@"
}

# Preserve existing published ports unless explicitly overridden.
existing_port() {
  local container_id
  container_id="$(compose ps -aq "$1")"
  if [[ -n "$container_id" ]]; then
    docker inspect --format "{{with index .HostConfig.PortBindings \"$2/tcp\"}}{{(index . 0).HostPort}}{{end}}" "$container_id"
  fi
}
existing_backend="$(existing_port backend 8000)"
existing_frontend="$(existing_port frontend 80)"
export BACKEND_PORT="${BACKEND_PORT:-${existing_backend##*:}}"
export FRONTEND_PORT="${FRONTEND_PORT:-${existing_frontend##*:}}"
export BACKEND_PORT="${BACKEND_PORT:-8000}"
export FRONTEND_PORT="${FRONTEND_PORT:-8080}"

report_failure() {
  local exit_code=$?
  trap - ERR
  compose ps >&2 || true
  compose logs --tail=50 >&2 || true
  exit "$exit_code"
}
trap report_failure ERR

case "$action" in
  --close)
    compose stop
    compose ps -a
    exit 0
    ;;
  --open)
    compose up -d --no-build --wait --wait-timeout 120
    ;;
  --restart)
    compose up -d --build --force-recreate --wait --wait-timeout 120
    ;;
esac
curl --fail --silent --show-error --connect-timeout 2 --max-time 10 \
  "http://127.0.0.1:$BACKEND_PORT/api/health"
curl --fail --silent --show-error --connect-timeout 2 --max-time 10 \
  "http://127.0.0.1:$FRONTEND_PORT/api/health"
printf '\nFrontend: http://127.0.0.1:%s/\nBackend: http://127.0.0.1:%s/\n' \
  "$FRONTEND_PORT" "$BACKEND_PORT"
compose ps
