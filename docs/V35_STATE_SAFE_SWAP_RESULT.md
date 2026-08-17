# v35：LLVM 引用图驱动的生命周期安全 swap 模块

## 阶段结论

v35 已从原本 built-in、没有现成 Kconfig 模块边界的 `mm/swapfile.c` 中，
自动生成可加载模块 `deferred_swap_v35.ko`。入口候选是
`candidate:31abb742bea93c07`，以 `swapon`/`swapoff` 两个 syscall 为种子，
工具通过配置匹配的 LLVM 引用图和 Clang 精确源码事实完成了：

1. AST 前向闭包、LLVM incoming ownership cut 和完整图覆盖检查；
2. 宏参数中的精确符号重写，以及宏生成锁、waitqueue、plist 等实体的
   再提取固定点；
3. 可变全局状态、地址逃逸对象、弱函数和已注册回调的生命周期边界；
4. 28 个 C 函数的自动迁移、6 个纯 inline 辅助函数复制、9 个同源函数
   与 12 个状态全局的常驻；
5. 强类型驻留依赖表、Kbuild/Kconfig bundle、事务集成和 MODPOST；
6. 真实 `swapon`/`swapoff` 的自动加载、卸载、再次加载，以及启动 A/B。

新模块通过严格尺寸门禁：`mm/swapfile.o` 的永久 allocatable section
减少 **9,591 B**，`bzImage` 相对同参数增量基线减少 **10,176 B**，最终
链接永久区间没有增长。4 轮 QEMU 生命周期测试和 8 对、共 16 次启动 A/B
全部通过。因此它不只是“生成机制可行”的实验候选，而是一个新的严格
合格函数模块。

当前仍不能声称减少了启动永久页。相对 v32 纯基线，对齐前 text payload
累计减少 45,056 B，但被 x86 的 2 MiB 对齐岛转化成等量 padding，最终
text 区间仍然不变；距离前一个边界还差 211,120 B。

## 自动划分的源码边界

| 边界部分 | 自动结果 |
|---|---:|
| 移入模块的函数 | 28 |
| 移入模块的全局变量 | 0 |
| 模块内复制的纯辅助函数 | 6 |
| 留在常驻侧的同源函数 | 9 |
| 留在常驻侧的同源全局变量 | 12 |
| 自动发现的跨源文件驻留导出 | 28 |
| 图专属直接依赖 | 7 |
| 最终新增驻留导出符号 | 9 |
| 自动复制的源文件私有宏 | 5 |

迁移函数为：

```text
__se_sys_swapoff                 __se_sys_swapon
_enable_swap_info                alloc_swap_info
claim_swapfile                   cluster_list_init
destroy_swap_extents             discard_swap
drain_mmlist                     enable_swap_info
find_next_to_unuse               free_swap_count_continuations
pte_same_as_swp                  read_swap_header
reinsert_swap_info               setup_swap_extents
setup_swap_info                  setup_swap_map_and_extents
swap_discardable                 swap_node
try_to_unuse                     unuse_mm
unuse_p4d_range                  unuse_pmd_range
unuse_pte                        unuse_pte_range
unuse_pud_range                  unuse_vma
```

`cluster_count`、`cluster_set_flag`、`cluster_set_null`、`first_se`、
`next_se` 和 `swap_count` 是无独占可变状态的静态 inline，自动复制到模块。
`add_swap_extent`、`add_to_avail_list`、`cluster_list_add_tail`、
`del_from_avail_list`、`inc_cluster_info_page`、`max_swapfile_size`、
`swap_discard_work`、`swap_free` 和 `try_to_free_swap` 仍被其他常驻路径使用，
所以留在原文件并通过窄依赖 ABI 提供给模块。

12 个全局变量全部留在常驻侧：`least_priority`、`nr_rotate_swap`、
`nr_swap_pages`、`nr_swapfiles`、`proc_poll_event`、`proc_poll_wait`、
`swap_active_head`、`swap_avail_heads`、`swap_info`、`swap_lock`、
`swapon_mutex` 和 `total_swap_pages`。这不是因为工具无法提取它们，而是
它们的所有权和生命周期不允许随模块卸载而销毁。

## 为什么不能只判断“启动没调用”

启动 trace 的缺席只能证明在给定工作负载的 loader-ready 之前没有观察到
执行，不能证明一个函数或状态可以安全卸载。v35 为此增加四类 intrinsic
resident frontier：

- LLVM 出现 `GLOBAL_WRITE` 的同源可变全局不能移入可重载模块，否则一次
  `rmmod` 会把已积累的内核状态重置；
