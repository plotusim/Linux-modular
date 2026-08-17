# v38：LLVM 回调表提升与生命周期安全函数模块化

## 阶段结论

这一阶段已经把“只能从 syscall 入口向下提取”推进为“从一个经过审计的
常驻回调表，借助 LLVM `GLOBAL_INITIALIZER` 边自动发现全部同源回调入口，
再把回调实现和私有闭包移入新模块”。结果不是 45 个函数的上限：在 v35
严格集合之上，v37 与 v38 又新增两个严格合格模块，共自动迁移 **81 个 C
函数定义**：

| 新模块 | 源文件 | 迁移函数 | 迁移全局 | 对象永久段减少 | `bzImage` 变化 |
|---|---|---:|---:|---:|---:|
| `deferred_io_uring_v37.ko` | `io_uring/io_uring.c` | 50 | 0 | **11,191 B** | **-5,408 B** |
| `deferred_perf_v38_cb.ko` | `kernel/events/core.c` | 31 | 1 | **9,071 B** | **-6,080 B** |
| 合计增量 | 2 个原本 built-in 边界 | **81** | **1** | **20,262 B** | **-11,488 B** |

两个模块都通过完整 Kbuild、MODPOST、4 次真实生命周期测试、严格 4 KiB
对象收益门禁和 8 对启动 A/B。v38 perf 的最终测试还保持一个 VMA 跨过
模块卸载：模块消失后才触发 VMA fault 和 close，随后同一个 perf fd 的
file operation 再把模块装回来。这补上了仅保持 fd、未保持 mmap 时遗漏的
生命周期风险。

回调表提升使 perf 候选从 syscall-only 的 2,943 B 对象收益扩大到
**9,071 B，提升 3.08 倍**。这说明更高收益的关键不是放松安全门禁，而是
让多个同触发时机、共享一套驻留边界的入口共同摊薄加载器和依赖 ABI。

当前候选级严格集合达到 **14 个新函数模块、231 个迁移 C 函数定义**；其中
140 个在基线最终 ELF 中仍有独立正大小符号，占 28,959 个最终函数的
**0.483442%**，对应 71,949 B、占最终函数字节的 **1.074854%**。相对 v32
纯基线，严格集合的受影响对象永久段累计减少 **61,241 B**，`bzImage` 从
5,056,256 B 降至 **5,022,624 B**，减少 **33,632 B**。

最终链接永久页仍没有下降。对齐前 payload 已累计减少 65,536 B，但 x86
的 2 MiB 链接岛把它全部变成 padding；距离跨过前一个边界还差 190,640 B。
因此本阶段只主张未加载对象和发布镜像收益，不虚构启动 RAM 页面收益。

## 回调表如何变成模块入口

`perf_event_open` 只是创建入口。创建出的 fd 会长期通过 `perf_fops` 调用
read、poll、ioctl、compat_ioctl、mmap 和 fasync。逐个手工指定这些函数既
容易漏掉，也无法证明它们确实属于这张表。v38 增加了下面的流程：

```text
经过审计的 perf_fops 全局表
        |
        | LLVM GLOBAL_INITIALIZER 边
        v
6 个同源回调目标 + perf_release
        |
        | 排除不能移动的 release；其余成为 source-closure seeds
        v
Clang AST 前向闭包 + LLVM incoming ownership cut
        |
        v
31 个实现函数 + if_tokens 进入 deferred_perf_v38_cb.ko
```

命令行入口是 `--promote-callback-table perf_fops`。这个选项有意要求先审计
表，而不是看到任意函数指针表就自动移动：IRQ、timer、workqueue、RCU、
VMA 和设备驱动回调可能在不可睡眠上下文执行，或在模块卸载后仍被持有。
表一旦获准，字段到函数的发现、同源选择、闭包扩展和 LLVM 图覆盖检查都
由工具完成。

`perf_fops` 自身继续常驻，VFS 保存的始终是原地址。六个字段仍指向同名
常驻包装器；包装器在睡眠进程上下文首次调用 `request_module()`，用
`symbol_get()` 固定模块，调用移入模块的实现，再 `symbol_put()`。因此
打开的 fd 不会保存模块文本地址，模块可以在 fd 存活时卸载，并在下一次
操作时重载。

自动提升的字段为：

```text
perf_compat_ioctl  perf_fasync  perf_ioctl
perf_mmap          perf_poll    perf_read
```

`perf_release` 被显式排除并留在常驻侧。close 是对象销毁路径，不应因为
模块缺失而失败；它还可能在进程退出等非普通功能触发路径运行。

## 自动划分出的 perf 闭包

31 个迁移函数为：

