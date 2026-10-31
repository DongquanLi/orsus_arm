#!/bin/bash
set -euo pipefail
exec 9>/run/board-usb-gpio-init.lock
flock -x 9
# Run the board USB power/reset sequence once per boot.
if [[ -f /run/board-usb-gpio-init.done ]]; then
  echo 'USB GPIO initialization already completed this boot.'
  exit 0
fi
trap 'echo "USB GPIO init failed at line $LINENO; systemd will retry." >&2' ERR
for gpio in 142 118; do
  path=/sys/class/gpio/gpio${gpio}
  ready=0
  for attempt in {1..60}; do
    if [[ -e "$path/direction" ]]; then ready=1; break; fi
    if [[ -w /sys/class/gpio/export && ! -d "$path" ]]; then
      { echo "$gpio" > /sys/class/gpio/export; } 2>/dev/null || true
    fi
    sleep 0.25
  done
  if (( ! ready )); then
    echo "GPIO $gpio not ready after 15 seconds" >&2
    exit 1
  fi
done

echo high > /sys/class/gpio/gpio142/direction
echo low > /sys/class/gpio/gpio118/direction
sleep 1
echo 1 > /sys/class/gpio/gpio118/value
[[ $(cat /sys/class/gpio/gpio142/value) == 1 ]]
[[ $(cat /sys/class/gpio/gpio118/value) == 1 ]]
touch /run/board-usb-gpio-init.done
echo 'USB GPIO initialization complete: GPIO142=1, GPIO118=1.'