- 可变全局地址逃逸后，模块也不能成为其唯一所有者；
- `max_swapfile_size` 是 weak 定义，必须保留原有链接覆盖语义；
- `swap_discard_work` 的函数地址已注册到异步 work，必须留在常驻侧，避免
  卸载后留下悬空回调。

本候选中 `least_priority`、`nr_swapfiles`、`swap_avail_heads`、`swap_info`
和 `total_swap_pages` 被 LLVM 写入事实直接判为模块生命周期状态；其他锁、
队列和表还存在常驻消费者。结果是模块卸载只回收可重建的代码，不回收或
初始化交换子系统的持久状态。

## 宏与强类型驻留边界

Linux 中很多实体并不是普通 C 声明。`WRITE_ONCE(nr_swapfiles, ...)` 和
`unlikely(pte_same_as_swp(...))` 里的 `DeclRefExpr` 位置可能落在宏展开名，
此前无法精确重写。`SourceExtractor` 现在用 Clang 的 file character range
把宏参数映射回用户写下的标识符；宏体或头文件 inline 中不可归属主文件的
引用仍然不产生可写区间。

`DEFINE_MUTEX(swapon_mutex)`、`DECLARE_WAIT_QUEUE_HEAD(proc_poll_wait)`、
`DEFINE_SPINLOCK(swap_lock)` 和 `PLIST_HEAD(swap_active_head)` 的名字也由宏
生成，`--all-main-globals` 不一定能发现。候选准备现在会根据已解析的同源
依赖再次显式提取，直到没有新的宏生成实体；头文件 inline 定义不会因此
被误复制。

模块自动复制的最小宏闭包是 `SWAPFILE_CLUSTER`、`LATENCY_LIMIT`、
`SWAP_CLUSTER_INFO_COLS`、`SWAP_CLUSTER_SPACE_COLS` 和
`SWAP_CLUSTER_COLS`。所有源码切片和标识符偏移在生成前都与当前文件重新
核对；相同 AST edit 会去重，冲突 edit 会硬失败。

驻留全局的字段声明使用 Clang 提取的精确类型，不再依赖模块侧未必可见的
`typeof(symbol)`。数组和函数指针对象分别生成正确的指针 declarator。
可打包依赖合并成一张 const 表，常驻侧放入 `.data..ro_after_init`：启动
初始化完成后保持只读，同时避免向最终 `.rodata` 注入小对象而意外跨过
4 KiB 对齐边界。无法安全重写的宏/inline 依赖继续走审计过的直接导出。
本 bundle 最终新增一张打包表和 8 个直接符号，共 9 个驻留导出；MODPOST
验证了真实符号边界。

## 构建与运行验证

内核使用 `LLVM=1 LLVM_IAS=0`，固定 `KBUILD_BUILD_VERSION=34`、构建用户、
主机和时间戳。`vmlinux`、全部模块、`bzImage` 与 MODPOST 均通过。最终
产物为：

| 产物 | 文件大小 | SHA-256 |
|---|---:|---|
| `vmlinux` | 34,732,072 B | `0022a4cedce7ebc2801812b637eed5d4bb27d3ada2f006b47b72a8ed64a21bd1` |
| `bzImage` | 5,034,112 B | `6769aed3f3dbed4e0272b7eb79b63a023c338f0f4568bc23dab1fc6a9a2c2b84` |
| `mm/swapfile.o` | 43,336 B | `14aa1a1c6230cacf2de32b308bc597502d50ab7341cf64cfb617700a3b52db75` |
| `deferred_swap_v35.ko` | 28,216 B | `67528044835482cb650f32ad080ef039446577f90648dfd88664afb54063523f` |

QEMU 测试实际建立 32 MiB loop-backed swap 设备，每轮完成 11/11 步：

```text
模块不存在
  -> 第一次 swapon 自动加载
  -> swapoff
  -> rmmod 并确认消失
  -> 第二次 swapon 再次自动加载
  -> 第二次 swapoff/rmmod
  -> 释放 loop 设备
```

4 轮均通过，完整场景耗时分别为 2.603、3.152、2.289 和 2.122 秒，中位数
2.446 秒。该测试同时证明第二次加载仍能使用第一次卸载后保留的常驻状态，
而不只是一次性调用成功。

平衡交错启动 A/B 使用相同 initramfs、`nokaslr`、512 MiB 和 2 vCPU，8 对
共 16 次启动全部通过。模块化相对基线的中位数变化为：永久内核内存
0 KiB、ready time -0.100 s、`MemAvailable` +10 KiB、Slab -40 KiB。
后三项处于小样本噪声范围，只用于证明没有观测到回退，不作为性能提升
声明。

## 尺寸与完整内核比例

