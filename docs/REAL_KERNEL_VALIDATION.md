# Linux 5.10.176 真实内核验证（历史 v29）

本页保留 v29 的历史实验口径。当前同配置完整图、八模块严格最终链接门禁
和 v32 统计见 [`V32_SOLID_RESULT.md`](V32_SOLID_RESULT.md)。

主验证对象是上游 Linux 5.10.176、x86_64 `vmlinux`，不是仓库测试夹具。
同一份 `.config`、同一套 Clang/LLVM 和固定 Kbuild 元数据用于建立纯
基线，再组合八个自动生成模块：

- `ioprio_lazy`
- `splice_lazy`
- `setns_lazy`
- `quotactl_lazy`
- `stat_lazy_v2`
- `sys_identity_lazy`
- `capability_lazy`
- `wallclock_lazy`

v29 保留四个已验证逐接口 `symbol_get()` 模块；v28/v29 新生成的多接口
模块使用一张强类型 operations 表，并让同一常驻源文件的包装器共享一个
非内联加载器。八模块重新完成真实构建、尺寸、QEMU 自动加载/卸载/重载
和 16 对启动 A/B。

## 构建与可复现性

- 纯基线 `bzImage`：9,756,736 字节
- 纯基线 SHA-256：
  `c7abc1efc511743731f8330002d6a1a66c5e9cfbb8dea231512ec2617f56e703`
- v29 八模块 `bzImage`：9,747,744 字节
- v29 八模块 SHA-256：
  `56bfee5b4473169f0c09d3bd0a1b97aad2482028ff073515a21442cacbf2309c`
- 镜像变化：**-8,992 字节**
- `vmlinux`、八个 `.ko` 和 `bzImage` 均完成真实 Kbuild/MODPOST，
  组合构建没有未解析符号或重复导出告警。

构建固定以下元数据，避免用户名、主机名、时间戳和 build version
造成伪差异：

```bash
KBUILD_BUILD_VERSION=1
KBUILD_BUILD_TIMESTAMP='2026-07-31 00:00:00 +0800'
KBUILD_BUILD_USER=linux-modular
KBUILD_BUILD_HOST=validation
```

生成 bundle 应用到真实内核源码只用于受控构建；验证完成后全部事务按
逆序回滚，仓库外保留构建镜像、模块、串口日志和机器 JSON。

## 精确尺寸门禁

尺寸门禁读取 ELF allocatable section，区分启动后释放的 init section
与永久 executable/readonly/writable section，不用 `.o` 文件大小或
压缩率扰动代替常驻内存。

| 模块 | 基线内建对象 | 模块化常驻对象 | 未加载节省 | `.ko` 永久字节 |
|---|---:|---:|---:|---:|
| `splice_lazy` | 11,460 | 9,626 | 1,834 | 3,346 |
| `ioprio_lazy` | 2,025 | 1,095 | 930 | 2,668 |
| `setns_lazy` | 3,264 | 2,408 | 856 | 2,584 |
| `quotactl_lazy` | 9,228 | 531 | 8,697 | 10,242 |
| `stat_lazy_v2` | 8,684 | 7,487 | 1,197 | 2,801 |
| `sys_identity_lazy` | 20,467 | 17,965 | 2,502 | 4,885 |
| `capability_lazy` | 2,982 | 1,778 | 1,204 | 2,831 |
| `wallclock_lazy` | 7,713 | 6,979 | 734 | 1,548 |
| **组合** | **65,823** | **47,869** | **17,954** | **30,905** |

模块未加载时减少 17,954 字节，通过 4,096 字节部署收益门禁。相比 v28
六模块的 16,016 字节，常驻节省增加 1,938 字节（12.10%）。

八模块全部加载后，常驻对象加模块共比基线增加 12,951 字节。这是
“按使用付费”的设计：未使用功能不承担模块本体成本，并不声称所有功能
同时使用时仍无条件更小。

机器尺寸报告 SHA-256：
`e2ee8f33cee7f4e57fe94bf1eb5ef92ad10659d62ea4e0114e357444f2180d0f`。

## 共享强引用边界

v28/v29 新生成候选的全部接口通过一张强类型 operations 表发布。常驻侧：

1. 一个共享 `noinline` 加载器用 `symbol_get()` 获取表；
2. 成功查找同时持有实现模块；
3. 首次缺失时检查当前不是 IRQ/NMI/atomic/禁中断上下文；
4. 在可睡眠进程上下文调用 `request_module()` 并重试；
5. 包装器调用 ABI 匹配的函数指针字段，返回后 `symbol_put()`。

