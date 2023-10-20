from config import config
from utils.file_utils import insert_content_to_file


# 给/kernel/kallsyms.c里的kallsyms_lookup_name函数添加宏
def add_export_kallsyms_look_up_macro():
    print("Add EXPORT_SYMBOL(kallsyms_look_up_name) to /kernel/kallsyms.c")
    file_path = config.kernel_source_root_path + "/kernel/kallsyms.c"
    insert_content_to_file(file_path, 179, "EXPORT_SYMBOL(kallsyms_lookup_name);")
