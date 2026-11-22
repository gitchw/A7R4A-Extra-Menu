# 下一会话交接文档（Handoff）

**最后更新**：2026-11-22  
**目标机型**：Sony `ILCE-7RM4A`（固件 `Ver. 1.12`）  
**GitHub 公开仓库**：`https://github.com/gitchw/A7R4A-Extra-Menu`  
**上游参考 Issue**：`https://github.com/PCIEARIA/A6400-Extra-Menu/issues/11`

---

## 1. 当前已验证完成的状态

1. **冷启动脱机持久化已实机跑通**：
   - Stage 1（`stage1`：`/tmp/gpslogdiag.so` + `/tmp_bt` 解包 + 20s settle）：冷启动无 USB 静置 >60s 正常，`BOOT_ISOLATION_STAGE1_OK`。
   - Stage 2（`stage2`：加 `SCHED_FIFO prio 50` 提速的 `a7r4a-ui-load-guard.elf`）：`freeze_ms=63..88`, `resume_ms=6..14`, `total_ms=120..138 ms`，菜单 `C1+C2` 正常弹出，`BOOT_ISOLATION_STAGE2_OK`。
   - Stage 3（`full`：加 `a6400-film-capture-daemon.elf` + 修复 `报错 21` 后的最终构建）：已刷入实机并经用户独立冷启动拍摄验证通过（此前已独立产出 61MP `9504×6336` `F7996291.JPG` 及多张缩放成片，修复版刷机后测试 OK）。
2. **代码与测试基线**：
   - 主机端 7 个核心测试套件（`test_film_native_command.py`、`test_film_capture_daemon.py`、`test_native_capture_probe.py`、`test_recorder_storage.py`、`test_film_runtime_package.py`、`test_a7r4a_loader_pack.py`、`test_a7r4a_boot_preload.py`）合计 **208 项全部通过**。
   - `dist_a7r4a/` 全量 21 个产物已用 `build_all_a7r4a.sh` 重新编译且 `SHA256SUMS` 21/21 校验通过。
   - 公开树规范检查 `python3 tools/check_public_tree.py`（检查无私有绝对路径、无二进制、无凭据）。

---

## 2. 本地工作区与外部依赖说明（未入库目录）

公开 Git 仓库仅包含源码、构建脚本、测试与文档；本地完整开发环境还依赖以下未入库目录（位于仓库外层工作目录）：

| 目录 | 用途 | 备注 |
| --- | --- | --- |
| `libs_112/` | 从 `ILCE-7RM4A Ver. 1.12` 固件提取的目标 `.so` / 符号参考 | 专有二进制，**严禁提交到公开仓库** |
| `camera_112_dumps/` | 固件解包与只读逆向分析原始数据 | 只读参考，**严禁修改或公开** |
| `toolchain/` | ARMv7 交叉编译工具链（`arm-linux-gnueabi-gcc` 等） | 编译 `host-tests/build_all_a7r4a.sh` 时使用 |
| `third_party/libjpeg-arm/` | ARMv7 静态/交叉编译版 `libjpeg` | `a7r4a-film-job.elf` 链接依赖 |
| `Sony-PMCA-RE/` | Sony USB Service Mode 通讯工具（含 `.venv`） | `autoboot_flash.sh` 与 `phase6_ram_e2e.sh` 依赖 |
| `dist_a7r4a/` | `build_all_a7r4a.sh` 生成的已签名分发产物（~1 MB） | `.gitignore` 已忽略；需要刷机时重编即可 |
| `A6400-Extra-Menu/`、`upstream_v3_ref/` | 上游参考代码 | 只读参考，**严禁修改** |

---

## 3. 硬件调试注意事项（踩坑必读）

1. **USB Service Mode 断线机制**：A7R4A 每次 `senserShellCommand` 结束（`setTerminalEnable(False)`）后有时会重置 USB 连接，需要物理拔插 USB 线或等待 MSC 重新枚举后才能进行下一条 USB 命令。
2. **真冷启动必须拔电池**：仅拨动电源开关可能未彻底清除内存状态，务必 **先拔 USB → 关机 → 拔出电池等 5 秒 → 装回电池 → 无 USB 开机**，并在开机后等待约 45–50 秒让 `boot.sh` 完成注入。
3. **macOS `sudo` 与移动盘卸载**：Mac 上 libusb 访问需要 `sudo` 权限，且在切换 Service Mode 前必须卸载 Sony MSC 磁盘（`autoboot_flash.sh` 与 `phase6_ram_e2e.sh` 已内置自动卸载逻辑）。
4. **61MP 耗时与相机休眠冲突**：`Full 61MP` 单张渲染约 88–105 秒；若相机「省电开始时间」为默认的 1 分钟，渲染中途休眠会卸载 `/dev/mmcb` 触发 `before_send` 失败。实拍 61MP 时需调长休眠时间（≥ 5 分钟），日常快拍推荐 `Fast 4MP` / `Medium 15MP`。
5. **紧急恢复指令**：若实验修改导致冷启动异常，拔电池重启后插 USB 运行：
   ```sh
   sudo ./scripts/autoboot_flash.sh uninstall
   ```

---

## 4. 后续可继续优化的方向

1. **`dist_a7r4a/runtime_pkg/` 自动同步**：当前 `build_all_a7r4a.sh` 构建主产物目录 `dist_a7r4a/`，若需使用旧版 `runtime_pkg` 打包布局，可把 `host-tests/build_film_runtime_package.py` 集成进 `build_all_a7r4a.sh` 末尾自动同步。
2. **GitHub Release 发布预编译包**：目前仓库按 `check_public_tree.py` 规范只跟踪源码，可在 GitHub Releases 页面上传带 `SHA256SUMS` 的 `dist_a7r4a` 发布包，方便无交叉编译环境的用户直接使用 `autoboot_flash.sh`。
3. **15MP / 61MP 渲染性能优化**：进一步利用 Cortex-A5 `vfpv4` 向量化或调优 LUT/光晕卷积步长，缩短 `Full 61MP` 耗时（目前 ~90–104s），争取压进 60s 默认休眠窗口内。
4. **多卡槽并发与休眠唤醒提醒**：在菜单内提示当前省电时长或双卡槽状态，进一步提升长期挂机体验。
