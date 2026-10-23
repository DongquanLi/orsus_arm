#!/usr/bin/env bash
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
exec bash "$TASK_ROOT/script/configure_udev.sh" "$@"
