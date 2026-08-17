# 完整内核函数模块化统计（v29 历史基线、v31 配置、v32-v39 同配置全图）

本报告记录 Linux 5.10.176、x86_64 上从 v29 到 v39 的连续实验。各阶段
内部都使用配对的基础 `.config`、Clang/LLVM 和构建元数据；v31/v32 的
自动启动配置与 v29 基线不同，因此只在各自分母内计算比例。统计分母均
来自最终链接镜像中的正大小函数/文本符号，不使用 IR 声明数、源码函数数
或候选数代替。

v29 保留 v28 已验证的 loader-ready 启动边界、完整引用图和所有安全
阻塞规则，在共享加载器优化之上继续把 READY 转化为真实模块：

1. 新生成的多接口模块只导出一张强类型 operations 表，不再为每个接口
   分别导出实现符号；已验证的 v27 模块仍保持原有强类型逐接口边界；
2. 新生成模块在同一常驻源文件内共享一个 `noinline` 懒加载器，集中执行
   `symbol_get()`、上下文检查、`request_module()` 和重试；
3. 后端可以显式复制经审计的纯静态辅助函数、导出最小常驻依赖，并记录
   已由其他 bundle 提供的导出；
4. `module_defines` 支持受限且可审计的单行函数式宏；
5. 以七接口真实 `kernel/signal.o` 重新校准后续接口的规划成本；
6. 新增 capability 与 wallclock 闭包，并在八模块组合中消除
   `find_task_by_vpid` 重复导出。

## CORE 口径修正

本报告的 `CORE` 是 planner 结论，含义是“在当时接口、依赖和加载安全
策略下暂时留在常驻镜像”，不是“启动阶段实际执行”或“启动绝对必需”。
因此 48,385 不能作为启动核心函数数。

v30 已新增完全不读取 planner disposition 的最终 ELF 启动对账。在同一
loader-ready 样本中，实际精确映射到最终 ELF 的 `BOOT_HOT` 为 5,544
个函数、1,460,841 字节；未观测的 `BOOT_COLD` 为 44,473 个函数、
12,010,088 字节。后者是该工作负载的经验性启动可延迟上限，不等于每个
函数都已通过源码提取和动态加载门禁。完整结果见
[`boot-defer-profile-v30-summary.json`](boot-defer-profile-v30-summary.json)。

此外，完整子系统边界可以连同 initcall 一起延后，所以“启动轨迹中执行
过”也不必然等于不可模块化。v30 将九个已有 tristate 子系统改为模块，
8 对启动 A/B 全部通过，常驻内核中位数减少 5,162 KiB；这部分作为已有
Kconfig 能力的 profile-first 基线，不计入新的函数提取器成果。
扣除这部分后，残余 built-in 镜像仍有 33,121 个 `BOOT_COLD` 函数、
8,282,414 字节，作为下一阶段自动提取的独立收益池；发布统计需在该
profile 配置上重新生成 trace 和完整引用图。

v31 已把 profile-first 自动化。依赖感知生成器对 137 个原本为 `y`、
可变为 `m` 的 tristate 发出请求，Kconfig 固定点最终产生 256 个
`y→m` 变化；部署 keep-list 中的 `BINFMT_SCRIPT` 和 `SERIAL_8250`
保持内建。完整构建和 MODPOST 通过后：

| 指标 | 基线 | v31 profile | 变化 |
|---|---:|---:|---:|
| 最终 ELF 正大小函数 | 51,683 | 28,959 | -22,724 (-43.968036%) |
| 最终 ELF 函数字节 | 13,506,837 | 6,693,841 | -6,812,996 (-50.441091%) |
| 永久 allocatable ELF 字节 | 26,854,034 | 16,221,225 | -10,632,809 (-39.594830%) |
| `bzImage` 字节 | 9,756,736 | 5,056,256 | -4,700,480 (-48.176767%) |

8 对平衡交错 QEMU A/B 全部通过：永久内核内存中位数减少 10,295 KiB，
loader-ready 提前 0.350 秒，`MemAvailable` 增加 16,338 KiB，Slab
减少 6,394 KiB。e1000 的延迟 `modprobe`/卸载场景通过；其余模块完成
构建和 MODPOST，但尚未逐项做功能负载。因此这是当前 QEMU/initramfs
部署画像的结果，不是通用硬件配置证明。机器可读报告见
[`boot-defer-auto-profile-v31-summary.json`](boot-defer-auto-profile-v31-summary.json)。

