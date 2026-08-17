# v34：LLVM 引用图驱动的源文件私有依赖自动提取

> 后续 v35 已处理可变模块生命周期状态、地址逃逸回调、宏参数精确重写和
> 宏生成实体，并从 `mm/swapfile.c` 自动生成通过严格门禁的可重载模块；
> 见 [v35 生命周期安全 swap 成果](V35_STATE_SAFE_SWAP_RESULT.md)。

## 阶段结论

v34 把此前需要手工补类型、宏和外部导出的源码闭包推进成了自动流程。
在 Linux 5.10.176、x86_64、v31 配置匹配的完整 LLVM 图上，工具从
`select/pselect/ppoll` 的 READY 候选出发，自动完成：

1. AST 前向闭包与 LLVM incoming ownership cut；
2. 文件作用域私有 `enum`、`typedef`、`struct` 和 `#define` 的最小固定点；
3. 同源共享函数留驻、一个可复制纯静态辅助函数，以及一个跨文件未导出
   依赖的自动边界推断；
4. 源码/Kbuild/Kconfig bundle、真实 `vmlinux modules bzImage` 与 MODPOST；
5. QEMU 中首次功能调用自动加载、重复调用、卸载和再次自动加载。

这证明“通过 LLVM 引用图，把原本 built-in 且没有现成 Kconfig 模块边界
的函数自动提取成新模块”已经覆盖了源文件私有 C 语言支撑项，不再只适用
于完全由公共头文件类型组成的简单函数。

但本轮候选不是严格发布收益：与完全同参数的 v33 增量基线相比，内建
`fs/select.o` 永久段减少 1,613 B，最终链接永久区间不变，而压缩
`bzImage` 增加 4,064 B。因此 v34 计入“机制/生命周期验证集合”，不计入
“零压缩镜像回退的严格发布集合”。严格发布集合仍为 v33 的 11 个模块、
87 个最终 ELF 函数；机制验证集合扩大到 12 个模块、91 个最终 ELF 函数。

## 自动生成的真实边界

候选为 `candidate:87b07c19cf44d74b`，模块名为
`deferred_select_v34`。生成器得到的边界如下：

| 边界部分 | 自动选择结果 |
|---|---|
| 移入模块 | `__se_sys_ppoll`、`__se_sys_pselect6`、`__se_sys_select`、`core_sys_select`、`do_pselect`、`get_fd_set`、`get_sigset_argpack`、`kern_select`、`set_fd_set` |
| 模块内复制 | `zero_fd_set` |
| 留在 `fs/select.c` | `do_select`、`do_sys_poll`、`poll_select_finish`、`poll_select_set_timeout` |
| 自动发现的跨文件导出 | `set_user_sigmask` |
| 自动复制的私有声明 | `enum poll_time_type`、`fd_set_bits` typedef、`struct sigset_argpack` |
| 自动复制的私有宏 | `FDS_BITPERLONG`、`FDS_LONGS`、`FDS_BYTES` |

`get_timespec64`、`get_old_timespec32` 和 `kvfree` 已在配置匹配的
`__ksymtab` 富化图中标记为既有导出，因此不会重复修改驻留源码。
`set_user_sigmask` 只有在图的 `symbol_size_enrichment.exported_nodes`
证明导出面已完整导入时才会被自动判为“定义存在但未导出”；原始 LLVM 图
缺少该证据时，工具不会把“没看到 exported 属性”误当成“确定未导出”。

## 实现变化

### Clang 源码事实

`SourceExtractor` 的 schema-v1 以向后兼容字段新增：

- `declarations`：记录主文件中完整的文件作用域 typedef、具名 record 和
  enum 的精确 UTF-8 字节区间、源码文本和可引用标识符；
- `macro_definitions`：通过预处理回调记录主文件中每个 `#define` 的活动
  定义区间和原文；
- 原有函数、全局变量、include、宏展开和 AST 引用事实保持不变。

Python 后端先验证所有源码切片仍与当前文件一致，再从被移动实体和打包
驻留签名的标识符出发做声明/宏固定点。宏会按使用点选择此前最近的定义，
并递归追踪 `FDS_BYTES -> FDS_LONGS -> FDS_BITPERLONG`。跨翻译单元出现
同名但不同内容的私有声明或宏时会硬失败。

生成模块时，私有支撑项按原词法顺序放在实现和驻留 ABI 声明之前；驻留
依赖表则放在原文件私有类型定义之后。若懒加载公共 ABI 自身使用源文件
私有类型，生成器会明确拒绝，因为当前公共头在两侧都无法安全看到该类型；
这比生成后等待编译偶然失败更可审计。

### LLVM/AST 联合边界

候选准备阶段会合并多翻译单元提取结果，并保留新增的声明和宏事实。对被
移动 AST 实体引用的外部符号，自动导出推断只接受以下同时成立的情况：

- 配置匹配图已经导入完整 `__ksymtab` 证据；
- 唯一 external-linkage、source-defined 节点与依赖种类一致；
- 不是已导出、头文件 inline、编译器 builtin 或已有 bundle 提供的边界。

