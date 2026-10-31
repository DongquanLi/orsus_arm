"""Select supported SLCAN CANable by USB identity, not tty numbering."""
import argparse
import re
import subprocess
from pathlib import Path


def select_device(devices, serial=None):
    matches = [d for d in devices if d.get('ID_VENDOR_ID') == 'ad50' and
               d.get('ID_MODEL_ID') == '60c4' and
               (serial is None or d.get('ID_SERIAL_SHORT') == serial)]
    if len(matches) != 1:
        raise ValueError('需要唯一 CANable ad50:60c4；多个设备用 --serial 指定。找到：' +
                         str([(d.get('DEVNAME'), d.get('ID_SERIAL_SHORT')) for d in matches]))
    device = matches[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', device.get('ID_SERIAL_SHORT', '')):
        raise ValueError('CANable 缺少有效的完整序列号')
    return device


def discover():
    result = []
    for entry in Path('/sys/class/tty').iterdir():
        info = subprocess.run(['udevadm', 'info', '--query=property', '--path', str(entry)],
                              capture_output=True, text=True)
        if info.returncode == 0:
            result.append(dict(line.split('=', 1) for line in info.stdout.splitlines() if '=' in line))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    device = select_device(discover(), args.serial)
    serial = device['ID_SERIAL_SHORT']
    args.output.write_text('SUBSYSTEM=="tty", ATTRS{idVendor}=="ad50", '
                          'ATTRS{idProduct}=="60c4", ATTRS{serial}=="' + serial + '", '
                          'SYMLINK+="imeta_y1_can0", GROUP="dialout", MODE="0660", '
                          'ENV{ID_MM_PORT_IGNORE}="1", ENV{ID_MM_DEVICE_IGNORE}="1"\n')
    print('选择 CANable：', device.get('DEVNAME'), 'serial=' + serial)


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc))
