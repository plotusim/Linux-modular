# Linux Function Modularizer

这是一个面向 Linux 内核的函数级模块化流水线。系统现在把两个问题分开：
启动报告只回答函数在 loader-ready 前是否执行，不再让后端常驻约束污染
启动收益上限；真正提取模块时，再用 LLVM 引用事实、函数指针固定点分析、
执行上下文、符号可见性、共享全局变量和真实构建结果保证边界成立。

当前生产链路已经覆盖：

- 链接属性感知的函数/全局变量身份，避免不同编译单元的同名 `static`
  函数被错误合并。
- LLVM IR 直接调用、间接调用、函数参数/返回值、全局函数指针和回调
  字段分析；无法解析的调用显式进入 `UNKNOWN`。
- 启动 ftrace 证据；同名静态符号无法区分时保守地标记全部候选。
- 模块边界约束：init/IRQ/NMI/atomic/noinstr、未导出常驻依赖、跨边界
  私有状态和特殊 section 均会阻止自动提取。
- Clang AST 精确源码区间提取，不使用正则表达式猜测函数边界。
- 无动态分配的 `symbol_get()`/`symbol_put()` 懒加载接口；模块引用覆盖
  整个实现调用，支持首次调用自动 `request_module()`、并发调用、安全
  卸载和再次加载。
- Kbuild/Kconfig 生成、哈希校验、事务应用、幂等重放与回滚。
- 可复现 initramfs、串口 QEMU 场景测试、平衡交错启动 A/B，以及对象
  永久段、最终链接 `vmlinux` 区间和可选压缩镜像三层体积门禁。

当前 v43 新增完整回调源文件组的批量自动提取器。它在一次运行中处理全部
69 个 READY 源文件组，按组隔离失败、原子记录结果并按哈希续跑：33 组
成功生成 bundle，覆盖 139 个原规划为 CORE 的函数定义和 106 个懒加载
接口；26 组识别为此前已模块化，10 组保留明确失败边界。真实编译和组合
链接从中保留 trace events、regmap debugfs、TTY、kernfs、proc page、profile、
timerfd、mqueue 八个模块，共迁移 64 个定义、45 个接口，对应 52 个最终
ELF 函数和 14,087 B。受影响对象减少 6,924 B，`bzImage` 再减少 9,280 B，
最终链接永久区间减少 64 B；4 轮 48 步真实自动加载/卸载/重载全部通过。
累计验证集合为 44 模块、524 个迁移定义、350/28,959 个最终 ELF 函数
（1.208605%），对象收益 119,267 B；同配置 `bzImage` 相对纯 v32 减少
67,456 B（1.334110%）。本轮放宽了单候选 4 KiB 门槛，因此没有把 44 个
模块全部称为严格集合；四个单独正收益候选也因组合页对齐回退而被回滚。
完整自动化命令、失败队列和证据见
[v43 批量自动提取成果](docs/V43_AUTOMATIC_PORTFOLIO_RESULT.md)。

当前 v42 继续放宽候选大小和规划器分类，但保留 loader、上下文、生命期、
ABI、构建和卸载边界。完整 LLVM 图自动提取 MTRR、kcore、relay、RTC、
socket、shmem、hugetlbfs、seccomp notification、eventfd、signalfd、dma-buf、
snapshot、PCI proc、bsg 和 inotify 的 89 个 built-in C 函数定义，生成 15 个
首次使用自动加载的模块；它们原先全部属于 CORE。最终 ELF 中有 56 个独立
函数、28,491 B，受影响常驻对象减少 20,220 B，`bzImage` 增量减少 10,080 B。
4 轮 84 步真实功能生命周期与合并 16 对启动 A/B 通过；第一批 8 对曾因
Slab 噪声超门槛失败，也保留在证据中。累计严格集合达到 36 模块、460 个迁移
定义，对应 298/28,959 个最终 ELF 函数（1.029041%），对象收益 112,343 B；
同配置 `bzImage` 相对纯 v32 减少 58,176 B（1.150575%），首次低于 5,000,000 B。
完整收益、全加载 +31,033 B 的权衡、`task_mmu` ABI 反例和测试边界见
[v42 扩展回调闭包成果](docs/V42_EXPANDED_CALLBACK_PORTFOLIO_RESULT.md)。

当前 v41 把候选范围从规划器 READY 扩展到可改写的常驻回调表：自动提取
SELinuxFS、VCS、VGA arbiter 和 pipe 的 62 个 C 函数定义、1 个私有全局，
生成四个首次使用自动加载的模块。这 62 个函数在原规划中全部属于 CORE；
新的 `summarize-source-closures` 命令按 LLVM 图与最终 ELF 的符号和尺寸自动
核算，其中 42 个独立函数共 18,157 B。组合使常驻对象减少 13,237 B、
`bzImage` 减少 5,408 B，并通过 4 轮 96 步生命周期与 16 对启动 A/B。累计
严格集合达到 21 模块、371 个迁移定义，对应 242/28,959 个最终 ELF 函数
（0.835664%），对象收益 92,123 B；同配置 `bzImage` 相对纯 v32 减少
48,096 B（0.951218%）。完整证据与工作负载限制见
[v41 CORE 回调闭包成果](docs/V41_BROAD_CALLBACK_PORTFOLIO_RESULT.md)。

