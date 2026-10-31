"""Isolated DDS simulation. Never starts the real SDK or accesses CAN."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'script'))
os.environ['ROS_DOMAIN_ID'] = '84'


def fake_driver(namespace, scenario):
    import rclpy
    from std_srvs.srv import Trigger
    from y1_msg.msg import ArmJointState, ArmStatus, ArmJointPositionControl
    rclpy.init()
    node = rclpy.create_node('fake_record_driver')
    pub = node.create_publisher(ArmJointState, namespace + '/joint_states', 10)
    status = node.create_publisher(ArmStatus, namespace + '/arm_status', 10)
    homed = False
    teaching = False
    count = 0

    def command(msg):
        nonlocal homed
        assert list(msg.joint_position) == [0.] * 6
        assert msg.joint_velocity == 1 and msg.gripper_stroke == 52
        print('ZERO_RECEIVED', flush=True)
        homed = scenario != 'timeout'

    def switch(request, response):
        nonlocal teaching
        assert homed
        print('TEACH_REQUEST', flush=True)
        teaching = scenario != 'reject'
        response.success = teaching
        response.message = 'fake transition'
        return response

    node.create_subscription(ArmJointPositionControl, namespace + '/joint_command', command, 10)
    node.create_service(Trigger, namespace + '/record_driver/enter_teach', switch)

    def tick():
        nonlocal count
        count += 1
        if scenario == 'stale' and count > 20:
            return
        msg = ArmJointState()
        msg.joint_position = [(.1 if teaching else (0. if homed else .2)), 0., 0., 0., 0., 0., 52.]
        msg.joint_velocity = [0.] * 7
        msg.joint_effort = [0.] * 7
        msg.end_pose = [0.] * 6
        pub.publish(msg)
        state = ArmStatus()
        state.error_code = [2 if scenario == 'fault' else 1] * 7
        status.publish(state)
    node.create_timer(.02, tick)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass


def run_recorder(scenario, directory):
    from acquisition_session import AcquisitionSession
    import record_demonstration

    def fake_start(self):
        self.log_path = Path(directory) / 'driver.log'
        self.log = self.log_path.open('w')
        self.log_reader = self.log_path.open()
        self.process = subprocess.Popen([sys.executable, __file__, 'fake', self.namespace, scenario],
                                        stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
    AcquisitionSession.start = fake_start
    sys.argv = ['record_demonstration.py', '--duration', '.5', '--home-timeout', '3',
                '--output', str(Path(directory) / 'episode.hdf5')]
    return record_demonstration.main()


def test():
    from acquisition_session import ZeroGate
    gate = ZeroGate()
    assert not gate.update([0.] * 7, [0.] * 7, [1] * 7, 0)
    assert not gate.update([0.] * 7, [0.] * 7, [2] * 7, .9)
    assert not gate.update([0.] * 7, [0.] * 7, [1] * 7, 1)
    assert gate.update([0.] * 7, [0.] * 7, [1] * 7, 2.1)
    assert not gate.update([float('nan')] * 7, [0.] * 7, [1] * 7, 3)
    assert not gate.update([0.] * 7, [.1] * 7, [1] * 7, 4)
    import h5py
    for scenario in ('success', 'timeout', 'reject', 'stale', 'fault'):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, __file__, 'run', scenario, directory],
                                    capture_output=True, text=True, timeout=25)
            log = (Path(directory) / 'driver.log').read_text()
            file = Path(directory) / 'episode.hdf5'
            if scenario == 'success':
                assert result.returncode == 0, result.stdout + result.stderr
                assert log.index('ZERO_RECEIVED') < log.index('TEACH_REQUEST')
                with h5py.File(file) as f:
                    assert f.attrs['complete'] and f.attrs['auto_home_before_teach']
                    assert len(f['timestamps']) >= 5
                    assert all(abs(x - .1) < 1e-8 for x in f['observations/qpos'][:, 0])
            else:
                assert result.returncode != 0, result.stdout
                assert not file.exists(), 'Failed preparation must not create a dataset'
                if scenario in ('timeout', 'stale', 'fault'):
                    assert 'TEACH_REQUEST' not in log
                if scenario == 'fault':
                    assert 'ZERO_RECEIVED' not in log
            print(f'PASS {scenario}: no real driver/CAN used', flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 1:
        test()
    elif sys.argv[1] == 'fake':
        fake_driver(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'run':
        raise SystemExit(run_recorder(sys.argv[2], sys.argv[3]))
