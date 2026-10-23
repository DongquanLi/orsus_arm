#!/usr/bin/env bash
set -eo pipefail
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TASK_MODE=${1:-normal}
shift || true
case "$TASK_MODE" in
  normal) TASK_LAUNCH=single_arm_control.launch.py ;;
  teach) TASK_LAUNCH=one_master.launch.py ;;
  *) echo "Unknown mode: $TASK_MODE" >&2; exit 2 ;;
esac
if (( $# )); then echo "Usage: bash script/start_driver.sh OR bash script/start_teach.sh" >&2; exit 2; fi
source "$TASK_ROOT/setup.bash"
mkdir -p "$TASK_ROOT/logs"
exec 9>"$TASK_ROOT/logs/y1-driver.lock"
flock -n 9 || { echo "[中止] 已有正常控制或示教启动器在运行。先在旧终端按 Ctrl+C。"; exit 1; }
if pgrep -x y1_controller >/dev/null; then
  echo "[中止] 已有 Y1 驱动进程；正常控制与示教不能同时运行。"
  pgrep -ax y1_controller
  exit 1
fi
echo "[预检] 等待 y1_can0 就绪（最多 20 秒）..."
TASK_READY=0
for TASK_I in {1..20}; do
  if python3 - <<'PY'
import json,subprocess,sys
p=subprocess.run(['ip','-json','link','show','dev','y1_can0'],capture_output=True,text=True)
data=json.loads(p.stdout) if p.returncode == 0 else []
sys.exit(0 if data and data[0].get('link_type') == 'can' and 'UP' in data[0].get('flags',[]) else 1)
PY
  then TASK_READY=1; break; fi
  sleep 1
done
if (( ! TASK_READY )); then
  echo "[失败] CAN 未就绪。检查 USB；首次执行 sudo bash $TASK_ROOT/script/install_system.sh"
  echo "已安装时查看：systemctl status y1-can --no-pager"
  exit 1
fi
TASK_PREFIX=$(ros2 pkg prefix y1_controller)
if [[ "$TASK_PREFIX" != "$TASK_ROOT/y1_sdk-humble/install/y1_controller" ]]; then
  echo "[失败] ROS2 找到另一个 y1_controller：$TASK_PREFIX"; exit 1
fi
echo "[预检] CAN 已 UP。该状态不等于电机已响应。"
echo "[启动] $TASK_MODE 模式将使能机械臂；请确认电源、接线和机械臂周围空间。"
echo "[提示] Ctrl+C 停止驱动；停止前托住机械臂。"
TASK_LOG="$TASK_ROOT/logs/driver-$TASK_MODE-$(date +%Y%m%d-%H%M%S).log"
set +e
ros2 launch y1_controller "$TASK_LAUNCH" 2>&1 | tee "$TASK_LOG"
TASK_RC=${PIPESTATUS[0]}
set -e
if grep -q 'Failed connect to J' "$TASK_LOG"; then
  echo "[诊断] 电机连接失败。检查电源、CAN_H/CAN_L、终端电阻和总线参数；接口 UP 不能证明这些正常。"
elif grep -q 'Failed to open can device' "$TASK_LOG"; then
  echo "[诊断] 无法打开 CAN。执行 sudo systemctl restart y1-can 后重试。"
elif grep -q 'success start' "$TASK_LOG"; then
  echo "[记录] 本次驱动曾完成初始化。"
fi
echo "[日志] $TASK_LOG"
exit "$TASK_RC"