v31 残余 built-in 镜像还有 23,362 个 `BOOT_COLD` 函数、5,373,211
字节，其中 560 个完整冷源文件包含 5,942 个函数、1,517,810 字节。
这些数复用了基线 trace 和引用图，仅用于排列下一轮函数提取优先级；在
v31 配置上重新生成 IR、引用图和启动 trace 后，才能作为发布分母。

## v32 当前结论

v32 已在 v31 自动配置上重新生成匹配该配置的完整 LLVM 引用图和新鲜
loader-ready trace，不再复用旧配置的图作为发布分母。最终 `vmlinux`
包含 28,959 个正大小函数、6,693,841 字节；其中 28,129 个函数
（97.133879%）可按 `(symbol, size_bytes)` 精确映射。

| 分类 | 函数数 | 函数占比 | 字节数 | 字节占比 |
|---|---:|---:|---:|---:|
| CORE | 26,732 | 92.309817% | 6,353,289 | 94.912458% |
| READY | 81 | 0.279706% | 56,123 | 0.838427% |
| BLOCKED | 296 | 1.022135% | 42,480 | 0.634613% |
| UNKNOWN | 1,850 | 6.388342% | 241,949 | 3.614502% |

八个函数模块共提取 70 个 C 函数，其中 45 个与最终 ELF 的独立正大小
函数精确对应，共 22,359 字节。真实成功比例为 **45/28,959 =
0.155392%**，函数字节比例为 **0.334023%**。受影响内建对象减少
18,740 B，最终链接永久区间变化 0 B，`bzImage` 减少 9,472 B；4 次
加载/卸载/重载和 8 对启动 A/B 全部通过。对象收益尚未跨过最终链接页
边界，因此当前不能声称启动永久页减少。详细证据和哈希见
[`V32_SOLID_RESULT.md`](V32_SOLID_RESULT.md) 与
[`full-kernel-v32-solid-summary.json`](full-kernel-v32-solid-summary.json)。

## v33-v35 当前结论

v33 的 LLVM incoming ownership cut 与 AST 前向源码闭包新增三个严格发布
模块，将集合扩大到 11 模块、122 个迁移 C 实现、87 个最终 ELF 函数
（0.300425%）和 37,516 B（0.560455%）。内建对象未加载合计减少
31,388 B，`bzImage` 相对 v32 纯基线减少 16,032 B；最终链接永久区间
仍未跨页。详见
[`V33_LLVM_SOURCE_CLOSURE_RESULT.md`](V33_LLVM_SOURCE_CLOSURE_RESULT.md)。

v34 新增源文件私有 typedef/enum/record 与宏固定点，并以配置匹配的导出
事实自动推断外部驻留边界。`select/pselect/ppoll` 的 9 函数真实闭包通过
Kbuild/MODPOST 和 QEMU 自动加载、卸载、重载；4 个独立最终 ELF 函数共
2,013 B。它使内建 `fs/select.o` 减少 1,613 B，但增量 `bzImage` 增加
4,064 B，严格零回退发布门禁失败。因此需要同时报告两种集合：

- 机制验证：12 模块、131 个迁移 C 实现、91 个最终 ELF 函数
  （0.314237%）、39,529 B（0.590528%）；
- 严格发布：仍为 11 模块、122 个迁移 C 实现、87 个最终 ELF 函数
  （0.300425%）、37,516 B（0.560455%）。

累计对齐前 payload 已减少 36,864 B，但最终 text 区间仍被 2 MiB 对齐
吸收，距离前一边界还差 219,312 B。详见
[`V34_AUTOMATIC_SOURCE_SUPPORT_RESULT.md`](V34_AUTOMATIC_SOURCE_SUPPORT_RESULT.md)
与
[`full-kernel-v34-source-support-summary.json`](full-kernel-v34-source-support-summary.json)。

v35 进一步加入模块生命周期状态边界、地址逃逸回调/全局边界、宏参数精确
重写、宏生成实体固定点和 `.data..ro_after_init` 强类型依赖表。从
`mm/swapfile.c` 自动迁移 28 个 built-in C 函数；13 个独立最终 ELF
函数共 9,992 B。`mm/swapfile.o` 永久段减少 9,591 B，增量 `bzImage`
减少 10,176 B，最终链接永久区间不增长；4 轮真实 swap 生命周期和 8 对
启动 A/B 通过，故该候选进入严格合格集合。

- 机制验证：13 模块、159 个迁移 C 实现、104 个最终 ELF 函数
  （0.359128%）、49,521 B（0.739799%）；
