"""Own one driver: slow joint homing, gravity compensation, then recording."""
import fcntl
import math
import os
from pathlib import Path
import signal
import subprocess
import time


class ZeroGate:
    """Require continuous, healthy feedback at rest, not a single zero sample."""
    def __init__(self):
        self.since = None

    def update(self, positions, velocities, codes, now):
        healthy = (len(positions) == 7 and len(velocities) == 7 and
                   len(codes) == 7 and all(c == 1 for c in codes) and
                   all(math.isfinite(x) for x in [*positions, *velocities]))
        ready = (healthy and all(abs(x) < 0.03 for x in positions[:6]) and
                 all(abs(x) < 0.05 for x in velocities[:6]))
        if not ready:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        return now - self.since >= 1.0


class AcquisitionSession:
    def __init__(self, root, home_timeout=60):
        self.root = Path(root)
        self.home_timeout = home_timeout
        self.namespace = f"/y1_record_{os.getpid()}"
        self.topic = self.namespace + "/joint_states"
        self.process = self.lock = self.log = self.log_reader = None
        self.position = self.status = None
        self.position_at = self.status_at = None
        self.subscriptions = []

    def start(self):
        from trajectory_io import replay_profile
        profile = replay_profile()  # Validate SDK before starting an enabled process.
        if not all(a <= 0 <= b for a, b in zip(profile['joint_lower'], profile['joint_upper'])):
            raise RuntimeError("当前 SDK 软限位不允许六关节零位")
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        self.lock = (logs / "y1-driver.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有驱动/录制程序运行；请先在原终端停止")
        if subprocess.run(['pgrep', '-x', 'y1_controller'], capture_output=True).returncode == 0:
            raise RuntimeError("已有 Y1 驱动运行；请先停止，不能同时控制同一机械臂")
        import json
        result = subprocess.run(['ip', '-json', 'link', 'show', 'dev', 'y1_can0'],
                                capture_output=True, text=True, check=True)
        links = json.loads(result.stdout)
        if not links or links[0].get('link_type') != 'can' or 'UP' not in links[0].get('flags', []):
            raise RuntimeError("y1_can0 未就绪")
        prefix = subprocess.check_output(['ros2', 'pkg', 'prefix', 'y1_controller'], text=True).strip()
        if Path(prefix) != self.root / 'y1_sdk-humble/install/y1_controller':
            raise RuntimeError("ROS2 加载了其他工作空间；请 source ~/robot_y1/setup.bash")
        self.log_path = logs / f"record-session-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
        self.log = self.log_path.open('w')
        self.log_reader = self.log_path.open()
        command = ['ros2', 'run', 'y1_controller', 'y1_controller', '--ros-args',
                   '-r', '__node:=record_driver', '-r', '__ns:=' + self.namespace]
        params = {'arm_can_id': 'y1_can0', 'arm_feedback_rate': 200,
                  'arm_control_type': 'normal_arm', 'arm_end_type': 3,
                  'auto_enable': 'true', 'allow_teach_switch': 'true',
                  'disable_on_shutdown': 'true',
                  'arm_joint_state_topic': self.topic,
                  'arm_status_topic': self.namespace + '/arm_status',
                  'arm_joint_position_control_topic': self.namespace + '/joint_command',
                  'arm_end_pose_control_topic': self.namespace + '/end_pose_command'}
        for key, value in params.items():
            command.extend(['-p', f'{key}:={value}'])
        print(f"[1/3] 启动驱动并使能；六关节将慢速回零，夹爪保持当前开度。\n日志：{self.log_path}", flush=True)
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT,
                                        start_new_session=True, close_fds=True)

    def check(self, require_feedback=True):
        if self.process.poll() is not None:
            raise RuntimeError(f"驱动提前退出，查看 {self.log_path}")
        text = self.log_reader.read()
        if any(marker in text for marker in ('ENOBUFS', 'Failed to write can frame',
                                             'Failed connect to J', 'Failed to open can device')):
            raise RuntimeError(f"驱动报告通信错误，停止流程；查看 {self.log_path}")
        if require_feedback:
            now = time.monotonic()
            if any(t is None or now - t > 1 for t in (self.position_at, self.status_at)):
                raise RuntimeError("关节反馈或电机状态超过 1 秒未更新，停止流程")
            if len(self.status.error_code) != 7 or any(c != 1 for c in self.status.error_code):
                raise RuntimeError(f"电机未使能或故障：{list(self.status.error_code)}")
            q = list(self.position.joint_position)
            v = list(self.position.joint_velocity)
            if len(q) != 7 or len(v) != 7 or not all(math.isfinite(x) for x in q + v):
                raise RuntimeError("关节反馈维度或数值无效")

    def prepare(self, node):
        import rclpy
        from rclpy.qos import qos_profile_sensor_data
        from std_srvs.srv import Trigger
        from y1_msg.msg import ArmJointState, ArmStatus, ArmJointPositionControl

        def positions(msg):
            self.position, self.position_at = msg, time.monotonic()

        def status(msg):
            self.status, self.status_at = msg, time.monotonic()

        self.subscriptions = [
            node.create_subscription(ArmJointState, self.topic, positions, qos_profile_sensor_data),
            node.create_subscription(ArmStatus, self.namespace + '/arm_status', status, qos_profile_sensor_data)]
        pub = node.create_publisher(ArmJointPositionControl, self.namespace + '/joint_command', 1)
        client = node.create_client(Trigger, self.namespace + '/record_driver/enter_teach')
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=.05)
                self.check(require_feedback=False)
                if self.position is not None and self.status is not None and pub.get_subscription_count() and client.service_is_ready():
                    break
            else:
                raise RuntimeError("15 秒内驱动/模式切换服务未就绪，未发送回零目标")
            self.check()
            gripper = float(self.position.joint_position[6])
            if not 0 <= gripper <= 100:
                raise RuntimeError("夹爪反馈超出当前 SDK 范围")
            msg = ArmJointPositionControl()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.joint_position = [0.] * 6
            msg.joint_velocity = msg.gripper_velocity = 1
            msg.gripper_stroke = gripper
            pub.publish(msg)
            print("[回零] 已发送慢速目标，等待六关节到位并静止 1 秒…", flush=True)
            gate = ZeroGate()
            deadline = time.monotonic() + self.home_timeout
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=.05)
                self.check()
                if gate.update(list(self.position.joint_position), list(self.position.joint_velocity),
                               list(self.status.error_code), min(self.position_at, self.status_at)):
                    break
            else:
                raise RuntimeError("回零超时，不切换重力补偿、不录制")
            print("[2/3] 回零完成，切换到重力补偿（同一驱动进程）…", flush=True)
            future = client.call_async(Trigger.Request())
            deadline = time.monotonic() + 5
            while not future.done() and time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=.05)
                self.check()
            if not future.done() or future.result() is None or not future.result().success:
                raise RuntimeError("重力补偿切换未成功确认，不开始录制")
            # Require fresh feedback after the acknowledged switch.
            switched = time.monotonic()
            deadline = switched + 2
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=.05)
                self.check()
                if self.position_at > switched and self.status_at > switched:
                    print("[3/3] 重力补偿已切换，开始录制；现在可以手拖。", flush=True)
                    return
            raise RuntimeError("切换后没有新反馈，不开始录制")
        finally:
            node.destroy_publisher(pub)
            node.destroy_client(client)

    def close(self):
        if self.process and self.process.poll() is None:
            print("[退出] 停止本次录制创建的驱动；请托住机械臂。", flush=True)
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise RuntimeError("驱动未退出，请使用设备停止措施；保留进程供排查")
        for handle in (self.log_reader, self.log, self.lock):
            if handle:
                handle.close()
