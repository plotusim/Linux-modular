# 模块加载器阶段模型

## 结论

当调用点继续留在 built-in 内核中时，“模块机制可用之前没有执行过”是
函数体按首次调用自动加载的必要条件之一，但不是充分条件。一个函数只有
在以下条件同时成立时，才适合保留常驻调用点并按首次调用自动加载：

1. 目标启动场景中，它及其不可拆分依赖在 loader-ready 标记前没有执行；
2. 首个可能入口处于可睡眠进程上下文，可以调用 `request_module()`；
3. 函数指针、共享全局状态、注册关系和符号可见性形成闭合边界；
4. 源码提取、真实 Kbuild/MODPOST、功能、卸载、尺寸和启动 A/B 均通过。

因此，“启动时没采到”只提供负证据，不能单独授权移动。

这条规则不应错误套到完整功能所有权边界。如果驱动、文件系统或协议栈连
同 initcall、注册表和私有状态一起进入模块，那么原先执行过的 initcall
也会随模块延后；它不再是 built-in 启动调用链的一部分。此类边界以同一
启动工作负载下“模块未加载仍能到达 READY”的构建与 A/B 实验作为启动
必要性证据，模块加载与卸载测试再证明功能可恢复。

## 两层判定

当前流水线不再用一个 `CORE` 同时表达启动和后端结论：

1. 启动层只按 loader-ready 观测把最终 ELF 分为 `BOOT_HOT`、
   `POST_BOOT_ONLY`、`BOOT_COLD` 和 `UNKNOWN`，并给出经验性可延迟上限；
2. 实现层再选择整子系统、整源文件或函数接口边界，并检查依赖闭包、加载
   上下文、状态所有权、构建、`modprobe`、卸载和 A/B 门禁。

因此，旧报告中的 48,385 个 `CORE` 表示“在当时 planner 策略下暂时
常驻”，不能解释为 48,385 个启动必需函数。

也就是说，启动层不先问一个函数以后是否应常驻；它只问“本部署能否在
这项实现尚未加载时到达 loader-ready”。常驻调用点加懒加载桩和移动整个
功能所有权边界是两种不同实现，只有进入实现层后才分别检查其安全条件。

## loader-ready 标记

启动观测 initramfs 在 PID 1 中挂载 proc、sysfs、devtmpfs 和 tracefs，
并同时验证：

- `/proc/modules` 可读，证明目标内核启用了模块子系统；
- `/proc/sys/kernel/modprobe` 可读且实际内容非空；
- `/sys/module` 已建立；
- `/sbin/modprobe` 可执行；
- `/lib/modules/<release>/modules.dep` 存在；
- tracefs 的 `trace_marker` 可写。

全部成立后，脚本向 trace buffer 写入
`LINUX_MODULARIZER_MODULE_LOADER_READY`。任何一项失败都会输出
`LINUX_MODULARIZER_MODULE_LOADER_NOT_READY`，不会伪造就绪证据。
规划时启用 `enforce_loader_ready_phase` 后，缺少 marker、存在未分阶段
观测，或只在策略文件中手写 `loader_ready_observed=true` 都会硬失败。

这个标记比内核内部“模块代码已经初始化”更晚：它还要求当前部署环境的
用户态 helper 和模块索引可用。这个偏晚边界会少选候选，但不会把尚不能
加载模块的依赖错误延迟。

## 四类候选

| 类别 | 含义 | 自动处理 |
|---|---|---|
| `EARLY_CORE` | 候选或其入口在 marker 前执行 | 留在 CORE |
| `LAZY_READY` | marker 前未使用，入口有可睡眠加载证明 | 可进入后端和七项门禁 |
| `PRELOAD` | marker 后才需要，但首个入口可能在 IRQ/NMI/atomic | 只能在注册/启用设备前预加载，不能在回调中加载 |
| `UNKNOWN_PHASE` | 阶段、入口或执行边证据不足 | 保守阻塞或等待证据 |

`PRELOAD` 不等于 READY。它还必须证明模块在相关回调注册前已经加载，并
证明卸载顺序不会留下函数指针或长生命周期对象。

同一个函数可以在 marker 前后都出现。观测 JSON 因此分别保存阶段成员
集合，并额外报告 `pre_loader_only`、`post_loader_only` 和
`pre_and_post_loader`，不能把 pre/post 数量直接相加作为总数。

## Linux 5.10.176 实测

本轮 x86_64 单核、`nokaslr`、function tracer 启动中：

