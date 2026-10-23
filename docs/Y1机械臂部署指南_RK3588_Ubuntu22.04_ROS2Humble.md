# Y1 机械臂部署与运行指南

适用环境：RK3588 / Ubuntu 22.04 / ROS2 Humble / ARM64

整理日期：2026-10-23

本文记录当前板卡的实际适配方法，沿用 `y1_sdk-humble` 和厂家提供的 SDK 动态库。普通控制、示教、录制和回放均通过该 SDK 完成。

## 一、适用环境与通信链路

| 项目 | 本机配置 |
| --- | --- |
| 板卡 | Rockchip RK3588 EVB7 V11 Board |
| 系统 | Ubuntu 22.04.5 LTS，ARM64 |
| ROS | ROS2 Humble |
| 当前内核 | `6.1.141 #106 SMP` |
| SDK 工作空间 | `~/robot_y1/y1_sdk-humble` |
| USB 转 CAN | CANable，串口/SLCAN 固件 |
| USB 标识 | Vendor `ad50`，Product `60c4` |
| 本机 CANable 序列号 | `003B00394B57531820383832` |
| 稳定设备链接 | `/dev/imeta_y1_can0` |
| SocketCAN 接口 | `y1_can0` |
| CAN 波特率 | 1 Mbps，`slcand -s8` |
| 机械臂末端类型 | `3`：夹爪与示教器二合一 |

硬件链路：

```text
RK3588 USB → CANable → CAN_H / CAN_L → Y1 机械臂
```

软件链路：

```text
ROS2 控制程序
    ↕
Y1 SDK
    ↕
SocketCAN 接口 y1_can0
    ↕
slcan 内核驱动 + slcand
    ↕
/dev/imeta_y1_can0 → /dev/ttyACM0
    ↕
CANable → 机械臂
```

SocketCAN 是 Linux 管理 CAN 总线的软件接口，不要求通过以太网、Wi-Fi 或互联网连接机械臂。

## 二、原部署文档在本机上的问题

原文档验证的是 `6.1.43-rt-rockchip-rk3588` 内核，而本机运行的是 `6.1.141`。

本机原始配置为：

```text
# CONFIG_CAN_SLCAN is not set
```

因此直接运行以下命令会失败：

```bash
sudo modprobe slcan
```

原始报错：

```text
modprobe: FATAL: Module slcan not found in directory /lib/modules/6.1.141
```

另外还有几处需要适配：

- 厂家 `set_only_one_can.sh` 截断 USB 序列号，规则无法匹配实际设备。
- CAN 接口统一使用 `y1_can0`，避免与其他接口混用。
- 本机真正的 SDK 工作空间在 `~/robot_y1/y1_sdk-humble`，原文部分环境加载路径不适用。
- 普通控制和示教配置都需要指定本机 CAN 接口及末端类型。
- `src` 配置修改后需要同步到 `install`，launch 实际读取安装目录。
- 仓库旧录制/回放脚本使用 ROS1 的 `rospy`，本流程使用补齐的 ROS2 工具。

本次处理方式：单独构建并补装 `slcan.ko`，保留原系统内核镜像和厂家 SDK。

## 三、配套目录与文件

本文的安装、启动、录制和回放命令依赖适配后的文件。迁移时需要同时保存这些文件，单独复制本文不会生成脚本或驱动模块。

```text
~/robot_y1/
├── setup.bash
├── y1_sdk-humble/                 # 厂家 SDK 工作空间，包含已适配的配置
├── slcan-build/
│   ├── artifacts/slcan.ko         # 本次构建并测试的驱动模块
│   ├── build.sh                   # 在已准备的源码/工具环境中复现编译
│   └── test-load.sh               # 临时加载测试脚本
├── deploy/
│   ├── system/
│   │   ├── imeta_y1_can.rules
│   │   ├── y1-can.json
│   │   ├── y1-can.service
│   │   └── y1-can-watchdog.py
│   ├── backups/
│   ├── system-install.json        # 安装后生成
│   └── validation.json
├── script/
│   ├── install_system.sh
│   ├── configure_udev.sh
│   ├── check_setup.sh
│   ├── check_setup.py
│   ├── start_can.sh
│   ├── start_driver.sh
│   ├── start_teach.sh
│   ├── start_mode.sh
│   ├── record_demonstration.py
│   ├── replay_episode.py
│   ├── replay_limits.json
│   └── trajectory_io.py
├── logs/
└── records/
```