当前 v40 在完整图回调排名之上增加了互补发布组合：自动寻找单个源文件组
不足 4 KiB、但组合后可通过严格收益门槛的候选。本轮排名器给出 54 个双组
组合，并自动提取 `fs/proc/base.c` 与 `drivers/dma-buf/sync_file.c` 的 35 个
C 函数定义为两个惰性模块。两组单独均未通过完整体积门禁，组合后常驻对象
减少 7,821 B、`bzImage` 减少 3,808 B；4 轮 16 步生命周期、同 FD 跨卸载
重载、四路并发首次调用和 8 对启动 A/B 全部通过。严格集合现为 17 模块、
309 个迁移 C 定义，对应 200/28,959 个最终 ELF 函数（0.690632%），累计
对象收益 78,886 B。完整证据见
[v40 互补回调组合成果](docs/V40_COMPLEMENTARY_CALLBACK_PORTFOLIO_RESULT.md)。

v39 已把回调边界从单表审计扩展到完整图自动发现。新分析器从
73,906 条 LLVM 全局初始化器边中去重并识别 685 张回调表，154 张满足当前
自动安全策略，37 张具有正的接口级估算收益。排名最高的未处理同源集合是
`kernel/trace/trace.c`：工具聚合 21 张常驻 fops，自动合成 table-only
候选，并在二次闭包中只剔除匿名类型和 x86 static-key 立即数约束的两个
接口。最终 `deferred_trace_v39_cb.ko` 以 33 个懒加载接口迁移 43 个 C 函数
和 `readme_msg`。`trace.o` 永久段减少 9,824 B，`bzImage` 再减少 5,248 B；
4 轮十步生命周期和 8 对启动 A/B 全部通过。严格集合现为 15 模块、274 个
迁移 C 定义，对应 175/28,959 个最终 ELF 函数（0.604303%），累计对象收益
71,065 B、同配置 `bzImage` 收益 38,880 B。最终页仍被 2 MiB 对齐吸收。
完整证据见
[v39 完整回调表自动提取成果](docs/V39_AUTOMATIC_CALLBACK_TABLE_RESULT.md)。

v38 已把函数提取扩展到“常驻回调表、懒加载包装器、模块实现”三段式
边界。对经过审计的 `perf_fops`，工具沿 LLVM `GLOBAL_INITIALIZER` 边自动
发现 read/poll/ioctl/compat_ioctl/mmap/fasync 六个回调，把它们提升为共享
模块入口，再由 Clang AST + LLVM incoming ownership cut 迁移
`kernel/events/core.c` 的 31 个函数和 1 个只读表。`perf_fops`、
`perf_mmap_vmops`、release/VMA 回调和持久状态继续常驻；真实测试让 fd 与
VMA 跨过模块卸载，在模块缺失时完成 fault/close，再由同一 fd 重载模块。
该候选让 `core.o` 永久段减少 9,071 B、`bzImage` 减少 6,080 B。连同 v37
自动提取的 50 个 io_uring 函数，v35 之后新增 81 个迁移实现、20,262 B
对象收益。候选级严格集合现为 14 模块、231 个迁移 C 函数定义，对应
140/28,959 个最终 ELF 函数；累计对象收益 61,241 B，`bzImage` 相对 v32
纯基线减少 33,632 B。最终链接页仍被 2 MiB 对齐吸收。完整证据见
[v38 回调表提升成果](docs/V38_CALLBACK_TABLE_EXTRACTION_RESULT.md)。

v35 已把函数级提取推进到带持久状态和宏生成实体的真实子系统边界。
工具从 `swapon/swapoff` READY 入口出发，自动迁移 `mm/swapfile.c` 的
28 个 built-in C 函数，并把被写入的全局状态、地址逃逸对象、weak 实现和
已注册回调保守地留在常驻侧；宏参数精确重写和宏生成实体固定点自动补齐
锁、waitqueue 与 plist。生成的 `deferred_swap_v35.ko` 通过完整构建、
MODPOST、4 轮真实 `swapon/swapoff` 自动加载/卸载/再加载和 8 对启动 A/B。
`mm/swapfile.o` 永久段减少 9,591 B，增量 `bzImage` 减少 10,176 B，严格
门禁通过。候选级严格合格集合现为 12 模块、100/28,959 个最终 ELF 函数；
包含 v34 select 实验的机制集合为 13 模块、104/28,959。最终链接永久页
仍未减少，距离 2 MiB 对齐边界还差 211,120 B。完整证据见
[v35 生命周期安全 swap 成果](docs/V35_STATE_SAFE_SWAP_RESULT.md)。

v34 已把源码闭包继续扩展到文件私有 C 语言支撑项：Clang 提取器
记录文件作用域 typedef/enum/record 和宏定义，Python 后端从被移动实体
做最小声明/宏固定点；候选准备阶段还会在配置匹配的 `__ksymtab` 富化图
上自动发现真正缺失的外部驻留导出。真实 `select/pselect/ppoll` 闭包自动
迁移 9 个 C 函数，并补齐 3 个私有声明、3 个私有宏、4 个同源驻留依赖、
1 个复制辅助函数和 `set_user_sigmask` 导出，完整构建与 4 次 QEMU
加载/卸载/重载均通过。该候选让 `fs/select.o` 减少 1,613 B，但相对同参数
v33 的 `bzImage` 增加 4,064 B，故只进入机制验证集合，不进入严格发布
集合。机制集合为 12 模块、91/28,959 个最终 ELF 函数；严格发布集合仍为
11 模块、87/28,959。完整证据见
[v34 自动源码支撑成果](docs/V34_AUTOMATIC_SOURCE_SUPPORT_RESULT.md)。