- marker 内核时间为 1.952301 秒；
- 完整图对齐 5,583 个函数；
- 5,426 个只在 marker 前出现；
- 157 个在 marker 前后都出现；
- 0 个只在 marker 后出现；
- 0 个处于 UNKNOWN phase；
- 3 个 tracer 符号无法映射，单独保留为诊断信息。

对 169,996 个函数节点重新传播后，v28 计划有 717 个候选：
`EARLY_CORE=5`、`LAZY_READY=390`、`PRELOAD=0`、
`UNKNOWN_PHASE=322`。其中 31 个同时通过接口、安全和收益门禁成为
READY，映射到 81 个最终 ELF 函数。PRELOAD 为 0 是本次短启动场景的
实测结果，不代表内核中不存在适合预加载的设备或网络功能簇；需要在
marker 后继续运行相应工作负载才能产生这类正证据。

同一份观测在 v30 使用独立的最终 ELF 启动对账，不读取 planner 的
`CORE/READY/BLOCKED`：

- `BOOT_HOT`：5,544 个函数、1,460,841 字节；
- `BOOT_COLD`：44,473 个函数、12,010,088 字节；
- `UNKNOWN`：1,666 个函数、35,908 字节；
- 1,124 个源文件的最终函数全部属于可延迟侧，共 5,174,893 字节。

随后把 i915、cfg80211/mac80211、NFS、ext4、e1000/e1000e、r8169 和
HDA 从 built-in 改为模块。完整 `vmlinux`、`bzImage`、模块与 MODPOST
均通过；8 对交错 A/B 中，常驻内核中位数减少 5,162 KiB，READY 提前
0.145 秒，`MemAvailable` 增加 7,604 KiB，Slab 减少 4,254 KiB。
e1000 在 READY 时保持未加载，之后 `modprobe` 和卸载均通过。这证明
部分已执行 initcall 也可以通过移动整个所有权边界安全延后。

扣除这些已有模块后，残余 built-in `vmlinux` 仍有 33,121 个
`BOOT_COLD` 函数、8,282,414 字节；其中 780 个源文件完全位于可延迟
侧，共 2,419,640 字节。这是后续“新函数模块化”的独立收益池。该残余
统计复用了基线观测并达到 97.681020% 精确 ELF 映射覆盖，发布前仍需用
profile 配置重新生成 trace 和引用图。

v31 又加入依赖感知的自动 Kconfig profile。配置器对原配置中为 `y`、
当前可赋值为 `m` 的 tristate 迭代发出模块请求，并强制显式 keep-list
保持原值。本次 137 个显式请求经 Kconfig 依赖传播形成 256 个 `y→m`、
8 个 `y→n`，以及由默认 CUBIC 变成模块引起的 Reno 默认选择变化；没有
显式启用原先关闭的功能。完整 `vmlinux`、`bzImage`、模块和 MODPOST
均通过。

与同一基线进行 8 对平衡交错 QEMU A/B 后，v31 profile 的结果为：

- 最终正大小函数 51,683 → 28,959，减少 22,724 个（43.968036%）；
- 永久 allocatable ELF 字节 26,854,034 → 16,221,225，减少
  10,632,809 字节（39.594830%）；
- `bzImage` 9,756,736 → 5,056,256 字节，减少 4,700,480 字节；
- 永久内核内存中位数 25,631 → 15,336 KiB，减少 10,295 KiB；
- loader-ready 中位数 0.840 → 0.490 秒，提前 0.350 秒；
- `MemAvailable` 增加 16,338 KiB，Slab 减少 6,394 KiB。

`e1000` 在 READY 时未加载，随后 `modprobe`、出现于 `/proc/modules` 和
卸载均通过。其余模块目前只有 Kbuild/MODPOST 证明，尚未逐功能执行负载，
因此 v31 是本 QEMU 启动画像的配置候选，而不是通用硬件发布配置。

自动使用已有模块边界后，残余 built-in 镜像还有 23,362 个
`BOOT_COLD` 函数、5,373,211 字节；560 个完整冷源文件包含 5,942 个
函数、1,517,810 字节。这才是下一轮新函数提取应使用的收益池。该残余
对账复用了基线 trace 和图，精确映射覆盖为 96.933596%；发布前必须在
v31 配置上重新生成 IR 图和启动 trace。机器可读结果见
[`boot-defer-auto-profile-v31-summary.json`](boot-defer-auto-profile-v31-summary.json)。

v32 已完成这次重生成：同一 v31 基础配置产生 1,503 个 LLVM fact stream、
143,300 个节点、910,007 条引用边和新鲜 loader-ready trace。当前最终
ELF 分母为 28,959 个函数，精确覆盖 97.133879%；`BOOT_HOT=4,219`、
`BOOT_COLD=23,910`、未映射 `UNKNOWN=830`。八个函数模块的 45 个最终
ELF 函数通过构建、生命周期、最终链接尺寸和启动 A/B 门禁。详细口径见
[`V32_SOLID_RESULT.md`](V32_SOLID_RESULT.md)。