本指南中的模块是为当前板卡环境构建并测试的候选模块，不能仅凭另一台设备也显示 `6.1.141` 就认定完全兼容。更换内核或板卡系统后，需要重新确认兼容性。

更换 CANable 时，必须使用新设备的完整序列号，并同步调整 udev 规则、部署配置及脚本中的设备身份检查；不能继续套用上表的序列号。

## 四、首次部署

当前这台机器已经完成安装。本节用于记录部署过程，日常运行直接看“普通控制”和“示教”部分。

### 4.1 检查基础环境

```bash
uname -r
uname -m
ls /dev/ttyACM*
```

本机对应输出应包括：

```text
6.1.141
aarch64
/dev/ttyACM0
```

USB 拔插后可能变成 `ttyACM1` 等名称，因此后续统一使用按序列号创建的 `/dev/imeta_y1_can0`。

### 4.2 安装基础依赖

需要从未准备过的系统重新部署时执行：

```bash
sudo apt update
sudo apt install -y \
  can-utils net-tools iproute2 psmisc \
  libgoogle-glog-dev libnlopt-cxx-dev \
  ros-humble-kdl-parser liborocos-kdl-dev \
  python3-yaml python3-numpy python3-h5py
```

本机基础 SDK 依赖已经安装，HDF5 依赖也已由系统安装脚本补齐。

### 4.3 编译 SDK 工作空间

沿用厂家指定的 `y1_sdk-humble` 包。若工作空间已编译且检查通过，可以跳过重新编译。

```bash
cd ~/robot_y1/y1_sdk-humble
source /opt/ros/humble/setup.bash
colcon build --packages-up-to y1_controller y1_description --executor sequential
```

环境加载统一使用：

```bash
source ~/robot_y1/setup.bash
```

该文件会依次加载 ROS2 Humble 和 `~/robot_y1/y1_sdk-humble/install/setup.bash`。

### 4.4 配置 USB 设备链接

已有 `/dev/imeta_y1_can0` 且指向正确设备时，可以直接进入下一步。

未配置时执行：

```bash
bash ~/robot_y1/script/configure_udev.sh
```

脚本会检查完整序列号、备份原规则、写入新规则并重载 udev。

本机规则位于：

```text
/etc/udev/rules.d/imeta_y1_can.rules
```

规则内容：

```udev
SUBSYSTEM=="tty", ATTRS{idVendor}=="ad50", ATTRS{idProduct}=="60c4", ATTRS{serial}=="003B00394B57531820383832", SYMLINK+="imeta_y1_can0", GROUP="dialout", MODE="0660", ENV{ID_MM_PORT_IGNORE}="1", ENV{ID_MM_DEVICE_IGNORE}="1"
```

验证：

```bash
ls -l /dev/imeta_y1_can0
udevadm info --query=property --name=/dev/imeta_y1_can0
```

应看到链接指向实际 `ttyACM` 节点，以及完整的 `ID_SERIAL_SHORT`。ModemManager 忽略属性只作用于此 CANable，不停止其他设备的 ModemManager 服务。

### 4.5 安装 SLCAN 与 CAN 自启服务

先停止正在运行的 Y1 控制器，再在本机终端执行：

```bash
sudo bash ~/robot_y1/script/install_system.sh
```

安装器会完成：

1. 检查内核版本、模块校验值和 USB 设备身份。
2. 检查是否存在 Y1 控制器或非本服务管理的 slcand，避免重配正在使用的总线。
3. 将即将覆盖的系统文件备份到 `deploy/backups/system-*/`。
4. 按需补齐 `python3-h5py`。
5. 安装 `slcan.ko`、更新模块索引并加载模块。
6. 安装 udev 规则、CAN 配置、守护程序和 systemd 服务。
7. 启用 CAN 开机自启，等待 `y1_can0` 就绪。
8. 写入 `deploy/system-install.json`。

安装成功时应看到：

```text
enabled
active
y1_can0          UP             <NOARP,UP,LOWER_UP>
[成功] 系统适配已安装。尚未启动机械臂，也尚未验证电机反馈。
```

