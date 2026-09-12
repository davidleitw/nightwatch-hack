#!/usr/bin/env bash
set -euo pipefail

# uv parses dotenv as data; never source a file containing credentials as shell code.
control_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
uv_args=(run --locked --offline --directory "$control_dir")
if [[ -f "$control_dir/.env" ]]; then
  uv_args+=(--env-file "$control_dir/.env")
elif [[ -f "$control_dir/../.env" ]]; then
  uv_args+=(--env-file "$control_dir/../.env")
fi

exec uv "${uv_args[@]}" nightwatch-agent \
  --report-schema "$control_dir/../contracts/schemas/agent-report.schema.json" \
  "$@"