READY 增长来自不改变 loader-ready 边界的三项改进：

- 策略显式声明 x86 已审计的 `__x64_sys_`、`__ia32_sys_` 和兼容 ABI
  包装器命名空间。兼容系统调用表未进入同一 LLVM 编译单元时，包装器
  仍保持常驻，并为其调用体提供结构化进程上下文入口证明。
- 多个独立安全、各自正收益的小候选可以用部署总收益满足 4 KiB 门槛。
  组合只能消除尺寸原因，不能覆盖阶段、上下文或证据问题。
- 同一候选的接口共享对象级固定成本。规划器按真实内核对象验证后的
  “首接口 640 B、后续接口 96 B”估算，而不是把每个接口都重复按
  640 B 计费。

v28/v29 新生成模块让同一模块的接口共享一张强类型导出表，并让同一
常驻源文件的所有包装器共享一个非内联加载器。七接口 `signal` 常驻边界实测成本由
v27 的 1,703 B 降至 995 B，说明 96 B 的后续接口估计仍有裕量。

严格计划中 io_uring 是唯一单独达到 4 KiB 的候选。真实后端预检在
20 条编译错误后停止：它依赖私有 `struct io_ring_ctx`、
`io_uring_fops` 和多项同编译单元静态函数。把整个运行时一起移动又会
跨越启动期 `io_uring_init` 和长生命周期对象所有权，因此该候选没有
计入成功模块化统计。其余 30 个 READY 候选属于静态组合收益组；其中
`ioprio`、`splice/vmsplice`、`setns`、`quotactl`、`stat`、系统身份、
capability 和 wallclock 功能簇已经通过真实后端七项门禁，其余候选仍需
逐一或按部署子集完成真实后端七项门禁。

## 适用边界

- 只在启动期使用、启动后可以永久释放的代码优先使用 `__init`/free-initmem，
  不应改成以后还能加载的模块。
- 启动期不用、运行期偶尔在进程上下文使用的功能适合 `LAZY_READY`。
- 启动期不用、但会在中断或原子回调中使用的功能只能走 `PRELOAD`。
- 内核线程是否可睡眠必须逐入口证明，不能因为它不是硬中断就默认安全。
- 新增文件系统、网卡、存储、容器或 LSM 场景时必须重新采集。新的正
  证据会扩大 `BOOT_HOT`；未观测函数不会自动成为安全后端候选。完整
  子系统是否为本次启动所需，应通过“未加载仍启动成功”的 A/B 门禁判断。

## 可复现命令

```bash
PROFILE=/absolute/path/to/profile-build
mkdir -p "$PROFILE"

python3 Runtime/build_boot_trace_initramfs.py \
  --busybox /absolute/path/to/static-busybox \
  --kernel-release "$(make -s -C "$KERNEL" O="$OUT" kernelrelease)" \
  --output "$RUN/boot-trace-initramfs.cpio.gz"

python3 -m kernel_modularizer observe-boot \
  "$RUN/reference-graph-sized.json" "$RUN/boot.trace" \
  --require-loader-ready-marker \
  --output "$RUN/boot-observations.json"

python3 -m kernel_modularizer summarize-boot-phase \
  "$RUN/reference-graph-sized.json" \
  --observations "$RUN/boot-observations.json" \
  --vmlinux "$OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --json-output "$RUN/boot-phase-stats.json" \
  --markdown-output "$RUN/boot-phase-stats.md"

python3 Runtime/plan_boot_module_profile.py \
  --kernel-root "$KERNEL" \
  --base-config "$OUT/.config" \
  --keep BINFMT_SCRIPT \
  --keep SERIAL_8250 \
  --arch x86 --srcarch x86 \
  --output-config "$PROFILE/.config" \
  --report-output "$RUN/boot-module-profile.json"

make -C "$KERNEL" O="$PROFILE" LLVM=1 olddefconfig
make -C "$KERNEL" O="$PROFILE" LLVM=1 -j"$(nproc)" \
  vmlinux bzImage modules

python3 -m kernel_modularizer plan \
  "$RUN/reference-graph-sized.json" \
  --policy docs/full-kernel-syscall-lazy-policy.json \
  --observations "$RUN/boot-observations.json" \
  --json-output "$RUN/plan.json" \
  --markdown-output "$RUN/plan.md"
```
