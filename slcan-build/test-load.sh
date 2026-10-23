#!/usr/bin/env bash
# Temporary module/SocketCAN receive test. No motion commands, no installation.
set -Eeuo pipefail

if (( EUID != 0 )); then
  echo "Run this script with sudo in a local terminal. No password is read by the script." >&2
  exit 77
fi

TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TASK_MODULE="$TASK_ROOT/artifacts/slcan.ko"
TASK_DEV=/dev/imeta_y1_can0
TASK_IFACE=y1_can0
TASK_HASH=c8390e77cdb9d19b05a2c04eade7fa91d9157c3bf5c7db54e44789169aeb909d
TASK_RUN=$(mktemp -d "$TASK_ROOT/logs/runtime-$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)-XXXXXX")
chmod 0755 "$TASK_RUN"
exec > >(tee "$TASK_RUN/test.log") 2>&1
TASK_LOADED=0
TASK_PID=
TASK_PHASE=preflight
TASK_RX=not-tested
TASK_CLEANUP=not-started
dmesg --raw > "$TASK_RUN/kernel-before.log"

kernel_delta() {
  dmesg --raw > "$TASK_RUN/kernel-after.log"
  python3 - "$TASK_RUN" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
before = (root / 'kernel-before.log').read_text().splitlines()
after = (root / 'kernel-after.log').read_text().splitlines()
start = 0
if before:
    for i in range(len(after) - 1, -1, -1):
        if after[i] == before[-1]:
            start = i + 1
            break
delta = after[start:]
(root / 'kernel-new.log').write_text('\n'.join(delta) + ('\n' if delta else ''))
PY
}

