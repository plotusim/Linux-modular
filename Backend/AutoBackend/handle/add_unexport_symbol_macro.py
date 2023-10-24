import os.path
import re
from utils.file_utils import append_string_to_file
from utils.func_utils import extract_function_info, extract_funcs, extract_source_location, is_inline_func, \
    get_children_funcs
from config import config


def copy_static_inline_func(func_name, module_dir):
    file_attr = config.func_file_attribute_pairs[func_name]
    file_info, _, _, _ = extract_source_location(file_attribute=file_attr, function_name=func_name)
    if file_info.endswith(".h"):
        return
    lines = extract_funcs(file_attribute=file_attr, func_name=func_name)
    print("".join(lines))
    file_name = file_attr.split('/')[-1]
    code_c_path = os.path.join(module_dir, file_name + "_code.c")
    print(f"Add func {func_name} to {code_c_path}.h")
    append_string_to_file(code_c_path, "\n")

    for i in lines:
        append_string_to_file(code_c_path, i)
    pass


# 向unexport_symbol.h里面添加UNEXPORT_VAR宏
def add_macro_to_unexport_func_header(name, type_str, para_str, module_dir):
    macro = f" UNEXPORT_FUNC({name},{type_str},{para_str})\n"
    macro = re.sub(r'\n\s*', ' ', macro)
    print("Add macro:\t" + macro + "\t to unexport_symbol.h")
    append_string_to_file(os.path.join(module_dir, "unexport_symbol.h"), "\n" + macro + "\n")


def add_unexport_func_macro(module_dir, unexport_funcs):
    work_set = set()
    temp_set = unexport_funcs.copy()

    while not (work_set.issubset(temp_set) and work_set.issuperset(temp_set)):
        work_set = temp_set.copy()
        temp_set.clear()
        for i in work_set:
            if is_inline_func(i):
                print(f"Found inline func {i}")
                copy_static_inline_func(i, module_dir)
                for next_func in get_children_funcs(i):
                    if next_func not in config.export_symbols_set:
                        temp_set.add(next_func)
            else:
                temp_set.add(i)

    unexport_funcs = work_set.copy()

    for i in unexport_funcs:
        file_attr = config.func_file_attribute_pairs[i]
        print(f"Add unexport_func_macro for {i} at Loc {file_attr}")
        return_type, param_string, _ = extract_function_info(
            "".join(extract_funcs(file_attribute=file_attr, func_name=i)))
        add_macro_to_unexport_func_header(i, return_type, param_string, module_dir)
    return unexport_funcs


def add_macro_to_unexport_var_header(name, type_str, module_dir):
    macro = f"\nUNEXPORT_VAR({name},{type_str})\n"
    print("Add macro:\t" + macro + "\t to unexport_symbol.h")
    append_string_to_file(os.path.join(module_dir, "unexport_symbol.h"), macro)


def modify_unexport_symbol_in_mod_func(origin_name, mod_dir_path):
    mod_name = "_" + origin_name
    # 定义正则表达式，查找不以"mod_"开头的target_str
    for root, dirs, files in os.walk(mod_dir_path):
        for file in files:
            if file.endswith("_code.c"):
                print(f"Modify var_identifier {origin_name}\tin\t{file}")
                # 获取文件的完整路径
                full_path = os.path.join(root, file)
                with open(full_path, 'r') as f:
                    content = f.read()

                new_content = re.sub(r'\b' + re.escape(origin_name) + r'\b', mod_name, content)

                with open(full_path, 'w') as f:
                    f.write(new_content)
