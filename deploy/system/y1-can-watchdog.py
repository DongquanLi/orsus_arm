#!/usr/bin/python3
"""Own one foreground slcand; recover USB/process/interface failures only."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def link_info(name):
    result = subprocess.run(
        ["/usr/sbin/ip", "-json", "-details", "-statistics", "link", "show", "dev", name],
        capture_output=True, text=True, timeout=3,
    )
    if result.returncode:
        return None
    links = json.loads(result.stdout)
    return links[0] if links else None


def device_identity(path):
    try:
        stat = path.stat()
        return stat.st_rdev, stat.st_ino
    except FileNotFoundError:
        return None


class Supervisor:
    def __init__(self, config):
        self.config = config
        self.device = Path(config["device"])
        self.interface = config["interface"]
        self.child = None
        self.identity = None
        self.running = True
        self.last_message = None
        self.last_log = 0.0
        self.state_dir = Path("/run/y1-can")

    def log(self, message):
        now = time.monotonic()
        if message != self.last_message or now - self.last_log >= 30:
            print(message, flush=True)
            self.last_message, self.last_log = message, now

    def stop_child(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    # systemd owns the cgroup. Do not spawn another serial owner.
                    raise RuntimeError("Our slcand did not stop; refusing a second instance")
            self.child = None
            self.identity = None

    def status(self, state, info=None):
        record = {
            "state": state,
            "device": str(self.device), "interface": self.interface,
            "usb_connected": device_identity(self.device) is not None,
            "slcand_pid": self.child.pid if self.child and self.child.poll() is None else None,
            "updated_at": time.time(),
            "rx_packets": ((info or {}).get("stats64") or {}).get("rx", {}).get("packets"),
            "tx_packets": ((info or {}).get("stats64") or {}).get("tx", {}).get("packets"),
        }
        temp = self.state_dir / "status.tmp"
        temp.write_text(json.dumps(record, indent=2) + "\n")
        os.chmod(temp, 0o644)
        temp.replace(self.state_dir / "status.json")

    def start_child(self):
        if link_info(self.interface) is not None:
            self.log(f"Interface {self.interface} already exists outside this service; waiting")
            self.status("interface-conflict")
            return False
        busy = subprocess.run(
            ["/usr/bin/fuser", str(self.device)], capture_output=True, timeout=3,
        )
        if busy.returncode == 0:
            self.log(f"Device {self.device} is in use by another process; waiting")
            self.status("device-busy")
            return False
        self.identity = device_identity(self.device)
        self.log(f"Starting {self.interface}: {self.device}, 1 Mbps")
        # -s8 sends C/S8 before O; -c sends C on a normal stop.
        self.child = subprocess.Popen([
            "/usr/bin/slcand", "-F", "-c", "-o", "-f",
            "-s" + self.config["slcan_speed"], str(self.device), self.interface,
        ])
        deadline = time.monotonic() + 5
        while self.running and time.monotonic() < deadline:
            if self.child.poll() is not None:
                self.log(f"slcand exited during startup: {self.child.returncode}")
                self.stop_child()
                return False
            if link_info(self.interface) is not None:
                return True
            time.sleep(0.1)
        self.log("Timed out waiting for CAN interface")
        self.stop_child()
        return False

    def run(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "supervisor.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                while self.running:
                    identity = device_identity(self.device)
                    if identity is None:
                        self.stop_child()
                        self.status("waiting-for-usb")
                        self.log(f"Waiting for {self.device}")
                        time.sleep(1)
                        continue
                    if self.child and (self.child.poll() is not None or identity != self.identity):
                        self.log("USB device changed or slcand exited; rebuilding")
                        self.stop_child()
                    if self.child is None and not self.start_child():
                        time.sleep(2)
                        continue
                    info = link_info(self.interface)
                    if info is None:
                        self.log("CAN interface disappeared; rebuilding our slcand")
                        self.stop_child()
                        time.sleep(1)
                        continue
                    can_state = info.get("linkinfo", {}).get("info_data", {}).get("state")
                    if can_state == "BUS-OFF":
                        self.log("CAN BUS-OFF reported; rebuilding")
                        self.stop_child()
                        time.sleep(2)
                        continue
                    if "UP" not in info.get("flags", []):
                        subprocess.run([
                            "/usr/sbin/ip", "link", "set", "dev", self.interface,
                            "txqueuelen", str(self.config["txqueuelen"]),
                        ], check=True, timeout=3)
                        subprocess.run([
                            "/usr/sbin/ip", "link", "set", "dev", self.interface, "up",
                        ], check=True, timeout=3)
                        info = link_info(self.interface)
                    self.status("ready", info)
                    self.log(f"{self.interface} is UP; RX silence alone does not trigger resets")
                    time.sleep(1)
            finally:
                self.stop_child()
                self.status("stopped")


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    if os.uname().release != config["kernel_release"]:
        print("Kernel changed; rebuild slcan before enabling this service", file=sys.stderr)
        return 78
    supervisor = Supervisor(config)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: setattr(supervisor, "running", False))
    supervisor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
