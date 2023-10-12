# 使用说明

## 提取内核模块：

### 修改config.py:

- res_graph_dot_path为要提取的模块的res.dot的路径
- module_name为模块名
- kernel_source_root_path为要修改的Linux源代码的路径
- kernel_bc_file_root_path为编译生成过bc文件的源代码路径
- module_template_files_dir为模版文件路径，即为Backend/sys_module文件夹

### 运行main.py:

- ```shell
  cd Backend/AutoBackend
  python main.py
  ```

  

## linux内核编译：

### 编译内核：

- ```
  cd linux-5.10.176
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

- error:implicit declaration of function

  将相关函数定义添加到unexport_symbol.hwen宏EXPORT_FUNC(func,ret,...)中,并将内核模块代码中相应的函数名func改为_func

- error:'var_name’ undeclared (first use in this function)

  将导出符号在模块中的名称修改为_var_name,这是由于为避免与内核代码冲突，将导出符号进行了重命名添加了' _ '前缀。

- error:passing argument  of ‘func’ from incompatible pointer type

  note: expected ‘ret *’ but argument is of type ‘ret **’

  - 一般情况下是未导出符号var通过宏导出后，模块内的函数将符号作为参数使用时添加了&，例如func(a,b,&var)，将&删除即可;

  - 第二种情况为func(a,b,var),此时需要修改为func(a,b,*var)

### 编译Busybox:

- ```shell
  cd busybox
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

- ```
  cd linux-5.10.176
  bash ~/Backend/make.sh
  ```

  
