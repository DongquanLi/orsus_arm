#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
[[ ${1:-} == --help ]] && { echo 'Usage: bash script/setup_device.sh [--serial CANABLE_SERIAL]'; exit 0; }
if (( EUID == 0 )); then echo '以普通用户运行本脚本，不要 sudo 整个脚本。'; exit 1; fi
[[ $# == 0 || ( $# == 2 && $1 == --serial ) ]] || { echo '参数仅支持 --serial SERIAL'; exit 2; }
[[ $(uname -m) == aarch64 && $(uname -r) == 6.1.141 && -f /opt/ros/humble/setup.bash ]] || {
  echo '需要已验证载板、aarch64 内核 6.1.141 和 ROS2 Humble；其他平台需先适配。'; exit 1;
}
if pgrep -x y1_controller >/dev/null; then echo '请先停止机械臂驱动。'; exit 1; fi
sudo -v
sudo apt-get update
sudo apt-get install -y --no-install-recommends can-utils iproute2 usbutils v4l-utils psmisc \
  build-essential cmake python3-colcon-common-extensions python3-h5py python3-numpy \
  python3-yaml python3-opencv libgoogle-glog-dev libnlopt-cxx-dev liborocos-kdl-dev \
  ros-humble-kdl-parser ros-humble-std-srvs ros-humble-cv-bridge
sudo usermod -aG video,dialout "$(id -un)"
sudo bash script/install_usb_autostart.sh
sleep 2
sudo udevadm settle --timeout=10
sudo bash script/install_system.sh "$@"
source /opt/ros/humble/setup.bash
colcon --log-base y1_sdk-humble/log build --base-paths y1_sdk-humble/src \
  --build-base y1_sdk-humble/build --install-base y1_sdk-humble/install
bash script/check_setup.sh
echo '部署完成。首次加入 video/dialout 组需重新登录。bash y1.sh usb --camera-test 检查 USB；bash y1.sh verify-arm 验证禁用状态通信。'
