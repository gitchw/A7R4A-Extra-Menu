# A7R4A Extra-Menu (`ILCE-7RM4A Ver. 1.12`) 技术架构与移植文档

本文档记录 `A7R4A-Extra-Menu` 从 `A6400-Extra-Menu`（V3.9 SSLF）移植到 **Sony ILCE-7RM4A（固件 Ver. 1.12）** 的完整技术细节、平台差异、内存约束及实机排障方案。

---

## 1. 目标平台特征（`ILCE-7RM4A Ver. 1.12`）

通过只读探针与相机壳层实测确认的关键硬件/内核参数：

| 属性 | 实测值 | 对移植的影响 |
| --- | --- | --- |
| **CPU 架构** | ARMv7 Cortex-A5 × 4 (`0xc05`, `351.43 BogoMIPS`)，支持 `vfpv4`，**无 NEON** | 交叉编译必须使用 `-march=armv7-a -mfpu=vfpv4`（禁止 NEON 指令） |
| **Linux 内核** | `3.0.27_nl-rt106+ #1 SMP PREEMPT RT armv7l` | 开启 `PREEMPT_RT` 实时抢占调度；`tmpfs` 不支持 `FALLOC_FL_PUNCH_HOLE`（`errno=27`） |
| **系统总内存** | `MemTotal: 128796 kB`（~125.7 MiB），空闲+可回收约 64.6–69.6 MiB | 无法整张 61MP RGB 驻留内存，必须采用**流式分块编码/解码 + 严格预算控制** |
| **用户态环境** | glibc `2.4` / `2.7`，主进程 `im.elf` 开启 **ASLR**（PIE / 动态库随机基址） | 所有 Hook 地址必须基于 `/proc/<pid>/maps` 动态解析的模块 RVA |
| **闪存与 `/tmp`** | `/tmp` 为 NAND VFAT（`nflasha10`），`/etc` 为可写分区（剩余约 6.4 MiB） | 严禁把大临时文件写入 `/tmp`；所有运行时解压与图像缓存统一放入 `/tmp_bt`（`tmpfs`） |
| **冷启动触发入口** | `/usr/bin/startBTCore.sh` + `im.elf` 加载 `/tmp/gpslogdiag.so`（受 Backup `0x00a10011=01` & `0x00a1000b=0000` 控制） | 无需修改 `/etc/ld.so.preload`，避免污染全局进程导致变砖风险 |

---

## 2. 整体系统架构

```text
冷启动开机 (无 USB)
  │
  ├─ Backup NVRAM (0x00a10011=01, 0x00a1000b=0000)
  │     └─ im.elf 启动约 21s 时 dlopen("/tmp/gpslogdiag.so")
  │           └─ a7r4a-boot.so (零依赖 freestanding DT_INIT_ARRAY)
  │                 └─ 后台 fork 执行 /etc/a7r4a_film/boot.sh
  │
  └─ /etc/a7r4a_film/boot.sh (支持 stage1 | stage2 | full)
        ├─ 挂载 /tmp_bt (tmpfs) 并解压 12 个 .gz 运行时文件
        ├─ 等待 settle=20s + im.elf 线程数稳定（threads=193）
        ├─ [stage2/full] a7r4a-ui-load-guard.elf install
        │     ├─ SCHED_FIFO prio 50 + 批量 PTRACE_ATTACH 冻结 im.elf (~138 ms)
        │     ├─ 注入 trampoline & dlopen("/tmp_bt/a7r4a-film-ui-clite.so")
        │     └─ 恢复原调度策略并 DETACH
        └─ [full] 启动 a6400-film-capture-daemon.elf watch (persistent=1)
              ├─ mpr_monio 轮询 DCIM 最新目录新增 JPEG
              ├─ 检测到新 JPEG 后派发 a6400-film-ram-job.sh apply
              │     ├─ IFSYS cp 读取原图 -> /tmp_bt/a6400-film-job/input.jpg (60 MiB 私有 tmpfs)
              │     ├─ a7r4a-film-job.elf 流式渲染 -> output.jpg (上限 56 MiB)
              │     ├─ 释放 input.jpg，执行 IFSYS absent 确认目标槽位空闲
              │     └─ IFSYS cp 回写 B:/A7R4A/TEST/F*.JPG -> flush -> readback SHA256 校验
              └─ 通过 UI IPC 在相机屏幕弹出进度与完成/失败 Toast
```

