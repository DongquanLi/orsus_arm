#!/usr/bin/env python3
"""Replay validated HDF5 positions through the existing Y1 SDK ROS2 interface."""
import argparse
from pathlib import Path
import time


def choose_file(value, root):
    files = sorted((root / "records").glob("demonstration_*/dataset/episode_*.hdf5"))
    if value == "latest":
        if not files:
            raise ValueError("没有本机录制的 HDF5 文件")
        return files[-1]
    if value:
        return Path(value).expanduser().resolve()
    if not files:
        raise ValueError("没有本机录制的 HDF5 文件")
    for index, file in enumerate(files, 1):
        print(f"{index}: {file.relative_to(root)}")
    number = int(input("选择回放编号："))
    if not 1 <= number <= len(files):
        raise ValueError("编号超出范围")
    return files[number - 1]


def main():
    parser = argparse.ArgumentParser(description="回放本机 Y1 HDF5 v1 轨迹，默认 normal 档（时间轴 0.5 倍速）")
    parser.add_argument("file", nargs="?", help="文件路径或 latest；不填则交互选择")
    parser.add_argument("--gear", choices=("slow", "normal", "fast"), default="normal", help="回放档位，默认 normal")
    parser.add_argument("--dry-run", action="store_true", help="只验证文件，不连接 ROS、不发送指令")
    parser.add_argument("--lead-in", type=float, default=3., help="到达起点后等待约多少秒开始动作，默认 3，与档位无关")
    args = parser.parse_args()
    from trajectory_io import load_episode, prepare_replay_targets, replay_profile
    root = Path(__file__).resolve().parents[1]
    path = choose_file(args.file, root)
    timestamps, positions = load_episode(path)
    positions, adjustments = prepare_replay_targets(positions)
    profile = replay_profile()
    scale, velocity = {"slow": (0.25, 1), "normal": (0.5, 3), "fast": (1.0, 5)}[args.gear]
    from trim_episode import replay_timing
    motion_index, schedule = replay_timing(timestamps, positions, scale, args.lead_in)
    print(f"轨迹有效：{len(timestamps)} 帧，原时长 {timestamps[-1]:.2f} 秒；{args.gear} 回放")
    print(f"文件：{path}")
    for item in adjustments:
        print(f"[目标限幅] J{item['joint']}：{item['frames']} 帧，最大调整 {item['max_adjustment_rad']:.5f} rad")
    if adjustments:
        print("[说明] 已按当前 SDK 的位置控制软限位生成目标；这些区段不会原样越界回放。原始录制文件未修改。")
    if motion_index is None:
        print("未检测到足够明确的运动，不发送回放指令；原文件保留。")
        return 0
    print(f"[起步] 起点到位后等待约 {args.lead_in:.2f} 秒；跳过原开头 {timestamps[motion_index]:.2f} 秒等待，中途停顿保留。")
    if args.dry_run:
        return 0

    import numpy as np
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from y1_msg.msg import ArmJointPositionControl, ArmJointState

    rclpy.init()
    node = Node("y1_episode_replay")
    publisher = node.create_publisher(ArmJointPositionControl, "/master_arm_right/joint_states", 1)
    feedback = None
    received_at = None

    def callback(message):
        nonlocal feedback, received_at
        feedback = np.asarray(message.joint_position, dtype=float)
        received_at = time.monotonic()

    node.create_subscription(ArmJointState, "/puppet_arm_right/joint_states", callback, qos_profile_sensor_data)

    def spin_until(deadline):
        while time.monotonic() < deadline and rclpy.ok():
            rclpy.spin_once(node, timeout_sec=min(0.02, max(0, deadline-time.monotonic())))
            if received_at is not None and time.monotonic() - received_at > 1:
                raise RuntimeError("机械臂反馈中断，已停止发送回放目标")

    def publish(position, speed):
        if feedback is None or len(feedback) < 6 or not np.isfinite(feedback).all():
            raise RuntimeError("没有有效的机械臂反馈")
        if time.monotonic() - received_at > 1:
            raise RuntimeError("反馈过期，停止回放")
        message = ArmJointPositionControl()
        message.header.stamp = node.get_clock().now().to_msg()
        message.joint_position = [float(v) for v in position[:6]]
        message.joint_velocity = speed
        if len(position) == 7:
            message.gripper_stroke = float(position[6])
        elif len(feedback) == 7:
            message.gripper_stroke = float(feedback[6])
        else:
            raise RuntimeError("轨迹和反馈均缺少夹爪行程，停止以避免发送错误夹爪目标")
        if not profile["gripper_lower"] <= message.gripper_stroke <= profile["gripper_upper"]:
            raise RuntimeError("夹爪反馈或目标不符合当前 SDK 原生范围 0~100")
        message.gripper_velocity = speed
        publisher.publish(message)

    try:
        deadline = time.monotonic() + 10
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            if feedback is not None and publisher.get_subscription_count() > 0:
                break
        if feedback is None or publisher.get_subscription_count() == 0:
            raise RuntimeError("正常控制驱动未就绪；先停止示教，再启动 start_driver.sh")
        input("机械臂将先慢速移到轨迹起点，确认空间无障碍后按 Enter：")
        spin_until(time.monotonic() + 0.1)
        publish(positions[0], 1)
        deadline = time.monotonic() + 30
        while rclpy.ok() and time.monotonic() < deadline:
            spin_until(time.monotonic() + 0.05)
            joints_ready = np.max(np.abs(feedback[:6] - positions[0, :6])) < 0.05
            gripper_ready = positions.shape[1] == 6 or (
                len(feedback) == 7 and abs(feedback[6] - positions[0, 6]) < 2
            )
            if joints_ready and gripper_ready:
                break
        else:
            raise RuntimeError("30 秒内未到达起点，停止后续回放")
        start = time.monotonic()
        for index in range(motion_index, len(timestamps)):
            if not rclpy.ok():
                break
            spin_until(start + float(schedule[index - motion_index]))
            if not rclpy.ok():
                break
            publish(positions[index], velocity)
        spin_until(time.monotonic() + 0.2)
        print("轨迹目标已发送完毕；最终到位状态请查看机械臂反馈。")
    except KeyboardInterrupt:
        print("已停止发送回放目标。")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        raise SystemExit(f"[失败] {exc}")