v33 已加入“Clang AST 前向闭包 + LLVM incoming ownership cut”：从
READY 入口自动扩展同源私有函数/全局变量，并把仍被其他编译单元引用的
实体切到常驻 frontier。新生成的 `fsopen`、keyctl、signal 三个模块迁移
52 个 C 函数；与 v32 合并后共有 11 个新函数模块、122 个迁移实现，能与
基线最终 ELF 精确对应的成功函数为 **87/28,959（0.300425%）**。11 模块
内建对象未加载净减少 31,388 B，`bzImage` 相对基线减少 16,032 B；最终
链接永久区间仍为 0 B 改善、0 B 回退。三个新模块通过 4 次自动加载/卸载/
重载和 8 对平衡启动 A/B。算法、严格口径和被安全拒绝的 io_uring 大闭包见
[v33 LLVM 源码闭包成果](docs/V33_LLVM_SOURCE_CLOSURE_RESULT.md)。

此前 v32 在同一 Linux 5.10.176、x86_64、v31 自动启动配置上闭合了首批
八模块严格验证：45/28,959 个基线最终 ELF 函数通过全部门禁，受影响内建
对象减少 18,740 B，`bzImage` 减少 9,472 B，最终链接永久区间不增长。
完整记录见 [v32 扎实阶段成果](docs/V32_SOLID_RESULT.md)。

历史 Linux 5.10.176 完整内核 v29 实验使用真实 loader-ready 阶段边界，
并以强类型 `symbol_get()` 边界和共享非内联加载器降低多接口常驻桩
成本。严格 4 KiB 计划仍为 31 个 READY 候选、81 个最终 ELF 函数；
本轮在 v28 六模块基础上完成 `capget/capset` 与
`settimeofday/adjtimex_time32` 两个真实闭包。八模块均通过源码提取、
Kbuild/MODPOST、QEMU 自动加载/卸载/重载、ELF 尺寸和 16 对启动 A/B。
当时 **45/51,683 个最终 ELF 函数（0.087069%）**已成功模块化，模块
未加载时八个受影响对象合计减少 **17,954 字节**，`bzImage` 减少
**8,992 字节**。阶段模型和完整口径见
[模块加载器阶段模型](docs/LOADER_PHASE_MODEL.md)与
[完整内核统计](docs/FULL_KERNEL_ACCOUNTING.md)。

v30 另外新增不读取 planner disposition 的启动阶段对账。当前 QEMU
initramfs 样本中，最终 ELF 有 5,544 个 `BOOT_HOT`、44,473 个
`BOOT_COLD`；后者代表 12,010,088 字节的经验性启动可延迟上限。手工
把九个已有 tristate 子系统从 `y` 改为 `m` 后，完整构建、延迟加载和
8 对启动 A/B 均通过。机器可读结果见
[v30 启动延迟摘要](docs/boot-defer-profile-v30-summary.json)。

v31 将这个 profile-first 步骤自动化：依赖感知配置器只对基线中原本为
`y`、且 Kconfig 当前允许为 `m` 的 tristate 发出模块请求，并要求部署
必需项保持不变。本次 137 个显式请求经依赖传播形成 256 个 `y→m`
变化；`vmlinux` 正大小函数从 51,683 降至 28,959，减少 22,724 个
（43.968036%）。完整 `vmlinux`、`bzImage`、模块与 MODPOST 均通过。
8 对交错 A/B 中，未加载模块的永久内核内存中位数减少 10,295 KiB，
READY 提前 0.350 秒，`MemAvailable` 增加 16,338 KiB；`e1000` 还通过
READY 时未加载、随后 `modprobe` 和卸载的场景验证。这部分仍属于已有
Kconfig 能力，不计作新函数提取成果。扣除后还剩 23,362 个
`BOOT_COLD` 函数、5,373,211 字节，作为下一阶段函数级模块化的经验
收益池。完整口径和限制见
[v31 自动启动配置摘要](docs/boot-defer-auto-profile-v31-summary.json)。

旧的 DOT/文本分析器和基于 `kallsyms_lookup_name` 的后端仍保留在仓库中
供对照，但不属于默认生产链路。

## 快速检查

要求 Python 3.10+、Clang/LLVM（已验证 LLVM 22；代码兼容 LLVM 15 的
关键接口）以及带 `libclang-cpp` 的开发文件。

```bash
make tools LLVM_HOME=/absolute/path/to/llvm
make test
```

也可以安装命令行入口：

```bash
python3 -m pip install -e .
kernel-modularizer --help
```

所有 Python 核心功能均不依赖第三方包。

自动生成依赖感知的 Kconfig 模块配置需要可选依赖：

```bash
python3 -m pip install -e '.[boot-profile]'
```

## 端到端流程

以下变量仅用于说明：

