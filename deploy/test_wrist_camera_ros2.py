"""Live camera integration test; isolated DDS domain, no arm commands.

Run after source ~/robot_y1/setup.bash. Owns and stops its camera subprocess.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

os.environ['ROS_DOMAIN_ID'] = '232'
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

root = Path(__file__).resolve().parents[1]
rclpy.init()
node = rclpy.create_node('wrist_camera_verification')
stamps, arrivals, ages = [], [], []
errors = []

def receive(msg):
    stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
    if (msg.width, msg.height, msg.encoding, msg.step, len(msg.data)) != (
            640, 480, 'rgb8', 1920, 921600):
        errors.append('Invalid image layout')
    if msg.header.frame_id != 'wrist_camera_optical_frame':
        errors.append('Unexpected frame_id')
    if stamps and stamp <= stamps[-1]:
        errors.append('Non-increasing timestamp')
    age = time.time() - stamp
    if not -0.02 < age < 0.5:
        errors.append(f'Unexpected timestamp age {age}')
    stamps.append(stamp)
    arrivals.append(time.monotonic())
    ages.append(age)
    if len(stamps) == 25:
        Path('/tmp/y1-wrist-camera-rgb.ppm').write_bytes(b'P6\n640 480\n255\n' + bytes(msg.data))

node.create_subscription(Image, '/wrist_camera/image_raw', receive, qos_profile_sensor_data)
with tempfile.TemporaryFile(mode='w+t') as log:
    process = subprocess.Popen(['bash', str(root / 'script/start_wrist_camera.sh')],
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            if process.poll() is not None:
                raise RuntimeError(f'Camera exited early: {process.returncode}')
            if len(arrivals) > 1 and arrivals[-1] - arrivals[0] >= 20:
                break
        if len(stamps) < 450:
            raise RuntimeError(f'Too few frames: {len(stamps)}')
        capture_hz = (len(stamps)-1)/(stamps[-1]-stamps[0])
        received_hz = (len(arrivals)-1)/(arrivals[-1]-arrivals[0])
        if not 24 <= capture_hz <= 26 or not 24 <= received_hz <= 26:
            errors.append('Measured frequency outside 24..26 Hz')
        intervals = [b-a for a,b in zip(stamps, stamps[1:])]
        report = dict(frames=len(stamps), capture_hz=capture_hz, received_hz=received_hz,
                      min_interval_ms=min(intervals)*1000, max_interval_ms=max(intervals)*1000,
                      mean_age_ms=sum(ages)/len(ages)*1000, max_age_ms=max(ages)*1000,
                      errors=errors, arm_commands_sent=False)
        print(json.dumps(report, indent=2))
        (root / 'deploy/wrist-camera-validation.json').write_text(json.dumps(report, indent=2)+'\n')
        if errors:
            raise RuntimeError('Image validation failed')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
        log.seek(0)
        print(log.read())
        node.destroy_node()
        rclpy.shutdown()
