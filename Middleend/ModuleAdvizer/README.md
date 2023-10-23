# ModuleAdvizer说明

ModuleAdvizer根据前端生成的各种类型的函数列表给子系统内的函数进行分类，然后基于调用关系给出模块化的建议传给后端进行提取

# 运行方法

## 分析每个子系统

cd ModuleAdvizer目录

```
python run.py -a -o 输出目录
```

比如

```
python run.py -a -o ../result_pa_new
```

## 分析特定的子系统

```
# please make sure that find_inline.py and find_use_trace_syscall.py run first,
# which automatically run when you run run.py
python run.py -i 子系统的相对路径 -o 输出目录
```

比如

```
python run.py -i net -o ../result_test/
```

这会在../result_test/net/res.dot中存储我们的模块化建议
其中，该子系统的原始调用图会存在 ../result_pa/net/temp/net_origin_v.dot中
然后建立外部调用边后合并不可模块化的调用图会存储在 ../result_pa/net/temp/net_extern_simplified_v.dot

# 项目文件说明

以下是该项目的文件结构以及每个文件的用途。

## analyze.py

这个文件是中端分析调用图给出模块化建议的主要的脚本，

## config 目录

这个目录包含配置相关的代码，如可视化函数类型的颜色映射、前端各种数据路径、设定的非模块化的函数类型集合等。

## graph_ops 目录

这个目录可能包含用于操作图数据结构'pydot.Graph'的代码。其中的每个文件可能负责不同的图操作，如构建邻接列表、构建外部引用节点、原始图、分割图、生成函数推荐的操作、简化图和一些转换操作。

## run.py

这个文件包含用于运行整个项目的主要脚本。它可能调用其他模块来执行特定任务，并管理整个流程。

## temp 目录

这个目录包含临时文件和数据，用于项目的中间步骤。这些文件通常不是项目的最终输出。

## utils 目录

这个目录包含各种实用工具和辅助函数。其中的文件提供用于缓存、加载邻接列表、合并DOT文件和读取特定类型文件的功能。