```bash
KERNEL=/absolute/path/to/linux
OUT=/absolute/path/to/kernel-build
RUN=/absolute/path/to/modularizer-run
LLVM_HOME=/absolute/path/to/llvm
```

### 1. 使用同一次配置和编译生成内核与 IR

推荐让 Clang 在正常 ELF `.o` 旁生成同参数 sidecar bitcode。这样分析
对象与最终内核来自同一次配置和同一次编译，而不是另一套近似构建；
同时兼容 LLVM 22 不允许 `-fembed-bitcode` 与 x86 内核 code-model
参数组合的限制。

```bash
make -C "$KERNEL" O="$OUT" LLVM=1 defconfig
make -C "$KERNEL" O="$OUT" LLVM=1 \
  KCFLAGS=-save-temps=obj -j"$(nproc)"

python3 -m kernel_modularizer extract-bitcode \
  --object-root "$OUT" \
  --output-directory "$RUN/bitcode" \
  --manifest-output "$RUN/bitcode-manifest.json"
```

提取器同时支持旧工具链的 ELF `.llvmbc` 和 `-save-temps=obj` 生成的
同名 `.bc`。汇编对象等没有 bitcode 的文件会被记录为跳过；如果整个
构建没有 bitcode，命令会硬失败。

做真实 `vmlinux` 比例统计时，不应扫描输出目录中的 vDSO、宿主工具和
已经是 `.ko` 的对象。将 `KBUILD_VMLINUX_OBJS` 中的每个对象或薄归档
作为重复的 `--link-input` 传入，并指定匹配工具链的 `--ar`；提取清单
会记录链接输入和分母：

```bash
python3 -m kernel_modularizer extract-bitcode \
  --object-root "$OUT" \
  --link-input init/built-in.a \
  --link-input drivers/built-in.a \
  --ar "$LLVM_HOME/bin/llvm-ar" \
  --output-directory "$RUN/vmlinux-bitcode" \
  --manifest-output "$RUN/vmlinux-bitcode-manifest.json"
```

### 2. 构建保守引用图

```bash
python3 Frontend/scripts/generate_reference_facts.py \
  --opt "$LLVM_HOME/bin/opt" \
  --plugin Frontend/LLVM_PASS/ReferenceFacts.so \
  --bitcode-root "$RUN/bitcode" \
  --source-root "$KERNEL" \
  --output "$RUN/facts"

python3 Frontend/scripts/solve_reference_facts.py \
  --facts "$RUN/facts" \
  --graph-output "$RUN/reference-graph.json" \
  --points-to-output "$RUN/points-to.json"

python3 -m kernel_modularizer enrich-sizes \
  "$RUN/reference-graph.json" \
  --facts-manifest "$RUN/facts/manifest.json" \
  --object-root "$OUT" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --objdump objdump \
  --graph-output "$RUN/reference-graph-sized.json" \
  --report-output "$RUN/symbol-sizes.json"

python3 -m kernel_modularizer validate-graph \
  "$RUN/reference-graph-sized.json"
```

`reference-graph.json` 是版本化 JSON，而不是以函数名为唯一键的 DOT。
每条边都保留种类、位置、字段路径、执行上下文和证据来源。
大小富化还会从 `__ksymtab_*`、`.pci_fixup_*` 和
`.x86_cpu_dev.init` 的 ELF 符号/重定位中恢复导出、直接注册回调及
CPU 厂商全局回调表，避免把系统调用、PCI fixup 或早期 CPU 初始化误作
可移动代码。
全量运行默认只在 `points-to.json` 保存集合规模、调用目标和 unresolved
点，不复制可能达到数十 GiB 的全部点集；调试小数据集时可显式加入
`--include-points-to`。

完整内核的 opaque-pointer 约束可产生上亿条点集关系，推荐安装压缩集合
后端并使用默认的自动选择。安装后，`auto` 会运行 Roaring 基础求解，
并仅对基础求解仍未解析的调用点执行字段、对象布局和 ABI 敏感回退：

```bash
python3 -m pip install '.[full-kernel]'
```

运行清单会记录实际使用的求解器后端。完整内核默认限制单个精确点集为
4096 个目标；超过预算的变量会标记为 saturated，并把受影响的间接
调用及潜在目标闭包保守归入 UNKNOWN。`--max-points-to-set 0` 可请求
无界精确求解，但其内存需求可能远超普通工作站。

### 3. 采集启动证据并规划边界

内核需启用 `CONFIG_TRACING`、`CONFIG_FTRACE`、
`CONFIG_FUNCTION_TRACER` 和 `CONFIG_DYNAMIC_FTRACE`。部分内核版本
没有独立的 `CONFIG_TRACEFS_FS` 配置项。早期启动可在命令行加入
`ftrace=function trace_buf_size=64M initcall_debug`，用户态起来后立即
冻结并导出 snapshot。仓库提供了可复现的观测 initramfs：

