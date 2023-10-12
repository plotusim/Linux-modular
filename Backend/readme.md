# 使用说明

## 提取内核模块：

### 修改config.py:

- res_graph_dot_path为要提取的模块的res.dot的路径
- module_name为模块名
- kernel_source_root_path为要修改的Linux源代码的路径
- kernel_bc_file_root_path为编译生成过bc文件的源代码路径
- module_template_files_dir为模版文件路径，即为本目录BACKEND下的sys_module文件夹

### 运行main.py:

- 1.进入目录AutoBackend
- 2.运行main.py

## linux内核编译：

### 编译内核：

- 1.进入linux内核目录
- 2.make menuconfig defconfig
- 3.选择Processor type and features ----> [] Randomize the address of the kernel image (KASLR),将值设为"N"。
- 4.make -j32

### 编译Busybox:

- 1.进入目录Busybox
- 2.make menuconfig
- 3.选择setting ----> Build static binary,将值设为"Y"。
- 4.sudo make -j32
- 5.make install

### 修改make.sh：

- busybox/_install/修改为当前系统下的绝对路径
- gen_fs.sh修改为当前系统下的绝对路径
- fs.sh修改为当前系统下的绝对路径

### 执行make.sh

- 1.进入linux内核目录
- 2.bash path_to_make.sh
