# 项目描述

linux内核自动模块化工具。

# 环境配置

1. 下载llvm-15.0.0

   ```shell
   cd Frontend
   bash build-llvm.sh
   ```
2. python require packages

   ```
   kconfiglib==14.1.0
   openpyxl==3.1.2
   pydot==1.4.2
   pyelftools==0.29
   ```

# 使用说明

## Frontend

前端函数分析过程集成到run_frontend.sh，步骤存在前后依赖，但是中间文件都会保存，故可以拆开单步执行。注意，前端分析时间较长。

```shell
cd Frontend
bash run_frontend.sh
```

## Middleend

ModuleAdvizer根据前端生成的各种类型的函数列表给子系统内的函数进行分类，然后基于调用关系给出模块化的建议传给后端进行提取

使用说明

```shell
cd Middleend/ModuleAdvizer
python run.py -a -o  ../result
```

输出结果会放到Middleend/result下

## Backend

使用说明
