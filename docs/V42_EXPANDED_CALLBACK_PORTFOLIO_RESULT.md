# v42：放宽候选门槛后的 15 组回调闭包

## 结论

这轮不再把规划器的 `CORE` 当作“永远不能模块化”，而是把它理解为
“启动可达或尚未证明可直接迁移”。在保留 loader、执行上下文、对象生命期、
ABI、源码闭包、构建和卸载检查的前提下，完整 LLVM 引用图又找到了 15 个
可改写的常驻回调表，并生成 15 个首次调用自动加载的模块。

- 新迁出 89 个 C 函数定义，全部原先被规划器归为 `CORE`。
- 其中 56 个是最终基线 ELF 中独立存在的函数，共 28,491 B；其余 33 个
  被内联或消除，不能重复计入 ELF 函数数和字节数。
- 15 个受影响常驻对象的永久段从 132,120 B 降到 111,900 B，未加载时
  净减少 20,220 B。
- `bzImage` 从 v41 的 5,008,160 B 降到 4,998,080 B，增量减少 10,080 B；
  同配置纯 v32 基线为 5,056,256 B，累计减少 58,176 B（1.150575%）。
- 完整 `vmlinux` 永久链接区间增量减少 64 B。启动日志中的永久内核分段和
  `rwdata` 各下降 1 KiB；更小的 ready 时间及空闲内存变化视为噪声。
- 4 轮 QEMU 共 84/84 个生命周期步骤通过；15 个模块均在 READY 时不存在，
  随真实功能首次使用加载，随后可以卸载并再次加载。

机器可检查的完整数据在
[full-kernel-v42-expanded-callback-summary.json](full-kernel-v42-expanded-callback-summary.json)。

## 新增闭包与真实收益

表中的“ELF”是能与不可变纯 v32 函数分母对应的独立最终函数；“常驻省”是
模块未加载时受影响 `.o` 永久可分配段的减少量；“全加载差”是把新模块永久
段全部加回来后的变化，因此为正数代表全加载时内存变大。

| 模块 | 原始源文件 / 回调表 | 接口 | C 定义 | ELF / 字节 | 常驻省 | 模块永久段 | 全加载差 |
|---|---|---:|---:|---:|---:|---:|---:|
| `deferred_mtrr_v42_cb` | `arch/x86/kernel/cpu/mtrr/if.c` / `mtrr_proc_ops` | 2 | 4 | 2 / 2,330 B | 1,855 B | 3,789 B | +1,934 B |
| `deferred_kcore_v42_cb` | `fs/proc/kcore.c` / `kcore_proc_ops` | 1 | 3 | 1 / 1,987 B | 1,681 B | 3,781 B | +2,100 B |
| `deferred_relay_v42_cb` | `kernel/relay.c` / `relay_file_operations` | 3 | 10 | 4 / 2,602 B | 2,058 B | 4,201 B | +2,143 B |
| `deferred_rtc_v42_cb` | `drivers/rtc/dev.c` / `rtc_dev_fops` | 5 | 5 | 5 / 2,129 B | 1,339 B | 3,539 B | +2,200 B |
| `deferred_socket_v42_cb` | `net/socket.c` / `socket_file_ops` | 9 | 17 | 13 / 4,564 B | 3,652 B | 6,366 B | +2,714 B |
| `deferred_shmem_v42_cb` | `mm/shmem.c` / `shmem_file_operations` | 2 | 3 | 2 / 2,112 B | 1,659 B | 3,548 B | +1,889 B |
| `deferred_hugetlbfs_v42_cb` | `fs/hugetlbfs/inode.c` / `hugetlbfs_file_operations` | 2 | 5 | 2 / 1,975 B | 1,325 B | 3,508 B | +2,183 B |
| `deferred_seccomp_notify_v42_cb` | `kernel/seccomp.c` / `seccomp_notify_ops` | 2 | 7 | 2 / 1,719 B | 1,365 B | 2,933 B | +1,568 B |
| `deferred_eventfd_v42_cb` | `fs/eventfd.c` / `eventfd_fops` | 4 | 4 | 4 / 1,256 B | 652 B | 2,664 B | +2,012 B |
| `deferred_signalfd_v42_cb` | `fs/signalfd.c` / `signalfd_fops` | 3 | 5 | 3 / 1,386 B | 865 B | 2,798 B | +1,933 B |
| `deferred_dma_buf_v42_cb` | `drivers/dma-buf/dma-buf.c` / `dma_buf_fops` | 4 | 6 | 5 / 1,644 B | 1,137 B | 3,191 B | +2,054 B |
| `deferred_snapshot_v42_cb` | `kernel/power/user.c` / `snapshot_fops` | 4 | 5 | 5 / 1,716 B | 815 B | 3,564 B | +2,749 B |
| `deferred_pci_proc_v42_cb` | `drivers/pci/proc.c` / `proc_bus_pci_ops` | 4 | 4 | 4 / 1,300 B | 723 B | 2,809 B | +2,086 B |
| `deferred_bsg_v42_cb` | `block/bsg.c` / `bsg_fops` | 1 | 4 | 1 / 884 B | 714 B | 2,186 B | +1,472 B |
| `deferred_inotify_v42_cb` | `fs/notify/inotify/inotify_user.c` / `inotify_fops` | 3 | 7 | 3 / 887 B | 380 B | 2,376 B | +1,996 B |
| **合计** | **15 个翻译单元 / 15 张表** | **49** | **89** | **56 / 28,491 B** | **20,220 B** | **51,253 B** | **+31,033 B** |

