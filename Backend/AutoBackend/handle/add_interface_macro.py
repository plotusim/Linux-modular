import os
import re
from utils.file_utils import append_string_to_file


# 自动添加interface宏到interface.h
def add_interface_macro_to_interface_header(module_dir, func_name, return_type, parameters):
    macro = "EXPORT_FUNC(" + func_name + "," + return_type + ","
    for i in parameters:
        macro += i
        macro += ","
    macro = macro[:-1]
    macro += ")"

    macro = re.sub(r'\n\s*', ' ', macro)
    macro = "\n" + macro + "\n"

    interface_header_file_path = os.path.join(module_dir, "interface.h")
    append_string_to_file(interface_header_file_path, macro)


if __name__ == '__main__':
    add_interface_macro_to_interface_header("/home/plot/hn_working_dir/AutoBackend/Kernel/drivers/auth_gss_module",
                                            "gss_svc_init_net", "int", ["struct net *net"])
