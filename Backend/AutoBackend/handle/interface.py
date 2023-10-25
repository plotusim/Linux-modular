import os
import re

from handle.add_interface_macro import add_interface_macro_to_interface_header
from utils.file_utils import insert_content_to_file, insert_after_last_keyword_list, extract_lines, replace_line_in_file
from utils.func_utils import extract_source_location, extract_function_info
from config import config
from handle.delete import del_funcs


# 生成替换的函数体
def generate_find_module_code(func_name, return_type, params, module_name: str):
    return_type = return_type.strip()
    # void返回类型的函数不能返回-1
    if "void" in return_type and "*" not in return_type:
        code = "noinline " + return_type + " " + func_name + "(" + params + ") { " + \
               "\tif(!find_module(\"" + module_name + "\")){ \t\trequest_module(\"" + \
               module_name + "\"); \t} \treturn; }"
    else:
        code = " noinline " + return_type + " " + func_name + "(" + params + ") { " + \
               "\tif(!find_module(\"" + module_name + "\")){ \t\trequest_module(\"" + \
               module_name + "\"); \t} \treturn -1; }"
    return code


# 生成提取出来的模块内对应的mod函数的函数定义
def generate_mod_func(name, body, return_type, params):
    new_func_name = "mod_" + name
    code = return_type + " " + new_func_name + "(" + params + ") " + body + " "
    return code


# 修改使用identifier的位置成mod_identifier
def modify_call_func_name(filename, identifier):
    # 定义正则表达式，查找不以"mod_"开头的target_str
    print(f"Modify func_identifier {filename}\t{identifier}")
    with open(filename, 'r') as file:
        content = file.read()

    # 使用正则替换
    new_content = re.sub(r'\b' + re.escape(identifier) + r'\b', 'mod_' + identifier, content)

    with open(filename, 'w') as file:
        file.write(new_content)


def handle_interface_func(func_name, file_attribute, module_name, module_dir_path):
    try:
        real_file_path, start_loc, end_loc, bc_start_loc = extract_source_location(file_attribute, func_name)
        source_file = config.kernel_source_root_path + real_file_path
        # 获得最初的函数定义的代码
        origin_lines = extract_lines(source_file, start_loc, end_loc)
        file_name = file_attribute.split('/')[-1]
        # 解析函数的返回类型, 参数列表和函数体
        return_type, params, body = extract_function_info("".join(origin_lines))
    except Exception as e:
        print(e)
        print("INTERFACE FUNC:\t" + func_name + "Can't be done")
        return

    print("INTERFACE FUNC:\t" + func_name)
    # 添加INTERFACE函数的宏
    add_interface_macro_to_interface_header(module_dir_path, func_name, return_type, [params])
    # 生成mod函数的代码
    mod_func_code = generate_mod_func(func_name, body, return_type, params)
    # 插入mod函数到模块源文件
    file_path = os.path.join(module_dir_path, file_name + "_code.c")
    insert_after_last_keyword_list(file_path, ["#include", "#define"], mod_func_code)
    # 生成接口函数体
    code = generate_find_module_code(func_name, return_type, params, module_name)
    # 删除原函数的代码
    del_funcs(file_attribute, func_name)
    # 插入接口函数到原位置
    replace_line_in_file(source_file, bc_start_loc, code)
    # 修改先前插入进去的函数对该函数的引用
    modify_call_func_name(file_path, func_name)


if __name__ == '__main__':
    handle_interface_func("gss_svc_init_net", "/net/sunrpc/auth_gss/svcauth_gss", "auth_gss_module",
                          "/home/plot/hn_working_dir/Kernel_src/drivers/auth_gss_module")
