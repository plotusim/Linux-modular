import os.path
from utils.file_utils import append_string_to_file


# 向unexport_symbol.h里面添加UNEXPORT_VAR宏
def add_macro_to_unexport_var_header(name, type_str, module_dir):
    if "." not in name:
        macro = f"UNEXPORT_VAR({name},{type_str})\n"
        print("Add macro:\t" + macro + "\t to unexport_symbol.h")
        append_string_to_file(os.path.join(module_dir, "unexport_symbol.h"), macro)


def add_unexport_var_macro(module_dir, unexport_vars):
    for i in unexport_vars:
        add_macro_to_unexport_var_header(i[0], i[1], module_dir)