这说明小闭包仍然有累计价值：每个候选单独只节省 380–3,652 B，但组合以后
超过 4 KiB 门槛，并同时降低最终链接区间和压缩镜像。另一方面，模块包装、
导出表和模块元数据有固定成本；若 15 个功能全部使用，相关常驻段加模块段
会比原 built-in 方案多 31,033 B。因此该方案优化的是“按需未加载集合”，
不是保证任何工作负载下的总内存都下降。

## 后端为这批代码补上的能力

`hugetlbfs` 暴露出一个此前会导致错误迁移的边界：源文件内的 `static inline`
辅助函数同时被常驻端和模块端使用。现在闭包分析会把这类函数登记为
`resident_inline_proxies`，保留其源定义，只向模块支撑代码复制所需声明；
源闭包统计也不再把它们算成迁出定义。

Clang 的 record 源区间还可能在 `struct X { ... } variable;` 中停在右花括号，
遗漏声明结尾。后端现在会为这类支撑声明补分号，并新增了嵌入 record 与
源内 inline proxy 的回归夹具。完整单元测试最终为 286/286。

## 没有强行迁出的候选

`task_mmu` 的两个回调虽然满足“启动阶段未使用”的直觉，但 x86
`asm/tlbflush.h` 把它需要的 TLB API 放在 `#ifndef MODULE` 下。这意味着模块
编译环境有意不提供该 ABI。绕过它需要修改 x86 核心 TLB 接口、增加新的
导出或改变体系结构约束，已经不是单纯的函数闭包提取，所以本轮将其剔除。

这个反例也回答了“init 没调用是否都能模块化”：不是。启动冷只表示值得尝试，
仍需证明 loader 已可用、调用上下文允许睡眠、已注册函数指针不会悬空、持久
状态所有权不跨越卸载边界、类型和符号对模块可见，并通过真实构建与卸载测试。

## 构建与体积验证

同一份 v41 构建目录快照作为基线，当前源码完成：

- `vmlinux`、`bzImage` 和全部内核模块完整构建；MODPOST 通过。
- 15 个生成模块全部构建，没有生成源码警告。
- `vmlinux` 文件为 34,711,544 B；永久链接区间从 15,704,584 B 降到
  15,704,520 B。
- `bzImage` 从 5,008,160 B 降到 4,998,080 B，是同配置累计实验中首次低于
  5,000,000 B。