- 候选级严格合格：12 模块、150 个迁移 C 实现、100 个最终 ELF 函数
  （0.345316%）、47,508 B（0.709727%）。

当前可启动 v35 产物仍包含只通过机制门禁的 v34 select 模块，因此第一项
对应当前机制超集，第二项是排除 v34 的候选级严格汇总。相对 v32 纯基线，
对齐前 payload 累计减少 45,056 B，但最终 text 区间仍被 2 MiB 对齐吸收，
距离前一边界还差 211,120 B。详见
[`V35_STATE_SAFE_SWAP_RESULT.md`](V35_STATE_SAFE_SWAP_RESULT.md) 与
[`full-kernel-v35-state-safe-swap-summary.json`](full-kernel-v35-state-safe-swap-summary.json)。

v37 用首次调用绑定的强类型依赖表解锁 `io_uring/io_uring.c`，迁移 50 个
C 函数；24 个基线最终 ELF 函数共 13,480 B。内建对象永久段减少
11,191 B，`bzImage` 减少 5,408 B。v38 再对经过审计的 `perf_fops` 使用
LLVM 全局初始化器边自动提升六个回调入口，迁移 31 个函数和 `if_tokens`；
16 个基线最终 ELF 函数共 10,961 B。`kernel/events/core.o` 永久段减少
9,071 B，`bzImage` 减少 6,080 B。

v38 同时补上 const 全局地址发布边界：`vma->vm_ops = &perf_mmap_vmops`
使整个 vm_ops 表和其 fault/open/close 回调常驻。QEMU 在模块卸载后用仍
存活的 VMA 实际触发 fault 和 close，再由同一个 fd 重载模块。两个新模块
均通过 4 次生命周期和 8 对启动 A/B。

- 机制验证：15 模块、240 个迁移 C 实现、144 个最终 ELF 函数
  （0.497255%）、73,962 B（1.104926%）；
- 候选级严格合格：14 模块、231 个迁移 C 实现、140 个最终 ELF 函数
  （0.483442%）、71,949 B（1.074854%）。

严格集合的受影响对象累计减少 61,241 B，`bzImage` 相对 v32 纯基线减少
33,632 B。对齐前 payload 累计减少 65,536 B，但仍全部转为 padding，距离
跨过 2 MiB 边界还差 190,640 B。详见
[`V38_CALLBACK_TABLE_EXTRACTION_RESULT.md`](V38_CALLBACK_TABLE_EXTRACTION_RESULT.md)
与
[`full-kernel-v38-callback-table-summary.json`](full-kernel-v38-callback-table-summary.json)。

v39 对完整 LLVM 图中的 73,906 条本地全局初始化器边去重并恢复结构字段，
识别 685 张回调表；154 张在当前进程上下文和生命周期规则下为 READY，37 张
具有正的接口级估算收益。排名第一的未处理同源组是
`kernel/trace/trace.c` 的 21 张 fops。table-only 准备器从 35 个冷接口开始，
自动把依赖匿名 `trace_clocks` 类型的 `tracing_stats_read` 和要求 static-key
链接期地址的 `tracing_mark_write` 留驻，其余 33 个接口形成一个模块。

`deferred_trace_v39_cb.ko` 最终迁移 43 个 C 函数和 `readme_msg`。其中 35 个
基线最终 ELF 函数共 10,420 B；`trace.o` 永久段减少 9,824 B，增量
`bzImage` 减少 5,248 B。4 轮真实 tracefs 生命周期覆盖 FD 跨卸载、同 FD
重载、poll/read/splice 和四路并发首次绑定；8 对启动 A/B 通过。

- 机制验证：16 模块、283 个迁移 C 实现、179 个最终 ELF 函数
  （0.618115%）、84,382 B（1.260592%）；
- 候选级严格合格：15 模块、274 个迁移 C 实现、175 个最终 ELF 函数
  （0.604303%）、82,369 B（1.230519%）。

严格集合的受影响对象累计减少 71,065 B，`bzImage` 相对 v32 纯基线从
5,056,256 B 降至 5,017,376 B，即减少 38,880 B（0.768948%）。对齐前
payload 累计减少 69,632 B，但最终 text 区间仍未缩短，距离上一条 2 MiB
边界还差 186,544 B。详见
[`V39_AUTOMATIC_CALLBACK_TABLE_RESULT.md`](V39_AUTOMATIC_CALLBACK_TABLE_RESULT.md)
与
[`full-kernel-v39-trace-callback-summary.json`](full-kernel-v39-trace-callback-summary.json)。

