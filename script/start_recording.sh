#!/usr/bin/env bash
set -eo pipefail
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$TASK_ROOT/setup.bash"
exec python3 "$TASK_ROOT/script/record_demonstration.py" "$@"
