# Y1 机械臂：部署、示教录制与回放

统一从仓库根目录 `~/robot_y1` 执行命令。支持当前使用的 RK3588 载板、Ubuntu 22.04、ROS2 Humble、aarch64 内核 **6.1.141**、SLCAN 固件 CANable（`ad50:60c4`）和 Sonix 腕部相机（`2c7f:4a60`）。

GPIO142 为 USB 供电、GPIO118 为复位的配置只适用于已验证载板，不能仅凭“RK3588”型号套用到其他载板。内核变化时需重新编译 SLCAN 模块，见 [模块构建说明](slcan-build/README.md)。本仓库继续使用厂商预编译 SDK。

## 新设备首次部署

先安装好 Ubuntu 22.04 和 ROS2 Humble，接好机械臂电源、CAN 转接板和相机：

```bash
git clone https://github.com/DongquanLi/orsus_arm.git ~/robot_y1
cd ~/robot_y1
bash y1.sh setup
```

以普通用户运行，脚本在需要时调用 sudo；首次加入 video/dialout 组后需重新登录。它安装依赖（含 glog、NLopt、HDF5、相机依赖），配置 USB 开机初始化，自动发现 CANable 并生成完整序列号绑定规则，安装 CAN 服务，然后编译 4 个 ROS2 包并执行部署检查。

只有一块支持的 CANable 时自动选择；多块时使用 `bash y1.sh setup --serial 完整序列号`。其他 VID/PID 或原生 SocketCAN 固件不属于当前自动适配范围。

**开机只初始化 USB 和 CAN，不启动控制器、不使能电机。** GPIO 初始化每次开机执行一次，重复启动服务不会再次拉低复位脚。启动时等待 GPIO 节点；失败记录错误并由 systemd 重试。系统配置覆盖前会备份到 `deploy/backups/` 和 `/var/backups/usb-gpio-autostart-*/`。

只补装 USB 自动初始化：

```bash
cd ~/robot_y1
bash y1.sh usb-init
```

## 检验 USB、相机和机械臂通信

```bash
cd ~/robot_y1
bash y1.sh usb --camera-test
bash y1.sh check
bash y1.sh verify-arm
```

- `usb` 保存 USB 设备清单、拓扑、内核日志及 CAN 状态到 `logs/usb-时间/`。`--camera-test` 读取 640×480 MJPG、25 Hz、250 帧，20 秒超时；先停止已有相机程序。
- `check` 检查库、构建产物、CAN 服务和接口，不启动机械臂。
- `verify-arm` 在电机禁用状态下运行 SDK 30 秒，检查反馈、禁用状态和通信错误；不发送运动目标。需先停止其他机械臂驱动。
- 枚举成功、接口 UP、相机实际取流、CAN 电机反馈是不同证据。空闲 CAN 没有 RX 不等于端口损坏。

逐个换口后重跑 USB 检查，才能确认对应端口。保存第一次输出的日志目录，再拔插并比较：

```bash
bash y1.sh usb --compare logs/usb-第一次检查的时间
```

不能仅凭两个已连接设备判断所有端口正常。`ttyUSB*` 可能是蜂窝模块；按 VID/PID、序列号和拓扑辨认，不能只看设备节点名称。没有权限读取 dmesg 时，日志会记录权限错误，可另执行 `sudo dmesg`。

## 日常快捷命令

均在 `~/robot_y1` 下执行，无需手工 source：

| 命令 | 用途 |
|---|---|
| `bash y1.sh record` | 慢速回零 → 零重力 → 开始记录 → 停止时自动裁剪 |
| `bash y1.sh replay` | 回放最新轨迹，自动启动并管理 control 驱动 |
| `bash y1.sh replay records/.../dataset/episode_0.hdf5` | 回放指定文件 |
| `bash y1.sh replay latest --dry-run` | 仅校验轨迹，不启动驱动或运动 |
| `bash y1.sh control` | 启动正常控制，供外部遥操作程序发布目标 |
| `bash y1.sh teach` | 单独启动零重力手拖模式 |
| `bash y1.sh camera` | 发布 25 Hz 腕部相机图像及帧时间戳 |

同一时间只运行一个机械臂驱动。`record`、`replay` 自带驱动，不要另开 `control` 或 `teach`；程序检测占用后会拒绝启动，不会终止其他程序。

### 录制

```bash
cd ~/robot_y1
bash y1.sh record
```

