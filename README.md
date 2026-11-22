# A7R4A Extra-Menu (`ILCE-7RM4A Ver. 1.12`)

面向 **Sony α7R IV A（`ILCE-7RM4A` 固件 `Ver. 1.12`）** 的机内触控胶片菜单、后台自动套色/颗粒/光晕渲染与**脱机冷启动持久化**移植项目，基于 [PCIEARIA/A6400-Extra-Menu](https://github.com/PCIEARIA/A6400-Extra-Menu)（V3.9 SSLF）移植并针对 A7R4A 的新平台特性与 61MP 高像素场景完成深度适配与实机验证。

> 本项目沿用上游 **PolyForm Noncommercial 1.0.0** 协议（见 [LICENSE](LICENSE)），仅用于个人研究与非商业用途。仓库内**不包含**任何 Sony 原厂固件、专有 `.so` 函式库、内存 dump 或私人凭据。

---

## 核心特性与实机验证状态

截至 **2026-11-22**，`ILCE-7RM4A Ver. 1.12` 实机已完成以下验证：

| 模块 | 状态 | 实机验证细节 |
| --- | --- | --- |
| **冷启动脱机持久化** | 已通过 | `/tmp/gpslogdiag.so`（`Ir 0a0_geo_res`）+ Backup NVRAM 标志触发 `/etc/a7r4a_film/boot.sh`（3 阶段隔离验证：`stage1` → `stage2` → `full`） |
| **UI Hook 与实时触控菜单** | 已通过 | `C1+C2`（或 `MENU+C1`）呼出 V3.9 触控菜单；`SCHED_FIFO` 优先级防抢占将 `PTRACE` 冻结+注入总耗时从 15.8–35.6s 降至 **120–138 ms**，彻底消除冷启动互锁 |
| **61MP 流式胶片渲染** | 已通过 | `a7r4a-film-job.elf` 支持 `Fast 4MP` / `Medium 15MP` / `Full 61MP (9504×6336)`，带内存预算自动降档（`scale=1→2→4`）与 61MP `q=85` 体积优化，实机冷启动已独立产出 `9504×6336`（16 MB）及多张缩放成片 |
| **双卡槽（Slot 1 / Slot 2）与大文件过滤** | 已通过 | 读取 `im.elf` 内存 `libInfraFsys.so` 自动映射 `A:/` / `B:/` 盘符；支持 `uint64_t` 文件大小与 `.DS_Store` / `.ARW` / >4 GiB 非 JPEG 文件安全跳过 |
| **Sony MWF 并发日志与非致命失败容错** | 已通过 | 放行 `[MWF] [RsrcAcsr]` 与 `[GetStillTargetMaxFileSize]` 正常并发输出，写卡前干净失败不再误升级为 `exit=21` 杀死守护进程 |
| **一键安装与无痕卸载/恢复** | 已通过 | `scripts/autoboot_flash.sh install \| status \| uninstall`（先校验后写入，`/tmp/disable_hook` + Backup 标志可秒级还原出厂状态） |

---

## 目录结构

- `camera/`：相机端 C / 汇编 / Shell 源码（UI Hook、Load Guard、Preload Trigger、Capture Daemon、Native Command、61MP Film Job、RAM Job 调度脚本）
- `host-tests/`：交叉编译脚本（`build_all_a7r4a.sh` 及各模块独立构建脚本）
- `scripts/`：主机端 E2E 与刷写工具（`autoboot_flash.sh`、`phase6_ram_e2e.sh`、`inspect_a7r4_elf.py`、`phase4b_61mp_bench.py`、`phase4b_elf_smoke.py`）
- `tools/`：公开树检查与打包辅助工具（`check_public_tree.py`、`pack_req.py`、`cube_to_a7r4a_lut.py`）
- `test_*.py`：无需相机的桌面端单元测试套件（已通过 208+ 项回归测试）
- `docs/`：详细技术文档（[TECHNICAL_ARCHITECTURE.md](docs/TECHNICAL_ARCHITECTURE.md)）与交接文档（[HANDOFF.md](docs/HANDOFF.md)）

---

## 快速开始

### 1. 运行主机单元测试（无需相机）

```sh
python3 -m unittest discover -p 'test_*.py' -v
```

### 2. 交叉编译相机端产物

需在仓库同级（或通过环境变量指定）准备 ARMv7 交叉编译工具链、`libjpeg-arm` 及本地提取的相机库目录（见 [docs/TECHNICAL_ARCHITECTURE.md](docs/TECHNICAL_ARCHITECTURE.md)）：

```sh
bash host-tests/build_all_a7r4a.sh ./dist_a7r4a
(cd dist_a7r4a && shasum -a 256 -c SHA256SUMS)
```

### 3. 纯内存试运行 / 持久化刷写 / 卸载

相机开机并设为 **大容量存储（Mass Storage）** USB 模式连接电脑：

```sh
# 仅在 /tmp_bt RAM 中试运行（断电即恢复出厂）
./scripts/phase6_ram_e2e.sh check
./scripts/phase6_ram_e2e.sh start

# 持久化安装（支持 A7R4A_BOOT_STAGE=stage1|stage2|full，默认 full）
sudo env A7R4A_BOOT_STAGE=full ./scripts/autoboot_flash.sh install

# 查看机内运行状态与 /tmp_bt 日志
sudo ./scripts/autoboot_flash.sh status

# 彻底卸载并恢复 Backup 标志为出厂默认
sudo ./scripts/autoboot_flash.sh uninstall
```

> **使用提示**：`Full 61MP (9504×6336)` 单张渲染耗时约 **88–105 秒**。若使用 61MP 档位，请将相机菜单中的「省电开始时间」设为 **5 分钟或更长**，避免渲染期间机身进入休眠卸载存储卡。日常抓拍推荐使用 **`Fast 4MP`** 或 **`Medium 15MP`**。

---

## 更多文档

- [技术架构与移植详解（docs/TECHNICAL_ARCHITECTURE.md）](docs/TECHNICAL_ARCHITECTURE.md)
- [下一会话交接文档（docs/HANDOFF.md）](docs/HANDOFF.md)
- [功能状态](docs/功能狀態.md) · [编译与目录说明](docs/編譯與目錄.md) · [授权说明](docs/授權說明.md)

## 致谢

感谢 [PCIEARIA/A6400-Extra-Menu](https://github.com/PCIEARIA/A6400-Extra-Menu)、**ivica00**、**DevCrashDay** 与 **ma1co / Sony-PMCA-RE** 贡献者们的开源与研究工作。完整致谢与第三方声明见 [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) 与 [THIRD_PARTY.md](THIRD_PARTY.md)。