## 历史 v29 函数级结论

以下数据专指 v29 函数级提取基线。最终 `vmlinux` 包含 51,683
个正大小函数，共 13,506,837 字节。
引用图按 `(symbol, size_bytes)` 精确映射其中 50,017 个，函数覆盖率为
96.776503%。未精确映射的最终 ELF 函数不会消失，而是保守计入 UNKNOWN。

严格 4,096 字节计划的最终 ELF 分类为：

| 分类 | 函数数 | 函数占比 | 字节数 | 字节占比 |
|---|---:|---:|---:|---:|
| CORE | 48,385 | 93.618791% | 13,096,340 | 96.960821% |
| READY | 81 | 0.156725% | 56,120 | 0.415493% |
| BLOCKED | 338 | 0.653987% | 46,787 | 0.346395% |
| UNKNOWN | 2,879 | 5.570497% | 307,590 | 2.277291% |

八个候选已经完成真实后端闭包和七项验证。最终有 **45 个正大小 ELF
函数、22,359 字节**成功模块化：

- 函数：**45 / 51,683 = 0.087069%**
- 基线函数字节：**22,359 / 13,506,837 = 0.165538%**
- 成功清单记录 70 个候选/闭包函数 ID，其中 45 个在基线最终 ELF 中
  保留为可独立计数的正大小符号；后端另复制 8 个纯静态辅助定义，它们
  不重复计入成功函数比例。

READY 是静态上允许进入后端的结论；“成功模块化”还要求源码闭包、真实
构建、功能、卸载重载、尺寸和启动 A/B 全部通过，两者不能混用。

## READY 收益

v27、v28 和 v29 使用同一张 169,996 函数、3,011,804 引用边的完整图、
同一 loader-ready 观测以及同一 4 KiB 发布门槛。v29 不放宽静态安全
规则，而是提高 READY 到真实成功的转化率：

| 指标 | v27 | v28 | v29 |
|---|---:|---:|---:|
| READY 候选 | 22 | 31 | 31 |
| READY 最终 ELF 函数 | 45 | 81 | 81 |
| READY 已知函数字节 | 43,091 | 56,120 | 56,120 |
| BLOCKED 最终 ELF 函数 | 374 | 338 | 338 |
| 聚合 READY 候选 | 21 | 30 | 30 |
| 聚合 READY 函数 | 42 | 78 | 78 |
| 聚合估计净收益 | 14,244 B | 24,969 B | 24,969 B |
| 已成功模块化函数 | 28 | 41 | 45 |
| 已成功模块化函数字节 | 14,617 | 20,028 | 22,359 |

v29 的 717 个候选分为 31 READY、350 BLOCKED 和 336
NEEDS_EVIDENCE。可行性漏斗为：

- 460 个候选具有常驻入口；
- 397 个具有真实可执行入口边；
- 390 个入口具有可睡眠加载证明；
- 201 个按估算为正净收益；
- 1 个候选单独达到 4 KiB；
- 30 个正收益小候选通过同一部署收益组达到 4 KiB；
- 最终共 31 个 READY。

组合规则只允许消除“候选单独低于 4 KiB”这一项原因，不能绕过阶段、
上下文、边界、间接调用或证据阻塞。

本轮新增的九个 READY 功能簇恰好对应 36 个最终函数：

| 功能簇 | READY 函数 |
|---|---:|
| 调度 affinity/attribute/parameter | 4 |
| POSIX timer/clock | 5 |
| pidfd | 2 |
| Linux AIO | 5 |
| 系统身份、优先级和 times | 9 |
| select/pselect/ppoll | 3 |
| nanosleep/time32 | 2 |
| copy_file_range/llseek/pwritev2 | 3 |
| futex/robust-list | 3 |

## 真实成功集合

八个按需加载模块的对象级结果如下。常驻节省采用最终组合构建；因此
`sys_identity_lazy` 复用 `ioprio_lazy` 的既有导出，
`capability_lazy` 也不再重复导出 `find_task_by_vpid`。

