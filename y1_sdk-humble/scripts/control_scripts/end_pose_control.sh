#!/usr/bin/env bash
set -eo pipefail
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
source "$TASK_ROOT/setup.bash"
if ! pgrep -x y1_controller >/dev/null; then
  echo "请先启动正常控制：bash ~/robot_y1/script/start_driver.sh"; exit 1
fi
echo "将发送机械臂运动目标；请确认正常控制驱动正在运行且运动空间无障碍。"
timeout 8s ros2 topic pub --once --qos-durability volatile /master_arm_right/end_pose_control y1_msg/msg/ArmEndPoseControl '{end_pose: [0.0535, -0.0476, 0.3963, 0.1990, -0.4414, -1.0352], joint_velocity: 1, gripper_stroke: 50, gripper_velocity: 1}'
