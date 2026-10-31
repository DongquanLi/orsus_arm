#!/usr/bin/env bash
set -eo pipefail
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$TASK_ROOT/setup.bash"
exec ros2 run y1_wrist_camera wrist_camera_node "$@"