最终 bundle 的 `source_local_support` 清单逐项记录被复制声明和宏，闭包
摘要单独记录 `auto_external_resident_exports`，使自动决策可以复核。

## 构建与运行验证

内核和模块使用与基线一致的 Clang/LLVM 配置构建：`LLVM=1 LLVM_IAS=0`，
并固定 `KBUILD_BUILD_VERSION`、时间戳、用户和主机。`vmlinux`、全部模块、
`bzImage` 和 MODPOST 均通过；最终模块为 11,136 B，永久 allocatable
section 为 3,329 B。

静态触发器实际创建 ready pipe 并调用 `select`、`pselect` 和 `ppoll`。
QEMU 场景重复 4 次，每次均完成 8/8 步：READY 时模块不存在，功能调用
自动加载，重复调用成功，`rmmod` 成功，确认模块消失，再次调用并重新加载。
4 次 loader-ready 中位数为 0.500 s。

平衡交错 A/B 使用 8 对、共 16 次启动并通过门禁。v34 相对 v32 基线的
中位数变化为：最终链接永久内存 0 KiB、ready 时间 0.000 s、
`MemAvailable` -28 KiB、Slab -4 KiB；后两项在预设噪声阈值内，不能解读
为确定收益或回退。

## 三层尺寸事实

### 与同参数 v33 增量基线比较

| 指标 | v33 基线 | 加入 v34 | 变化 |
|---|---:|---:|---:|
| `fs/select.o` 永久段 | 11,617 B | 10,004 B | -1,613 B |
| 新模块永久段 | 0 B | 3,329 B | +3,329 B |
| 全部加载后的合计 | 11,617 B | 13,333 B | +1,716 B |
| 最终链接永久区间 | 15,704,648 B | 15,704,648 B | 0 B |
| `bzImage` | 5,040,224 B | 5,044,288 B | +4,064 B |

探索门禁允许最多 4,096 B 的压缩镜像波动，因而通过；严格发布门禁要求
`bzImage` 不增长，因而失败。压缩率会随符号表、布局和字节模式非单调变化，
不能用内建对象减少量推断压缩镜像一定减少。

### 与 v32 纯基线比较

v32 纯基线到当前 12 模块构建的对齐前 payload 共减少 36,864 B，但被
x86 的 2 MiB entry-text 对齐岛完整吸收，最终 text 区间仍无变化。距离
前一个对齐边界还需减少 219,312 B。当前 `bzImage` 为 5,044,288 B，
相对 v32 纯基线 5,056,256 B 仍累计减少 11,968 B；但它比 v33 的
5,040,224 B 更大，故不能把累计值归因成 v34 的新增收益。

## 完整内核比例

最终 ELF 分母保持为 28,959 个正大小函数、6,693,841 B；分类也保持：

| 分类 | 函数 | 占比 | 字节 | 字节占比 |
|---|---:|---:|---:|---:|
| CORE | 26,732 | 92.309817% | 6,353,289 | 94.912458% |
| READY | 81 | 0.279706% | 56,123 | 0.838427% |
| BLOCKED | 296 | 1.022135% | 42,480 | 0.634613% |
| UNKNOWN | 1,850 | 6.388342% | 241,949 | 3.614502% |

v34 移动 9 个 C 实现，其中 4 个在基线最终 ELF 中保留独立正大小符号：
`__se_sys_ppoll` 336 B、`__se_sys_select` 359 B、
`__se_sys_pselect6` 430 B、`core_sys_select` 888 B，共 2,013 B。其余 5 个
辅助函数被编译器内联或合并，不能重复计为最终函数。

- 机制验证集合：12 模块、131 个迁移 C 函数、91/28,959 个最终 ELF
  函数（0.314237%）、39,529 B（0.590528%）。
- 严格发布集合：11 模块、122 个迁移 C 函数、87/28,959 个最终 ELF
  函数（0.300425%）、37,516 B（0.560455%）。

## 下一阶段建议

下一轮不应继续按“函数个数最多”选择候选，而应按真实发布收益排序：

1. 用当前私有声明/宏能力批量试编译此前机械失败的同源闭包；
2. 将可由同一加载时机触发的多个小候选合并到一个模块，共享 operations
   表、加载器、modinfo 和重定位开销；
3. 对每个组合建立可复现 v33 增量基线，先以对象永久节省和严格
   `bzImage <= baseline` 淘汰，再投入 QEMU 功能验证；
4. 从 `BOOT_COLD` 而不是 planner `CORE` 名称出发重新发现大闭包，但仍
   必须重新证明跨边界调用、回调生命周期、状态所有权和加载上下文；
5. 以再减少至少 219,312 B 对齐前 payload 为链接页目标，跨界后仍以最终
   `vmlinux` 和启动 A/B 为准。

机器可读数据见
[`full-kernel-v34-source-support-summary.json`](full-kernel-v34-source-support-summary.json)。
