# v33：LLVM 引用图驱动的源码闭包自动提取

> 后续 v34 已自动补齐源文件私有 typedef/enum/record、宏定义和有完整
> 导出面证据的跨文件驻留依赖；真实 select 闭包及严格发布门禁结果见
> [v34 自动源码支撑成果](V34_AUTOMATIC_SOURCE_SUPPORT_RESULT.md)。

## 阶段结论

v33 已把后端从“只能提取 planner 直接列出的 syscall 函数”推进到“以候选
入口为种子，自动发现同编译单元的私有函数/全局变量闭包，再用完整 LLVM
引用图切开跨编译单元共享边界”。本轮从原本 built-in、没有现成 Kconfig
模块边界的 `fsopen`、keyctl 和 signal 代码中生成三个新 `.ko`，并完成
Kbuild/MODPOST、QEMU 自动加载、卸载、重载和启动 A/B。

本轮新增迁移 52 个 C 函数和 1 个私有全局变量；其中 42 个函数能与基线
最终 ELF 的独立正大小函数精确对应。与 v32 的八个模块合并后，当前严格
验证集合为：

- 11 个新函数模块；
- 122 个迁移的 C 函数实现；
- 87/28,959 个基线最终 ELF 函数，比例 **0.300425%**；
- 对应基线函数体 37,516/6,693,841 B，比例 **0.560455%**。

这里的“87 个”表示其基线实现已被迁移并通过七项发布门禁。syscall 的原
ABI 符号仍以很小的常驻加载桩存在，所以不能把这个数字解释为 `vmlinux`
中直接消失了 87 个符号。

## 新增的自动闭包算法

流程现在是：

```text
READY syscall 入口
        │
        ▼
Clang AST 精确前向闭包 ──► 同源私有函数与全局变量
        │
        ▼
LLVM incoming ownership edges
        │
        ├─ 只有本组合候选使用 ──► 移入新模块
        └─ 仍被其他编译单元使用 ─► 留在常驻 frontier
                                      │
                                      ▼
                              一张强类型依赖表
```

实现入口是 `prepare-candidate --expand-private-source-closure`。它让
`SourceExtractor` 枚举主源文件的定义，以 AST 引用区间做闭包，再检查每个
实体的 LLVM 入边：

- `DIRECT_CALL`、`GLOBAL_READ/WRITE`、`GLOBAL_INITIALIZER` 和
  `ADDRESS_TAKEN` 等真实所有权边会阻止错误搬移；
- generic dispatcher 到潜在回调目标的 `INDIRECT_CALL` 不是链接期所有权，
  不会单独把所有目标留在核心；保存函数地址的全局初始化器仍会被检查；
- LLVM 图没有覆盖到的闭包实体会导致硬失败，不能把“图中缺失”当成私有；
- 被其他编译单元使用的 frontier 自动转入强类型常驻依赖表，不逐项制造
  大量 ksymtab 边界；
- 报告保存每个 frontier 的 AST 用户、LLVM 用户、边类型和 node ID，便于
  审计为什么某个函数没有被移动。

`--merge-candidate` 支持把 ABI 上属于同一功能的候选作为一个所有权域。
为了防止它绕过 planner，主候选现在必须为 READY；附加候选如果是
`NEEDS_EVIDENCE`，只允许“缺少函数尺寸数据”这一种原因，并且必须保持
`LAZY_READY`，所有执行入口都已有可睡眠加载证明。阶段、IRQ/atomic 或
未解析调用的不确定性不能靠合并消除。

后端同时增加了 `__se_compat_sys_*`/`__do_compat_sys_*` 的 AST 宏恢复，
因此 compat syscall 不再需要手写函数体。生成模块中的本地双引号 include
会转换为从 `kernel/linux_modularizer` 出发的确定路径，避免多个模块积累
`-I` 后把不同子系统都叫 `internal.h` 的头文件解析错。

## 三个新模块

