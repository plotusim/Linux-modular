# 项目描述

linux内核自动模块化工具。

# 环境配置

1. 下载llvm-15.0.0

   ```shell
   cd Frontend
   bash build-llvm.sh
   ```

  在Frontend目录下出现llvm-project目录，则说明llvm下载成功。

2. python require packages

   ```
   kconfiglib==14.1.0
   openpyxl==3.1.2
   pydot==1.4.2
   pyelftools==0.29
   ```

  自行下载相关python包。

# 使用说明

整个项目分为前端，中端，后端三个部分，其相关代码分别放入Frontend，Middleend和Backend目录下。Data目录用于存放Frontend分析出的数据，而Kernel目录存放内核源代码（需自行放入）。

当前项目的内核配置文件默认使用defconfig，若要使用特定配置的config文件，需进行以下步骤进行配置，以temp_config为例：

1. 将temp_config放入相关的架构目录arch/xxx/configs下。若使用x86架构，则放入arch/x86/configs目录下;

2. 修改Frontend的run_frontend.sh的CONFIG变量，即`CONFIG="temp_config"`;

3. 在Backend提取模块时，编译内核需使用`make temp_config`。（即前后端使用的config保持一致）


## Frontend

前端函数分析的整个流程已集成到run_frontend.sh中，故理想状态下直接执行以下命令：

```shell
cd Frontend
bash run_frontend.sh
```

因为前端函数分析在combine阶段及其之后花费时间较长，故建议将run_frontend.sh拆成多步执行。建议拆分步骤如下（sh中有相关备注）：

1. 编译so --> 生成指针分析的dots
2. combine pa dots
3. 生成merge dots，combine merge dots
4. 函数分类

步骤1结束后，在Data/dots和Data/dots_pa目录下会生成dot文件。

步骤2结束后，在Data/dots_pa生成all.dot，在当前测试下，all.dot的大小在30mb左右。（因为combine脚本非多线程实现，故建议在单核强的机器上跑combine阶段，在13th i9-13900k下combine阶段花费10-20分钟）

步骤3结束后，在Data/dots_merge下生成dot文件和all.dot，all.dot的大小在20mb-30mb的范围内，同样建议在单核强的机器上跑。

步骤4结束后，在func_list下生成函数的分类结果：

* export_symbols.txt
* inline_funcs_list.txt  
* trace_funcs.txt
* init_funcs.txt        
* modular_funcs.txt      
* virtual_structs_top_funcs.txt
* init_reach_funcs.txt  
* syscall_funcs.txt      
* virtual_structs.txt

可通过以上信息判断各阶段执行情况。

## Middleend

ModuleAdvizer根据前端生成的各种类型的函数列表给子系统内的函数进行分类，然后基于调用关系给出模块化的建议传给后端进行提取

使用说明

```shell
cd Middleend/ModuleAdvizer
python run.py -a -o  ../result
```

输出结果会放到Middleend/result下

## Backend

AutoBackend根据中端生成的提取建议自动提取内核模块，目前存在需要手动调试的情况

使用说明

### 提取内核模块：

#### 编译提取工具

- ```shell
  cd Backend/AutoBackend/cpp
  make
  ```

#### 修改config.py:

- res_graph_dot_path为要提取的模块的res.dot的路径
- module_name为模块名
- kernel_source_root_path为要修改的Linux源代码的路径
- kernel_bc_file_root_path为编译生成过bc文件的源代码路径
- module_template_files_dir为模版文件路径，即为Backend/sys_module文件夹

#### 运行main.py:

- ```shell
  cd Backend/AutoBackend
  python main.py
  ```

  

### linux内核编译：

#### 编译内核：

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

#### 手动调试：

- error:implicit declaration of function

  将相关函数定义添加到unexport_symbol.h的宏EXPORT_FUNC(func,ret,...)中，并将内核模块代码中相应的函数名func改为_func

- error:'var_name’ undeclared (first use in this function)

  将未导出符号在模块中的名称修改为_var_name，这是由于为避免与内核代码冲突，将未导出符号进行了重命名添加了' _ '前缀。在.c中进行全局替换即可。

- error:passing argument  of ‘func’ from incompatible pointer type

  note: expected ‘ret *’ but argument is of type ‘ret **’

  - 一般情况下是未导出符号var通过宏导出后，模块内的函数将符号作为参数使用时添加了&，例如func(a,b,&var)，将&删除即可;

  - 第二种情况为func(a,b,var)，此时需要修改为func(a,b,*var)

#### 编译Busybox:

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


#### 修改make.sh：

- busybox/_install/修改为当前系统下的绝对路径
- gen_fs.sh修改为当前系统下的绝对路径
- fs.sh修改为当前系统下的绝对路径

#### 执行make.sh

- ```
  cd linux-5.10.176
  bash ~/Backend/make.sh
  ```
