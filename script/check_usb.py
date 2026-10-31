"""Bounded, non-motion USB checks. Enumeration is not a motor communication test."""
import argparse
import datetime
import json
import subprocess
from pathlib import Path


def snapshot():
    devices = []
    for path in sorted(Path('/sys/bus/usb/devices').iterdir()):
        if not (path / 'idVendor').exists():
            continue
        record = {'port': path.name}
        for field in ('idVendor', 'idProduct', 'product', 'serial', 'speed'):
            try:
                record[field] = (path / field).read_text().strip()
            except OSError:
                record[field] = ''
        devices.append(record)
    return devices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-test', action='store_true', help='读取 250 帧，要求目标相机存在')
    parser.add_argument('--compare', type=Path, help='与上次报告目录或 summary.json 比较拔插变化')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output or root / 'logs' / ('usb-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    out.mkdir(parents=True, exist_ok=False)
    devices = snapshot()
    report = {'devices': devices, 'camera_test': None, 'motor_communication_tested': False}
    for name, cmd in [('lsusb', ['lsusb']), ('topology', ['lsusb', '-t']),
                      ('kernel', ['dmesg']), ('video', ['v4l2-ctl', '--list-devices']),
                      ('can', ['ip', '-details', '-statistics', 'link', 'show', 'type', 'can'])]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            (out / (name + '.txt')).write_text(result.stdout + result.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            (out / (name + '.txt')).write_text(str(exc))
    for d in devices:
        print(f"USB {d['port']:8} {d['idVendor']}:{d['idProduct']} {d['speed']}M {d['product']}")
    report['canable_present'] = any(d['idVendor'] == 'ad50' and d['idProduct'] == '60c4' for d in devices)
    report['camera_present'] = any(d['idVendor'] == '2c7f' and d['idProduct'] == '4a60' for d in devices)
    if args.compare:
        previous = args.compare / 'summary.json' if args.compare.is_dir() else args.compare
        old = json.loads(previous.read_text())['devices']
        key = lambda d: (d['port'], d['idVendor'], d['idProduct'], d['serial'])
        before, after = {key(d) for d in old}, {key(d) for d in devices}
        report['removed'] = sorted(before - after)
        report['added'] = sorted(after - before)
        print('消失设备：', report['removed'], '\n新增设备：', report['added'])
    failed = False
    if args.camera_test:
        candidates = []
        for entry in Path('/sys/class/video4linux').glob('video*'):
            # Select capture nodes from this USB camera; never use a hardcoded video number.
            for parent in (entry.resolve(), *entry.resolve().parents):
                if (parent / 'idVendor').exists():
                    if ((parent / 'idVendor').read_text().strip(), (parent / 'idProduct').read_text().strip()) == ('2c7f', '4a60'):
                        index = entry / 'index'
                        if index.exists() and index.read_text().strip() == '0':
                            candidates.append('/dev/' + entry.name)
                    break
        if len(candidates) != 1:
            report['camera_test'] = {'passed': False, 'error': '未找到唯一目标相机 capture 节点', 'candidates': candidates}
            failed = True
        else:
            cmd = ['v4l2-ctl', '-d', candidates[0], '--set-fmt-video=width=640,height=480,pixelformat=MJPG',
                   '--set-parm=25', '--stream-mmap=4', '--stream-count=250', '--stream-to=/dev/null']
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                (out / 'camera-stream.txt').write_text(result.stdout + result.stderr)
                failed = result.returncode != 0
                report['camera_test'] = {'device': candidates[0], 'passed': not failed, 'exit_code': result.returncode,
                                         'requested_frames': 250, 'requested_fps': 25}
            except (OSError, subprocess.TimeoutExpired) as exc:
                report['camera_test'] = {'passed': False, 'error': str(exc)}
                failed = True
        print('相机取流：', report['camera_test'])
    (out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('日志：', out)
    print('CANable 枚举：', report['canable_present'], '；相机枚举：', report['camera_present'])
    print('仅确认当前连接路径；未插入设备的 USB 口需逐个换口复测。CAN 电机通信另运行 bash y1.sh verify-arm。')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
