import sys
import os
import shutil
import fileinput
import re

module_name = 'sys_module'         #模块目录名称
driver_dir = 'drivers/user'
src_dir = now_dir = os.path.join(os.getcwd(),'src')    # 源目录路径
dst_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../linux-5.10.156-1/' + driver_dir   # 目标目录路径

makefile_str = 'obj-y   += ' + module_name + '/' #插入的makefile语句
makefile_path = dst_dir + '/Makefile'    # 待操作的文件路径
# 获取当前工作路径并打印
def delete_str(file_path,str):
    with fileinput.input(files=(file_path), inplace=True) as f:
        for line in f:
            # 删除包含目标字符串的整行
            if not str in line:
                print(line, end='')
            else:
                print("zhaodaole")

if __name__ =="__main__":
    delete_str(makefile_path, makefile_str)
