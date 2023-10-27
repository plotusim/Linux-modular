
- cd Backend/AutoBackend/

- 修改Backend/AutoBackend/config.ini文件
  - modulename = name_module //加上后缀避免同名导致config无法通过

  
- python main.py

- cd ../../工作目录/Linux-modular

- make defconfig

- make menuconfig defconfig

- 将下面的值设为N

 	```
 	Processor type and features ----> 
 		[] Randomize the address of the kernel image (KASLR)
 	```

- make -j32
- 调试编译通过
- 



常见error：

**error1:** *drivers/name_module/name**_code.c*** :invalid use of undefined type' *<u>typename</u>* '

**#结构体类型未定义**

- **solve:** 对于提取函数中的使用到的原文件中的结构体类型定义需要复制出来

**error2:** *drivers/name_module/name**_code.c*** :implicit declaration of function ‘*func_name*’

**#使用的原文件宏定义未导出**

- **solve:**使用到的原文件中的宏定义需要复制到目前未定义的文件name**_code.c*** 中

**error3:**./include/**path_to**/**name**.h:236:1: error: conflicting types for ‘**func_name**’; 

**#有些头文件没有ifndef，会出现重复定义**

- **solve:**在未导出符号处理过程中包含了错误的头文件导致产生了重复定义，需要在unexport_symbol.h或jmp_interface.h删除相关include语句

**error4:** *drivers/name_module/name**_code.c*** :passing argument 1 of ‘func’ from incompatible pointer type
​				func(&var)

**#未导出符号使用错误**

  - **solve:**由于导出符号方法的局限，此处需要删除&

**error4:**

drivers/hw_random_module/core_code.c: ‘_rng_list’ is a pointer; did you mean to use ‘->’?
26 |                 new_rng = list_entry(_rng_list.next, struct hwrng, list);

**#未导出符号使用错误**

- **solve:**由于导出符号方法局限，此处需将rng_list.next改为rng_list->next

**error4:**

drivers/hw_random_module/core_code.c: error: assignment to ‘struct hwrng *’ from incompatible pointer type ‘struct hwrng **’ [-Werror=incompatible-pointer-types]

50 |         old_rng = _current_rng;

**#未导出符号使用错误**

- **solve:**由于导出符号方法局限，此处需修改为*_current_rng

**error5:**

unexport_symbol.h中UNEXPORT_VAR或UNEXPOIRT_FUNC中提示struct name undefine

 e.g. UNEXPORT_VAR(a,A) 其中A的类型应该为struct A

**#自动导出符号宏时遇到结构体定义，没有将struct前缀复制**

- **solve:**将struct前缀加上即可

**debug过程中查找bug相关符号的定义（尤其是未定义的结构体、宏）使用[Linux source code (v5.10.176) - Bootlin](https://elixir.bootlin.com/linux/v5.10.176/source)**





编译通过后运行/home/plot/test_linux_modular/Linux-modular/Backend/make.sh

进入qemu后

```
cd lib/modules/5.10.176/kernel/drivers/
cd auth_gss_module/  //打开模块目录
insmod auth_gss_module.ko
```

出现以下调试信息

![image-20231025203020070](C:\Users\felix\AppData\Roaming\Typora\typora-user-images\image-20231025203020070.png)

将error的未导出符号在https://elixir.bootlin.com/linux/v5.10.176/source中查找到定义所在的文件之后，在内核代码中找到相应文件的该符号定义位置，将static前缀删除。

再重新

```
make
bash /home/plot/test_linux_modular/Linux-modular/Backend/make.sh
```

直到所有未导出符号success