**开机只自动启动 CAN，不自动启动或使能机械臂。** 原内核镜像保持不变。

### 4.6 完成启动前检查

```bash
bash ~/robot_y1/script/check_setup.sh
```

检查内容包括：

- ROS2、Y1 消息、NumPy 和 HDF5 Python 依赖。
- 普通控制与示教的源码/安装目录配置是否一致。
- 控制器二进制和动态库依赖。
- 带夹爪 URDF。
- CAN 服务、自启状态、SLCAN 模块和 `y1_can0` 状态。

本机已通过 13 项启动前检查。

`RX=0` 会显示为信息，表示尚无收帧证据；它不能证明电机连接正常，也不能单独证明驱动有问题。

## 五、SDK 关键配置

### 5.1 普通控制

源码配置：

```text
~/robot_y1/y1_sdk-humble/src/y1_controller/config/single_arm_control.yaml
```

内容：

```yaml
right_normal_arm:
  ros__parameters:
    arm_can_id: y1_can0
    arm_feedback_rate: 200
    arm_control_type: normal_arm
    arm_end_type: 3
    auto_enable: true
    arm_end_pose_control_topic: /master_arm_right/end_pose_control
    arm_joint_position_control_topic: /master_arm_right/joint_states
    arm_joint_state_topic: /puppet_arm_right/joint_states
    arm_status_topic: /puppet_arm_right/arm_status
```

### 5.2 示教 / 重力补偿

源码配置：

```text
~/robot_y1/y1_sdk-humble/src/y1_controller/config/one_master.yaml
```

内容：

```yaml
right_master_arm:
  ros__parameters:
    arm_can_id: y1_can0
    arm_feedback_rate: 400
    arm_control_type: leader_arm
    arm_end_type: 3
    auto_enable: true
    arm_joint_state_topic: /master_arm_right/joint_states
    arm_status_topic: /master_arm_right/arm_status
```

`auto_enable: true` 表示明确执行控制器启动命令时会使能机械臂。

### 5.3 修改后同步到 install

本工作空间使用普通安装，修改 `src` 后需重新构建或同步文件：

```bash
cd ~/robot_y1/y1_sdk-humble

cp src/y1_controller/config/single_arm_control.yaml \
  install/y1_controller/share/y1_controller/config/

cp src/y1_controller/config/one_master.yaml \
  install/y1_controller/share/y1_controller/config/
```

本次已同步这两个配置，并修复对应 launch 文件，使 `params_file` 参数实际传入节点。

## 六、普通控制：日常启动流程

### 6.1 启动驱动

机械臂上电，确认接线、终端电阻及周围运动空间后执行：

```bash
bash ~/robot_y1/script/start_driver.sh
```

保持该终端运行。启动器会等待 CAN、检查重复实例，随后前台启动：

```bash
ros2 launch y1_controller single_arm_control.launch.py
```

统一使用启动器可以避免普通控制和示教同时运行。

运行日志保存到：

```text
~/robot_y1/logs/driver-normal-时间.log
```

### 6.2 检查真实关节反馈

另开终端：

```bash
source ~/robot_y1/setup.bash
timeout 10s ros2 topic echo /puppet_arm_right/joint_states --once
```

正常控制驱动初始化成功且关节状态持续回传，才算完成 SDK 到机械臂的端到端验证。

也可以被动检查总线：

```bash
timeout 10s candump -n 5 y1_can0
```

10 秒无数据时，`timeout` 可能返回 124。仅凭无数据不能确定是电源、接线、波特率，还是设备需要请求后才返回数据。

### 6.3 停止驱动

在驱动终端按 Ctrl+C。

停止前请托住机械臂。Ctrl+C 用于结束控制程序，不等同于硬件急停。

## 七、运动示例

以下命令会发送真实运动目标。先启动普通控制驱动，确认反馈正常，再按需执行；不要将本节三个命令当成部署步骤连续执行。

### 7.1 关节回零

```bash
bash ~/robot_y1/y1_sdk-humble/scripts/control_scripts/go_zero_position.sh
```

六个关节目标均为 0，夹爪目标为 0。

### 7.2 关节位置示例

```bash
bash ~/robot_y1/y1_sdk-humble/scripts/control_scripts/joint_position_control.sh
```

