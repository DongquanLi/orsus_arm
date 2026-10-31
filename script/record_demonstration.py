#!/usr/bin/env python3
"""Default: home joints, enter gravity compensation, then record ROS2 feedback."""
import argparse
import datetime
from pathlib import Path
import time
import math
import signal


def main():
    parser = argparse.ArgumentParser(description="一键录制：使能、慢速回零、重力补偿、记录数据；启动前清空运动空间")
    parser.add_argument("--record-only", action="store_true", help="只订阅已有示教反馈，不启动驱动、不回零")
    parser.add_argument("--topic", help="仅 --record-only 可指定；默认 /master_arm_right/joint_states")
    parser.add_argument("--home-timeout", type=float, default=60, help="回零最长等待秒数，默认 60")
    parser.add_argument("--rate", type=float, default=25, help="最高录制频率 Hz，默认 25")
    parser.add_argument("--duration", type=float, default=0, help="录制秒数；0 为 Ctrl+C 停止")
    parser.add_argument("--output", type=Path, help="自定义输出文件，默认保存到 records/")
    parser.add_argument("--no-trim", action="store_true", help="保留全部开头等待；默认完成后自动裁剪并保存 raw 原件")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or not 1 <= args.rate <= 400 or args.duration < 0:
        parser.error("rate 需为 1~400；duration 不能为负数")
    if not math.isfinite(args.home_timeout) or args.home_timeout <= 0:
        parser.error("home-timeout 必须为正数")
    if args.topic and not args.record_only:
        parser.error("--topic 仅用于 --record-only；自动流程使用专用话题")
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from y1_msg.msg import ArmJointState
    from trajectory_io import EpisodeWriter
    from rclpy.signals import SignalHandlerOptions

    root = Path(__file__).resolve().parents[1]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = args.output or root / "records" / ("demonstration_" + stamp) / "dataset/episode_0.hdf5"
    if path.exists():
        parser.error(f"输出文件已存在：{path}")
    # Fail early on an unwritable destination, before enabling a motor.
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryFile(dir=path.parent):
        pass
    session = None
    if not args.record_only:
        from acquisition_session import AcquisitionSession
        session = AcquisitionSession(root, args.home_timeout)
    topic = session.topic if session else (args.topic or '/master_arm_right/joint_states')
    writer = None
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = Node("y1_demonstration_recorder")
    first = None
    last = None
    started = None

    def callback(message):
        nonlocal first, last
        now = time.monotonic()
        if last is not None and now - last < 1 / args.rate:
            return
        if first is None:
            first = now
        writer.append(now - first, list(message.joint_position), list(message.joint_velocity),
                      list(message.joint_effort), list(message.end_pose))
        last = now
        if writer.count % 25 == 0:
            print(f"已录制 {writer.count} 帧，{now-first:.1f} 秒", flush=True)

    def interrupt(*_):
        raise KeyboardInterrupt

    old_term = signal.signal(signal.SIGTERM, interrupt)
    complete = True
    try:
        if session:
            session.start()
            session.prepare(node)
        writer = EpisodeWriter(path, topic)
        writer.file.attrs['auto_home_before_teach'] = session is not None
        if session:
            writer.file.attrs['home_joint_target'] = [0.] * 6
            writer.file.attrs['home_gripper_policy'] = 'preserve'
        node.create_subscription(ArmJointState, topic, callback, qos_profile_sensor_data)
        started = time.monotonic()
        print(f"订阅 {topic}；Ctrl+C 保存并退出。\n文件：{path}", flush=True)
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            if session:
                session.check()
            now = time.monotonic()
            if first is None and now - started > 10:
                raise RuntimeError("10 秒内没有反馈；检查示教驱动、话题和电机连接")
            if last is not None and now - last > 3:
                raise RuntimeError("反馈已中断 3 秒，停止录制")
            if args.duration and first is not None and now - first >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    except BaseException:
        complete = False
        raise
    finally:
        # Do not allow a second terminal interrupt to orphan the enabled child.
        previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            if writer:
                writer.close(complete=complete)
                print(f"已保存 {writer.count} 帧：{path}", flush=True)
        finally:
            try:
                if session:
                    session.close()
            finally:
                node.destroy_node()
                if rclpy.ok():
                    rclpy.shutdown()
                signal.signal(signal.SIGINT, previous_int)
                signal.signal(signal.SIGTERM, old_term)
    if writer and writer.count >= 2 and complete and not args.no_trim:
        from trim_episode import trim_file
        try:
            report = trim_file(path)
            print(f"[开头裁剪] {report['status']}；删除 {report.get('removed_seconds', 0):.2f} 秒；原始数据保留。", flush=True)
        except (ValueError, OSError) as exc:
            print(f"[裁剪未完成] 数据已保存，请检查：{exc}", flush=True)
            return 1
    return 0 if writer and writer.count >= 2 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        raise SystemExit(f"[失败] {exc}")