```bash
python3 Runtime/build_boot_trace_initramfs.py \
  --busybox /absolute/path/to/static-busybox \
  --kernel-release "$(make -s -C "$KERNEL" O="$OUT" kernelrelease)" \
  --output "$RUN/boot-trace-initramfs.cpio.gz"

qemu-system-x86_64 -nodefaults -no-reboot -nographic -serial stdio \
  -smp 1 -m 1024 \
  -kernel "$OUT/arch/x86/boot/bzImage" \
  -initrd "$RUN/boot-trace-initramfs.cpio.gz" \
  -append 'console=ttyS0 panic=-1 rdinit=/init ftrace=function trace_buf_size=64M initcall_debug' \
  > "$RUN/boot.trace"

python3 -m kernel_modularizer observe-boot \
  "$RUN/reference-graph-sized.json" "$RUN/boot.trace" \
  --require-loader-ready-marker \
  --output "$RUN/boot-observations.json"
```

先单独生成不受 `CORE/READY/BLOCKED` 影响的最终 ELF 启动分层：

```bash
python3 -m kernel_modularizer summarize-boot-phase \
  "$RUN/reference-graph-sized.json" \
  --observations "$RUN/boot-observations.json" \
  --vmlinux "$OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --json-output "$RUN/boot-phase-stats.json" \
  --markdown-output "$RUN/boot-phase-stats.md"
```

该报告使用 `BOOT_HOT`、`POST_BOOT_ONLY`、`BOOT_COLD`、`UNKNOWN` 四个
互斥类别，并给出完整源文件均为启动冷代码的聚类。`BOOT_COLD` 是当前
工作负载的经验上限，不等于已经通过模块提取门禁。

在新造函数级模块之前，先自动使用内核已经具备的 tristate 模块边界：

```bash
PROFILE=/absolute/path/to/profile-build
mkdir -p "$PROFILE"

python3 Runtime/plan_boot_module_profile.py \
  --kernel-root "$KERNEL" \
  --base-config "$OUT/.config" \
  --keep BINFMT_SCRIPT \
  --keep SERIAL_8250 \
  --arch x86 --srcarch x86 \
  --cc clang --ld ld.lld \
  --output-config "$PROFILE/.config" \
  --report-output "$RUN/boot-module-profile.json"

make -C "$KERNEL" O="$PROFILE" LLVM=1 olddefconfig
make -C "$KERNEL" O="$PROFILE" LLVM=1 -j"$(nproc)" \
  vmlinux bzImage modules
```

生成器只显式请求基线中已启用、当前可变为 `m` 的 tristate；Kconfig
依赖与 choice 传播导致的所有附带变化都会进入报告。上面的 keep-list
只适用于本项目的最小 QEMU/initramfs 场景。真实部署必须把根文件系统、
启动存储、解密、控制台和早期网络等必需项加入 keep-list，或保证它们在
使用前被预加载，并用同一工作负载重新做启动 A/B。完整子系统连同
initcall 一起移动时，不要求该 initcall 在旧 built-in 启动轨迹里从未
执行；判据是新配置在模块未加载时仍能到达 loader-ready，随后功能可通过
`modprobe` 恢复。

观测 initramfs 不以“PID 1 已出现”直接代表模块可加载。它会确认目标内核
模块子系统、内核 modprobe 路径、`/sbin/modprobe`、`modules.dep` 和
tracefs 均可用，再写入
`LINUX_MODULARIZER_MODULE_LOADER_READY`。schema-v2 observation 保留
每个函数的首次/末次时间戳以及 marker 前后阶段。启用
`enforce_loader_ready_phase` 后，缺 marker 或存在未分阶段的启动观测会
硬失败。

规划策略示例：

```json
{
  "schema_version": 1,
  "boot_roots": ["start_kernel"],
  "resident_roots": ["reviewed_process_entry"],
  "load_safe_roots": ["reviewed_process_entry"],
  "deferred_roots": ["large_optional_function"],
  "allow_dead_code_removal": false
}
```

`load_safe_roots` 是显式人工审查结论：这些常驻入口只能在允许睡眠并可
调用用户态 helper 的进程上下文执行。没有上下文证据的常驻入口默认会把
依赖闭包留在核心内核。

阶段感知策略还会把候选划为四类：marker 前可达的 `EARLY_CORE`、
可在首次进程上下文调用时加载的 `LAZY_READY`、必须在 IRQ/atomic 回调
注册前预加载的 `PRELOAD`，以及证据不足的 `UNKNOWN_PHASE`。其中只有
`LAZY_READY` 可以继续尝试自动 `request_module()`；`PRELOAD` 仍需独立
的注册和卸载生命周期证明。

某些兼容 ABI 系统调用表由其他编译单元或生成文件提供，包装器可能无法
从当前 IR 获得 `syscall_entry` 属性。策略可用
`structural_syscall_entry_prefixes` 显式声明目标架构已审计的保留入口
命名空间；匹配包装器保持常驻，只把其调用体作为候选。该字段不是默认
名称猜测，迁移到其他架构时必须重新审计。

```bash
python3 -m kernel_modularizer plan \
  "$RUN/reference-graph-sized.json" \
  --policy "$RUN/planner-policy.json" \
  --observations "$RUN/boot-observations.json" \
  --json-output "$RUN/plan.json" \
  --markdown-output "$RUN/plan.md" \
  --manifest-output "$RUN/plan-manifest.json" \
  --fail-on-unknown
```

