import os.path
import re
import subprocess

from config import config
from utils.file_utils import extract_lines
from utils.bc_utils import parse_bc_file, parse_dbinfo, get_func_defined_file_and_start


# 返回函数的定义
def extract_funcs(file_attribute, func_name):
    file, start_loc, end_loc = extract_source_location(file_attribute, func_name)
    src_file_path = config.kernel_source_root_path + file
    lines = extract_lines(src_file_path, start_loc, end_loc)
    return lines


# 获得函数在源代码中定义的具体位置
def extract_source_location(file_attribute, function_name):
    bc_file_path = config.kernel_bc_file_root_path + file_attribute + ".bc"
    # 使用llvm-dis将.bc文件转换为.ll文件
    ll_content = parse_bc_file(bc_file_path)
    # 解析.ll文件以查找函数的源位置
    lines = ll_content.split('\n')
    db_info = parse_dbinfo(lines)
    # 使用get_func_defined_file_and_start获得文件和开始行号
    file_info, start_loc = get_func_defined_file_and_start(lines, db_info, function_name)

    if file_info is None:
        raise RuntimeError("Not Found File Info: " + file_attribute + " " + function_name)

    src_file_path = config.kernel_source_root_path + file_info

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


def remove_static_inline(s, index):
    # 删除指定索引前的'static'和'inline'关键字
    return_type = re.sub(r'\b(static|inline)\b', '', s[:index])

    # 删除开始和结束的空白字符
    return_type = return_type.strip()

    return return_type


def find_word_before_index(s, index):
    # 查找以单词字符结束且在指定索引前的所有单词
    matches = list(re.finditer(r'\b\w+\b', s[:index]))

    # 如果没有找到匹配项，则返回None
    if not matches:
        return None

    # 取最后一个匹配项，它将是指定索引前的最后一个单词
    match = matches[-1]
    return match.start(), match.end()


def find_first_parenthesis_pair(s):
    # 找到第一个左括号的索引
    start_index = s.find('(')
    if start_index == -1:
        return None

    end_index = start_index + 1
    num = 1
    while num > 0 and end_index < len(s):
        if s[end_index] == '(':
            num += 1
        elif s[end_index] == ')':
            num -= 1
        end_index += 1

    if end_index == -1 or end_index >= len(s):
        return None

    return start_index, end_index - 1


def remove_comments(code):
    # 匹配 /* */ 形式的注释
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)

    # 匹配 // 形式的注释
    code = re.sub(r'//.*', '', code)

    return code


# # 老版本使用正则表达式的方式来提取函数体函数名字和函数返回值
# def extract_function_info(function_code):
#     # pattern = r'(\w+\s*\*?)\s+(\w+)\s*\(([^)]*)\)'
#     # pattern = r'(\w+)\s*(\*+)?\s+(\w+)\s*\(([^)]*)\)\s*({.*?})'
#     # pattern = r'(\w+(\s*\*+)?\s+)\s*(\w+)\s*\(([^)]*)\).*\{.*\}'
#     # pattern = r'(?:static\s+|inline\s+)?(?:static\s+|inline\s+)?\s*(\w+(\s*\*+\s*)?)\s+(\w+)\s*\(([^)]*)\)\s*'
#     # pattern = r'\s*(?:static\s+|inline\s+)?(?:static\s+|inline\s+)?\s*(\w+(\s*\*+\s*)?)\s+(\w+)\s*\(([^)]*)\)'
# pattern = r'\s*(?:static\s+|inline\s+)?(?:static\s+|inline\s+)?\s*(\w+\s+\w+|\w+(\s*\*+\s*)?)\s+(\w+)\s*\(([^)]*)\)'
#     function_code = remove_comments(function_code)
#
#     match = re.match(pattern, function_code)
#     if not match:
#         print("Not counted as a function:\n" + function_code)
#         raise ValueError("Invalid function code")
#
#     return_type = match.group(1).strip()
#     func_name = match.group(3)
#     param_string = match.group(4)
#
#     # Split parameters and extract type
#     params = [param.strip() for param in param_string.split(",") if param]
#
#     start_index = function_code.find('{')
#     end_index = function_code.rfind('}')
#
#     body = ""
#
#     if start_index != -1 and end_index != -1 and start_index < end_index:
#         body = function_code[start_index:end_index + 1].strip()
#
#     return return_type, params, body