```text
__perf_event_ctx_lock_double   __perf_read
__perf_read_group_add          __se_sys_perf_event_open
_perf_ioctl                    find_lively_task_by_vpid
is_event_hup                   perf_addr_filter_new
perf_compat_ioctl              perf_copy_attr
perf_event_for_each            perf_event_init_userpage
perf_event_modify_attr         perf_event_modify_breakpoint
perf_event_parse_addr_filter   perf_event_set_addr_filter
perf_event_set_bpf_handler     perf_event_set_bpf_prog
perf_event_set_clock           perf_event_set_filter
perf_event_set_output          perf_event_validate_size
perf_fasync                    perf_fget_light
perf_ioctl                     perf_mmap
perf_need_aux_event            perf_poll
perf_read                      perf_read_group
perf_read_one
```

`if_tokens` 是唯一迁移的全局只读表。`perf_event__state_init` 是无独占状态的
小型 inline，复制进模块。36 个仍被常驻路径共享的同源函数、4 个同源全局
对象和 12 个跨源文件依赖留在内核；它们通过一张首次调用时填充的强类型
依赖表传入模块。依赖表位于模块内存，常驻侧没有为每个依赖增加 ksymtab
符号，最终新增驻留导出数为 **0**。

LLVM 图覆盖检查对所选源码闭包为完整，未用“图里没有看到”替代源码事实。
31 个 C 函数中，16 个在优化后的基线 `vmlinux` 仍保留独立符号，共
10,961 B；另外 15 个已被内联、拆分或合并，不能再次把源码长度当作 ELF
收益累加。严格对象差值 9,071 B 是最终归因口径。

## 运行时发布对象的生命周期修正

初版闭包曾把 `perf_mmap_vmops` 一起移进模块。静态 LLVM 图只看到
`perf_mmap()` 读取这个全局，因而会认为它是私有对象；但源码实际上执行：

```c
vma->vm_ops = &perf_mmap_vmops;
```

该地址会被发布到可能长期存活的 VMA。懒包装器只在 `perf_mmap()` 调用期间
固定模块，返回后 VMA 仍持有地址；如果这时卸载模块，后续 page fault 或
munmap 就可能跳进已释放的数据/文本。这是单靠调用图无法表达的对象生命
周期。

Clang 提取器现在为一元 `&global` 记录
`dependencies.address_taken_globals`。源码闭包把这种对象保守分类为
`source_address_taken_global`，即使对象是 const 也保持常驻。最终边界为：

- `perf_mmap_vmops` 常驻；
- `perf_mmap_open`、`perf_mmap_fault`、`perf_mmap_close` 因函数地址逃逸常驻；
- 模块中的 `perf_mmap` 通过强类型依赖表取得常驻表地址；
- `perf_fops` 同样因地址发布和 VFS 所有权常驻；
- `sysctl_perf_event_mlock`、`sysctl_perf_event_sample_rate` 因
  `__read_mostly`/section 标记常驻。

这个修正只让对象收益从 9,159 B 调整到 9,071 B，却把模块永久段从
12,684 B 降到 12,452 B，并让 `bzImage` 再小 64 B。安全边界并不必然牺牲
整体收益；保留完整的常驻表反而去掉了三个初始化 trampoline。

## 宏声明组、局部 static 和旧声明

真实 `kernel/events/core.c` 还暴露了三类此前会阻止大闭包的问题：

1. `DEVICE_ATTR_*`、`PMU_FORMAT_ATTR` 等宏通过 token paste 一次生成一个或
   多个全局声明。提取器现在找到宏调用中与 AST 名称匹配的最长前缀/后缀
   标识符，并把整个调用标为 `macro_declaration_group`。在同一次宏展开的
   所有共同定义尚未完整建模前，闭包把它作为常驻 frontier，后端也会硬
   拒绝拆半迁移。
2. 函数内部的 `static const actions[]` 有静态存储期，但不是翻译单元作用域
   全局。它现在随所属函数源码自然迁移，不再生成一个 LLVM 中不存在的假
   全局闭包节点。
3. 搬走 `perf_event_set_output`、`perf_event_set_filter`、
   `perf_event_set_bpf_prog` 和 `perf_copy_attr` 后，源文件前部原有 prototype
   会变成未使用声明并触发警告。提取器记录定义之前的精确声明切片，后端
   只对非接口、已迁移函数删除这些切片。生成内核已在 `-Werror` 测试夹具
   中覆盖，真实内核构建也不再产生这四条警告。

这些规则不是 perf 特例；它们已经进入通用 SourceExtractor、源码闭包和
后端路径，并有 token-paste、重复宏参数、局部 static、前置声明和地址发布
的回归测试。

## 严格尺寸和可复现构建

v38 使用 v37 内核作为配对基线：

