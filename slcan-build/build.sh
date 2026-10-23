#!/usr/bin/env bash
# Build only. This script never installs or loads a kernel module.
set -euo pipefail

TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TASK_SOURCE="$TASK_ROOT/source"
TASK_OUT="$TASK_ROOT/kernel-out"
TASK_CC="$TASK_ROOT/tools/usr/bin/gcc-10"
TASK_EXPECTED_RELEASE=6.1.141

for TASK_FILE in "$TASK_SOURCE/Makefile" "$TASK_ROOT/running-kernel.config" \
  "$TASK_CC" "$TASK_ROOT/tools/usr/bin/flex" "$TASK_ROOT/tools/usr/bin/bison"; do
  [[ -f "$TASK_FILE" ]] || { echo "Missing input: $TASK_FILE" >&2; exit 1; }
done

mkdir -p "$TASK_OUT" "$TASK_ROOT/logs" "$TASK_ROOT/artifacts"
export BISON_PKGDATADIR="$TASK_ROOT/tools/usr/share/bison"
TASK_MAKE=(make -C "$TASK_SOURCE" O="$TASK_OUT" ARCH=arm64
  CC="$TASK_CC" HOSTCC=/usr/bin/gcc
  LEX="$TASK_ROOT/tools/usr/bin/flex" YACC="$TASK_ROOT/tools/usr/bin/bison")

cp -- "$TASK_ROOT/running-kernel.config" "$TASK_OUT/.config"
"${TASK_MAKE[@]}" olddefconfig 2>&1 | tee "$TASK_ROOT/logs/configure.log"
"$TASK_SOURCE/scripts/diffconfig" "$TASK_ROOT/running-kernel.config" "$TASK_OUT/.config" \
  > "$TASK_ROOT/logs/config-diff.txt"

TASK_RELEASE=$("${TASK_MAKE[@]}" -s --no-print-directory kernelrelease)
if [[ "$TASK_RELEASE" != "$TASK_EXPECTED_RELEASE" ]]; then
  echo "Unexpected source release: $TASK_RELEASE; expected $TASK_EXPECTED_RELEASE" >&2
  exit 1
fi

"${TASK_MAKE[@]}" -j2 modules_prepare 2>&1 | tee "$TASK_ROOT/logs/prepare.log"

# The vendor's Module.symvers is unavailable. Preserve modpost warnings, then
# report symbol presence separately; do not fabricate a vendor symbol table.
"${TASK_MAKE[@]}" -j2 M="$TASK_SOURCE/drivers/net/can/slcan" \
  CONFIG_CAN_SLCAN=m KBUILD_MODPOST_WARN=1 modules \
  2>&1 | tee "$TASK_ROOT/logs/module-build.log"

install -m 0644 "$TASK_SOURCE/drivers/net/can/slcan/slcan.ko" \
  "$TASK_ROOT/artifacts/slcan.ko"
modinfo "$TASK_ROOT/artifacts/slcan.ko" | tee "$TASK_ROOT/artifacts/modinfo.txt"
(cd "$TASK_ROOT/artifacts" && sha256sum slcan.ko > slcan.ko.sha256)
python3 "$TASK_ROOT/verify.py"
echo "Build complete: $TASK_ROOT/artifacts/slcan.ko"
echo "The module has NOT been installed or loaded. Runtime compatibility is unverified."
