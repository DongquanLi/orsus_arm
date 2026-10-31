"""Conservative leading-idle trimming; preserve raw HDF5 and all later pauses."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np


def motion_start(times, positions):
    """Confirm 0.005 rad / 1 SDK gripper-unit departure for >=0.2 s.

    Backtrack to 0.001 rad / 0.5 unit departure to retain slow beginnings.
    Use measured positions, not noisy reported velocities. None means no
    sufficiently confident movement, not proof that no physical motion occurred.
    """
    t = np.asarray(times, dtype=float)
    q = np.asarray(positions, dtype=float)
    if len(t) < 2 or q.ndim != 2 or q.shape[0] != len(t) or q.shape[1] not in (6, 7):
        raise ValueError('Invalid trajectory dimensions')
    if not np.isfinite(t).all() or not np.isfinite(q).all() or np.any(np.diff(t) <= 0):
        raise ValueError('Nonfinite or nonmonotonic trajectory')
    delta = np.abs(q - q[0])
    high = np.array([.005] * 6 + ([1.] if q.shape[1] == 7 else []))
    low = np.array([.001] * 6 + ([.5] if q.shape[1] == 7 else []))
    active = np.any(delta >= high, axis=1)
    run = None
    for i in range(len(t)):
        if i and t[i] - t[i - 1] > .25:
            run = None
        if not active[i]:
            run = None
        elif run is None:
            run = i
        elif t[i] - t[run] >= .2:
            # Retain all deviations before the confirmation, including a very
            # slow ramp. A spurious early excursion may retain extra idle;
            # conservatively prefer that to deleting an intentional movement.
            departed = np.flatnonzero(np.any(delta[:run + 1] >= low, axis=1))
            return max(0, int(departed[0]) - 1) if len(departed) else max(0, run - 1)
    return None


def replay_timing(times, positions, scale, lead_in=3.):
    if not np.isfinite(scale) or scale <= 0 or not np.isfinite(lead_in) or lead_in < 0:
        raise ValueError('Invalid replay timing')
    start = motion_start(times, positions)
    if start is None:
        return None, None
    times = np.asarray(times, dtype=float)
    # Only the initial wait is independent of gear. All subsequent intervals,
    # including deliberate mid-demonstration pauses, keep the original scale.
    return start, lead_in + (times[start:] - times[start]) / scale


def trim_file(path, keep_seconds=3.):
    import h5py
    path = Path(path).resolve()
    if not np.isfinite(keep_seconds) or keep_seconds < 0:
        raise ValueError('keep_seconds must be finite and nonnegative')
    with h5py.File(path, 'r') as f:
        if f.attrs.get('format') != 'y1_demonstration' or f.attrs.get('format_version') != 1:
            raise ValueError('Only Y1 HDF5 v1 is supported')
        if not f.attrs.get('complete', False):
            raise ValueError('Refusing to trim an incomplete/active recording')
        if f.attrs.get('leading_trim_version') == 1:
            return {'file': str(path), 'status': 'already_trimmed'}
        t = f['timestamps'][:]
        q = f['observations/qpos'][:]
        onset = motion_start(t, q)
        if onset is None:
            return {'file': str(path), 'status': 'no_confident_motion', 'removed_seconds': 0.}
        cut = max(0, int(np.searchsorted(t, t[onset] - keep_seconds, side='right')) - 1)
        names = []
        f.visititems(lambda name, obj: names.append(name) if isinstance(obj, h5py.Dataset) else None)
        expected = {'timestamps', 'observations/qpos', 'observations/qvel',
                    'observations/effort', 'observations/end_pose'}
        if set(names) != expected or any(f[name].shape[0] != len(t) for name in names):
            raise ValueError('Unknown or misaligned datasets; refusing automatic trim')
    if cut == 0:
        return {'file': str(path), 'status': 'no_excess_idle',
                'motion_start_seconds': float(t[onset] - t[0]), 'removed_seconds': 0.}
    raw = path.parent / 'raw' / path.name
    raw.parent.mkdir(exist_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # Exclusive creation: never overwrite an existing raw recording.
    try:
        with path.open('rb') as src, raw.open('xb') as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
    except FileExistsError:
        if hashlib.sha256(raw.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Raw backup already differs: {raw}')
    if hashlib.sha256(raw.read_bytes()).hexdigest() != digest:
        raise ValueError('Raw backup verification failed')
    fd, temp = tempfile.mkstemp(prefix='.trim-', suffix='.hdf5', dir=path.parent)
    os.close(fd)
    try:
        shutil.copy2(path, temp)
        with h5py.File(temp, 'r+') as f:
            for name in names:
                data = f[name][cut:]
                if name == 'timestamps':
                    data = data - data[0]
                f[name].resize(len(data), axis=0)
                f[name][:] = data
            f.attrs['frames'] = len(t) - cut
            f.attrs['leading_trim_version'] = 1
            f.attrs['leading_trim_removed_seconds'] = float(t[cut] - t[0])
            f.attrs['leading_trim_original_frames'] = len(t)
            f.attrs['leading_trim_motion_start_seconds'] = float(t[onset] - t[cut])
            f.attrs['leading_trim_raw_sha256'] = digest
            f.attrs['leading_trim_raw_file'] = 'raw/' + path.name
            f.attrs['leading_trim_policy'] = 'position departure .005 rad/1 gripper unit for .2s; backtrack .001 rad/.5 unit; leading only'
            f.flush()
        with open(temp, 'rb') as f:
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {'file': str(path), 'raw_file': str(raw), 'status': 'trimmed',
            'original_frames': len(t), 'frames': len(t) - cut,
            'original_duration': float(t[-1] - t[0]), 'duration': float(t[-1] - t[cut]),
            'motion_start_seconds': float(t[onset] - t[0]),
            'removed_seconds': float(t[cut] - t[0]),
            'retained_lead_seconds': float(t[onset] - t[cut])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='裁剪 HDF5 开头静止段，保留 raw 原件及约 3 秒前导')
    parser.add_argument('file', type=Path)
    parser.add_argument('--keep-seconds', type=float, default=3.)
    args = parser.parse_args()
    print(json.dumps(trim_file(args.file, args.keep_seconds), ensure_ascii=False, indent=2))
