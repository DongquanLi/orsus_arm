# RK3588 / Ubuntu 22.04 / ROS2 Humble 部署指南

部署步骤现统一维护在 [根目录 README](../README.md)，避免新旧手工 CAN 命令与 systemd 服务并用。

1. 对照 README 确认载板 GPIO142/118、内核 6.1.141 和 CANable 固件。
2. 克隆到 ~/robot_y1，普通用户运行 bash y1.sh setup。
3. 运行 bash y1.sh usb --camera-test，逐个换口复测，日志自动保存。
4. bash y1.sh check 检查部署；bash y1.sh verify-arm 验证禁用状态通信。
5. bash y1.sh record 示教；停止后 bash y1.sh replay 一键回放。

内核适配见 [SLCAN 构建说明](../slcan-build/README.md)。本次工程整合和测试边界见 [整合记录](整合与验证记录.md)。