厂家示例目标：

```text
[0.6, -0.6, 0.6, 0.5, 0.4, 0]
```

### 7.3 末端位姿示例

```bash
bash ~/robot_y1/y1_sdk-humble/scripts/control_scripts/end_pose_control.sh
```

厂家示例目标：

```text
[0.0535, -0.0476, 0.3963, 0.1990, -0.4414, -1.0352]
```

末端目标的可达性需要实际确认。关节与夹爪示例的速度参数已设置为 1；当前厂家末端回调没有使用消息中的 `joint_velocity` 字段，不能据此保证末端运动也按该值限速。

## 八、示教与录制

### 8.1 切换到示教模式

先在普通控制终端按 Ctrl+C，再运行：

```bash
bash ~/robot_y1/script/start_teach.sh
```

该命令启动厂家重力补偿模式，对应：

```bash
ros2 launch y1_controller one_master.launch.py
```

普通控制与示教模式不能同时运行。进入示教前托住机械臂，确认反馈正常后再手拖。

### 8.2 录制 HDF5

另开终端：

```bash
source ~/robot_y1/setup.bash
python3 ~/robot_y1/script/record_demonstration.py
```

录制程序只订阅反馈，不发送控制命令。

- 默认订阅 `/master_arm_right/joint_states`。
- 从首帧有效反馈开始，默认最高录制频率 25 Hz。
- Ctrl+C 停止并保存。
- 10 秒没有首帧，或反馈中断超过 3 秒时停止。

指定录制 30 秒：

```bash
python3 ~/robot_y1/script/record_demonstration.py --duration 30
```

输出位置：

```text
~/robot_y1/records/demonstration_时间/dataset/episode_0.hdf5
```

## 九、回放 HDF5

### 9.1 切回普通控制

先在示教终端 Ctrl+C，再运行：

```bash
bash ~/robot_y1/script/start_driver.sh
```

### 9.2 先检查文件

另开终端：

```bash
source ~/robot_y1/setup.bash
python3 ~/robot_y1/script/replay_episode.py latest --dry-run
```

`--dry-run` 只检查文件，不连接 ROS，也不发送运动指令。

### 9.3 慢速回放最近一次录制

```bash
python3 ~/robot_y1/script/replay_episode.py latest --gear slow
```

### 9.4 交互选择文件

```bash
python3 ~/robot_y1/script/replay_episode.py
```

### 9.5 指定文件

将下面路径替换为实际文件：

```bash
python3 ~/robot_y1/script/replay_episode.py \
  ~/robot_y1/records/demonstration_实际时间/dataset/episode_0.hdf5 \
  --gear slow
```

回放程序会检查时间递增、数据维度、有限数及当前 URDF 的模型范围。示教录到的位置可能跨过位置控制的软限位，不能直接将其当成损坏数据而整段拒绝。

程序按已核对的当前 SDK 控制范围生成目标，并显示各关节被限幅的帧数和最大调整量，原始录制文件不修改。SDK 自身的目标限幅仍保留。超出 SDK 软限位的区段会停在边界，不能称为原样复现；若要求精确覆盖这些区段，需要由厂家确认控制模式和软限位策略，不能单纯扩大本脚本范围。

确认后先慢速移到限幅后的起点，收到到位反馈后再发送后续轨迹；反馈中断时停止继续发送目标。

| 挡位 | 相对录制时间的播放速率 | 关节/夹爪速度参数 |
| --- | --- | --- |
| `slow`，默认 | 0.25 倍 | 1 |
| `normal` | 0.5 倍 | 3 |
| `fast` | 1 倍 | 5 |

首次实机使用慢速。Ctrl+C 停止后续目标发送，不等于对已经执行的运动执行硬件急停。

### 9.6 文件格式与单位

本工具使用 Y1 HDF5 v1 格式，包含：

```text
timestamps
observations/qpos
observations/qvel
observations/effort
observations/end_pose
```

前六项关节位置单位为弧度，第七项保留本版本 SDK 的原生夹爪行程值，范围为 0～100，不做 100→80 缩放。