cleanup() {
  local TASK_RC=$?
  trap - EXIT INT TERM
  set +e
  echo "[cleanup] Removing only resources created by this test."
  TASK_CLEANUP=complete
  if [[ -n "$TASK_PID" ]]; then
    if kill -0 "$TASK_PID" 2>/dev/null; then
      # No other slcand process is killed.
      kill -TERM "$TASK_PID" 2>/dev/null
      for TASK_I in {1..30}; do
        kill -0 "$TASK_PID" 2>/dev/null || break
        sleep 0.1
      done
      if kill -0 "$TASK_PID" 2>/dev/null; then
        echo "[cleanup warning] Test slcand is still running; no forced kill/unload."
        TASK_CLEANUP=incomplete
      else
        wait "$TASK_PID" 2>/dev/null
      fi
    else
      wait "$TASK_PID" 2>/dev/null
    fi
  fi
  if (( TASK_LOADED )); then
    if [[ "$TASK_CLEANUP" == complete ]]; then
      rmmod slcan
      if (( $? != 0 )); then
        echo "[cleanup warning] slcan could not be unloaded; no forced unload attempted."
        TASK_CLEANUP=incomplete
      fi
    fi
  fi
  kernel_delta
  if grep -En 'BUG:|Oops:|WARNING:|Call trace:|Unknown symbol|disagrees about version|invalid module|version magic|Unable to handle kernel' "$TASK_RUN/kernel-new.log"; then
    echo "[FAIL] New kernel warnings/errors were detected."
    (( TASK_RC != 0 )) || TASK_RC=1
  fi
  [[ "$TASK_CLEANUP" == complete ]] || { (( TASK_RC != 0 )) || TASK_RC=1; }
  python3 - "$TASK_RUN/status.json" "$TASK_PHASE" "$TASK_RC" "$TASK_RX" "$TASK_CLEANUP" <<'PY'
from pathlib import Path
import json, sys
Path(sys.argv[1]).write_text(json.dumps({
    'last_phase': sys.argv[2], 'exit_code': int(sys.argv[3]),
    'receive_result': sys.argv[4], 'cleanup': sys.argv[5],
    'installed': False, 'autostart_configured': False,
    'motion_commands_sent': False,
}, indent=2) + '\n')
PY
  echo "[result] phase=$TASK_PHASE receive=$TASK_RX cleanup=$TASK_CLEANUP exit=$TASK_RC"
  echo "[logs] $TASK_RUN"
  exit "$TASK_RC"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "[logs] $TASK_RUN"
echo "[1/4] Preflight"
[[ "$(uname -r)" == 6.1.141 ]] || { echo "Unexpected running kernel."; exit 1; }
[[ "$(uname -m)" == aarch64 ]] || { echo "Unexpected architecture."; exit 1; }
[[ "$(sha256sum "$TASK_MODULE" | cut -d ' ' -f 1)" == "$TASK_HASH" ]] || {
  echo "Module checksum mismatch; stopped."; exit 1;
}
[[ "$(modinfo -F vermagic "$TASK_MODULE")" == '6.1.141 SMP mod_unload aarch64' ]] || {
  echo "Module vermagic mismatch; stopped."; exit 1;
}
[[ -e "$TASK_DEV" ]] || { echo "USB2CAN device link is absent."; exit 1; }
[[ ! -e /sys/module/slcan ]] || { echo "slcan is already loaded; preserving it and stopping."; exit 1; }
if ip link show dev "$TASK_IFACE" >/dev/null 2>&1; then
  echo "$TASK_IFACE already exists; preserving it and stopping."
  exit 1
fi
if pgrep -x slcand >/dev/null || pgrep -x y1_controller >/dev/null; then
  echo "An slcand or Y1 controller process is already running; stopped."
  exit 1
fi
if fuser "$TASK_DEV" > "$TASK_RUN/device-users.log" 2>&1; then
  echo "USB2CAN is in use; stopped."
  cat "$TASK_RUN/device-users.log"
  exit 1
fi

echo "[2/4] Temporarily loading slcan.ko"
TASK_PHASE=module-load
insmod "$TASK_MODULE"
TASK_LOADED=1
[[ -e /sys/module/slcan ]] || { echo "Module is not registered."; exit 1; }
grep -E '^slcan[[:space:]]' /proc/modules
grep -E '^slcan[[:space:]]+17$' /proc/tty/ldiscs
kernel_delta
if grep -En 'BUG:|Oops:|WARNING:|Call trace:|Unknown symbol|invalid module|Unable to handle kernel' "$TASK_RUN/kernel-new.log"; then
  echo "New kernel errors; stopping before attaching the USB device."
  exit 1
fi
echo "[PASS] Module and SLCAN line discipline registered."

echo "[3/4] Attaching USB2CAN and enabling $TASK_IFACE at 1 Mbps"
TASK_PHASE=interface-attach
slcand -F -o -f -s8 "$TASK_DEV" "$TASK_IFACE" > "$TASK_RUN/slcand.log" 2>&1 &
TASK_PID=$!
for TASK_I in {1..30}; do
  ip link show dev "$TASK_IFACE" >/dev/null 2>&1 && break
  if ! kill -0 "$TASK_PID" 2>/dev/null; then
    cat "$TASK_RUN/slcand.log"
    echo "slcand exited before creating the interface."
    exit 1
  fi
  sleep 0.1
done
ip link show dev "$TASK_IFACE" >/dev/null 2>&1 || {
  cat "$TASK_RUN/slcand.log"; echo "CAN interface was not created."; exit 1;
}
ip link set dev "$TASK_IFACE" txqueuelen 1000
ip link set dev "$TASK_IFACE" up
ip -details -statistics link show dev "$TASK_IFACE" | tee "$TASK_RUN/interface-before.log"
echo "[PASS] CAN interface created and enabled."

echo "[4/4] Receiving up to 5 CAN frames for at most 10 seconds; no CAN frames are sent by this test."
TASK_PHASE=receive
set +e
timeout --signal=TERM 10s stdbuf -oL candump -n 5 "$TASK_IFACE" > "$TASK_RUN/candump.log" 2>&1
TASK_DUMP_RC=$?
set -e
cat "$TASK_RUN/candump.log"
ip -details -statistics link show dev "$TASK_IFACE" | tee "$TASK_RUN/interface-after.log"
if (( TASK_DUMP_RC != 0 && TASK_DUMP_RC != 124 )); then
  echo "candump failed: $TASK_DUMP_RC"
  exit "$TASK_DUMP_RC"
fi
if grep -Eq "^[[:space:]]*$TASK_IFACE[[:space:]]+[0-9A-Fa-f]+[[:space:]]+\\[" "$TASK_RUN/candump.log"; then
  TASK_RX=frames-received
  echo "[PASS] CAN frames received."
else
  TASK_RX=no-frames-within-10s
  echo "[INCONCLUSIVE] No frames received; arm power/bus wiring/bitrate remain unverified."
fi
TASK_PHASE=complete