模块引用覆盖整个实现调用，因此 `rmmod` 不能释放仍在执行的模块文本。
后端不需要每接口 RCU 指针、owner、共享锁、publish/withdraw 或 grace
period。共享导出还减少 ksymtab、kallsyms 和字符串开销。

## 新增闭包

### stat/lstat/fstat

三个接口 `__se_sys_newstat`、`__se_sys_newlstat` 和
`__se_sys_newfstat` 组成一个候选。后端显式复制纯静态辅助函数
`cp_new_stat`，并只导出经审计的 `vfs_fstat`、`vfs_fstatat` 常驻依赖。

独立真实对象从 8,684 B 降至 7,487 B，未加载节省 1,197 B；三接口
功能 QEMU 场景通过，16 对独立启动 A/B 也通过。

### 系统身份、优先级和 times

九个规划接口包括 get/set priority、hostname/domainname、rlimit、
setpgid、times、gethostname 和 olduname。闭包显式复制：

- `set_one_prio_perm`
- `set_one_prio`
- `do_sys_times`
- `override_release`

模块侧还用受限函数式宏提供 `override_architecture(name) = 0`。独立
预检需要 13 个常驻导出；与 `ioprio_lazy` 组合后复用
`find_task_by_vpid`、`find_user`、`free_uid` 和 `tasklist_lock`，因此
最终只新增 9 个导出，MODPOST 不产生重复导出告警。

当前 x86_64 功能触发器覆盖可直接到达的七个接口。`gethostname` 和
`olduname` 包装器没有出现在当前 x86_64 syscall 表的直接可触达路径，
因此不能把本轮场景解释为这两个兼容入口的动态覆盖；兼容 ABI 发布前
仍需单独工作负载。

组合构建中 `kernel/sys.o` 从 20,467 B 降至 17,965 B，未加载节省
2,502 B。

### capability

两个接口 `__se_sys_capget`、`__se_sys_capset` 组成一个候选。模块复制
`warn_legacy_capability_use`、`warn_deprecated_v2`、
`cap_validate_magic` 和 `cap_get_target_pid` 四个无共享状态静态辅助
函数，常驻侧只新增三个 capability/audit 导出，并复用 ioprio 边界已有的
`find_task_by_vpid`。组合构建中 `kernel/capability.o` 从 2,982 B
降至 1,778 B，未加载节省 1,204 B。

### wallclock/time32

两个接口 `__se_sys_settimeofday`、`__se_sys_adjtimex_time32` 组成一个
候选。四个仍被其他常驻路径调用的时间实现保持常驻并显式导出。
`kernel/time/time.o` 从 7,713 B 降至 6,979 B，未加载节省 734 B。

静态 64 位触发器用 `MAP_32BIT` 分配兼容缓冲区并执行 `int 0x80`，
实际到达 i386 `adjtimex_time32`；随后用空参数调用 64 位
`settimeofday`，覆盖第二条模块入口且不改变时钟或时区。

## QEMU 功能验证

BusyBox initramfs 包含八个模块、`modules.dep` 和组合功能触发器。
组合串口场景完成 8/8 步并正常关机，验证：

1. 启动完成时八模块均未加载；
2. 一个组合触发器首次执行时自动加载八模块；
3. 各功能返回值符合基线预期；
4. 已加载快路径可重复调用；
5. 八模块均可卸载；
6. 卸载后再次调用可重新加载全部模块。

QEMU 结果为 `passed=true`，8 个步骤耗时 1.630 秒。结果 SHA-256：
`7e31d08b3dd2db0dd3e5e1f45599a4aa219ae6d79d310f04eedfd71531739e54`。

## 启动资源 A/B

基线和模块化场景都使用 `nokaslr`，按“基线→模块化、
模块化→基线”交替执行 16 对相邻样本，共 32 次启动。

| 指标 | 基线中位数 | v29 八模块中位数 | 差值 | 门禁 |
|---|---:|---:|---:|---:|
| READY uptime | 0.850 s | 0.860 s | +0.010 s | ≤ +0.250 s |
| 永久内核汇总 | 25,631 KiB | 25,631 KiB | 0 KiB | ≤ 0 KiB |
| `MemAvailable` | 465,532 KiB | 465,534 KiB | +2 KiB | ≥ -128 KiB |
| Slab | 12,598 KiB | 12,612 KiB | +14 KiB | ≤ +64 KiB |