旧头文件注释写的是 0～80 mm，但当前二进制的 `SetGripperStroke` 实际将输入限制到 0～100，实际示教反馈中也出现 90、100。因此本工具使用该版本原生标度，不仅凭旧注释推断量纲。`script/replay_limits.json` 绑定当前 SDK 动态库校验值，更换 SDK 后需重新核对。

原文档提到的历史 HDF5 文件及其录制程序未提供，因此不能保证格式相同。未识别格式会被拒绝回放，需要先确认字段和单位后再转换。

## 十、CAN 服务与故障恢复

### 10.1 查看状态

```bash
systemctl status y1-can --no-pager
systemctl is-enabled y1-can
cat /run/y1-can/status.json
ip -details -statistics link show y1_can0
```

查看服务日志：

```bash
sudo journalctl -u y1-can -n 50 --no-pager
```

### 10.2 手动恢复 CAN

先停止机械臂控制驱动，再执行：

```bash
sudo systemctl restart y1-can
```

恢复后重新检查服务，再启动机械臂控制驱动。

### 10.3 停止自启

先停止机械臂控制驱动，再执行：

```bash
sudo systemctl disable --now y1-can
```

重新启用：

```bash
sudo systemctl enable --now y1-can
```

### 10.4 自动恢复范围

守护程序只管理自己启动的前台 slcand，不使用全局 `pkill slcand`。

| 场景 | 处理方式 |
| --- | --- |
| 开机时 USB 未就绪 | 等待指定设备出现 |
| slcand 退出 | 重建本服务的子进程和接口 |
| USB 拔出、重新插入 | 等待设备并重建链路 |
| 接口消失 | 重建本服务的 slcand |
| 接口被设为 DOWN | 尝试重新拉 UP |
| 驱动明确报告 BUS-OFF | 重建链路 |
| 守护程序退出 | systemd 重启服务 |
| RX=0 或总线安静 | 不据此反复重置总线 |

上述是服务实现的处理逻辑；本次没有实机注入所有故障场景，不将其全部标记为已实测。

USB 在线但 CANable 内部控制器卡住，不能仅凭接口 UP 可靠识别。可在停止机械臂驱动后手动重启服务，重新下发关闭、1 Mbps 配置和打开命令。

### 10.5 常见现象

| 现象 | 检查方法 |
| --- | --- |
| `Module slcan not found` | 检查当前内核版本、模块安装目录及是否完成安装器步骤 |
| `/dev/imeta_y1_can0` 不存在 | 检查 USB、完整序列号和 udev 规则 |
| 服务 active，但没有 `y1_can0` | 查看 `/run/y1-can/status.json` 和服务日志，检查设备占用或接口冲突 |
| `y1_can0` UP，但 RX=0 | 检查机械臂电源、接线、终端电阻、总线参数和反馈触发条件 |
| `Failed connect to J...` | 电机未按预期响应；不能仅凭接口 UP 排除物理链路问题 |
| `bitrate 0` | 本流程用 `slcand -s8` 设置设备；不能单凭内核显示值认定实际波特率错误 |
| 正常控制/示教无法同时启动 | 这是启动器的互斥机制；先停止另一模式 |
| 输出“目标限幅” | 原始示教位置超过 SDK 控制软限位；回放目标已限幅，超出区段不能原样复现 |
| 提示 HDF5 格式不支持 | 先确认历史文件字段、单位及格式版本，不直接强行回放 |

`ERROR-ACTIVE` 是 CAN 状态名称，不等于 Linux 驱动报错，也不能单独证明电机已连通。

## 十一、系统文件与 SLCAN 构建记录

### 11.1 安装位置

| 文件 | 用途 |
| --- | --- |
| `/lib/modules/6.1.141/extra/slcan.ko` | SLCAN 内核模块 |
| `/etc/udev/rules.d/imeta_y1_can.rules` | USB 稳定命名与 ModemManager 忽略规则 |
| `/etc/y1-can/config.json` | 本机 CAN 参数 |
| `/usr/local/libexec/y1-can-watchdog.py` | CAN 守护程序 |
| `/etc/systemd/system/y1-can.service` | 开机自启服务 |
| `/run/y1-can/status.json` | 当前运行状态 |

### 11.2 systemd 服务

