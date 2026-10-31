"""Offline trajectory trimming and replay timing checks; no ROS or CAN."""
from pathlib import Path
import sys
import tempfile
import unittest

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'script'))
from trim_episode import motion_start, replay_timing, trim_file
from trajectory_io import EpisodeWriter


class TrimTests(unittest.TestCase):
    def setUp(self):
        self.t = np.arange(0, 20, .04)
        self.q = np.zeros((len(self.t), 7))
        self.q[:, 6] = 52
        self.q[:, 0] = np.clip((self.t - 10) * .1, 0, .2)

    def test_start_and_fixed_wait_all_gears(self):
        i = motion_start(self.t, self.q)
        self.assertAlmostEqual(self.t[i], 10, delta=.08)
        for scale in (.25, .5, 1):
            j, schedule = replay_timing(self.t, self.q, scale)
            self.assertEqual(i, j)
            self.assertEqual(schedule[0], 3)
            np.testing.assert_allclose(np.diff(schedule), np.diff(self.t[i:]) / scale)

    def test_noise_spike_and_static(self):
        q = np.zeros_like(self.q)
        q[:, 0] = .0003 * np.sin(self.t * 5)
        q[50, 0] = .1
        self.assertIsNone(motion_start(self.t, q))

    def test_gripper_and_slow_ramp(self):
        q = np.zeros_like(self.q)
        q[:, 6] = np.clip((self.t - 5) * 10, 0, 10)
        self.assertAlmostEqual(self.t[motion_start(self.t, q)], 5, delta=.12)
        q[:, 6] = 0
        q[:, 0] = np.maximum(self.t - 2, 0) * .0005
        # Retain the subtle start rather than trimming to the .005 threshold.
        self.assertLess(self.t[motion_start(self.t, q)], 4.1)

    def test_immediate(self):
        q = self.q.copy()
        q[:, 0] = self.t * .1
        self.assertEqual(motion_start(self.t, q), 0)

    def test_backup_alignment_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'episode_0.hdf5'
            writer = EpisodeWriter(path, '/test')
            for t, q in zip(self.t, self.q):
                writer.append(t, q, [t] * 7, [t * 2] * 7, [t * 3] * 6)
            writer.close()
            original = path.read_bytes()
            report = trim_file(path)
            self.assertEqual(report['status'], 'trimmed')
            self.assertAlmostEqual(report['retained_lead_seconds'], 3, delta=.05)
            self.assertEqual((path.parent / 'raw' / path.name).read_bytes(), original)
            with h5py.File(path) as f:
                cut = len(self.t) - len(f['timestamps'])
                np.testing.assert_allclose(f['timestamps'][:], self.t[cut:] - self.t[cut])
                np.testing.assert_array_equal(f['observations/qpos'][:], self.q[cut:])
                np.testing.assert_array_equal(f['observations/qvel'][:, 0], self.t[cut:])
                np.testing.assert_array_equal(f['observations/effort'][:, 0], 2 * self.t[cut:])
                self.assertTrue(f.attrs['complete'])
            self.assertEqual(trim_file(path)['status'], 'already_trimmed')


if __name__ == '__main__':
    unittest.main()