| 模块 | 选择的 C 函数 | 最终 ELF 函数 | 基线函数字节 | 未加载常驻节省 | `.ko` 永久字节 |
|---|---:|---:|---:|---:|---:|
| ioprio | 4 | 3 | 1,577 | 930 | 2,668 |
| splice/vmsplice | 9 | 4 | 2,628 | 1,834 | 3,346 |
| setns | 6 | 1 | 1,462 | 856 | 2,584 |
| quotactl | 34 | 20 | 8,950 | 8,697 | 10,242 |
| stat/lstat/fstat | 3 | 3 | 1,691 | 1,197 | 2,801 |
| 系统身份/优先级 | 10 | 10 | 3,720 | 2,502 | 4,885 |
| capability | 2 | 2 | 1,131 | 1,204 | 2,831 |
| wallclock/time32 | 2 | 2 | 1,200 | 734 | 1,548 |
| **合计** | **70** | **45** | **22,359** | **17,954** | **30,905** |

八个受影响内建对象由 65,823 字节降到 47,869 字节，模块未加载时减少
17,954 字节。相比 v28 六模块增加 1,938 字节（**12.10%**）。

八模块全部加载后比基线增加 12,951 字节。因此该机制的收益条件仍是
“功能尚未使用”：它把可选成本推迟到实际工作负载需要时，不声称所有
模块都加载后仍更小。

可复现纯基线 `bzImage` 为 9,756,736 字节，v29 八模块镜像为
9,747,744 字节，变化为 **-8,992 字节**。相比 v28 的 -6,880 字节，
压缩镜像缩减量再增加 2,112 字节。

### v29 新增闭包

`capability_lazy` 移动 `capget/capset` 两个入口，复制 4 个无共享状态的
静态校验辅助函数，只新增 `security_capget`、`security_capset` 和
`__audit_log_capset` 三个导出；`find_task_by_vpid` 由已有 ioprio 边界
提供。`kernel/capability.o` 永久段从 2,982 B 降到 1,778 B。

`wallclock_lazy` 移动 `settimeofday` 与兼容
`adjtimex_time32`，保留并导出 4 个被其他常驻路径继续使用的实现。
`kernel/time/time.o` 永久段从 7,713 B 降到 6,979 B。功能触发器通过
低地址 `MAP_32BIT` 缓冲区和 `int 0x80` 实际覆盖 i386
`adjtimex_time32`，同时以空参数覆盖 x86_64 `settimeofday`，不修改
时钟或时区。

## 七项成功门禁

机器统计只有在以下字段全部为 `true` 时才接受一个候选：

1. Clang AST 源码提取成功；
2. 内核及模块构建、MODPOST 成功；
3. QEMU 启动成功；
4. 首次功能调用自动加载模块；
5. 模块可卸载，且再次调用可重新加载；
6. 真实永久 ELF section 尺寸门禁通过；
7. 平衡交错启动 A/B 门禁通过。

八模块组合场景完成 8/8 步。16 对 A/B、共 32 次 `nokaslr` 启动结果：

| 指标 | 基线中位数 | v29 八模块中位数 | 模块化 - 基线 | 门禁 |
|---|---:|---:|---:|---:|
| READY uptime | 0.850 s | 0.860 s | +0.010 s | ≤ +0.250 s |
| 启动日志永久内核汇总 | 25,631 KiB | 25,631 KiB | 0 KiB | ≤ 0 KiB |
| `MemAvailable` | 465,532 KiB | 465,534 KiB | +2 KiB | ≥ -128 KiB |
| Slab | 12,598 KiB | 12,612 KiB | +14 KiB | ≤ +64 KiB |

四项均通过。KiB 汇总、页粒度和正常启动噪声会遮蔽约 18 KiB 的对象
变化，因此 A/B 只证明没有观测到资源或启动时间回归；可归因收益仍采用
成对对象 ELF section 的 17,954 字节。

## 后端成本校准

v28/v29 新生成的常驻包装器通过共享加载器获取一张强类型 operations 表。成功的
`symbol_get()` 同时持有模块引用；包装器调用对应字段，返回后执行
`symbol_put()`。模块引用覆盖整个实现调用，因此卸载不能释放仍在执行
的模块文本。

七接口 signal 预检给出：

- 目标函数已知文本：3,445 B；
- 基线 `kernel/signal.o` 永久段：39,565 B；
- v28 常驻对象永久段：37,115 B；
- 实际常驻节省：2,450 B；
- 反推实际边界成本：995 B；
- 规划模型：`640 + 6 × 96 = 1,216 B`；
- 保守余量：221 B。

相对 v27 的 1,703 B 边界成本，共享表和共享加载器降低 708 B。signal
模块本体仍受私有依赖闭包阻塞，所以这里只校准常驻成本，不计入成功
集合。真实部署始终以构建后的尺寸门禁为最终依据。

## 完整引用图覆盖