启动阶段未观测到只是一条负证据，不会单独触发模块化。只有边界闭合、
间接调用已解析、入口可加载、常驻依赖可导出且状态所有权清晰的候选才
能进入后端。规划报告默认还要求候选的静态净收益至少为 4096 字节；
默认按共享加载器后端的实测上界使用“首接口 640 字节、同候选后续接口
96 字节”的摊销模型估算常驻成本。经过审计的多个正收益小候选可以组成同一发布
收益组，但组合规则只能消除“低于 4 KiB”这一项原因，不能绕过启动阶段、
上下文、边界或证据阻塞。这些都只是前置筛选，不替代构建后的 ELF
门禁。报告的
`summary.candidate_viability` 会逐级给出正收益、常驻接口、真实执行边、
已证明可睡眠接口和最终 READY 数，便于区分“看起来未使用”与“确实可按需
加载”。

规划完成后，以最终链接的 `vmlinux` 正大小文本符号作为真实分母。工具
按“符号名 + 字节大小”逐个对账；无法从 IR 精确映射的汇编、链接器或
生成符号一律进入 UNKNOWN，而不是从分母中消失：

```bash
python3 -m kernel_modularizer summarize-full-kernel \
  "$RUN/reference-graph-sized.json" \
  --plan "$RUN/plan.json" \
  --vmlinux "$OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --json-output "$RUN/full-kernel-stats.json" \
  --markdown-output "$RUN/full-kernel-stats.md"
```

只有同时通过源码提取、内核构建、QEMU 启动、`modprobe`、卸载、真实
尺寸和启动 A/B 七项门禁的 READY 候选，才会计入“成功模块化函数”。
若实验放宽了可选的压缩镜像上限，统计必须标成“机制验证”；严格发布集合
还要求配置的 `bzImage` 门禁通过，二者不能混用。
完整 Linux 5.10.176 实测分母和当前结果见
[完整内核统计](docs/FULL_KERNEL_ACCOUNTING.md)。

若对象层已经变小而最终 `vmlinux` 区间不动，可直接诊断链接器对齐吸收：

```bash
python3 -m kernel_modularizer analyze-linker-alignment \
  "$BASE_OUT/vmlinux" "$MOD_OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --json-output "$RUN/linker-alignment.json" \
  --markdown-output "$RUN/linker-alignment.md"
```

在当前 x86 实验中，对齐前 payload 已累计减少 65,536 B，但全部转成
entry text 前的 2 MiB 对齐填充；还需减少 190,640 B 才能触及前一边界。
这个报告把“对象节省但启动页为 0”的原因和下一阈值明确分开。

### 4. AST 提取并生成模块

先为已配置内核生成 `compile_commands.json`。Linux 自带脚本通常可用：

```bash
python3 "$KERNEL/scripts/clang-tools/gen_compile_commands.py" \
  -d "$OUT" -o "$RUN/compile_commands.json"
```

选择 `plan.json` 中的候选 ID：

```bash
python3 -m kernel_modularizer prepare-candidate \
  "$RUN/reference-graph-sized.json" "$RUN/plan.json" \
  candidate:0123456789abcdef \
  --source-extractor Backend/AutoBackend/cpp/SourceExtractor \
  --compile-database "$RUN/compile_commands.json" \
  --kernel-root "$KERNEL" \
  --module-name deferred_example \
  --output-directory "$RUN/deferred_example"
```

对于结构体返回值或具有特殊失败语义的接口，可通过
`--failure-expressions` 传入 JSON 映射。后端会拒绝变参、匿名参数、
init/exit/noinstr/特殊 section、per-CPU/静态键等不能安全搬移的实体。

需要审计整个编译单元闭包时，`SourceExtractor` 还支持
`--all-main-functions` 和 `--all-main-globals`；宏生成的函数/全局定义不会
被该模式自动选中。提取器还记录主文件的文件作用域 typedef、enum、具名
record 和 `#define` 精确区间。提取器输出明确标记 `utf-8-bytes`，Python
后端会在源码改写前转换 Clang 字节偏移，因此非 ASCII 注释不会破坏区间。

生产入口可直接增加 `--expand-private-source-closure`：后端会从候选入口
递归扩展 AST 依赖，并用 LLVM 图的跨编译单元入边自动识别不能一起搬走的
常驻 frontier；随后从移动实体和打包驻留签名递归选择实际使用的私有声明
与宏定义。图中缺失的实体会导致硬失败。若配置匹配的大小富化图已导入
完整 `__ksymtab` 事实，工具还会自动推断 source-defined、external-linkage
且尚未导出的跨文件驻留依赖；原始 LLVM 图不会启用这项推断。ABI 上属于
同一功能的候选可用重复的 `--merge-candidate` 合并，但主候选必须 READY；
附加候选只有在
唯一缺口为函数尺寸、阶段仍为 `LAZY_READY` 且所有入口均 load-safe 时才
允许加入，不能借此绕过 IRQ/atomic、启动阶段或未解析调用门禁。

对 file_operations 等常驻函数指针表，可在逐表审计执行上下文和对象生命
周期后使用 `--promote-callback-table TABLE`。工具会沿 LLVM 全局初始化器
边把同源字段目标提升为额外接口；不适合移动的字段用重复的
`--exclude-interface FUNCTION` 留驻。例如：