# 提取函数的返回值，参数列表和函数体
def extract_function_info(function_code):
    function_code = remove_comments(function_code)  # 首先去掉注释

    # 找到函数体
    start_index = function_code.find('{')
    end_index = function_code.rfind('}')

    if start_index != -1 and end_index != -1 and start_index < end_index:
        body = function_code[start_index:end_index + 1].strip()
    else:
        print("Not counted as a function:\n" + function_code)
        raise ValueError("Invalid function code, no body")

    # 找到第一个出现的(及与之对应的)，中间作为参数列表
    parenthesis_start_index, parenthesis_end_index = find_first_parenthesis_pair(function_code)
    if parenthesis_end_index is None or parenthesis_start_index is None:
        print("Not counted as a function:\n" + function_code)
        raise ValueError("Invalid function code, no parameters")

    param_string = function_code[parenthesis_start_index + 1: parenthesis_end_index]

    # 找到参数列表的(之前的一个单词作为函数的名字
    func_name_start, func_name_end = find_word_before_index(function_code, parenthesis_start_index)

    if func_name_start is None or func_name_end is None:
        print("Not counted as a function:\n" + function_code)
        raise ValueError("Invalid function code, no func_name")

    # 把函数名字之前的内容去掉static和inline后作为函数的返回值
    return_type = remove_static_inline(function_code, func_name_start)

    return return_type, param_string, body


# 调用C++程序提取函数使用到的未导出函数
def extract_func_used_from_ir(ir_file_path, function_name):
    ExtractFuncSym = os.path.join(config.current_project_dir, "cpp", "ExtractFuncSym")
    # 构建命令行参数
    cmd = [ExtractFuncSym, ir_file_path, function_name]
    # 使用subprocess来执行命令并捕获输出
    result = subprocess.run(cmd, capture_output=True, text=True)

    # 如果进程返回了非零退出代码，可能是因为有错误
    if result.returncode != 0:
        print(f"Error executing ExtractGlobalSymbol:\n{result.stderr}")
        return None
    # 返回标准输出的内容
    return result.stdout


# 解析寻找未导出符号的输出
def parse_unexport_func_res(res: str):
    res = res.split("\n")
    unexport_func = set()
    for line in res:
        func_name = line.split(":")[0]
        if func_name not in config.export_symbols_set:
            if len(func_name.strip()):
                unexport_func.add(func_name)

    return unexport_func


def extract_func_used_func(file_attribute, func_name):
    bc_file = config.kernel_bc_file_root_path + file_attribute + ".bc"
    return parse_unexport_func_res(extract_func_used_from_ir(bc_file, func_name))


def extract_func_used_gv(file_attribute, func_name):
    bc_file = config.kernel_bc_file_root_path + file_attribute + ".bc"
    return extract_used_gv_from_ir(bc_file, func_name).difference(config.export_symbols_set)


def extract_used_gv_from_ir(ir_file_path, function_name):
    ExtractGlobalVar = os.path.join(config.current_project_dir, "cpp", "ExtractGlobalVar")
    # 构建命令行参数
    cmd = [ExtractGlobalVar, ir_file_path, function_name]
    # 使用subprocess来执行命令并捕获输出
    result = subprocess.run(cmd, capture_output=True, text=True)

    # 如果进程返回了非零退出代码，可能是因为有错误
    if result.returncode != 0:
        print(f"Error executing ExtractGlobalSymbol:\n{result.stderr}")
        return None
    # 标准输出的内容
    res = result.stdout
    unexport_var = set()
    for line in res.split("\n"):
        splits = line.split(":")
        name = splits[0]
        if len(splits) > 1:
            type_str = splits[1]
        else:
            type_str = ""
        if name not in config.export_symbols_set:
            unexport_var.add((name, type_str))
    return unexport_var


if __name__ == '__main__':
    print(extract_func_used_from_ir(
        "/home/plot/clang-linux-5.10.176/linux-5.10.176/net/netfilter/nf_conntrack_netlink.bc", "ctnetlink_parse_help"))


def contains_inline(s):
    # 使用正则表达式搜索'inline'单词，\b表示单词边界
    pattern = r'\binline\b'

    # 搜索整个字符串，看是否有匹配
    if re.search(pattern, s):
        return True
    else:
        return False


def is_inline_func(func_name):
    file_attr = config.func_file_attribute_pairs[func_name]
    lines = extract_funcs(file_attribute=file_attr, func_name=func_name)
    return contains_inline(" ".join(lines))


def get_children_funcs(func_name):
    # print(func_children[func_name])
    return config.func_children[func_name]
