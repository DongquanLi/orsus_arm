"""No hardware: CAN identity selection and replay child-process ownership."""
import os
import tempfile
import signal
import numpy as np
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'script'))
from can_device import select_device
from replay_session import stop_owned


def device(serial='new_board_123', name='/dev/ttyACM7'):
    return dict(ID_VENDOR_ID='ad50', ID_MODEL_ID='60c4', ID_SERIAL_SHORT=serial, DEVNAME=name)


class WorkflowTests(unittest.TestCase):
    def test_new_serial_and_non_acm_name(self):
        d = device(name='/dev/ttyUSB9')
        self.assertEqual(select_device([d]), d)

    def test_modem_not_selected(self):
        with self.assertRaises(ValueError):
            select_device([dict(ID_VENDOR_ID='2c7c', ID_MODEL_ID='6005')])

    def test_ambiguity_requires_exact_serial(self):
        devices = [device('first'), device('second')]
        with self.assertRaises(ValueError):
            select_device(devices)
        self.assertEqual(select_device(devices, 'second'), devices[1])
        with self.assertRaises(ValueError):
            select_device(devices, 'absent')

    def test_rule_injection_rejected(self):
        for serial in ('', 'bad"serial', '*', '../path'):
            with self.assertRaises(ValueError):
                select_device([device(serial)])

    def test_only_owned_process_group_stopped(self):
        own = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        try:
            stop_owned(own)
            self.assertIsNotNone(own.poll())
            self.assertIsNone(other.poll())
            with patch('replay_session.os.killpg') as kill:
                stop_owned(own)
                stop_owned(None)
                kill.assert_not_called()
        finally:
            for proc in (own, other):
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)


class ReplayFlowTests(unittest.TestCase):
    def test_validation_failure_never_starts_driver(self):
        import replay_session
        with patch('sys.argv', ['replay', 'missing.hdf5']), patch('replay_episode.choose_file', side_effect=ValueError('missing')), patch('replay_session.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):
                replay_session.main()
            spawn.assert_not_called()

    def run_flow(self, fail_driver=False):
        import replay_session
        real_popen = subprocess.Popen
        children = []
        root = Path(replay_session.__file__).resolve().parents[1]
        def spawn(command, **kwargs):
            if command[0] == 'ros2':
                kwargs['stdout'].write('y1_controller success start!\n')
                kwargs['stdout'].flush()
                if fail_driver:
                    raise OSError('simulated driver launch failure')
                child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            else:
                child = real_popen([sys.executable, '-c', 'raise SystemExit(0)'], **kwargs)
            children.append(child)
            return child
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1 if command[0] == 'pgrep' else 0)
        previous_int, previous_term = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
        try:
            with patch('sys.argv', ['replay', 'test.hdf5']), patch('replay_episode.choose_file', return_value=Path('/tmp/test.hdf5')), patch('trajectory_io.load_episode', return_value=(np.array([0.,1.]), np.zeros((2,7)))), patch('trajectory_io.prepare_replay_targets', return_value=(np.zeros((2,7)), [])), patch('trim_episode.replay_timing', return_value=(1, np.array([3.]))), patch('replay_session.subprocess.run', side_effect=run), patch('replay_session.subprocess.check_output', return_value=str(root/'y1_sdk-humble/install/y1_controller')), patch('replay_session.subprocess.Popen', side_effect=spawn):
                if fail_driver:
                    with self.assertRaises(OSError):
                        replay_session.main()
                else:
                    self.assertEqual(replay_session.main(), 0)
            self.assertTrue(all(c.poll() is not None for c in children))
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)
            for c in children:
                if c.poll() is None: c.terminate()
                c.wait(timeout=5)

    def test_success_stops_owned_driver(self):
        self.run_flow()

    def test_launch_failure_releases_lock(self):
        self.run_flow(fail_driver=True)
        self.run_flow()


if __name__ == '__main__':
    unittest.main()
