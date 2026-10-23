"""HDF5 storage and validation shared by ROS2 recording/replay tools."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def replay_profile():
    profile = json.loads((ROOT / "script/replay_limits.json").read_text())
    library = ROOT / profile["sdk_library"]
    if hashlib.sha256(library.read_bytes()).hexdigest() != profile["sdk_sha256"]:
        raise ValueError("SDK 动态库已变化，需重新核对回放控制范围，不能沿用旧版映射")
    lower = np.asarray(profile["joint_lower"], dtype=float)
    upper = np.asarray(profile["joint_upper"], dtype=float)
    if lower.shape != (6,) or upper.shape != (6,) or not np.isfinite([lower, upper]).all() or np.any(lower >= upper):
        raise ValueError("SDK 回放范围配置无效")
    return profile


def model_joint_limits():
    profile = replay_profile()
    joints = {joint.attrib["name"]: joint for joint in ET.parse(ROOT / profile["urdf"]).getroot().findall("joint")}
    limits = [joints[f"JOINT{index}"].find("limit") for index in range(1, 7)]
    lower = np.asarray([float(limit.attrib["lower"]) for limit in limits])
    upper = np.asarray([float(limit.attrib["upper"]) for limit in limits])
    if not np.isfinite([lower, upper]).all() or np.any(lower >= upper):
        raise ValueError("URDF 关节范围无效")
    return lower, upper


def validate_trajectory(times, positions):
    times = np.asarray(times, dtype=float)
    positions = np.asarray(positions, dtype=float)
    if times.ndim != 1 or positions.ndim != 2 or positions.shape[1] not in (6, 7):
        raise ValueError("轨迹必须包含时间数组和 N×6 或 N×7 位置数组")
    if len(times) < 2 or len(times) != len(positions):
        raise ValueError("轨迹至少需要两帧，且时间与位置帧数必须一致")
    if not np.isfinite(times).all() or not np.isfinite(positions).all():
        raise ValueError("轨迹含 NaN 或无穷数，禁止回放")
    if times[0] < 0 or np.any(np.diff(times) <= 0):
        raise ValueError("轨迹时间必须非负且严格递增")
    # Feedback captured during teaching need not lie inside the narrower
    # position-command soft limits. Validate it against the actual model here;
    # command targets are bounded separately, matching the SDK's own behavior.
    lower, upper = model_joint_limits()
    invalid = (positions[:, :6] < lower) | (positions[:, :6] > upper)
    if invalid.any():
        frame, joint = np.argwhere(invalid)[0]
        raise ValueError(f"第 {frame} 帧 J{joint+1} 超出当前 URDF 模型范围：{positions[frame,joint]:.5f} rad")
    profile = replay_profile()
    if positions.shape[1] == 7 and np.any((positions[:, 6] < profile["gripper_lower"]) | (positions[:, 6] > profile["gripper_upper"])):
        raise ValueError("夹爪反馈超出当前 SDK 原生范围 0~100；请确认数据来源及映射")
    return times - times[0], positions


def prepare_replay_targets(positions):
    """Return a copy bounded to SDK command limits and a visible change summary."""
    profile = replay_profile()
    source = np.asarray(positions, dtype=float)
    if source.ndim != 2 or source.shape[1] not in (6, 7) or not np.isfinite(source).all():
        raise ValueError("回放目标维度或数值无效")
    targets = source.copy()
    targets[:, :6] = np.clip(source[:, :6], profile["joint_lower"], profile["joint_upper"])
    summary = []
    for joint in range(6):
        delta = np.abs(targets[:, joint] - source[:, joint])
        changed = delta > 0
        if changed.any():
            summary.append({"joint": joint + 1, "frames": int(changed.sum()),
                            "max_adjustment_rad": float(delta.max())})
    return targets, summary


def load_episode(path):
    import h5py
    with h5py.File(path, "r") as file:
        if file.attrs.get("format") != "y1_demonstration" or file.attrs.get("format_version") != 1:
            raise ValueError("文件不是本工具的 Y1 HDF5 v1 格式；历史文件需先确认字段和单位，不能直接回放")
        if not file.attrs.get("complete", False):
            raise ValueError("录制文件未完整结束，禁止直接回放")
        if "timestamps" not in file or "observations/qpos" not in file:
            raise ValueError("轨迹文件缺少时间或位置数据")
        return validate_trajectory(file["timestamps"][:], file["observations/qpos"][:])


class EpisodeWriter:
    def __init__(self, path, topic):
        import h5py
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = h5py.File(self.path, "x")
        self.file.attrs["format"] = "y1_demonstration"
        self.file.attrs["format_version"] = 1
        self.file.attrs["source_topic"] = topic
        self.file.attrs["joint_units"] = "radians; seventh component, if present: SDK-native gripper stroke"
        self.file.attrs["gripper_scale"] = "sdk_native_0_100"
        self.file.attrs["complete"] = False
        self.datasets = None
        self.count = 0

    def append(self, elapsed, position, velocity, effort, end_pose):
        n = len(position)
        if n not in (6, 7) or len(velocity) != n or len(effort) != n or len(end_pose) != 6:
            raise ValueError("反馈位置/速度/力矩维度不一致")
        values = [float(elapsed), *position, *velocity, *effort, *end_pose]
        if not np.isfinite(values).all():
            raise ValueError("反馈包含非有限值，停止录制")
        if self.datasets is None:
            self.datasets = {}
            for name, shape in (("timestamps", ()), ("observations/qpos", (n,)),
                                ("observations/qvel", (n,)), ("observations/effort", (n,)),
                                ("observations/end_pose", (6,))):
                self.datasets[name] = self.file.create_dataset(
                    name, shape=(0, *shape), maxshape=(None, *shape),
                    chunks=(128, *shape), dtype="f8",
                )
        if self.datasets["observations/qpos"].shape[1] != n:
            raise ValueError("录制过程中关节数发生变化")
        if self.count and elapsed <= self.datasets["timestamps"][self.count - 1]:
            raise ValueError("接收时间未递增")
        for name, value in (("timestamps", elapsed), ("observations/qpos", position),
                            ("observations/qvel", velocity), ("observations/effort", effort),
                            ("observations/end_pose", end_pose)):
            dataset = self.datasets[name]
            dataset.resize(self.count + 1, axis=0)
            dataset[self.count] = value
        self.count += 1
        if self.count % 25 == 0:
            self.file.flush()

    def close(self, complete=True):
        self.file.attrs["complete"] = bool(complete and self.count >= 2)
        self.file.attrs["frames"] = self.count
        self.file.flush()
        self.file.close()
