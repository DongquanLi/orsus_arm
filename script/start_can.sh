#!/usr/bin/env bash
set -e
if ! systemctl cat y1-can.service >/dev/null 2>&1; then
  echo "请先执行：sudo bash ~/robot_y1/script/install_system.sh"; exit 1
fi
sudo systemctl start y1-can.service
for TASK_I in {1..15}; do
  if ip -br link show dev y1_can0 2>/dev/null | grep -q '<[^>]*UP'; then
    ip -br link show dev y1_can0; exit 0
  fi
  sleep 1
done
systemctl status y1-can.service --no-pager -l
exit 1
