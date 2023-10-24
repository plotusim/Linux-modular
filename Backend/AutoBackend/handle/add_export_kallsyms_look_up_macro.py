from config import config
from utils.file_utils import replace_line_in_file, check_string_in_file


# 给/kernel/kallsyms.c里的kallsyms_lookup_name函数添加宏
def add_export_kallsyms_look_up_macro():
    file_path = config.kernel_source_root_path + "/kernel/kallsyms.c"
    MACRO = "EXPORT_SYMBOL(kallsyms_lookup_name);"
    if check_string_in_file(file_path, MACRO):
        print(MACRO + " is already in /kernel/kallsyms.c")
    else:
        print("Add EXPORT_SYMBOL(kallsyms_look_up_name) to /kernel/kallsyms.c")
        replace_line_in_file(file_path, 179, MACRO)
