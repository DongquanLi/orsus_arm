#!/usr/bin/env bash
# Only for the validated carrier: GPIO142 power, GPIO118 reset.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then exec sudo bash "$0" "$@"; fi
[[ $(uname -m) == aarch64 && $(uname -r) == 6.1.141 ]] || {
  echo '仅适配已验证的 RK3588 载板 / aarch64 / 6.1.141。'; exit 1;
}
if pgrep -x y1_controller >/dev/null; then echo '请先停止机械臂驱动。'; exit 1; fi
backup=$(mktemp -d /var/backups/usb-gpio-autostart-XXXXXXXX)
for item in /usr/local/sbin/board-usb-gpio-init.sh /etc/systemd/system/board-usb-gpio-init.service /etc/systemd/system/y1-can.service.d/10-usb-gpio.conf; do
  if [[ -f "$item" ]]; then cp --parents -a "$item" "$backup/"; fi
done
install -m 0755 "$ROOT/deploy/system/board-usb-gpio-init.sh" /usr/local/sbin/board-usb-gpio-init.sh
install -m 0644 "$ROOT/deploy/system/board-usb-gpio-init.service" /etc/systemd/system/board-usb-gpio-init.service
mkdir -p /etc/systemd/system/y1-can.service.d
printf '[Unit]\nRequires=board-usb-gpio-init.service\nAfter=board-usb-gpio-init.service\n' > /etc/systemd/system/y1-can.service.d/10-usb-gpio.conf
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/board-usb-gpio-init.service
systemctl enable --now board-usb-gpio-init.service
echo "已设置 USB 开机初始化；本次开机重复执行不会再次复位。备份：$backup"
