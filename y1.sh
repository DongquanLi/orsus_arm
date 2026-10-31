#!/usr/bin/env bash
set -eo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT"
cmd=${1:-help}
shift || true
case "$cmd" in
  setup) exec bash script/setup_device.sh "$@" ;;
  usb-init) exec bash script/install_usb_autostart.sh "$@" ;;
  usb) exec python3 script/check_usb.py "$@" ;;
  check) exec bash script/check_setup.sh "$@" ;;
  control) exec bash script/start_driver.sh "$@" ;;
  teach) exec bash script/start_teach.sh "$@" ;;
  record) exec bash script/start_recording.sh "$@" ;;
  replay) exec bash script/start_replay.sh "$@" ;;
  camera) exec bash script/start_wrist_camera.sh "$@" ;;
  verify-arm) source ./setup.bash; exec python3 deploy/test_arm_feedback_disabled.py "$@" ;;
  help|--help|-h) cat <<'HELP'
Usage: bash y1.sh COMMAND [args]
  setup [--serial SERIAL]  新设备安装 USB 自启、CAN、依赖并编译 SDK
  usb-init                安装并启动 USB GPIO 开机初始化（指定载板）
  usb [--camera-test]      USB 枚举/拓扑/可选 250 帧相机测试，保存日志
  check                   部署自检，不启动机械臂
  verify-arm              30 秒禁用状态通信测试，不发送运动目标
  control                 正常控制模式（供外部遥操作发布器使用）
  teach                   独立零重力模式
  record [args]           回零 → 零重力 → 录制 → 自动裁剪
  replay [latest|FILE]     自动启动 control → 回放 → 停止自己的驱动
  camera                  25 Hz 腕部相机 ROS2 发布
HELP
  ;;
  *) echo "未知命令：$cmd；运行 bash y1.sh help"; exit 2 ;;
esac
