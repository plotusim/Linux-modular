import sys
import os
import shutil
import fileinput
import re

module_name = 'sys_module'         #模块目录名称
driver_dir = 'drivers/user'
src_dir = now_dir = os.path.join(os.getcwd(),'src')    # 源目录路径
dst_dir = os.path.dirname(os.path.abspath(__file__)) + '/../linux-5.10.156-1/' + driver_dir   # 目标目录路径
module_dir = os.path.join(dst_dir, module_name)

def add_src(): #将src的代码添加到kernel/driver/user中
    if(not os.path.exists(os.path.join(dst_dir, module_name))):
        # 在目标目录下创建新目录
        os.mkdir(os.path.join(dst_dir, module_name))
    else:
        print("重复创建模块目录")
        return -1

    for root, dirs, files in os.walk(src_dir):
        for file in files:
            src_file = os.path.join(root, file)
            module_file = os.path.join(module_dir, os.path.relpath(src_file, src_dir))
            shutil.copy2(src_file, module_file)
    print("代码已添加到内核代码树中！")

def remove_src(): #将添加的代码删除 
    if(os.path.exists(module_dir)):
        shutil.rmtree(module_dir)
        print("模块代码目录及文件已删除！")
    else:
        print("要删除的文件不存在！")
        return -1

def find_str(path,str):
    found = False
    for line in fileinput.input(path, inplace=True):
        if str in line:
            found = True
        print(line, end='')
    return found
    
makefile_str = 'obj-y   += ' + module_name + '/' #插入的makefile语句
makefile_path = dst_dir + '/Makefile'    # 待操作的文件路径

kconfig_str = 'source ' + '"' + driver_dir + '/' + module_name + '/' + 'Kconfig' + '"'
kconfig_path = dst_dir + '/Kconfig'    # 待操作的文件路径

def modify_build(): #修改kernel/driver/user中的Makefile和Kconfig
    #修改Makefile
    # 判断目标字符串是否存在于文件中,如果目标字符串不存在，则在文件最后一行添加该字符串
    if(find_str(makefile_path, makefile_str)):
        with open(makefile_path,'a') as f:
            f.write(makefile_str + '\n')
    
    #修改Kconfig
    target_str = 'source'  # 目标字符串

    # 查找最后一个source语句所在的行，并在其后添加新的source语句
    with fileinput.input(files=(kconfig_path), inplace=True) as f:
        last_match_line = None  # 最后一个匹配行
        for line in f:
            # 查找包含目标字符串的行
            if re.search(target_str, line):
                last_match_line = f.filelineno()  # 更新最后一个匹配行

            print(line, end='')

        # 最后一个匹配行不为None，则在其后添加新字符串
        if last_match_line is not None:
            with open(file_path, 'r+') as f:
                lines = f.readlines()
                lines.insert(last_match_line, kconfig_str)
                f.seek(0)
            f.writelines(lines)     

def restore_bulid(): #恢复Makefile和Kconfig
    #恢复Makefile
    with fileinput.input(files=(file_path), inplace=True) as f:
        for line in f:
            # 删除包含目标字符串的整行
            if not re.search(target_str, line):
                print(line, end='')
    
    #恢复Kconfig

def modify_code(): #修改kernel/sys.c中的代码
    print()

if __name__ =="__main__":
    print()