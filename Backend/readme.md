# 使用说明

## 提取内核模块：

### 修改config.py:

- res_graph_dot_path为要提取的模块的res.dot的路径
- module_name为模块名
- kernel_source_root_path为要修改的Linux源代码的路径
- kernel_bc_file_root_path为编译生成过bc文件的源代码路径
- module_template_files_dir为模版文件路径，即为Backend/sys_module文件夹

### 运行main.py:

- 1.进入目录AutoBackend
- 2.运行main.py

## linux内核编译：

### 编译内核：

- 进入linux内核目录

- ```shell
  make menuconfig defconfig
  ```
  
- 将KASLR选项设置为"N"
  ```shell
  Processor type and features ----> 
  		[] Randomize the address of the kernel image (KASLR)
  ```

- ```shell
  make -j32
  ```

### 手动调试：

- 

### 编译Busybox:

- 进入目录Busybox

- ```shell
  make menuconfig
  ```

- 将Build static binary选项设置为"Y"

  ```
  setting ----> Build static binary
  ```

- ```shell
  sudo make -j32
  make install
  ```

  

### 修改make.sh：

- busybox/_install/修改为当前系统下的绝对路径
- gen_fs.sh修改为当前系统下的绝对路径
- fs.sh修改为当前系统下的绝对路径

### 执行make.sh

- 1.进入linux内核目录
- 2.bash path_to_make.sh