| 模块 | 迁移 C 函数 | 基线 ELF 函数 | 基线对象永久段 | 常驻对象永久段 | 未加载节省 | `.ko` 永久段 |
|---|---:|---:|---:|---:|---:|---:|
| `deferred_fsopen_v33` | 6 | 5 | 2,952 B | 1,506 B | 1,446 B | 3,325 B |
| `deferred_keyctl_v33` | 34 | 30 | 11,027 B | 1,362 B | 9,665 B | 13,407 B |
| `deferred_signal_v33` | 12 | 7 | 39,565 B | 38,028 B | 1,537 B | 3,427 B |
| **合计** | **52** | **42** | **53,544 B** | **40,896 B** | **12,648 B** | **20,159 B** |

### fsopen

种子为 `fsopen/fsconfig/fspick` 三个 syscall。AST 自动追加
`fscontext_alloc_log`、`fscontext_create_fd` 和
`vfs_fsconfig_locked`。`fscontext_fops` 看起来在同一源文件内，但 LLVM
图证明 namespace/fsmount 仍会跨编译单元使用它，因此它被自动切到常驻
frontier，而不是随闭包搬走。这正是单纯源码 grep 无法可靠处理的情况。

### keyctl

native keyctl 主候选与 compat keyctl 候选合并为一个模块。若单独分析 native
源文件，compat 的跨单元入边会迫使 26 个函数常驻；把两个入口声明为同一
组合所有权域后，闭包可移动 34 个函数和 `keyrings_capabilities`，且没有
自动常驻 frontier。21 个真正的常驻依赖通过一张只读类型表返回模块。

compat 候选本身缺少机器码尺寸，因此仍是 `NEEDS_EVIDENCE`；它没有阶段或
上下文缺口，最终真实编译和尺寸门禁替代了这项估计证据。这是当前唯一允许
合并的非 READY 情况。

### signal

七个 syscall 种子自动扩展为 12 个函数。LLVM 入边把仍被核心 signal 路径
使用的 12 个同源函数留在常驻 frontier；另有一个审计过的常驻函数和两个
外部常驻依赖。模块实现最终保留 7 个独立 ELF 函数，其余私有 helper 被
编译器内联。这解释了“迁移 C 函数数”与“最终 ELF 函数数”不相等。

## 全内核真实统计

统计使用带对象尺寸的 hybrid 图，并以未模块化基线 `vmlinux` 的正大小
函数符号为分母。无尺寸图只能用于引用分析；若误拿它做最终对账，所有符号
都会保守落入 UNKNOWN。

| 分类 | 函数 | 占比 | 字节 |
|---|---:|---:|---:|
| CORE | 26,732 | 92.309817% | 6,353,289 |
| READY | 81 | 0.279706% | 56,123 |
| BLOCKED | 296 | 1.022135% | 42,480 |
| UNKNOWN | 1,850 | 6.388342% | 241,949 |

IR→最终 ELF 精确覆盖为 28,129/28,959（97.133879%）。CORE 仍是“当前
安全策略下常驻”，不是“启动阶段实际执行”；启动冷热上限与可安全提取边界
继续采用两套独立统计。

最终成功统计通过两个独立 validation artifact 合并生成，CLI 的
`--successful-validations` 现在可重复传入，并拒绝重复 candidate ID：

```bash
python3 -m kernel_modularizer summarize-full-kernel \
  "$RUN/vmlinux-reference-graph-v32-hybrid-sized.json" \
  --plan "$RUN/module-plan-v32-hybrid.json" \
  --vmlinux "$BASE_OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --successful-validations \
    "$RUN/successful-validation-packed-eight-v32.json" \
  --successful-validations "$RUN/successful-validation-v33.json" \
  --json-output "$RUN/full-kernel-stats-v33-final.json" \
  --markdown-output "$RUN/full-kernel-stats-v33-final.md"
```

## 三层尺寸结果

