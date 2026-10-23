"""Offline checks for trajectory rejection and CAN process ownership."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))
from trajectory_io import EpisodeWriter, load_episode, validate_trajectory, prepare_replay_targets, replay_profile

spec = importlib.util.spec_from_file_location("can_watchdog", ROOT / "deploy/system/y1-can-watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class TrajectoryTest(unittest.TestCase):
    def setUp(self):
        self.positions = np.array([[0, -0.5, 0.5, 0, 0, 0, 10], [0.01, -0.5, 0.5, 0, 0, 0, 11]])

    def test_roundtrip_and_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "episode.hdf5"
            writer = EpisodeWriter(path, "/test")
            for t, q in zip([0, 0.1], self.positions):
                writer.append(t, q, np.zeros(7), np.zeros(7), np.zeros(6))
            writer.close()
            times, positions = load_episode(path)
            np.testing.assert_allclose(times, [0, 0.1])
            np.testing.assert_allclose(positions, self.positions)
            result = subprocess.run([sys.executable, str(ROOT / "script/replay_episode.py"),
                                     str(path), "--dry-run"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reject_nonfinite_out_of_range_and_nonmonotonic(self):
        for value in (float("nan"), float("inf"), 4):
            bad = self.positions.copy()
            bad[1, 0] = value
            with self.assertRaises(ValueError):
                validate_trajectory([0, 0.1], bad)
        for times in ([0, 0], [0.1, 0], [-1, 0]):
            with self.assertRaises(ValueError):
                validate_trajectory(times, self.positions)

    def test_incomplete_recording_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.hdf5"
            writer = EpisodeWriter(path, "/test")
            writer.append(0, self.positions[0], np.zeros(7), np.zeros(7), np.zeros(6))
            writer.close(complete=False)
            with self.assertRaisesRegex(ValueError, "未完整"):
                load_episode(path)

    def test_teaching_feedback_is_accepted_and_commands_match_sdk_limits(self):
        feedback = np.array([[0, 0.00125, -0.03, 0, 0, 0, 100],
                             [0, -3.1526, 0.5, 0, 0, 0, 90]])
        original = feedback.copy()
        _, accepted = validate_trajectory([0, 0.1], feedback)
        commands, changes = prepare_replay_targets(accepted)
        profile = replay_profile()
        np.testing.assert_array_equal(feedback, original)
        self.assertEqual(commands[0, 1], 0)
        self.assertEqual(commands[0, 2], 0)
        self.assertEqual(commands[1, 1], profile["joint_lower"][1])
        self.assertEqual(commands[0, 6], 100)
        self.assertEqual({item["joint"] for item in changes}, {2, 3})
        self.assertTrue(np.all(commands[:, :6] >= profile["joint_lower"]))
        self.assertTrue(np.all(commands[:, :6] <= profile["joint_upper"]))

    def test_feedback_outside_model_is_still_rejected(self):
        feedback = self.positions.copy()
        feedback[0, 1] = -3.6
        with self.assertRaisesRegex(ValueError, "URDF"):
            validate_trajectory([0, 0.1], feedback)


class OwnershipTest(unittest.TestCase):
    def supervisor(self):
        return watchdog.Supervisor({"device": "/dev/imeta_y1_can0", "interface": "y1_can0"})

    def test_stop_does_not_kill_an_unrelated_process(self):
        own = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            supervisor = self.supervisor()
            supervisor.child = own
            supervisor.stop_child()
            self.assertIsNotNone(own.poll())
            self.assertIsNone(unrelated.poll())
        finally:
            for process in (own, unrelated):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=3)

    def test_existing_interface_is_never_taken_over(self):
        supervisor = self.supervisor()
        with patch.object(watchdog, "link_info", return_value={"ifname": "y1_can0"}), \
             patch.object(supervisor, "status"), patch.object(watchdog.subprocess, "Popen") as spawn:
            self.assertFalse(supervisor.start_child())
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
