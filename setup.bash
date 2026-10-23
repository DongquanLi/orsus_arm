# Source this file: source ~/robot_y1/setup.bash
_Y1_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source /opt/ros/humble/setup.bash
source "$_Y1_ROOT/y1_sdk-humble/install/setup.bash"
unset _Y1_ROOT
