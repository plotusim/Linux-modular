import sys
import os
import shutil

dir_name = 'sys_module'         #模块目录名称
src_dir = now_dir = os.path.join(os.getcwd(),'src')    # 源目录路径
dst_dir = os.path.dirname(os.path.abspath(__file__)) + '/../linux-5.10.156-1/drivers/user/'    # 目标目录路径
module_dir = os.path.join(dst_dir, dir_name)

def add_src(): #将src的代码添加到kernel/driver/user中
    if(not os.path.exists(os.path.join(dst_dir, dir_name))):
        # 在目标目录下创建新目录
        os.mkdir(os.path.join(dst_dir, dir_name))
    else:
        print("重复创建模块目录")
        return -1

    for root, dirs, files in os.walk(src_dir):
        for file in files:
            src_file = os.path.join(root, file)
            module_file = os.path.join(module_dir, os.path.relpath(src_file, src_dir))
            shutil.copy2(src_file, module_file)

def remove_src(): #将添加的代码删除 
    shutil.rmtree(module_dir)

def modify_module(): #修改kernel/driver/user中的makefile和Kconfig
    print()

def modify_code(): #修改kernel/sys.c中的代码
    print()

if __name__ =="__main__":
    add_src()