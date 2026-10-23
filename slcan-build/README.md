# SLCAN 模块构建记录

已在独立目录构建 `artifacts/slcan.ko`，并完成一次临时加载、接口建立和正常卸载测试。之后用户授权的系统安装已于 2026-10-23 20:07 完成：模块安装到 `/lib/modules/6.1.141/extra/slcan.ko`，CAN 服务已启动并启用自启。保留原系统内核镜像和厂家 SDK，未启动机械臂控制。此前 10 秒收帧测试没有接收到 CAN 帧，安装后当前 RX 也为 0，实际总线通信仍未验证。完整部署记录见上级目录的 `部署与运行.md` 和 `deploy/system-install.json`。

## 构建输入

- 目标：本机 `6.1.141 #106 SMP`，AArch64。
- 原始配置：`running-kernel.config`，从本机 `/proc/config.gz` 保存。
- 公开源码：Rockchip `rockchip-linux/kernel`，固定提交 `7a7734a16bd5bc545ec614e5b57eaa6417416e4b`，源码版本为 `6.1.141`。
- 源码不是已确认的厂家原始构建版本。来源及压缩包 SHA-256 记录在 `source.json`。
- 编译器：Ubuntu GCC 10.3.0；原内核使用 Arm GCC 10.3.1。没有将两者表述为完全相同的工具链。
- GCC、flex、bison 的软件包仅解压到 `tools/`，没有执行系统软件包安装。包版本和校验值见 `tool-packages.json`。

## 复现构建

已有源码和工具解压完成后，在本目录执行：

```bash
bash build.sh
```

脚本根据原始配置执行 `olddefconfig` 和 `modules_prepare`，然后以外部模块方式只构建 SLCAN。构建配置中的 SLCAN 保留原值，使用命令行 `CONFIG_CAN_SLCAN=m` 选择该模块；不生成或安装替换系统内核的镜像。

`olddefconfig` 对公开源码不认识的厂家选项及工具链选项所做的调整，完整保存在 `logs/config-diff.txt`。CAN 基础配置、页面大小、SMP、抢占方式、模块版本检查和签名设置等已另行检查。

## 已完成的检查及限制

- 模块为 AArch64 ELF，`vermagic` 为 `6.1.141 SMP mod_unload aarch64`。
- `struct module` 所在节的大小与本机已加载、且 srcversion 与磁盘文件一致的三个 AIC 模块比较。
- 对全部导入符号，检查本机 kallsyms 可见性，并对部分数据符号补充已加载模块的导入证据。具体结果见 `artifacts/verification.json`。
- 完整构建日志在 `logs/`；模块属性在 `artifacts/modinfo.txt`。

**原内核的 `Module.symvers` 缺失，因此构建使用了 `KBUILD_MODPOST_WARN=1`，产生了未解析符号警告。** 这些警告保留在 `logs/module-build.log`，未伪造厂家符号表来消除它们。本机未启用 `CONFIG_MODVERSIONS`，但这不等于模块兼容性已得到保证。

本机未启用 `CONFIG_KALLSYMS_ALL`，数据符号不一定出现在 `/proc/kallsyms`。符号存在、版本标记相同、`struct module` 大小相同，都不能确认所有内核结构布局和函数 ABI 一致。该文件是供进一步验证的候选模块，不能宣称已在本机可用。

## 运行验证结果（2026-10-23 19:53，Asia/Shanghai）

运行验证包括加载候选模块、检查内核日志、连接 CANable 并确认 CAN 帧接收。这会开始执行新的内核代码，存在不兼容导致系统异常的风险，须与本次纯编译阶段区分。

用户已授权执行临时加载测试。由于当前账号需要 sudo 密码，由用户在本机终端执行了以下命令：

```bash
sudo bash /home/gs/robot_y1/slcan-build/test-load.sh
```

该脚本验证模块校验值和内核版本，拒绝接管已加载的 SLCAN、已有 CAN 接口或被占用的设备；随后临时加载模块、创建 `y1_can0`，最多等待 10 秒接收 5 帧。它不调用 `cansend`，不启动机械臂 SDK 或 ROS2 控制器。测试结束后只终止自己启动的 slcand，并尝试正常卸载本次加载的模块，绝不强制卸载。每次运行的日志和状态保存在 `logs/runtime-*/`。

实际日志目录：`logs/runtime-20261023-195343-M4SOlQ/`。已经确认：

- `insmod` 成功，SLCAN 线路规程 17 注册成功。
- `slcand` 成功连接 `/dev/imeta_y1_can0`，建立并启用 `y1_can0`。
- 测试期间新增内核日志没有 BUG、Oops、WARNING 或未知符号错误。
- 10 秒内 RX/TX 均为 0，没有收到 CAN 帧；通信结果为不确定。
- 本次 slcand、临时接口和模块均已清理，正常卸载成功。
- 测试没有启动 ROS2 控制器，没有发送 CAN 数据帧或机械臂运动命令。

汇总保存在 `artifacts/runtime-test-status.json`。`artifacts/verification.json` 是之前纯静态检查阶段的快照，其“尚未进行运行验证”描述不再代表最新状态，应结合运行记录阅读。此次结果证明已测试的加载/接口管理/卸载路径可工作，尚不能证明收发数据路径或长时间运行稳定。

优先采用临时加载进行验证，确认稳定前不写入模块自启配置。测试 CAN 收帧也不等于已验证机械臂控制；本次没有启动 ROS2 控制器或发送机械臂运动指令。

## 文件

- `artifacts/slcan.ko`：候选驱动模块，保留调试信息。
- `artifacts/slcan.ko.sha256`：模块校验值，在 artifacts 目录执行 `sha256sum -c slcan.ko.sha256` 检查。
- `artifacts/verification.json`：静态检查报告和未验证事项。
- `artifacts/public-source-exports.txt`：公开源码中可直接检索到的相关导出声明，仅作源码证据。
- `build.sh` / `verify.py`：构建、检查脚本，都不会安装或加载驱动。
- `test-load.sh`：需要本机 sudo 的临时加载、收帧测试和清理脚本，不配置自启。
- `source/` / `kernel-out/`：源码及构建准备文件。
- `downloads/` / `tools/`：原始下载文件及本地构建工具。

源码和构建工具总共约占 2 GB；它们保留用于复现和排查。

## 参考

- https://github.com/rockchip-linux/kernel/tree/7a7734a16bd5bc545ec614e5b57eaa6417416e4b
- https://docs.kernel.org/6.1/kbuild/modules.html
