"""Exercise ROS2 recording on an isolated DDS domain; never publish controls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

os.environ["ROS_DOMAIN_ID"] = "231"
import rclpy
from rclpy.node import Node
from y1_msg.msg import ArmJointState

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))
from trajectory_io import load_episode


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "synthetic-feedback.hdf5"
    process = subprocess.Popen([
        sys.executable, str(ROOT / "script/record_demonstration.py"),
        "--record-only",
        "--topic", "/y1_test_feedback", "--duration", "0.5", "--output", str(path),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
    rclpy.init()
    node = Node("synthetic_y1_feedback_test")
    pub = node.create_publisher(ArmJointState, "/y1_test_feedback", 10)
    deadline = time.monotonic() + 12
    try:
        while process.poll() is None and time.monotonic() < deadline:
            message = ArmJointState()
            message.header.stamp = node.get_clock().now().to_msg()
            message.joint_position = [0.0, -0.5, 0.5, 0.0, 0.0, 0.0, 10.0]
            message.joint_velocity = [0.0] * 7
            message.joint_effort = [0.0] * 7
            message.end_pose = [0.0] * 6
            pub.publish(message)
            rclpy.spin_once(node, timeout_sec=0.02)
        if process.poll() is None:
            process.terminate()
        output, _ = process.communicate(timeout=3)
        print(output)
        if process.returncode:
            raise SystemExit(f"Recorder integration test failed: {process.returncode}")
        times, positions = load_episode(path)
        assert len(times) >= 5 and positions.shape[1] == 7
        print(f"ROS2 recording test passed: {len(times)} frames on isolated domain 231")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
