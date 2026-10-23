#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then exec sudo bash "$0"; fi
python3 - <<'PY'
import glob,subprocess
matches=[]
for dev in glob.glob('/dev/ttyACM*'):
 p=subprocess.check_output(['udevadm','info','--query=property','--name',dev],text=True)
 props=dict(line.split('=',1) for line in p.splitlines() if '=' in line)
 if (props.get('ID_VENDOR_ID'),props.get('ID_MODEL_ID'),props.get('ID_SERIAL_SHORT')) == (
   'ad50','60c4','003B00394B57531820383832'):
  matches.append(dev)
if len(matches) != 1: raise SystemExit('没有唯一匹配的 CANable；不覆盖规则。')
print('已找到完整序列号匹配的 CANable：',matches[0])
PY
if [[ -f /etc/udev/rules.d/imeta_y1_can.rules ]]; then
  mkdir -p "$TASK_ROOT/deploy/backups"
  cp -a /etc/udev/rules.d/imeta_y1_can.rules "$TASK_ROOT/deploy/backups/imeta_y1_can.rules-$(date +%Y%m%d-%H%M%S)"
fi
install -m 0644 "$TASK_ROOT/deploy/system/imeta_y1_can.rules" /etc/udev/rules.d/imeta_y1_can.rules
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=tty
udevadm settle --timeout=5
test -e /dev/imeta_y1_can0
ls -l /dev/imeta_y1_can0