启动后会使能电机并让六关节慢速回零，夹爪保持当前开度。反馈确认到位并静止 1 秒后，在同一个驱动进程内切换重力补偿，确认新反馈后才开始记录。回零超时、状态异常或通信中断会停止流程。

看到“开始录制”后进行手拖示教；`Ctrl+C` 停止并保存，同时退出本次驱动并请求禁用电机，结束前托住机械臂。默认记录频率 25 Hz，可用 `--rate` 设置采样上限；不保证任意设置都能达到实际采样率。

文件位于 `records/demonstration_时间/dataset/episode_0.hdf5`。根据实际关节/夹爪运动检测裁剪开头空白，保留约 3 秒起步等待；中间停顿和尾部保留。裁剪前的完整文件保存在同目录 `raw/episode_0.hdf5`。静止或无明确运动的记录不裁剪；重复裁剪不会重复覆盖原件。`--no-trim` 关闭自动裁剪。

如需订阅已有示教驱动，可用 `bash y1.sh record --record-only`；这种模式不会自行回零或切换重力补偿。

### 回放

先停止录制，再运行：

```bash
cd ~/robot_y1
bash y1.sh replay
```

**不需要另一个终端启动 control。** 新入口先验证文件，再自动启动正常控制驱动。按提示确认空间后，机械臂慢速到轨迹起点，再等待约 3 秒开始动作。结束或 Ctrl+C 会退出它创建的驱动并请求禁用电机，请托住机械臂。

默认档位为 `normal`；沿用 SDK 流程的档位定义：

| 档位 | 轨迹时间轴倍率 | SDK 速度参数 |
|---|---:|---:|
| slow | 0.25× | 1 |
| **normal（默认）** | **0.5×** | **3** |
| fast | 1.0× | 5 |

因此 `normal` 是本项目的正常档，**不是示教原时长的 1×**。可用 `--gear fast` 选择 1× 时间轴；实际到位还受 SDK 和机械臂限制。`--lead-in 3` 控制起点到位后的等待，独立于档位。超出 SDK 控制软限位的目标会限幅并打印提示，原始 HDF5 不修改。

旧命令 `python3 script/replay_episode.py latest` 仍可使用，但它只发布目标，需要另一个终端先启动 `bash y1.sh control`，并先 `source ./setup.bash`。日常推荐新的一键入口。

### 遥操作与相机

`bash y1.sh control` 提供 `/master_arm_right/joint_states` 位置目标入口和 `/puppet_arm_right/joint_states` 反馈。**控制驱动本身不是键盘/主臂遥操作发布器**；外部遥操程序需在相同 ROS_DOMAIN_ID 下发布 `y1_msg/ArmJointPositionControl`。单臂手拖示教直接使用 `record`。

相机命令在独立终端运行，发布 `/wrist_camera/image_raw`，`sensor_msgs/Image.header.stamp` 含该帧时间戳。当前 HDF5 录制器保存机械臂状态，**没有把相机帧同步写入 HDF5**。见 [相机说明](docs/腕部相机ROS2.md)。

## 日志与故障排查

```bash
cd ~/robot_y1
systemctl status board-usb-gpio-init y1-can --no-pager
journalctl -u board-usb-gpio-init -u y1-can -b --no-pager
cat /run/y1-can/status.json
```

`No buffer space available` 表示 CAN 发送路径队列无法及时消化，不能直接断言硬件上限或端口损坏。换 USB 路径后复测，保留拓扑和 SDK 日志；不要通过重复启动 slcand 或无限增大发送队列掩盖持续拥塞。

`.148`、`.149` 已有禁用状态通信和相机验证，`.70` 曾出现 USB 枚举失败；详细边界见 [整合与验证记录](docs/整合与验证记录.md)。开机配置的启用状态和当前运行状态已检查，整机重启、真实自动回零/新回放入口运动仍需现场验证。

## 开发验证与推送

```bash
cd ~/robot_y1
source ./setup.bash
python3 deploy/test_adaptation.py
python3 deploy/test_trim_episode.py
python3 deploy/test_workflow.py
python3 deploy/test_gpio_init.py
python3 deploy/test_acquisition_ros2.py
python3 deploy/test_recording_ros2.py
```

以上为离线或隔离 ROS2 模拟测试；真实禁用通信使用 `bash y1.sh verify-arm`，相机集成测试使用 `python3 deploy/test_wrist_camera_ros2.py`。

配置 GitHub 凭据后，检查 `git diff` 和 `git status` 再提交推送。`records/`、`logs/`、构建目录、机器安装回执和备份均不入库；不在仓库内保存 SSH 密码、令牌或私钥。
