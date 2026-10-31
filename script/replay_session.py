"""One owned normal driver per replay, with validation before enabling."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description='一键回放：自动启动 control；结束时停止并禁用本次驱动，请托住机械臂')
    parser.add_argument('file', nargs='?', default='latest')
    parser.add_argument('--gear', choices=('slow', 'normal', 'fast'), default='normal')
    parser.add_argument('--lead-in', type=float, default=3)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from replay_episode import choose_file
    path = choose_file(args.file, root)
    command = [sys.executable, str(root / 'script/replay_episode.py'), str(path),
               '--gear', args.gear, '--lead-in', str(args.lead_in)]
    # Reject an invalid/incomplete trajectory without enabling motors.
    subprocess.run(command + ['--dry-run'], check=True)
    if args.dry_run:
        return 0
    from trajectory_io import load_episode, prepare_replay_targets
    from trim_episode import replay_timing
    times, positions = load_episode(path)
    positions, _ = prepare_replay_targets(positions)
    if replay_timing(times, positions, {'slow': .25, 'normal': .5, 'fast': 1}[args.gear], args.lead_in)[0] is None:
        return 0
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    driver = replay = None
    with (logs / 'y1-driver.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有录制/control/示教程序，请先在原终端 Ctrl+C；不接管现有驱动。')
        if subprocess.run(['pgrep', '-x', 'y1_controller'], capture_output=True).returncode == 0:
            raise RuntimeError('已有机械臂驱动，请先停止再回放。')
        subprocess.run(['bash', str(root / 'script/check_setup.sh')], check=True)
        prefix = subprocess.check_output(['ros2', 'pkg', 'prefix', 'y1_controller'], text=True).strip()
        if Path(prefix) != root / 'y1_sdk-humble/install/y1_controller':
            raise RuntimeError('ROS2 加载了其他工作空间')
        log_path = logs / f'replay-driver-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}.log'
        print('启动正常控制；结束或 Ctrl+C 后禁用本次驱动，请托住机械臂。日志：', log_path, flush=True)
        with log_path.open('w') as log:
            try:
                driver = subprocess.Popen(['ros2', 'run', 'y1_controller', 'y1_controller', '--ros-args',
                    '-r', '__node:=right_normal_arm', '--params-file',
                    str(root / 'y1_sdk-humble/install/y1_controller/share/y1_controller/config/single_arm_control.yaml'),
                    '-p', 'disable_on_shutdown:=true'], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    text = log_path.read_text(errors='replace')
                    if driver.poll() is not None or any(s in text for s in ('Failed connect to J', 'No buffer space available', 'Failed to open can device')):
                        raise RuntimeError(f'驱动启动失败，查看 {log_path}')
                    if 'y1_controller success start!' in text:
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError(f'驱动启动超时，查看 {log_path}')
                # Same terminal process group keeps interactive stdin available.
                replay = subprocess.Popen(command)
                while replay.poll() is None:
                    if driver.poll() is not None:
                        raise RuntimeError('驱动退出，终止回放')
                    time.sleep(.1)
                return replay.returncode
            finally:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    if replay is not None and replay.poll() is None:
                        replay.send_signal(signal.SIGINT)
                        try:
                            replay.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            replay.terminate()
                            replay.wait(timeout=5)
                finally:
                    stop_owned(driver)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('回放已停止。')
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'[失败] {exc}')
