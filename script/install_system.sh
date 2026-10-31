#!/usr/bin/env bash
# Install the tested module and CAN service. Never start a robot controller.
set -Eeuo pipefail
if (( EUID != 0 )); then echo "请执行 sudo bash $0" >&2; exit 77; fi
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TASK_DEPLOY="$TASK_ROOT/deploy/system"
TASK_MODULE="$TASK_ROOT/slcan-build/artifacts/slcan.ko"
TASK_HASH=c8390e77cdb9d19b05a2c04eade7fa91d9157c3bf5c7db54e44789169aeb909d
TASK_KERNEL=6.1.141
mkdir -p "$TASK_ROOT/deploy/backups"
TASK_BACKUP=$(mktemp -d "$TASK_ROOT/deploy/backups/system-$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)-XXXXXX")
chmod 0755 "$TASK_BACKUP"
exec > >(tee "$TASK_BACKUP/install.log") 2>&1
trap 'echo "[失败] 安装未完成，原配置备份及日志：$TASK_BACKUP"' ERR
echo "[1/6] 检查当前内核、设备和控制进程"
[[ "$(uname -r)" == "$TASK_KERNEL" && "$(uname -m)" == aarch64 ]] || {
  echo "内核已变化；请重新编译模块。"; exit 1;
}
[[ "$(sha256sum "$TASK_MODULE" | cut -d ' ' -f 1)" == "$TASK_HASH" ]] || {
  echo "模块校验失败。"; exit 1;
}
if pgrep -x y1_controller >/dev/null; then
  echo "Y1 控制器正在运行，请先在原终端 Ctrl+C，再安装。"; exit 1
fi
for TASK_PID in $(pgrep -x slcand || true); do
  if ! grep -q '/y1-can.service' "/proc/$TASK_PID/cgroup"; then
    echo "检测到非本服务管理的 slcand（PID $TASK_PID），请先停止该实例。"; exit 1
  fi
done
# Resolve the serial before changing installed configuration.
python3 "$TASK_ROOT/script/can_device.py" --output "$TASK_BACKUP/new.rules" "$@"
for TASK_CMD in modprobe depmod slcand ip udevadm fuser systemctl; do command -v "$TASK_CMD" >/dev/null; done

echo "[2/6] 备份将要修改的系统文件"
TASK_TARGETS=(
  "/lib/modules/$TASK_KERNEL/extra/slcan.ko"
  /etc/udev/rules.d/imeta_y1_can.rules
  /etc/y1-can/config.json
  /usr/local/libexec/y1-can-watchdog.py
  /etc/systemd/system/y1-can.service
)
for TASK_FILE in "${TASK_TARGETS[@]}"; do
  if [[ -e "$TASK_FILE" || -L "$TASK_FILE" ]]; then
    mkdir -p "$TASK_BACKUP$(dirname -- "$TASK_FILE")"
    cp -a -- "$TASK_FILE" "$TASK_BACKUP$TASK_FILE"
    printf '%s\n' "$TASK_FILE" >> "$TASK_BACKUP/existed.txt"
  else
    printf '%s\n' "$TASK_FILE" >> "$TASK_BACKUP/absent.txt"
  fi
done
systemctl is-enabled y1-can.service > "$TASK_BACKUP/service-enabled-before.txt" 2>&1 || true
systemctl is-active y1-can.service > "$TASK_BACKUP/service-active-before.txt" 2>&1 || true
systemctl stop y1-can.service 2>/dev/null || true

echo "[3/6] 补齐录制回放依赖（按需安装 python3-h5py）"
if ! /usr/bin/python3 -c 'import h5py' 2>/dev/null; then
  apt-get install -y --no-install-recommends python3-h5py
fi

echo "[4/6] 安装 slcan 模块和 CAN 服务文件"
install -D -m 0644 "$TASK_MODULE" "/lib/modules/$TASK_KERNEL/extra/slcan.ko"
depmod -a "$TASK_KERNEL"
modprobe -n -v slcan
modprobe slcan
install -D -m 0644 "$TASK_BACKUP/new.rules" /etc/udev/rules.d/imeta_y1_can.rules
install -D -m 0644 "$TASK_DEPLOY/y1-can.json" /etc/y1-can/config.json
install -D -m 0755 "$TASK_DEPLOY/y1-can-watchdog.py" /usr/local/libexec/y1-can-watchdog.py
install -D -m 0644 "$TASK_DEPLOY/y1-can.service" /etc/systemd/system/y1-can.service
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=tty
udevadm settle --timeout=5
systemd-analyze verify /etc/systemd/system/y1-can.service

echo "[5/6] 启用 CAN 开机自启；不会启动机械臂控制器"
systemctl daemon-reload
systemctl enable y1-can.service
systemctl restart y1-can.service
TASK_READY=0
for TASK_I in {1..20}; do
  if python3 - <<'PY'
import json,sys
from pathlib import Path
p=Path('/run/y1-can/status.json')
try: data=json.loads(p.read_text())
except (OSError,ValueError):sys.exit(1)
sys.exit(0 if data.get('state') == 'ready' else 1)
PY
  then TASK_READY=1; break; fi
  sleep 1
done
if (( ! TASK_READY )); then
  systemctl status y1-can.service --no-pager -l || true
  journalctl -u y1-can.service -n 40 --no-pager || true
  echo "CAN 服务未就绪，查看上方日志。"; exit 1
fi

echo "[6/6] 写入安装记录"
python3 - "$TASK_ROOT" "$TASK_BACKUP" <<'PY'
from pathlib import Path
import datetime,json,subprocess,sys
root=Path(sys.argv[1])
record={
 'installed_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
 'kernel_release':'6.1.141','module_installed':True,'can_service_enabled':True,
 'can_service_active':True,'backup':sys.argv[2],
 'robot_controller_started':False,'motion_commands_sent':False,
 'can_receive_verified':False,
 'runtime':json.loads(Path('/run/y1-can/status.json').read_text()),
}
(root/'deploy/system-install.json').write_text(json.dumps(record,indent=2)+'\n')
PY
systemctl is-enabled y1-can.service
systemctl is-active y1-can.service
ip -br link show dev y1_can0
echo "[成功] 系统适配已安装。尚未启动机械臂，也尚未验证电机反馈。"
echo "正常控制：bash $TASK_ROOT/script/start_driver.sh"
echo "示教模式：bash $TASK_ROOT/script/start_teach.sh"
echo "备份与安装日志：$TASK_BACKUP"
