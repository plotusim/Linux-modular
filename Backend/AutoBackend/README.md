# 使用说明

目前完成：

- 创建模块文件夹，复制模版文件
- 自动提取include宏添加到模块的源文件
- 删除DELETE函数，提取NORMAL
- 自动创建接口函数以及添加对应接口函数宏到interface.h
- 修改Module目录下的Makefile文件和修改Kconfig文件
- 修改drivers目录下的的Makefile文件和Kconfig文件

### 修改config.py:

- res_graph_dot_path为要提取的模块的res.dot的路径
- module_name为模块名
- kernel_source_root_path为要修改的Linux源代码的路径
- kernel_bc_file_root_path为编译生成过bc文件的源代码路径，"/home/plot/clang-linux-5.10.176/linux-5.10.176"可用
- module_template_files_dir为模版文件路径，这里设置的是 "/home/plot/Linux-modular/cjh/test/sys_module"

### 运行main.py:

- 1.进入目录AutoBackend
- 2.运行main.py

# 目前未完成

### 处理头文件中的函数会有bug，暂时发现inline函数的依赖关系问题

### 寻找未导出符号添加UNEXPORT_VAR宏有bug

1. 寻找到的未导出符号名字可能会含有.符号，还未去寻找原因
2. 提取未导出符号类型在C源码中的类型可能会出现问题，目前的程序只能处理常见的数值类型和struct以及他们的指针

# 项目结构

```
.
├── config.py 一些路径的设置
├── CopyKernelSrc.sh 方便我删除复制新的内核源代码的副本
├── cpp 包含提取函数使用到的全局符号的C++代码
├── handle 包含具体处理每一个需求的handle
├── main.py
├── readme.md
├── tests 对一些函数的测试
└── utils 在handle中使用到的一些工具函数，比如关于文件、函数的操作
```