```bash
python3 -m kernel_modularizer prepare-candidate \
  "$RUN/reference-graph-sized.json" "$RUN/plan.json" CANDIDATE \
  --source-extractor Backend/AutoBackend/cpp/SourceExtractor \
  --compile-database "$RUN/compile_commands.json" \
  --kernel-root "$KERNEL" \
  --module-name deferred_callbacks \
  --expand-private-source-closure \
  --promote-callback-table example_fops \
  --exclude-interface example_release \
  --pack-resident-dependencies \
  --bind-packed-dependencies-on-first-use \
  --output-directory "$RUN/deferred_callbacks"
```

这不是对所有回调表的无条件自动提升。IRQ、timer、work、RCU、VMA 或设备
回调可能在不可睡眠上下文执行，或在包装器释放模块引用后仍被长期对象持有，
必须先留驻或提供专门的生命周期 pin。

完整图可以先自动排名，并按生产体积门槛输出互补源文件组合：

```bash
python3 -m kernel_modularizer rank-callback-tables \
  "$RUN/reference-graph-sized.json" "$RUN/plan.json" \
  --minimum-release-savings 4096 \
  --json-output "$RUN/callback-ranking.json" \
  --markdown-output "$RUN/callback-ranking.md"
```

需要把排名直接推进到逐源文件的自动源码闭包和模块 bundle 时，使用批量
入口。每组在独立目录运行，失败默认只记录本组并继续；顶层 JSON/Markdown
和每组阶段 manifest 都按哈希记录，可安全续跑：

```bash
python3 -m kernel_modularizer prepare-callback-portfolio \
  "$RUN/reference-graph-sized.json" "$RUN/plan.json" \
  --source-extractor Backend/AutoBackend/cpp/SourceExtractor \
  --compile-database "$RUN/compile_commands.json" \
  --kernel-root "$KERNEL" \
  --output-directory "$RUN/callback-portfolio" \
  --minimum-estimated-net-bytes -4096
```

默认门槛为 `0`，并保留未知大小组；负值用于激进探索，让真实源码闭包和
对象尺寸淘汰图上低估的候选。可重复使用 `--only-source`/`--exclude-source`
限定范围，`--max-source-groups` 限制批量大小，`--integrate` 事务应用成功
bundle，`--integration-dry-run` 只验证应用。`PREPARED` 只表示闭包与 bundle
生成成功，不等价于构建或运行验证；发布前仍必须执行组合 `validate-size`
以及首次使用、卸载和重载场景。

`release_portfolios` 只组合每个源文件组已经扣除常驻包装器成本后的正收益
估算，并排除本身已达到门槛的组。它用于决定闭包生成顺序，不是发布证明；
源码闭包可能增加或减少实际收益，压缩布局也可能让单个候选的 `bzImage`
反向变化。组合发布时，`validate-size` 应重复传入每个受影响的
`--baseline-object`、`--resident-object` 和 `--module-object`，以同一最终
`vmlinux` 和 `bzImage` 做一次组合门禁。

```bash
python3 -m kernel_modularizer prepare-candidate \
  "$RUN/reference-graph-sized.json" "$RUN/plan.json" \
  candidate:0123456789abcdef \
  --source-extractor Backend/AutoBackend/cpp/SourceExtractor \
  --compile-database "$RUN/compile_commands.json" \
  --kernel-root "$KERNEL" \
  --module-name deferred_example \
  --expand-private-source-closure \
  --pack-resident-dependencies \
  --output-directory "$RUN/deferred_example"
```

后端策略可显式配置 `duplicate_functions`、`resident_exports`、
`external_resident_exports`、`pack_resident_dependencies`、
`direct_resident_dependencies` 和 `module_defines`。它们分别用于复制经
审计的纯静态辅助函数、声明同编译单元或外部常驻依赖、把可精确改写的依赖
打包到一张只读强类型表、将宏/头文件内联隐藏而无法改写的依赖降级为逐项
审计的直接导出，以及补充受限的单行常量宏或函数式宏。静态初始化器需要
常量函数地址时，打包器会生成 ABI 匹配的本地 trampoline。所有扩大可见性
的操作都会进入 bundle manifest；自动复制的私有声明/宏也写入
`source_local_support`。公共懒加载 ABI 若直接使用源文件私有类型会被明确
拒绝；未配置的静态符号不会被自动改写，最终仍由 MODPOST 验证符号边界。

### 5. 事务集成、构建和体积门禁

先做只读验证，再应用：

```bash
python3 -m kernel_modularizer apply-bundle \
  "$RUN/deferred_example/bundle" \
  --kernel-root "$KERNEL" --dry-run

python3 -m kernel_modularizer apply-bundle \
  "$RUN/deferred_example/bundle" \
  --kernel-root "$KERNEL"
```

命令输出 transaction ID。任何源文件漂移、哈希不符、路径逃逸或
Kbuild/Kconfig 冲突都会在写入前失败。需要恢复时：

```bash
python3 -m kernel_modularizer rollback-bundle \
  --kernel-root "$KERNEL" --transaction-id TRANSACTION_ID
```

构建基线和模块化内核后，以受影响的内建 `.o` 做严格门禁：

