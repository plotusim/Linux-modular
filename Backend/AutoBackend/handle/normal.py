import os
from utils.file_utils import insert_after_last_keyword_list
from utils.func_utils import extract_funcs
from handle.delete import del_funcs


def handle_normal_funcs(func_name, file_attribute, module_name, module_dir_path):
    try:
        lines = extract_funcs(file_attribute, func_name)
    except Exception as e:
        print(e)
        print("NORMAL FUNC:\t" + func_name + "Can't be done")
        return False

    print("NORMAL FUNC:\t" + func_name)
    file_name = file_attribute.split('/')[-1]
    module_src_file = os.path.join(module_dir_path, file_name + "_code.c")
    code = "".join(lines)
    insert_after_last_keyword_list(module_src_file, ["#include", "#define"], code)
    del_funcs(file_attribute, func_name)
    return True


if __name__ == '__main__':
    handle_normal_funcs("init_gssp_clnt", "/net/sunrpc/auth_gss/gss_rpc_upcall", "auth_gss_module",
                        "/home/plot/hn_working_dir/Kernel_src/drivers/auth_gss_module")
