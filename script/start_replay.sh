#!/usr/bin/env bash
set -eo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/setup.bash"
exec python3 "$ROOT/script/replay_session.py" "$@"