### v35 增量严格门禁

| 指标 | v34 同参数基线 | 加入 swap 模块 | 变化 |
|---|---:|---:|---:|
| `mm/swapfile.o` 永久段 | 23,722 B | 14,131 B | **-9,591 B** |
| 新模块永久段 | 0 B | 11,345 B | +11,345 B |
| 全部加载后的合计 | 23,722 B | 25,476 B | +1,754 B |
| 最终链接永久区间 | 15,704,648 B | 15,704,648 B | 0 B |
| `bzImage` | 5,044,288 B | 5,034,112 B | **-10,176 B** |

全部功能都加载后增加 1,754 B 是预期的模块元数据、重定位和加载边界成本。
该机制的目标是让未使用 swap 的启动/常驻系统不支付这部分成本，而不是
宣称“所有模块全部加载后仍然更小”。

### 最终 ELF 对账

28 个迁移 C 函数中，13 个在基线最终 ELF 中保留独立正大小符号，共
9,992 B；其余 15 个被编译器内联、拆分或合并，不能重复计数。新增模块
本身占完整内核分母的 13/28,959 = **0.044891%**，基线函数字节占比为
9,992/6,693,841 = **0.149272%**。

分类分母不因成功子集增加而改变：

| 分类 | 函数 | 占比 | 字节 | 字节占比 |
|---|---:|---:|---:|---:|
| CORE | 26,732 | 92.309817% | 6,353,289 | 94.912458% |
| READY | 81 | 0.279706% | 56,123 | 0.838427% |
| BLOCKED | 296 | 1.022135% | 42,480 | 0.634613% |
| UNKNOWN | 1,850 | 6.388342% | 241,949 | 3.614502% |

当前两种集合必须继续区分：

- 机制验证集合：13 模块、159 个迁移 C 函数、104/28,959 个最终 ELF
  函数（0.359128%）、49,521 B（0.739799%）；
- 候选级严格合格集合：12 模块、150 个迁移 C 函数、100/28,959 个最终
  ELF 函数（0.345316%）、47,508 B（0.709727%）。

当前 v35 可启动产物是包含 v34 select 实验模块的 13 模块机制超集；第二项
是按候选严格门禁汇总的集合，不把 v34 的 4 个函数和 2,013 B 混入严格
数字。相对 v32 纯基线，当前超集 `bzImage` 累计减少 22,144 B；其中只有
相对 v34 增量基线的 10,176 B 可以归因给本轮 swap 模块。

## 对齐限制与后续候选

链接布局诊断如下：

| 指标 | v32 纯基线 | 当前 v35 | 变化 |
|---|---:|---:|---:|
| entry-text 对齐前 payload | 6,547,632 B | 6,502,576 B | -45,056 B |
| 对齐前 padding | 1,840,976 B | 1,886,032 B | +45,056 B |
| 最终 text 区间 | 10,493,448 B | 10,493,448 B | 0 B |

这说明对象级节省真实存在，但还没有释放最终链接页。至少还需移走 211,120 B
对齐前 payload，跨界后也必须重新测量完整 `vmlinux`，不能直接按对象字节
外推。

本轮还审计了两个方向：namespace 候选在旧的只读表布局下已通过 Kbuild/
MODPOST，但向 `.rodata` 增加的小表触发了 4 KiB 对齐回退；新的
`.data..ro_after_init` 布局正是由该失败推动，候选应在最新代码上重新跑
严格门禁。scheduler 候选暴露了 `CREATE_TRACE_POINTS` 的 include/undef
状态依赖，说明下一步需要按实体记录预处理器状态，不能只复制源文件的最终
include 集合。

下一阶段按以下顺序推进：

1. 用最新 `ro_after_init` 布局重跑 namespace 边界，只有严格尺寸门禁通过
   后才投入功能生命周期测试；
2. 为每个提取实体记录 include、define 和 undef 的词法状态，解锁 trace
   定义附近的大闭包；
3. 按未加载对象永久节省、常驻依赖成本和生命周期状态比例联合排序，而非
   只按 READY 函数个数排序；
4. 聚合同一触发时机、同一驻留依赖表的小候选，摊薄 loader、modinfo 和
   relocation 成本；
5. 从 `BOOT_COLD` 大闭包继续发现收益，但每个候选仍重新证明写状态、回调、
   弱覆盖、执行上下文和卸载时机；
6. 以再减少至少 211,120 B 对齐前 payload 为阶段链接目标。

机器可读事实、比例、门禁结果和全部 SHA-256 见
[`full-kernel-v35-state-safe-swap-summary.json`](full-kernel-v35-state-safe-swap-summary.json)。
