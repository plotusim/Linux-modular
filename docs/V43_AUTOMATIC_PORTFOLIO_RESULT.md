# v43：完整引用图批量自动提取与放宽收益集

## 结论

这一阶段把“从完整 LLVM 图找候选”推进成了可直接运行的批处理提取链路。
新命令会按源文件聚合常驻回调表，逐组生成 LLVM/Clang 源码闭包、懒加载
包装器、模块实现、Kbuild/Kconfig 片段和事务清单；一组失败不会中断其余组，
中断后还能按输入与产物哈希继续执行。

在同一个 Linux 5.10.176、x86_64、v31 配置和完整大小富化图上，放宽到
`--minimum-estimated-net-bytes -4096` 后，工具处理了全部 69 个 READY
源文件组：

- 33 组成功生成独立 bundle，包含 139 个源函数定义和 106 个懒加载接口；
- 这 139 个函数在旧规划器中全部是 `CORE`，说明回调边界提升确实扩大了
  原先的自动模块化范围；
- 26 组被识别为此前已经改写，没有用旧图重复修改源码；
- 10 组失败被保留为结构化数据，没有污染其他候选。

再经过逐候选编译、MODPOST、对象尺寸、组合最终链接、`bzImage` 和 QEMU
真实功能门禁，最终保留 8 个新模块：64 个源函数定义、45 个懒加载接口，
对应纯 v32 最终 ELF 中 52 个独立函数、14,087 B。受影响常驻对象减少
6,924 B，`bzImage` 相对 v42 减少 9,280 B，最终链接永久区间减少 64 B。
4 轮 QEMU 共 48/48 步通过，并证明 8 个模块在 loader-ready 时都尚未加载。

这不是“所有启动未调用函数都可以无条件搬走”的证明。它证明的是：启动
冷函数可以作为更大的候选池，但必须把常驻回调表、首次使用包装器、执行
上下文、持久对象生命期和 ABI 边界一起处理。

## 新的自动化入口

```bash
python3 -m kernel_modularizer prepare-callback-portfolio \
  "$RUN/reference-graph-sized.json" "$RUN/plan.json" \
  --source-extractor Backend/AutoBackend/cpp/SourceExtractor \
  --compile-database "$RUN/compile_commands.json" \
  --kernel-root "$KERNEL" \
  --output-directory "$RUN/callback-portfolio" \
  --minimum-estimated-net-bytes -4096
```

默认值 `0` 会保留估算为零和未知大小的探索候选；负数用于更激进地让真实
编译结果推翻不准确的图上估算。常用控制项包括：

- `--only-source`、`--exclude-source` 和 `--max-source-groups` 控制范围；
- `--integrate` 将成功 bundle 逐个事务应用，`--integration-dry-run` 只验证；
- 默认开启哈希验证续跑，`--no-resume` 强制重新生成；
- 默认隔离候选失败，`--fail-fast` 用于 CI 中快速停止；
- 已有 linux-modular 改写的源码默认跳过，避免把陈旧引用图套到新源码上。

每个源文件组都有独立目录、`candidate-result.json`、阶段 manifest、源码
闭包、后端策略和 bundle。顶层 `portfolio.json`/`portfolio.md` 在每组完成
后原子更新。因此进程中断时已经完成的闭包不会丢失，也不会信任半写入文件。

`PREPARED` 的含义被刻意限定为“源码闭包与 bundle 已生成”。它不代表内核
已经编译，更不代表运行时安全；构建、体积和生命周期仍是后续门禁。

## 完整图结果

| 项目 | 数量 |
|---|---:|
| 完整图识别的回调表 | 685 |
| READY 回调表 | 154 |
| READY 源文件组 | 69 |
| 闭包前接口 | 283 |
| 新生成 bundle | 33 |
| 已模块化而跳过 | 26 |
| 明确失败 | 10 |
| bundle 中移动定义 | 139 |
| 闭包后懒加载接口 | 106 |

33 个成功闭包都满足 LLVM 图覆盖完整。10 个失败主要落在两种真实边界：
宏或条件编译生成的私有全局定义当前无法从源码中精确定位，以及 variadic
驻留依赖无法生成强类型边界。涉及 printk、random、kprobes、HPET、PM QoS、
nsfs、blktrace、blk-mq debugfs、trace uprobe 和 proc sysctl。它们没有被
静默忽略，而是进入报告，正好形成下一阶段的提取器修复队列。

## 最终保留的 8 个模块

