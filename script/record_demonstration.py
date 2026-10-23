#!/usr/bin/env python3
"""Record ROS2 Y1 feedback without sending commands."""
import argparse
import datetime
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description="ROS2 Y1 示教录制；只订阅反馈，不控制机械臂")
    parser.add_argument("--topic", default="/master_arm_right/joint_states")
    parser.add_argument("--rate", type=float, default=25, help="最高录制频率 Hz，默认 25")
    parser.add_argument("--duration", type=float, default=0, help="录制秒数；0 为 Ctrl+C 停止")
    parser.add_argument("--output", type=Path, help="自定义输出文件，默认保存到 records/")
    args = parser.parse_args()
    if not 1 <= args.rate <= 400 or args.duration < 0:
        parser.error("rate 需为 1~400；duration 不能为负数")
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from y1_msg.msg import ArmJointState
    from trajectory_io import EpisodeWriter

    root = Path(__file__).resolve().parents[1]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = args.output or root / "records" / ("demonstration_" + stamp) / "dataset/episode_0.hdf5"
    writer = EpisodeWriter(path, args.topic)
    rclpy.init()
    node = Node("y1_demonstration_recorder")
    first = None
    last = None
    started = time.monotonic()

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

    node.create_subscription(ArmJointState, args.topic, callback, qos_profile_sensor_data)
    print(f"订阅 {args.topic}；请先启动示教模式。Ctrl+C 保存退出。\n文件：{path}", flush=True)
    complete = True
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
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
        writer.close(complete=complete)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        print(f"已保存 {writer.count} 帧：{path}", flush=True)
    return 0 if writer.count >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
