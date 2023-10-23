import re
import os
import subprocess

import pydot

from config.data_path import kernel_source_root_path, inline_funcs_list_file, kernel_bc_file_root_path, \
    llvm_bin_path_prefix, linux_whole_kernel_dot
from utils.read_file import read_dot


def parse_bc_file(bc_file):
    # 使用llvm-dis将.bc文件转换为.ll字符串
    llvm_dis_path = os.path.join(llvm_bin_path_prefix, "llvm-dis")
    process = subprocess.Popen([llvm_dis_path, bc_file, "-o", "-"], stdout=subprocess.PIPE)
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
                    file_info = find_file_debug_info(lines, db_info, file_info_num).rsplit("Kernel_src", 1)[1]
                    start_loc = location_info.group(1)
                    return file_info, int(start_loc)

    return None, None


# 返回文件start到end行的内容
def extract_lines(file_path, start, end):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    return lines[start - 1:end]


# 返回函数的定义
def extract_funcs(file_attribute, func_name):
    file, start_loc, end_loc = extract_source_location(file_attribute, func_name)
    src_file_path = kernel_source_root_path + file
    lines = extract_lines(src_file_path, start_loc, end_loc)
    return lines


# 获得函数在源代码中定义的具体位置
def extract_source_location(file_attribute, function_name):
    bc_file_path = kernel_bc_file_root_path + file_attribute + ".bc"
    # 使用llvm-dis将.bc文件转换为.ll文件
    ll_content = parse_bc_file(bc_file_path)
    # 解析.ll文件以查找函数的源位置
    lines = ll_content.split('\n')
    db_info = parse_dbinfo(lines)
    # 使用get_func_defined_file_and_start获得文件和开始行号
    file_info, start_loc = get_func_defined_file_and_start(lines, db_info, function_name)

    if file_info is None:
        raise RuntimeError("Not Found File Info: " + file_attribute + " " + function_name)

    src_file_path = kernel_source_root_path + file_info

    with open(src_file_path, 'r') as f:
        lines = f.readlines()

    # 统计 {的出现次数 - }出现次数，当该次数为0的时候说明函数定义结束
    # 初始化{计数器
    counter = 0
    flag = False
    end_loc = start_loc
    for idx, line in enumerate(lines[start_loc - 1:], start=start_loc):

        if "{" in line:
            flag = True

        if flag:
            counter += line.count("{")
            counter -= line.count("}")

        if flag and counter <= 0:
            end_loc = idx
            break

    # 找到第一个 "(" 前的所有内容
    substring_before_bracket = lines[start_loc - 1].split("(", 1)[0]

    # 使用正则表达式匹配"(" 前最后一个单词，作为函数的标识符
    last_word_match = re.search(r'(\S+)(?:\s*)$', substring_before_bracket)
    if not last_word_match:
        print(str(start_loc) + ":\t" + lines[start_loc - 1])
        print("File:\t" + src_file_path + "\tFunc name:\t" + function_name)
        raise ValueError("Can't not find a function signature")

    last_word_start_idx = last_word_match.start()

    # 检查该单词之前是否还有一个单词，即返回类型
    content_before_last_word = substring_before_bracket[:last_word_start_idx]
    prev_word_match = re.search(r'\S', content_before_last_word)

    # 如果函数标识符前没有返回类型，则返回类型在上一行
    if bool(prev_word_match):
        return file_info, start_loc, end_loc
    else:
        return file_info, start_loc - 1, end_loc


def contains_inline(s):
    # 使用正则表达式搜索'inline'单词，\b表示单词边界
    pattern = r'\binline\b'

    # 搜索整个字符串，看是否有匹配
    if re.search(pattern, s):
        return True
    else:
        return False


def is_inline_func(func_name, file_attr):
    lines = extract_funcs(file_attribute=file_attr, func_name=func_name)
    return contains_inline(" ".join(lines))


def find_all_inline_func():
    print("Reading file...")
    funcs = {}
    pattern2 = r'(.*?)\s*\[file=\"(.*?)\"(.*?)\]'
    with open(linux_whole_kernel_dot, 'r') as file:
        for line in file:
            match = re.search(pattern2, line)
            if match:
                part1 = match.group(1)
                part2 = match.group(2)
                if "__virtual_init" in part1:
                    continue
                else:
                    funcs[part1] = part2

    inline_funcs = []

    for func, file_attr in funcs.items():
        if is_inline_func(func, file_attr):
            print("Function " + func + " is tagged with inline")
            inline_funcs.append(func)

    print("Completed finding inline functions, writing to file")
    with open(inline_funcs_list_file, 'w') as file:
        # Iterate through the list and write each item to the file, followed by a newline character
        for item in inline_funcs:
            file.write(f"{item}\n")


if __name__ == '__main__':
    find_all_inline_func()