- 即使用户允许放宽，本组合仍意外通过原严格发布门禁：对象常驻收益至少
  4,096 B，最终永久链接区间不回退，`bzImage` 不回退。

这些数字与曾经出现的“9,756,736 B 降到 5,056,256 B”不是同一种实验：后者
混入了内核配置变化，不能归因于函数自动模块化。这里始终用同一配置的纯 v32
5,056,256 B 作累计基线，因此可归因收益是 58,176 B，而不是 48%。

## QEMU 生命周期验证

测试使用真实用户 API 触发 eventfd、signalfd、socket、inotify、shmem、
hugetlbfs、kcore、MTRR、RTC、snapshot、PCI proc、bsg、dma-buf、relay 和
seccomp notification。dma-buf 与 relay 由仅用于 QEMU 的小型内核夹具提供
真实对象，bsg 使用 QEMU IDE 盘形成 `/dev/bsg/0:0:0:0`。

4 次独立启动各完成 21 步，共 84/84：

- loader-ready 时 15 个目标模块全部不存在；
- 每项真实功能第一次调用均按 alias 自动加载对应模块；
- 每个模块在使用结束后均能 `rmmod`；
- 15 项功能组合再次使用后，全部模块能再次加载并统一卸载；
- eventfd 同一个已打开 fd 跨越卸载和重载后仍能继续工作；
- 四个进程并发首次绑定 eventfd 通过；
- 最终 dmesg 无 BUG、Oops、WARNING、general protection fault 或 panic。

relay 的 read/poll 路径已实际完成；其 splice 探针在本 Linux 5.10 夹具上返回
预期的 `EINVAL`，测试只把它记为“预期错误路径”，没有声称 splice 传输成功。

## 启动 A/B 的诚实解释

相同 initramfs、磁盘和 QEMU 参数下运行了两批各 8 对平衡交错 A/B：

- 第一批永久内核为 -1 KiB，但 Slab 中位数为 +80 KiB，超过 +64 KiB 噪声
  门槛，因此该批整体判失败。
- 第二批通过，Slab 为 -48 KiB。
- 合并 16 对后通过：永久内核 -1 KiB、`rwdata` -1 KiB、ready -0.04 s、
  `MemAvailable` +4 KiB、Slab +10 KiB。

后四项会受启动调度和 slab 分配抖动影响，不能解释成性能提升。可靠结论是：
精确 ELF/对象/镜像均减少，且 printk 的段级 KiB 统计在 32 次启动中稳定显示
永久内核少 1 KiB；没有观察到超出门槛的综合启动回退。

## 累计结果与函数占比

| 指标 | v41 累计 | v42 累计 | 本轮增量 |
|---|---:|---:|---:|
| 严格合格模块 | 21 | 36 | +15 |
| 迁出 C 函数定义 | 371 | 460 | +89 |
| 最终 ELF 函数 | 242 | 298 | +56 |
| ELF 函数占纯 v32 | 0.835664% | 1.029041% | +0.193377 个百分点 |
| 匹配函数字节 | 111,302 B | 139,793 B | +28,491 B |
| 匹配字节占纯 v32 | 1.662752% | 2.088382% | +0.425630 个百分点 |
| 常驻对象累计收益 | 92,123 B | 112,343 B | +20,220 B |
| `bzImage` 相对纯 v32 | -48,096 B | -58,176 B | -10,080 B |

不可变纯 v32 的 28,959 个最终 ELF 函数仍按保守规划分区：`CORE` 26,732、
`READY` 81、`BLOCKED` 296、`UNKNOWN` 1,850。成功迁出的 298 个函数是与这四类
正交的“已验证成功集合”；不能为了让 READY 看起来更大而从分母中删除它们。

下一阶段应把真实目标发行版的 first-use 时间加入排名：优先选择长期不使用、
闭包字节大、接口少的回调源组，同时继续累计多个小正收益组。这样能提高长期
未加载收益，而不是仅凭“单次最小 initramfs 没有调用”把所有冷函数强行迁出。
