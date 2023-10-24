import os
from utils.file_utils import insert_after_last_include
from utils.func_utils import extract_funcs, is_inline_func
from handle.delete import del_funcs


def handle_normal_funcs(func_name, file_attribute, module_name, module_dir_path):
    print("NORMAL FUNC:\t" + func_name)
    lines = extract_funcs(file_attribute, func_name)
    file_name = file_attribute.split('/')[-1]
    module_src_file = os.path.join(module_dir_path, file_name + "_code.c")
    code = "".join(lines)
    insert_after_last_include(module_src_file, code)
    if not is_inline_func(func_name):
        del_funcs(file_attribute, func_name)


if __name__ == '__main__':
    handle_normal_funcs("init_gssp_clnt", "/net/sunrpc/auth_gss/gss_rpc_upcall", "auth_gss_module",
                        "/home/plot/hn_working_dir/Kernel_src/drivers/auth_gss_module")
