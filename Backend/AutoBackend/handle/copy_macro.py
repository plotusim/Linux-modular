from config import config
import os
from utils.file_utils import append_string_to_file


def extract_macros(lines):
    macros = []
    define_str = "#define"
    inside_macro = False
    macro_content = ""

    for line in lines:
        stripped_line = line.strip()

        # 开始一个新的宏定义
        if stripped_line.startswith(define_str) and not inside_macro:
            inside_macro = True

            if stripped_line.endswith("\\"):
                macro_content += stripped_line[:-1].strip().replace("\\", " ") + "    "
            else:
                inside_macro = False
                macro_content += stripped_line + "    "
                macros.append(macro_content)
                macro_content = ""
            continue

        if inside_macro:
            # 检查是否为多行宏定义
            if stripped_line.endswith("\\"):
                # 去除末尾的 '\' 字符，并添加内容
                macro_content += stripped_line[:-1].strip().replace("\\", " ") + "    "
            else:
                # 最后一行内容
                macro_content += stripped_line + "    "
                inside_macro = False  # 当前宏定义结束
                macros.append(macro_content.strip())
                macro_content = ""

    return macros


def copy_macro_file(file_attr, module_dir):
    print(f"Copy macro defined in {file_attr}")
    kernel_source_file_path = config.kernel_source_root_path + "/" + file_attr + ".c"
    with open(kernel_source_file_path, 'r') as file:
        lines = file.readlines()
    for i in extract_macros(lines):
        print(f"Add {i} to " + file_attr)
        file_name = file_attr.split('/')[-1]
        dest_file = os.path.join(module_dir, file_name + "_code.c")
        append_string_to_file(content=i + "\n", filename=dest_file)