| 指标 | 基线 | 当前 11 模块内核 | 差值 |
|---|---:|---:|---:|
| 本轮 4 个受影响 `.o` 永久段 | 53,544 B | 40,896 B | **-12,648 B** |
| 最终链接永久区间 | 15,704,648 B | 15,704,648 B | **0 B** |
| `bzImage` | 5,056,256 B | 5,040,224 B | **-16,032 B** |

本轮三个 `.ko` 全加载后永久段为 20,159 B，相对本轮对象节省增加 7,511 B。
这符合“未使用时不为实现体付费”的目标，但不适合所有功能同时常开的部署。

v32 与 v33 的受影响对象互不重叠，所以 11 模块的对象层未加载净减少为
31,388 B。最终 x86 链接布局仍被固定区间和对齐吸收，永久区间没有跨过一
页；因此当前可宣称的是 **0 B 启动永久页改善、0 B 回退**，不能把对象层
31,388 B 写成启动内存下降。`bzImage` 的 16,032 B 是磁盘/传输体积收益，
也不是运行时内存收益。

v33 新增 `analyze-linker-alignment` 后，0 B 的原因可以精确量化。x86 链接
脚本在普通主 text 后把 entry text 和其后区域按 2 MiB 对齐：

| 对齐诊断 | 基线 | 11 模块 |
|---|---:|---:|
| `__kprobes_text_end` 前的 payload | 6,547,632 B | 6,514,864 B |
| entry text 前的对齐填充 | 1,840,976 B | 1,873,744 B |
| 最终 text 区间 | 10,493,448 B | 10,493,448 B |

也就是说主 text 实际减少了 32,768 B，但填充恰好增加 32,768 B。当前
`__kprobes_text_end` 还超过前一个 PMD 边界 223,408 B；至少再移走这么多
对齐前 payload，才有机会让 entry/softirq 两个对齐岛整体前移一个 2 MiB
台阶，之后仍必须用真实链接验证。当前 READY 最终函数池只有 56,123 B，
所以仅完成现有小候选不可能跨过这个阈值；需要发现更大的安全源码闭包，或
设计把可延迟 cold text 放到最后一个对齐岛之后的专用布局。

可复现诊断命令：

```bash
python3 -m kernel_modularizer analyze-linker-alignment \
  "$BASE_OUT/vmlinux" "$MOD_OUT/vmlinux" \
  --nm "$LLVM_HOME/bin/llvm-nm" \
  --json-output "$RUN/v33-linker-alignment.json" \
  --markdown-output "$RUN/v33-linker-alignment.md"
```

## QEMU 生命周期与启动 A/B

新的静态触发器实际执行：

- `fsopen(tmpfs)`、`fsconfig(size)` 和 `FSCONFIG_CMD_CREATE`；
- session keyring、`add_key`、`request_key`、read 和 unlink；
- `rt_sigprocmask`、`kill`、`rt_sigtimedwait`、`pidfd_open` 和
  `pidfd_send_signal`。

同一场景重复 4 次，每次完成 READY 时三模块不存在、功能触发自动加载、
重复调用、三个 `rmmod`、确认消失、再次功能触发并重新加载，共 8/8 步。
4 次全部通过，READY 中位数 0.520 s。

8 对平衡交错启动 A/B 全部通过：

| 指标 | 当前 - 基线 | 门禁 |
|---|---:|---:|
| 永久内核汇总 | 0 KiB | ≤ 0 KiB |
| READY uptime | 0.000 s | ≤ +0.250 s |
| `MemAvailable` | +48 KiB | ≥ -128 KiB |
| Slab | -24 KiB | ≤ +64 KiB |

后两项的小幅变化只作为“没有明显回退”的证据，不声明为稳定收益。

## 被拒绝的大闭包：io_uring

io_uring 的 AST 闭包看起来更诱人：127 个可移动函数、66 个常驻 frontier
函数和 3 个 frontier 全局变量。但它没有被应用，原因不是编译困难，而是
生命周期不满足当前 syscall 懒加载模型：

- io_uring 工作和 callback 可以在发起 syscall 返回后继续运行；
- 常驻 `file_operations` 不会自动为被移动实现持有长期模块引用；
- 后端还识别到 `req_ref_get` 的特殊源码语义并拒绝生成。

