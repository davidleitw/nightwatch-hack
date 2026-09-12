#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf 'Usage: %s [--open|--close|--help]\n  (default) Build and recreate Guard Room on localhost:9999\n  --open   Start the existing image\n  --close  Stop Guard Room, preserving data\n' "$0"
}
if (( $# > 1 )); then
  usage >&2
  exit 2
fi
action="${1:---restart}"
case "$action" in
  --restart|--open|--close) ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

guardroom_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repo_dir="$(dirname -- "$guardroom_dir")"
project_name="${GUARDROOM_PROJECT_NAME:-nightwatch-guardroom}"
for dependency in docker curl; do
  command -v "$dependency" >/dev/null || { printf 'Missing dependency: %s\n' "$dependency" >&2; exit 1; }
done

compose() {
  docker compose --project-directory "$guardroom_dir" -p "$project_name" -f "$guardroom_dir/compose.yaml" "$@"
}
compose version >/dev/null
compose config --quiet

report_failure() {
  local exit_code=$?
  trap - ERR
  compose ps -a >&2 || true
  compose logs --tail=50 guardroom >&2 || true
  exit "$exit_code"
}
trap report_failure ERR

if [[ "$action" == "--close" ]]; then
  compose stop guardroom
  exit 0
fi

# The existing shop backend writes this directory; Guard Room only mounts it read-only.
# A custom MONITOR_LOG_DIR must already exist.
mkdir -p "$repo_dir/control/tmp"
if [[ "$action" == "--restart" ]]; then
  compose build guardroom
fi
# Validate the mounted topology before replacing a working container.
compose run --rm --no-deps --pull never --entrypoint python guardroom -c 'import os, sys; from pathlib import Path; sys.path.insert(0, "/app/control/server"); from graph_state import GraphConfig; GraphConfig.model_validate_json(Path(os.environ["GUARDROOM_CONFIG"]).read_text())'
if [[ "$action" == "--restart" ]]; then
  compose up -d --no-build --force-recreate --wait --wait-timeout 120 guardroom
else
  compose up -d --no-build --wait --wait-timeout 120 guardroom
fi
binding="$(compose port guardroom 9999)"
curl --fail --silent --show-error --connect-timeout 2 --max-time 5 "http://$binding/health/ready"
printf '\nGraph: http://%s/api/graph\nDocs: http://%s/docs\nProject: %s\n' "$binding" "$binding" "$project_name"
