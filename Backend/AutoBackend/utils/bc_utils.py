import os

from config import config

import subprocess


def get_func_debug_info(executable_path, bc_file, function_name):
    try:
        # 调用C++程序，并捕获输出
        result = subprocess.run([executable_path, bc_file, function_name],
                                capture_output=True, text=True, check=True)

        output = result.stdout.strip()
        if output:
            # 解析标准输出来获取文件路径和行号
            parts = output.split(':')
            if len(parts) == 2:
                filepath, line = parts
                return filepath, int(line)

    except subprocess.CalledProcessError as e:
        print(f"An error occurred while getting function debug info: {str(e)}")

    return None, None


def get_func_debug_file_and_start_loc(bc_file, function_name):
    executable_path = os.path.join(config.current_project_dir, "cpp", "FunctionFileAndStartLine")
    filepath, line = get_func_debug_info(executable_path, bc_file, function_name)
    return filepath.rsplit("Kernel_src", 1)[1], line


def get_gv_debug_info(executable_path, bc_file):
    res = {}
    try:
        # 调用C++程序，并捕获输出
        result = subprocess.run([executable_path, bc_file],
                                capture_output=True, text=True, check=True)

        output = result.stdout.strip()
        if output:
            # 解析标准输出来获取文件路径
            parts = output.split(':')
            if len(parts) == 2:
                parts[0] = parts[1]

    except subprocess.CalledProcessError as e:
        print(f"An error occurred while getting GV debug info: {str(e)}")

    return res


def get_gv_defined_file(file_attr, gv_name):
    executable_path = os.path.join(config.current_project_dir, "cpp", "GlobalVariableLocation")
    return get_gv_debug_info(executable_path, bc_file=config.kernel_bc_file_root_path + file_attr + ".bc")[gv_name]


def extract_used_gv_from_ir(ir_file_path, function_name):
    extract_global_var = os.path.join(config.current_project_dir, "cpp", "ExtractGlobalVar")
    # 构建命令行参数
    cmd = [extract_global_var, ir_file_path, function_name]
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


# 调用C++程序提取函数使用到的未导出函数
def extract_func_used_from_ir(ir_file_path, function_name):
    extract_func_sym = os.path.join(config.current_project_dir, "cpp", "ExtractFuncSym")
    # 构建命令行参数
    cmd = [extract_func_sym, ir_file_path, function_name]
    # 使用subprocess来执行命令并捕获输出
    result = subprocess.run(cmd, capture_output=True, text=True)

    # 如果进程返回了非零退出代码，可能是因为有错误
    if result.returncode != 0:
        print(f"Error executing ExtractGlobalSymbol:\n{result.stderr}")
        return None
    # 返回标准输出的内容
    return result.stdout