| 指标 | v37 基线 | 加入 perf 模块 | 变化 |
|---|---:|---:|---:|
| `kernel/events/core.o` 永久段 | 88,419 B | 79,348 B | **-9,071 B** |
| 新模块永久段 | 0 B | 12,452 B | +12,452 B |
| 全部加载后的合计 | 88,419 B | 91,800 B | +3,381 B |
| 最终链接永久区间 | 15,704,648 B | 15,704,648 B | 0 B |
| `bzImage` | 5,028,704 B | 5,022,624 B | **-6,080 B** |

全部模块都加载后增加 3,381 B 是模块元数据、重定位和边界成本。项目目标是
让未使用 perf/io_uring 的启动系统不承担这些代码，而不是声称“模块全部
加载后仍更小”。

最终 v38 产物为：

| 产物 | 文件大小 | SHA-256 |
|---|---:|---|
| `vmlinux` | 34,723,808 B | `31a75eafa6d3d8c26fcc9257d620e2269396cccb247fd7834601a87d211ac0d0` |
| `bzImage` | 5,022,624 B | `cfd04a99b3241058061d0ee49d414ce0ee47cc63a75459f92033dddb2642f595` |
| `kernel/events/core.o` | 169,816 B | `ff5a1c0e2464a4fbb88d2df6bf860bcb8bdad88207545a0805f2545a102b6a73` |
| `deferred_perf_v38_cb.ko` | 28,216 B | `c1773670267d42af9bea170e7df6e9eb2b043ce10fc7e7e0a104802cd67ca326` |

在不改源码的情况下再次执行 `make ... bzImage modules`，上述四个哈希全部
保持一致。唯一环境警告是宿主缺少 libelf，导致既有
`CONFIG_STACK_VALIDATION` 检查不可用；生成源码、编译、链接和 MODPOST
没有新警告。

## QEMU 生命周期与启动 A/B

最终场景连续 4 次完成 10/10 步，每次覆盖：

1. 模块初始不存在；
2. `perf_event_open` 首次调用自动加载；
3. native/compat ioctl、read、poll、mmap、fasync 全部执行；
4. 卸载模块；
5. 新建 perf fd 和 VMA 后，在 fd/VMA 仍存活时再次卸载；
6. 模块缺失状态下访问映射，得到 `PERF_HELD_FAULT_OK`；
7. 模块缺失状态下 munmap，得到 `PERF_HELD_CLOSE_OK`；
8. 同一个 fd 的回调重新自动装载模块；
9. 4 路并发首次调用全部成功，再次卸载/加载成功；
10. dmesg 不含 BUG、Oops、WARNING、GPF 或 panic。

4 轮完整场景耗时为 4.102、4.110、4.104、4.060 秒；loader-ready 中位数
0.660 秒。8 对、共 16 次平衡交错启动 A/B 全部通过：模块化相对基线的
中位数变化为永久内核内存 0 KiB、ready time +0.010 s、
`MemAvailable` +34 KiB、Slab -20 KiB。后三项属于小样本噪声，只证明
没有越过回归门禁，不作为性能提升声明。

完整 Python/C++/触发器测试为 230 项通过；新增机器摘要契约另有 8 项测试。

## 当前真实比例和下一步

最终 ELF 分类分母仍是同一个配置下的 28,959 个正大小函数：

| 分类 | 函数 | 占比 | 字节 |
|---|---:|---:|---:|
| CORE | 26,732 | 92.309817% | 6,353,289 |
| READY | 81 | 0.279706% | 56,123 |
| BLOCKED | 296 | 1.022135% | 42,480 |
| UNKNOWN | 1,850 | 6.388342% | 241,949 |

这里的 READY 是“可作为加载入口的规划候选”，不是最终可迁移函数数上限。
v38 正是从一个 READY 创建入口和一张回调表继续扩展，迁移出大量原本并非
独立 READY 入口的私有实现。最终成功比例必须继续用完整 ELF 分母和严格
构建证据计算，而不能把 81 个 READY 直接当作 81 个已成功模块化函数。

下一阶段收益策略已经更清晰：

1. 优先审计 file_operations、proto_ops、seq_operations 等“常驻表 + 可睡眠
   回调”组合，保留表和包装器，只搬实现；
2. 用同一触发时机聚合多个回调，确保对象净收益至少 4 KiB，并减少每个
   模块的固定开销；
3. 对 VMA、work、timer、RCU、IRQ 等地址发布对象，先证明生命周期或保留
   完整常驻表，不能只看启动未调用；
4. 为模块卸载时仍存活的 fd、VMA、task/work 对象分别增加真实跨卸载测试；
5. 继续按对象永久节省和对齐前 payload 排序，目标至少再移走 190,640 B，
   然后重新检查最终 `vmlinux` 是否真正少一个 2 MiB 链接页。

全部机器可读数字、函数清单、比例、门禁结果和 SHA-256 见
[`full-kernel-v38-callback-table-summary.json`](full-kernel-v38-callback-table-summary.json)。
