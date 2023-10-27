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

#### 修改config.ini:

- modulename = name_module //加上后缀避免同名导致config无法通过

- resgraphdotpath 为Middleend目录下的result中的result.dot路径

- kernelsourcerootpath 为需要提取内核模块的内核文件目录

- ```shell
  cd Backend/AutoBackend
  python main.py
  ```

  

### linux内核编译：

#### 编译内核：

- ```
  cd linux-5.10.176  //进入内核文件目录
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

- **error1:** *drivers/name_module/name**_code.c*** :invalid use of undefined type' *<u>typename</u>* '

  - **结构体类型未定义**

  - **solve:** 对于提取函数中的使用到的原文件中的结构体类型定义需要复制出来

- **error2:** *drivers/name_module/name**_code.c*** :implicit declaration of function ‘*func_name*’

  - **使用的原文件宏定义未导出**

  - **solve:**使用到的原文件中的宏定义需要复制到目前未定义的文件name**_code.c*** 中

- **error3:**./include/**path_to**/**name**.h:236:1: error: conflicting types for ‘**func_name**’; 

  - **有些头文件没有ifndef，会出现重复定义**

  - **solve:**在未导出符号处理过程中包含了错误的头文件导致产生了重复定义，需要在unexport_symbol.h或jmp_interface.h删除相关include语句

- **error4:** *drivers/name_module/name**_code.c*** :passing argument 1 of ‘func’ from incompatible pointer type

  ​				func(&var)

  - **未导出符号使用错误**

    - **solve:**由于导出符号方法的局限，此处需要删除&

- **error4:**

  drivers/hw_random_module/core_code.c: ‘_rng_list’ is a pointer; did you mean to use ‘->’?
  26 |                 new_rng = list_entry(_rng_list.next, struct hwrng, list);

  - **未导出符号使用错误**

  - **solve:**由于导出符号方法局限，此处需将rng_list.next改为rng_list->next

- **error4:**

  drivers/hw_random_module/core_code.c: error: assignment to ‘struct hwrng *’ from incompatible pointer type ‘struct hwrng **’ [-Werror=incompatible-pointer-types]

  50 |         old_rng = _current_rng;

  - **未导出符号使用错误**

  - **solve:**由于导出符号方法局限，此处需修改为*_current_rng

- **error5:**

  unexport_symbol.h中UNEXPORT_VAR或UNEXPOIRT_FUNC中提示struct name undefine

   e.g. UNEXPORT_VAR(a,A) 其中A的类型应该为struct A

  - **自动导出符号宏时遇到结构体定义，没有将struct前缀复制**

  - **solve:**将struct前缀加上即可

- **debug过程中查找bug相关符号的定义（尤其是未定义的结构体、宏）使用[Linux source code (v5.10.176) - Bootlin](https://elixir.bootlin.com/linux/v5.10.176/source)**

#### 编译Busybox:

- ```shell
  cd busybox //进入busybox目录
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
  bash ~/Backend/make.sh //此处为make.sh的路径
  ```




#### 进入qemu

- ```
  cd lib/modules/5.10.176/kernel/drivers/
  cd name_module/  //打开模块目录
  insmod name_module.ko
  ```


- 出现以下调试信息

  ```
  [   15.491581] success:text_mutex
  [   15.498366] success:var1_name
  [   15.514655] success:func1_name
  [   15.523455] error:func2_name
  [   15.523482] find symbol success
  ```

- 将error的未导出符号在https://elixir.bootlin.com/linux/v5.10.176/source中查找到定义所在的文件之后，在内核代码中找到相应文件的该符号定义位置，将static前缀删除。


- 重新执行


  ```
  make
  bash /home/plot/test_linux_modular/Linux-modular/Backend/make.sh
  ```


​		直到所有未导出符号success