---

## 3. 核心移植与疑难排障记录

### 3.1 `PREEMPT-RT` 下冷启动死锁根因与 `SCHED_FIFO` 修复（`camera/native_freeze.h`）

- **现象**：早期冷启动阶段，`load-guard` 在无 USB 时频繁导致相机黑屏卡死；日志显示 `FREEZE` 阶段正常，但 `PHASE resume_ms` 高达 **15,869–35,623 ms**（15.8–35.6 秒）。
- **根因**：`im.elf` 在 `PREEMPT-RT` 内核下运行着多个 `SCHED_RR`（优先级 `2..8`）实时线程（如 `uipc_proxy_0..2`、`InfraOnDmdLdr`、`IMD-RM`）。原版 `load-guard` 以普通 `SCHED_OTHER`（优先级 0）逐个 `PTRACE_ATTACH`/`DETACH`，一旦恢复第一个 `SCHED_RR` 线程，调度器立刻抢占 `load-guard`，导致其余 190+ 线程持续冻结长达数十秒，`im.elf` 在实时任务间发生互斥锁死锁。
- **修复**：在 `camera/native_freeze.h` 中：
  1. 冻结前通过 `sched_setscheduler` 临时将 `load-guard` 提升至 `SCHED_FIFO` 优先级 `50`，并在 `freeze_resume()` 完成后恢复原策略；
  2. 先对所有线程批量发送 `PTRACE_ATTACH`，再统一 `waitpid` 等待 `SIGSTOP`。
- **效果**：实机冷启动 `freeze_ms=63..88`, `resume_ms=6..14`, **`total_ms=120..138 ms`**（提速超百倍），冷启动 100% 稳定无死锁。

### 3.2 61MP (`9504×6336`) 内存预算与编码调优（`camera/a7r4a_film_job.c`, `camera/film_ram_job.sh`）

- A7R4A `60M X.FINE` 单张 JPEG 体积达 `26–38 MB`，`MemTotal` 仅 `125.7 MiB`：
  - 将 `film_ram_job.sh` 的私有 `tmpfs` 上限从 `48 MiB` 提升至 **`60 MiB`**（`62914560`），输出与工作上限提升至 **`56 MiB`**（`58720256`）。
  - 在 `a7r4a_film_job.c` 中对全尺寸大图（`scale == 1 && w * h > 25,000,000`）采用 JPEG 质量 **`85`**（其余档位保持 `92`），实测将 `35 MB` 61MP 输入的输出控制到约 `16–20 MB`，避免 `input+output` 撑爆 60 MiB `tmpfs`。
  - 增加 `scale=1 → 2 → 4` 预算自适应回退：当超大照片导致可用内存不足以 `scale=1` 解码时自动降档，确保不发生 OOM 崩溃。

### 3.3 `mpr_monio` 64 位文件大小与异常文件名（`camera/native_capture_probe.c`, `camera/film_capture_daemon.c`）

- 64 GB ExFAT 卡上存在 `> 4 GiB` 文件（如固件/压缩包）及 macOS 隐藏文件（`.DS_Store`、`._*`）和 `.ARW` RAW 文件。
- 原版 `size` 使用 `uint32_t` 解析，遇到 >4 GiB 非 JPEG 文件直接报 `invalid_size` 导致基线扫描失败；且 `component()` 拒绝 `.` 开头文件名、`BASELINE_TIMEOUT` 仅 60s。
- **修复**：文件大小升级为 `uint64_t`（`%llu`）；允许非 `.`/`..` 的点开头文件在扫描阶段安全跳过，仅严格校验目标 `.JPG`；`BASELINE_TIMEOUT` 扩至 `600000 ms`，传输重试上限由 `6` 增至 `256`。