```ini
[Unit]
Description=Y1 CANable SocketCAN supervisor
After=systemd-udevd.service
ConditionKernelVersion=6.1.141
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStartPre=/usr/sbin/modprobe slcan
ExecStart=/usr/bin/python3 /usr/local/libexec/y1-can-watchdog.py /etc/y1-can/config.json
Restart=always
RestartSec=3
RestartPreventExitStatus=78
KillMode=control-group
TimeoutStopSec=10
RuntimeDirectory=y1-can
RuntimeDirectoryMode=0755
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

服务限定当前内核版本。更换内核后会跳过启动，需先为新内核重新适配模块。

### 11.3 模块来源

- 公开源码：`rockchip-linux/kernel`。
- 固定提交：`7a7734a16bd5bc545ec614e5b57eaa6417416e4b`。
- 源码版本：Linux 6.1.141。
- 配置来源：本机 `/proc/config.gz`。
- 构建方式：准备构建环境后，单独构建 SLCAN 模块，不安装新内核镜像。
- 模块标记：`6.1.141 SMP mod_unload aarch64`。

源码链接：https://github.com/rockchip-linux/kernel/tree/7a7734a16bd5bc545ec614e5b57eaa6417416e4b

已准备的源码与本地构建工具保留在 `slcan-build/`。在这套完整构建目录内可执行：

```bash
bash ~/robot_y1/slcan-build/build.sh
```

该命令只用于复现构建，不安装或加载模块；它依赖目录内已经准备好的源码、原始配置和工具文件，不是面向任意新系统的一键构建器。

本次模块 SHA-256：

```text
c8390e77cdb9d19b05a2c04eade7fa91d9157c3bf5c7db54e44789169aeb909d
```

原厂家 `Module.symvers` 未提供，构建时保留了符号检查警告。后续实际加载、线路规程注册、接口建立和正常卸载已通过，但完整数据收发和长期运行稳定性尚未完成验证。

## 十二、部署验收记录

截至 2026-10-23 本次部署核验时：

| 验收项目 | 结果 |
| --- | --- |
| 单独编译 SLCAN | 已完成 |
| 临时加载与线路规程注册 | 已通过 |
| 建立并启用 CAN 接口 | 已通过 |
| 正常清理、卸载模块 | 已通过 |
| 安装模块及 CAN 开机自启配置 | 已完成 |
| 当前 CAN 服务 | active，enabled |
| 当前接口 | `y1_can0`，UP |
| SDK 动态库、配置和 launch 检查 | 已通过 |
| 13 项启动前检查 | 全部通过 |
| HDF5 文件及轨迹校验测试 | 已通过 |
| ROS2 模拟反馈录制 | 隔离测试域中录制并读回 13 帧 |
| 真实电机反馈 | 尚未验证，部署核验时 RX=0 |
| 机械臂实际运动、手拖与回放 | 尚未验证 |
| 重启、USB 拔插等故障注入 | 尚未实测 |

系统安装于 2026-10-23 20:07 完成。安装记录、验证报告及备份分别位于：

```text
~/robot_y1/deploy/system-install.json
~/robot_y1/deploy/validation.json
~/robot_y1/deploy/backups/
```

后续实机验收应确认：普通控制器初始化成功、关节状态持续回传，再依次验证低速关节运动、示教和慢速回放。

## 十三、日常命令速查

正常控制：

```bash
bash ~/robot_y1/script/start_driver.sh
```

示教：先停止普通控制，再执行：

```bash
bash ~/robot_y1/script/start_teach.sh
```

录制：示教运行期间，另开终端执行：

```bash
source ~/robot_y1/setup.bash
python3 ~/robot_y1/script/record_demonstration.py
```

回放：先停止示教、启动普通控制，再另开终端执行：

```bash
source ~/robot_y1/setup.bash
python3 ~/robot_y1/script/replay_episode.py latest --gear slow
```

只读检查：

```bash
bash ~/robot_y1/script/check_setup.sh
```

## 十四、回放校验修订记录（2026-10-23）

原版 Python 检查对文档限位严格逐帧拒绝，不适合直接处理手拖反馈。已修正为“URDF 检查录制数据、SDK 软限位约束发送目标”，并输出可见的限幅摘要。该版本通过 7 项离线测试及两份实机录制的纯检查回放；原始 HDF5 校验值不变，修订验证没有发送运动命令。
