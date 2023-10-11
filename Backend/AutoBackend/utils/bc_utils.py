import subprocess
import re
import os

from config import kernel_source_root_path, kernel_bc_file_root_path


def parse_bc_file(bc_file):
    # 使用llvm-dis将.bc文件转换为.ll字符串
    process = subprocess.Popen(["llvm-dis", bc_file, "-o", "-"], stdout=subprocess.PIPE)
    ll_content, _ = process.communicate()

    # 将内容从字节转换为字符串
    ll_content = ll_content.decode('utf-8')

    return ll_content


# 提取lines里的debug信息，存在字典里
def parse_dbinfo(lines):
    dbinfo = {}
    for line in lines:
        db_symbol = re.search(r"!(\d+)", line)
        if db_symbol:
            db_num = db_symbol.group(1)
            dbinfo[db_num] = line
    return dbinfo


# 分析debug信息里包含的文件位置的信息
def find_file_debug_info(lines, db_info, file_di_num):
    line = db_info[file_di_num]
    match = re.search(r'filename: "(.*?)", directory: "(.*?)"', line)
    if match:
        filename = match.group(1)
        directory = match.group(2)
        full_path = os.path.join(directory, filename)
        return full_path
    else:
        print("No match found!")
        return None


# 分析debug信息里包含的函数定义的位置以及函数定义在该文件里开始的行号，注意这是标识符所在的行号，可能会出现返回类型在上一行的情况
def get_func_defined_file_and_start(lines, db_info, function_name):
    for line in lines:
        if re.search(f"define.*?@{function_name}\(", line) and "!dbg" in line:

            dbg_ref = re.search(r"!dbg !(\d+)", line)
            if dbg_ref:
                dbg_num = dbg_ref.group(1)
                line = db_info[dbg_num]
                # 解析位置信息
                file_info_line = re.search(r"file: !(\d+),", line)
                location_info = re.search(r"line: (\d+),", line)

                if file_info_line and location_info:
                    file_info_num = file_info_line.group(1)
                    file_info = find_file_debug_info(lines, db_info, file_info_num)[len(kernel_bc_file_root_path):]
                    start_loc = location_info.group(1)
                    return file_info, int(start_loc)

    return None, None