| 源文件 | 定义 | 接口 | ELF 函数/字节 | 对象节省 | 模块永久节 |
|---|---:|---:|---:|---:|---:|
| `kernel/trace/trace_events.c` | 15 | 13 | 14 / 3,152 B | 918 B | 4,845 B |
| `drivers/base/regmap/regmap-debugfs.c` | 11 | 6 | 8 / 2,776 B | 1,969 B | 4,596 B |
| `drivers/tty/tty_io.c` | 19 | 13 | 16 / 3,506 B | 1,688 B | 5,031 B |
| `fs/kernfs/file.c` | 4 | 3 | 3 / 950 B | 430 B | 2,436 B |
| `fs/proc/page.c` | 5 | 2 | 3 / 1,542 B | 1,274 B | 3,128 B |
| `kernel/profile.c` | 5 | 3 | 3 / 850 B | 239 B | 2,406 B |
| `fs/timerfd.c` | 3 | 3 | 3 / 823 B | 231 B | 2,233 B |
| `ipc/mqueue.c` | 2 | 2 | 2 / 488 B | 175 B | 1,768 B |
| **合计** | **64** | **45** | **52 / 14,087 B** | **6,924 B** | **26,443 B** |

这些移动定义在原始规划中全部属于 `CORE`。常驻回调表本身没有搬走；表项
仍指向常驻包装器，包装器第一次被调用时通过强类型符号边界请求模块，并在
实现调用期间持有模块引用。这样无需等到原始内核把这张表改成可模块化配置。

## 为什么没有把所有正收益候选都留下

第二轮还实际编译了 `fs/proc/inode.c`、`fs/proc/vmcore.c`、
`drivers/acpi/proc.c` 和 `kernel/trace/trace_events_trigger.c`。它们单独都使
对应对象缩小，分别为 24、118、41 和 94 B。可是六个第二轮正候选全部加入
时，虽然对象总收益达到 7,201 B、`bzImage` 减少 10,400 B，最终 `vmlinux`
的永久区间却因 BSS/页对齐增加 4,032 B，因此组合门禁失败。

子集搜索得到的稳定边界是原六组加 mqueue、timerfd：最终链接为 -64 B，
`bzImage` 为 -9,280 B。再加入 proc inode 就重新触发 +4,032 B 回退，因此
后四组被回滚。另有 `drivers/char/mem.c`、`fs/block_dev.c`、
`kernel/irq/proc.c` 的真实对象收益分别为 -24、-69、-150 B，也被回滚。

这说明图上函数字节数只能负责候选排序，不能代替实际链接。小候选之间会
经过 section、符号表、重定位、BSS 和页对齐产生非线性组合，发布选择必须
在同一个最终 `vmlinux` 上做组合门禁。

## 为真实内核补上的后端能力

本轮构建暴露并修复了两个通用问题：

1. 常驻加载器前导代码的插入位置现在与函数替换一起使用原始源码偏移排序，
   避免源文件的第一个条件 include 位于函数之后时，把加载器插入函数体内。
2. Clang 提取器会从成员访问语义记录隐含的文件私有 record 定义。例如移动
   代码只通过成员链使用一个私有结构、文本中从未直接拼写结构名时，闭包仍
   会把完整定义带入模块。这修复了 kernfs 的真实构建失败。

两个问题都有最小 fixture 和语法/后端回归测试，不是针对单个内核文件硬编码。

## QEMU 生命周期验证

最终 initramfs 加入一个真实 regmap fixture 和静态触发器。每轮 12 步验证：

- loader-ready 时 8 个模块全部不在 `/proc/modules`；
- tracefs、regmap debugfs、TTY、sysfs/kernfs、`/proc/kpagecount`、
  `/proc/profile` 分别触发自动加载；
- timerfd 完成创建、poll 和到期 read；POSIX mqueue 完成 open、poll 和状态 read；
- 每个模块可卸载，随后首次使用可重新加载；
- 组合重载/卸载通过，dmesg 无 BUG、Oops、WARNING、GPF 或 panic。

4 轮结果均为 12/12，总计 48/48。最终内核为 4,988,800 B，SHA-256 为
`c31ae7b48e090a330aaa65248fcbbc5de10073078d5082ac81ddad2a2e9bef9a`。

## 累计口径与限制

v42 的严格集合为 36 模块、460 个移动定义。加上本轮“放宽单候选收益但仍
通过构建/最终链接/QEMU”的 8 个模块后，验证集合达到：

- 44 个模块、524 个移动源函数定义；
- 350/28,959 个纯 v32 最终 ELF 函数，即 1.208605%；
- 对应 153,880 B，纯 v32 函数字节占比 2.298830%；
- 累计受影响对象收益 119,267 B；
- 同配置 `bzImage` 从 5,056,256 B 降到 4,988,800 B，减少 67,456 B
  （1.334110%）。

本轮单候选门槛是 1 B，不再是此前的 4 KiB 严格门槛，因此不能把 44 个模块
全部称为“严格 4 KiB 集合”。同时，8 个模块全部加载后，受影响常驻对象加
模块永久节比原 built-in 实现多 19,519 B；收益只存在于模块尚未使用时。
本轮也没有新增易受噪声影响的整机启动 A/B 内存声称，可信收益是精确的
对象、最终链接、压缩镜像和可重复生命周期结果。

机器可读的完整数字、失败边界、哈希和算术关系在
[`full-kernel-v43-automatic-portfolio-summary.json`](full-kernel-v43-automatic-portfolio-summary.json)。