如果只证明“启动阶段没调用”就强拆，这类候选很容易在卸载后形成异步
use-after-free。未来必须提供“对象持有期间 pin module、销毁时 put”的
子系统级生命周期协议，不能复用当前只覆盖一次同步调用的 wrapper。

## 可复现提取入口

普通候选现在使用：

```bash
python3 -m kernel_modularizer prepare-candidate \
  "$RUN/vmlinux-reference-graph-v32-hybrid.json" \
  "$RUN/module-plan-v32-hybrid.json" CANDIDATE_ID \
  --source-extractor Backend/AutoBackend/cpp/SourceExtractor \
  --compile-database "$RUN/v33-compile-db/compile_commands.json" \
  --kernel-root "$KERNEL" \
  --module-name MODULE_NAME \
  --expand-private-source-closure \
  --pack-resident-dependencies \
  --output-directory "$RUN/MODULE_NAME"
```

keyctl 额外使用：

```text
--merge-candidate candidate:b9766c20907420c1
--module-defines {"KEY_MAX_DESC_SIZE":"4096"}
```

闭包和 frontier 是自动生成的；当前跨源、尚未导出的常驻依赖仍需进入审计
过的 `--external-resident-export` 清单，并由 MODPOST 最终确认。下一阶段会
把 MODPOST 未解析符号反馈、图中的 `exported` 证据和已应用 bundle 的共享
导出注册表合并，减少这部分人工清单，但不能在缺少唯一符号身份时自动扩大
可见性。

机器可读汇总见
[`full-kernel-v33-source-closure-summary.json`](full-kernel-v33-source-closure-summary.json)。
关键外部报告哈希：

- v33 validation：`9a26d926ddfa39b92d3e83e70386cff30869d7ea9f1ee05175cb0f22d68ba53d`；
- 合并全内核统计：`744ce1dce91a717efccfe45cc690d3902357dc8c9319f3fd053929bbf26b4f3b`；
- 严格尺寸门禁：`a526081cc10740f67deb3f0fa9fbdf55d92fc309bc912b00ea440f45ef60d851`；
- 链接器对齐诊断：`61e72237d6b4e9e3acf4e73c10d312629328eec4133ebaacb45fdc831b912f42`；
- 4 次 QEMU 生命周期：`5bf179ff66dfc4c94201b4ea73ff984b5e31fbe1612eae364e6ef4a21fb440e0`；
- 8 对启动 A/B：`8ba774beb5220d6da17e2fa37ea1e42ae6d3b044d35ee4be62cd8c85df53068c`；
- 当前 `vmlinux`：`f7984b84285bb25856340840f85b25d6a4d1df3c7806179e3a0e49a2cafe3664`；
- 当前 `bzImage`：`204f7861486d2ccbd98580789eba99c1f949e9af12af562b0e083a95a4954e16`。

## 下一步

当前机制已经证明“LLVM 图不仅用来列调用关系，还能直接决定源码所有权
闭包和常驻切面”。下一阶段最有价值的改进不是放松安全条件，而是：

1. 把 MODPOST 未解析依赖变成结构化反馈，自动区分已导出、可打包、宏/内联
   隐藏和必须人工审计四类；
2. 建立跨 bundle 的共享常驻依赖注册表，避免多个模块重复导出同一符号；
3. 用真实链接后的边际区间变化训练组合选择器，优先累计至少 223,408 B 的
   对齐前安全闭包，或把 cold text 布置到最后一个对齐岛之后；
4. 为异步回调、`file_operations` 和长期对象设计独立的 module pin 生命周期
   后端，再重新评估 io_uring、网络和存储候选；
5. 扩展文件系统、网络、容器、LSM、PREEMPT/RT 和多架构工作负载矩阵。

在完成第 3 项前，项目已经有扎实的“新函数模块自动生成”成果，但仍不应
宣传为已经降低了启动永久内存页。
