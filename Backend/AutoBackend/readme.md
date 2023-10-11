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

### 编译寻找未导出符号添加UNEXPORT_VAR宏