四项均通过。结果 SHA-256：
`0edabdfa5b61a99ac9c7ee220e5cf1ed26279f3349449cac9b8d2aaf9bf06d4d`。

对象级精确节省约 18 KiB，启动日志 KiB 汇总、页粒度和正常噪声会遮蔽
这一量级。因此 A/B 只证明没有观测到启动时间或资源回归；不把
`MemAvailable` 或 Slab 的波动误报为可归因收益。

## 成功统计

成功清单要求 AST 提取、内核构建、QEMU 启动、自动加载、卸载/重载、
尺寸门禁和启动 A/B 七项全部为真。

八个候选的成功清单记录 70 个候选/闭包函数 ID；后端另复制 8 个纯静态
辅助定义，不重复计数。其中 45 个清单函数对应基线最终 ELF 的独立正大小
函数，共 22,359 字节：

- 45 / 51,683 = 0.087069% 的最终 ELF 函数；
- 22,359 / 13,506,837 = 0.165538% 的基线函数字节。

尺寸和启动 A/B 按八模块部署 bundle 判定。单个小模块不必独立达到
4 KiB，但被接受的部署子集必须通过真实组合尺寸门禁。完整
CORE、READY、BLOCKED、UNKNOWN 对账见
[完整内核函数模块化统计](FULL_KERNEL_ACCOUNTING.md)。

## READY 与成本校准

v29 复用同一完整计划：717 个候选中 31 READY、350 BLOCKED、336
NEEDS_EVIDENCE；
31 个 READY 映射到 81 个最终 ELF 函数，比 v27 的 45 个增加 80%。

七接口 signal 常驻预检中，基线 `kernel/signal.o` 为 39,565 B，v28
常驻对象为 37,115 B，实际节省 2,450 B。由 3,445 B 目标文本反推的
边界成本为 995 B；规划器使用 `640 + 6 × 96 = 1,216 B`，保留 221 B
裕量。相对 v27 的 1,703 B 边界成本降低 708 B。模块侧仍受私有 signal
依赖阻塞，所以该预检不计入成功集合。

## v31 自动启动配置验证

为避免在已有 Kconfig 模块能力上重复制造函数级模块，v31 先运行
`Runtime/plan_boot_module_profile.py`。它对基线中原本为 `y`、当前可
赋值为 `m` 的 tristate 做依赖固定点求解，并强制本 QEMU 部署所需的
`CONFIG_BINFMT_SCRIPT`、`CONFIG_SERIAL_8250` 保持内建。137 个显式
模块请求最终形成 256 个 `y→m` 变化；所有附带 Kconfig 变化均写入机器
报告。

profile 的 `vmlinux`、`bzImage`、`modules` 和 MODPOST 均通过。最终
正大小函数从 51,683 减至 28,959；永久 allocatable ELF 从
26,854,034 减至 16,221,225 字节。8 对平衡交错启动 A/B 全部通过，
永久内核内存中位数从 25,631 降至 15,336 KiB，loader-ready 从
0.840 降至 0.490 秒，`MemAvailable` 增加 16,338 KiB，Slab 减少
6,394 KiB。

单独的 e1000 场景验证了 READY 时模块不存在、`modprobe` 后出现和
`rmmod` 成功，共 3/3 步。其他生成模块尚未逐项执行真实功能负载，因而
不能由“全模块构建通过”推导为“所有硬件功能已验证”。完整结果、限制和
哈希见
[`boot-defer-auto-profile-v31-summary.json`](boot-defer-auto-profile-v31-summary.json)。

## 适用范围

本轮只验证一份 x86_64 配置和 BusyBox/initramfs 工作负载。不同架构、
PREEMPT/RT、LSM、文件系统、容器和设备组合必须重新构图、重新采样并
重跑七项门禁。启动未观测到不能单独证明安全，结构化系统调用前缀也
必须由目标架构策略显式审计。

本轮同时拒绝了四个 READY 方向：`nanosleep` 常驻变化为 -66 B，
read/write 仅节省 23 B，`pidfd` 依赖私有 `pidfd_fops`，`select`
仍跨越多项私有 poll 辅助闭包。这些结果保留为“静态 READY、后端未
成功”，不计入 45 个成功函数。

大型构建和 QEMU 日志保存在仓库外；仓库提交机器摘要、策略、触发器、
测试夹具和可复现命令，不提交 `vmlinux`、`bzImage` 或 `.ko` 二进制。
