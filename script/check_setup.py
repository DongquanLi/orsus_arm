#!/usr/bin/env python3
"""Read-only deployment checks. Never instantiate the SDK/controller."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="只读检查 Y1 部署状态，不启动电机")
    parser.add_argument("--offline", action="store_true", help="不要求 CAN 服务已安装运行")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sdk = root / "y1_sdk-humble"
    import yaml
    checks = []

    def check(label, passed, detail=""):
        checks.append(bool(passed))
        print(f"[{'通过' if passed else '失败'}] {label}" + (f"：{detail}" if detail else ""))

    for name in ("rclpy", "y1_msg", "h5py", "numpy"):
        check(f"Python {name}", importlib.util.find_spec(name) is not None)
    for filename, node_name in (("single_arm_control.yaml", "right_normal_arm"),
                                ("one_master.yaml", "right_master_arm")):
        source = sdk / "src/y1_controller/config" / filename
        installed = sdk / "install/y1_controller/share/y1_controller/config" / filename
        a, b = yaml.safe_load(source.read_text()), yaml.safe_load(installed.read_text())
        params = b[node_name]["ros__parameters"]
        check(filename, a == b and params["arm_can_id"] == "y1_can0" and params["arm_end_type"] == 3)
    binary = sdk / "install/y1_controller/lib/y1_controller/y1_controller"
    check("Y1 控制器已构建", binary.is_file())
    if binary.is_file():
        result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True)
        check("控制器动态库", result.returncode == 0 and "not found" not in result.stdout)
    check("带夹爪 URDF", (sdk / "install/y1_controller/share/y1_controller/urdf/y1_with_gripper.urdf").is_file())
    if not args.offline:
        for condition, label in ((["systemctl", "is-enabled", "--quiet", "y1-can.service"], "CAN 自启"),
                                 (["systemctl", "is-active", "--quiet", "y1-can.service"], "CAN 服务")):
            check(label, subprocess.run(condition, capture_output=True).returncode == 0)
        check("SLCAN 模块已加载", Path("/sys/module/slcan").exists())
        result = subprocess.run(["ip", "-json", "-statistics", "link", "show", "dev", "y1_can0"], capture_output=True, text=True)
        links = json.loads(result.stdout) if result.returncode == 0 else []
        check("y1_can0 已 UP", bool(links and "UP" in links[0].get("flags", [])))
        if links:
            rx = (links[0].get("stats64") or {}).get("rx", {}).get("packets", 0)
            print(f"[信息] CAN RX={rx}；没有收到帧不能证明电机连接正常。")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