```bash
python3 -m kernel_modularizer validate-size \
  --baseline-object "$BASE_OUT/path/to/source.o" \
  --resident-object "$MOD_OUT/path/to/source.o" \
  --module-object "$MOD_OUT/kernel/linux_modularizer/deferred_example.ko" \
  --baseline-linked-kernel "$BASE_OUT/vmlinux" \
  --resident-linked-kernel "$MOD_OUT/vmlinux" \
  --baseline-image "$BASE_OUT/arch/x86/boot/bzImage" \
  --resident-image "$MOD_OUT/arch/x86/boot/bzImage" \
  --minimum-resident-savings 4096 \
  --maximum-linked-kernel-regression 0 \
  --maximum-image-regression 0 \
  --output "$RUN/size-report.json"
```

门禁同时检查三层：受影响内建对象中 init 段释放后的永久 allocatable ELF
字节、最终链接 `vmlinux` 的代码/只读/可写/BSS 区间，以及可选的压缩
`bzImage`。对象层用于归因；最终链接层用于捕获 ksymtab、kallsyms、链接
脚本和页对齐造成的真实常驻回退；镜像层只检查发布体积。默认对象永久净
收益下限为 4096 字节，`--maximum-linked-kernel-regression 0` 和
`--maximum-image-regression 0` 要求后两层均不增长。若只做机制验证，可
显式调低 `--minimum-resident-savings`，但不能据此把微型候选标为生产
收益。

### 6. QEMU 自动加载与启动资源回归

```bash
python3 Runtime/build_validation_initramfs.py \
  --busybox /absolute/path/to/static-busybox \
  --kernel-release "$(make -s -C "$KERNEL" O="$MOD_OUT" kernelrelease)" \
  --module "$MOD_OUT/kernel/linux_modularizer/deferred_example.ko" \
  --output "$RUN/initramfs.cpio.gz"

python3 -m kernel_modularizer qemu-validate \
  "$RUN/qemu-scenario.json" \
  --log-output "$RUN/qemu.log" \
  --json-output "$RUN/qemu-result.json"

python3 -m kernel_modularizer qemu-ab-benchmark \
  "$RUN/qemu-baseline-scenario.json" \
  "$RUN/qemu-modular-scenario.json" \
  --pairs 4 \
  --log-directory "$RUN/qemu-ab-logs" \
  --json-output "$RUN/qemu-ab.json"
```

场景格式见 `Runtime/qemu-scenario.example.json`。合格场景至少验证模块
起初不存在、首次功能调用后出现、`rmmod` 成功，以及再次调用后重新
加载。initramfs 在 READY 前记录内核永久段汇总、uptime、
`MemAvailable` 和 Slab。A/B 命令按“基线→模块化、模块化→基线”交替
运行相邻样本，使用中位数门禁，减少宿主机随时间漂移造成的假回归。
默认拒绝永久内核内存增长、超过 0.25 秒的启动回归、超过 128 KiB 的
可用内存回归或超过 64 KiB 的 Slab 增长。串口日志始终保存，超时或
QEMU 提前退出都视为失败。

比较几 KiB 级变化时，建议给基线和模块化场景使用相同的 `nokaslr`
内核参数，避免随机内核布局掩盖真实差异；这只提高实验重复性，不放宽
运行时安全门禁。

## 安全边界

- `request_module()` 不能用于 IRQ、NMI、atomic、禁中断或早期启动路径。
  规划器会保守地常驻这些闭包，生成桩在运行时也有二次检查。
- 未解析间接调用永远不是空边；其调用者为 `UNKNOWN`。
- 模块只能依赖已导出的常驻符号；否则候选在规划阶段被拒绝，最终还要
  通过内核 MODPOST。
- `symbol_get()` 在获得模块 operations 表时增加模块引用，
  `symbol_put()` 在实现返回后释放；整个调用期间模块文本不能被卸载。
  边界不分配堆内存，也不再维护每接口 RCU 发布状态。
- 自动提取只会对 `resident_exports` 中逐项审计并写入 manifest 的
  同编译单元依赖去掉 `static`；其他静态符号保持原可见性，所有模块
  依赖仍须通过正式导出和 MODPOST，不通过 kallsyms 绕过规则。
- 启用常驻依赖打包时，只会改写 AST 已提供精确引用区间的调用；静态
  初始化器使用类型匹配 trampoline。宏或头文件内联隐藏的引用必须列入
  `direct_resident_dependencies`，不能靠文本替换猜测。
- Clang 发现 `&global` 后会把该对象标为源码地址发布边界。即使它是 const，
  也默认常驻，避免 VMA、回调拥有者或其他长期对象在模块卸载后留下悬空
  数据地址；只有引入并验证专门的生命周期 pin 后才应放宽。
- 当前后端只自动处理 C 函数和候选私有全局变量。汇编入口、链接脚本
  特殊对象、异常表/替代指令等保留在核心或进入 `UNKNOWN`。

实现细节见 [架构说明](docs/ARCHITECTURE.md)，当前结果见
[v39 完整回调表自动提取成果](docs/V39_AUTOMATIC_CALLBACK_TABLE_RESULT.md)、
[v38 回调表提升成果](docs/V38_CALLBACK_TABLE_EXTRACTION_RESULT.md)，此前的
[v33 LLVM 源码闭包成果](docs/V33_LLVM_SOURCE_CLOSURE_RESULT.md)与
[v32 扎实阶段成果](docs/V32_SOLID_RESULT.md)和历史 Linux 5.10.176 v29
验证记录见 [验证报告](docs/REAL_KERNEL_VALIDATION.md)。