- `KBUILD_VMLINUX_OBJS` 展开为 2,499 个真实链接对象；
- 2,470 个对象存在匹配 LLVM bitcode，29 个汇编/生成对象没有 bitcode；
- 图包含 266,009 个节点、3,011,804 条边和 169,996 个函数节点；
- 间接调用求解后仍有 1,256 个 unresolved 调用；
- 最终 ELF 中 50,017 个函数精确匹配；
- 1,666 个未匹配最终函数、35,908 字节计入 UNKNOWN；
- 75 个零大小文本别名单独记录，不进入 51,683 的分母。

引用边覆盖直接调用、间接调用、函数取地址、全局初始化器、字段/偏移
传播、参数与返回值传播、系统调用表、导出表、PCI fixup 和 x86 CPU
厂商回调表。饱和 points-to 集不会被解释成空集合。

## 限制与下一步收益空间

- v39 已证明 175 个最终 ELF 函数达到候选级严格门禁，但累计对象节省仍未
  跨过最终链接页；x86 对齐诊断要求至少再减少 186,544 B 对齐前 payload。
- “loader-ready 前未执行”仍然只是候选发现证据。被写入或地址逃逸的全局
  状态、weak 实现和已发布回调必须独立留驻，不能为了增加函数数而随模块
  卸载。
- 81 个 READY 最终函数不是后端成功上限。v37 已把 io_uring 的 50 个源码
  实现做成严格模块；v38 从一个 READY 创建入口扩展出 31 个 perf 实现；
  v39 更不依赖 syscall 候选，直接从 21 张 trace 回调表迁移 43 个实现。
  READY 只统计加载入口，常驻表后的冷实现和私有闭包可以更多。
- loader-ready 前未观测只是候选证据，并非“所有 init-cold 都可直接移动”。
  static-key 的链接期地址、匿名类型 ABI、持久状态、open/release 生命周期和
  不可睡眠上下文仍是硬边界；v39 会把原因传播到具体接口并只保留该接口。
- signal 的常驻成本已经通过，但模块侧仍有私有 signal 依赖。
- `sys_identity_lazy` 的 `gethostname` 和 `olduname` 包装器在当前
  x86_64 syscall 表中不可直接触达；组合测试覆盖其余七个接口。兼容 ABI
  发布前仍需专门工作负载。
- 当前启动证据只覆盖一份 x86_64 BusyBox/initramfs 场景。文件系统、
  网络、存储、容器、LSM、PREEMPT/RT 和其他架构必须重新构图、采样并
  重跑七项门禁。
- v29 已真实预检 `nanosleep`、`pidfd`、`select` 和 read/write：
  `nanosleep` 在导出两个常驻依赖后反而增加 66 B；read/write 仅节省
  23 B；`pidfd` 被私有 `pidfd_fops` 阻塞；`select` 仍需移动多项私有
  轮询闭包。它们不能只因 READY 就计为收益。
- 下一阶段应优先聚合同源、共享同一导出面的更大调用闭包，并把
  “新增导出/包装器成本”纳入组合优化。对 file_operations 可保留常驻表与
  包装器、移动实现；对 VMA/work/timer/RCU/IRQ 等长生命周期对象必须保留
  发布表或增加专门 pin，不能逐函数强拆。
- 只在启动期使用、之后不再需要的代码应优先转为 `__init` 并由
  `free_initmem()` 回收，而不是为了增加模块数量强行改成可重载模块。

## 可复现统计命令

```bash
python3 -m kernel_modularizer plan \
  "$RUN/vmlinux-reference-graph-v25-sized.json" \
  --policy docs/full-kernel-syscall-lazy-policy.json \
  --observations "$RUN/loader-phase-v26/boot-observations.json" \
  --interface-stub-bytes 640 \
  --additional-interface-stub-bytes 96 \
  --minimum-estimated-savings 4096 \
  --json-output "$RUN/ready-optimization-v28/vmlinux-module-plan-v28.json"

python3 -m kernel_modularizer summarize-full-kernel \
  "$RUN/vmlinux-reference-graph-v25-sized.json" \
  --plan "$RUN/ready-optimization-v28/vmlinux-module-plan-v28.json" \
  --vmlinux "$BASE_OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --successful-validations \
    "$RUN/ready-optimization-v29/successful-validation-v29.json" \
  --json-output \
    "$RUN/ready-optimization-v29/vmlinux-full-kernel-stats-v29.json"
```

精简后的机器可读摘要保存在
[`full-kernel-v29-closure-expansion-summary.json`](full-kernel-v29-closure-expansion-summary.json)。
