#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then exec sudo bash "$0" "$@"; fi
if pgrep -x y1_controller >/dev/null; then echo '请先停止机械臂驱动。'; exit 1; fi
mkdir -p "$ROOT/deploy/backups"
backup=$(mktemp -d "$ROOT/deploy/backups/udev-XXXXXXXX")
python3 "$ROOT/script/can_device.py" --output "$backup/new.rules" "$@"
if [[ -f /etc/udev/rules.d/imeta_y1_can.rules ]]; then
  cp -a /etc/udev/rules.d/imeta_y1_can.rules "$backup/previous.rules"
fi
install -m 0644 "$backup/new.rules" /etc/udev/rules.d/imeta_y1_can.rules
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=tty
udevadm settle --timeout=10
test -e /dev/imeta_y1_can0
ls -l /dev/imeta_y1_can0
