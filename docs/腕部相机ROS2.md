# 腕部相机 ROS2 采集与发布

程序位于 `~/robot_y1/y1_sdk-humble/src/y1_wrist_camera`，适用于本次 RK3588 / Ubuntu 22.04 / ROS2 Humble 部署。

## 启动

```bash
bash ~/robot_y1/script/start_wrist_camera.sh
```

保持终端运行，Ctrl+C 停止并释放相机；不启动机械臂、不发送机械臂指令，也不配置开机自启。USB 必须先可用。若设备不存在，检查 `board-usb-gpio-init.service`、接线和 `lsusb`。

默认参数：640×480、25 fps、YUYV 采集转换为 RGB8。使用 `/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._KD-USB_Cameras_KD-USB_Cameras-video-index0`，避免 video81 编号变化。若连接多台身份相同的相机，需要通过 `device` 显式指定经过核实的 by-path 设备路径。

```bash
bash ~/robot_y1/script/start_wrist_camera.sh --ros-args -p fps:=25 -p width:=640 -p height:=480
# 必要时指定设备：
bash ~/robot_y1/script/start_wrist_camera.sh --ros-args -p device:=/dev/video81
```

参数在启动时生效。其他分辨率不保证支持 YUYV 25 fps；驱动未接受指定分辨率或帧率时程序报错退出，不静默降帧。当前实现不使用 MJPEG；相机在 MJPEG 模式支持高分辨率 25 fps，不等于当前 YUYV 节点在该分辨率可用。

## 话题与时间戳

| 字段 | 值/语义 |
| --- | --- |
| 话题 | `/wrist_camera/image_raw` |
| 类型 | `sensor_msgs/msg/Image` |
| 编码 | `rgb8` |
| frame_id | `wrist_camera_optical_frame`（仅名称，不发布 TF） |
| QoS | SensorData：best effort、volatile、深度 5 |
| header.stamp | V4L2 帧时间戳由 CLOCK_MONOTONIC 换算到系统时间，单位秒+纳秒 |

本次驱动报告时间戳来源为 start-of-exposure。该值来自驱动，不是发布瞬间打点；不等同于经过标定的硬件同步时间。程序拒绝无法确定时钟域的帧；实际传感器曝光时刻精度未另行标定。需与关节数据同步时比较两路 header.stamp，并确认它们使用相同系统时钟。系统时间跳变可能影响跨消息同步；程序发现时间戳不递增时退出。真机模式要求 `use_sim_time=false`。

程序每收到一帧才发布一帧，不用重复旧帧凑 25 Hz；丢帧、USB 抖动或系统负载会影响实际频率。每约 5 秒打印发布频率、驱动序号缺口和错误帧计数；初始化首个窗口包含起流耗时。两秒无新帧或设备断开时明确报错退出，修复后重新运行。

## 查看、录制与验证

另开终端：

```bash
source ~/robot_y1/setup.bash
ros2 topic hz /wrist_camera/image_raw
ros2 topic echo /wrist_camera/image_raw --field header --once --qos-reliability best_effort
mkdir -p ~/robot_y1/records
ros2 bag record -o ~/robot_y1/records/wrist_camera_$(date +%Y%m%d_%H%M%S) /wrist_camera/image_raw
```

Bag 保存图像消息及其中的 header.stamp；本节点自身不写图像文件。订阅端需要使用兼容的 best-effort QoS。RGB 原始数据约 23 MB/s（640×480×3×25），长时录制前检查磁盘空间。本节点不提供标定后的 CameraInfo，也未将相机数据接入现有关节 HDF5 录制脚本。

## 构建

```bash
cd ~/robot_y1/y1_sdk-humble
source /opt/ros/humble/setup.bash
colcon build --packages-select y1_wrist_camera --executor sequential
```

依赖现有的 ament_cmake、rclcpp、sensor_msgs 和 Linux V4L2 头文件，不需要 OpenCV/cv_bridge。

## 本次实测：2026-10-29

- 相机：KD-USB Cameras（2c7f:4a60），UVC；驱动接受 YUYV 640×480 @ 25 fps。
- 在隔离 DDS domain 232 测试约 20 秒，订阅到 502 帧。
- 按帧时间戳测得 25.0003 Hz；按订阅到达时间测得 25.0060 Hz。
- 相邻帧时间戳间隔 39.915～40.121 ms，严格递增。
- 本机订阅收到消息时，相对帧时间戳的平均延迟 45.10 ms，最大 56.92 ms。
- 图像大小、编码和 frame_id 校验通过；节点报告驱动序号缺口 0、错误帧 0。
- 以上是短时本机单相机结果，不保证长时、跨网络或与其他负载并行时的表现。

测试脚本 `deploy/test_wrist_camera_ros2.py` 会自行启动节点、订阅检查并停止，只应在相机空闲时执行：

```bash
source ~/robot_y1/setup.bash
python3 ~/robot_y1/deploy/test_wrist_camera_ros2.py
```

报告：`deploy/wrist-camera-validation.json`；测试抓拍：`/tmp/y1-wrist-camera-rgb.ppm`。

时间戳依据：https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/buffer.html
