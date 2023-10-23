# 使用说明

目前完成：

- 创建模块文件夹，复制模版文件
- 自动提取include宏添加到模块的源文件
- 删除DELETE函数，提取NORMAL
- 自动创建接口函数以及添加对应接口函数宏到interface.h
- 修改Module目录下的Makefile文件和修改Kconfig文件
- 修改drivers目录下的的Makefile文件和Kconfig文件

### 修改config.ini:

```ini
[MODULE]
modulename = drivers_ptp
resgraphdotpath = ../../Middleend/result/drivers/ptp/res.dot
kernelsourcerootpath = ../../Backend/test_hn
```

- resgraphdotpath 为要提取的模块的res.dot的路径
  比如/net/netfilter就是../../Middleend/result/net/netfilter/res.dot
- modulename 模块的名字，比如/net/filter目录下的模块名可以取为 net_netfilter_module
- kernelsourcerootpath 就是你要修改的源代码的路径

### 运行main.py:

- 1.进入目录AutoBackend
- 2.运行main.py

# 项目结构

```
.
├── config.py 配置类
├── config.ini 配置文件
├── CopyKernelSrc.sh 方便删除复制新的内核源代码的副本
├── cpp 包含提取函数使用到的全局符号的C++代码
├── handle 包含具体处理每一个需求的handle
├── main.py
├── readme.md
├── tests 对一些函数的测试
└── utils 在handle中使用到的一些工具函数，比如关于文件、函数的操作
```