### 3.4 双卡槽动态盘符映射（`camera/film_native_command.c`）

- A7R4A 双卡槽情况下，Slot 2 存储卡在索尼原生 `IFSYS` 中的盘符为 **`B:/`**（而非 `A:/`）。
- `film_native_command.c` 在运行前读取 `im.elf` 进程内存中 `libInfraFsys.so` 的挂载表，按实际 `/dev/mmca` / `/dev/mmcb` 挂载情况动态改写 `IFSYS` 命令盘符，确保不向未挂载盘符盲目写入。

### 3.5 `报错 21`（`absence_unproven` -> `job_uncertain`）根因与双保险修复

- **现场现象**：冷启动拍摄 61MP `CHW06718.JPG`（渲染 103.77s 正常生成 `16.85 MB` `output.jpg`）后弹窗 `Failed (21)` 并永久停止守护进程。
- **根因**：
  1. `IFSYS cp B:/A7R4A/TEST/F7995276.JPG` 执行写前空闲检查时，索尼 `mwf_monio` 并发穿插打印了无害的正常状态日志：`[MWF]  [RsrcAcsr] Not acquired! / already Acquired!` 与 `[GetStillTargetMaxFileSize]target[...] max[...]`，随后紧接真正的 `[IFSYS]Can't open file B:/A7R4A/TEST/F7995276.JPG\nMWF>`。
  2. `absent_reply()` 原本拒绝除 `MWF>` 和回显外的任何文本行，导致误报 `absence_unproven`（`exit=1`）。
  3. `film_ram_job.sh` 将所有未预设的 `native_rc != 0` 一律升级为 `exit=21`（`native_completion_unconfirmed`），进而触发 `film_capture_daemon.c` 的 `FILM_CAPTURE_STOP result=21`。
- **修复**：
  1. `camera/film_native_command.c`：新增 `benign_mwf_line()`，在确保不含任何 `ERROR/error/fail/err/Can't open/OVERFLOW` 关键词的前提下，放行 `[MWF] ` 与 `[GetStillTargetMaxFileSize]` 正常状态行。
  2. `camera/film_ram_job.sh`：当且仅当 `MODE=apply && TARGET_ATTEMPTED=0`（尚未触碰 SD 卡目标路径）、`INPUT` 不存在且 `native_report_ok` 明确验证 `uncertain=0` 时，`absent` / `read` 的可确认失败返回 `exit=1`（`retain=0`），只报错该张而**不杀死后台守护进程**。

---

## 4. 安全边界与恢复方案

1. **三阶段隔离验证（`A7R4A_BOOT_STAGE`）**：
   - `stage1`：仅验证 `/tmp/gpslogdiag.so` 触发与 `/tmp_bt` 解包 + 20s 等待；
   - `stage2`：加入 `load-guard` UI Hook 注入，暂不启动守护进程；
   - `full`：完整启用 UI Hook + `capture-daemon`。
2. **即时恢复**：
   - 任何异常只需执行 `sudo ./scripts/autoboot_flash.sh uninstall`（或通过 PMCA Service Mode 将 Backup `0x00a10011=00 0x00a1000b=0200` 并删除 `/tmp/gpslogdiag.so` + 写入 `/tmp/disable_hook`），即可立即恢复原厂状态。
3. **只读保护**：
   - `film_ram_job.sh` 在执行前校验 `/dev/mmc*` 未被 Linux 挂载，且原片仅通过 Sony 原生 `IFSYS` 只读拷贝到 RAM，回写目标固定为 `<drive>:/A7R4A/TEST/Fxxxxxxx.JPG`，不修改原片。
