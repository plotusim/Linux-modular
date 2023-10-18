from collections import defaultdict

from utils.graph_utils import get_res
from utils.func_utils import extract_func_used_func, extract_func_used_gv
from config import res_graph_dot_path, module_name
from handle.init_module_dir import init_module_dir
from handle.add_includes import add_includes, add_header_file_include_linux_module, \
    add_includes_to_jump_interface, add_unexport_symbol_header
from handle.interface import handle_interface_func
from handle.delete import handle_delete_funcs
from handle.normal import handle_normal_funcs
from handle.modify_makefile import modify_makefile
from handle.modify_Kconfig import modify_kconfig
from handle.add_export_kallsyms_look_up_macro import add_export_kallsyms_look_up_macro
from handle.add_unexport_symbol_macro import add_unexport_func_macro, add_macro_to_unexport_var_header, \
    modify_unexport_var_symbol_in_mod_func


# 自动化模块化的主函数
def modular(module_name, dot_path=res_graph_dot_path):
    # 初始化模块源代码目录，包括了创建目录，复制模版文件
    module_dir_path = init_module_dir(module_name)
    # 修改模块目录下的kconfig
    modify_kconfig(module_dir_path, module_name)
    # 读取中端给出的建议，res是一个字典，key文件，value是该文件里面需要处理的函数
    res = get_res(dot_path)

    files_name = []

    # 需要按照函数开始行从大到小排序，因为修改某函数之前的函数可能会影响到该函数的开始位置，所以我们需要从后往前修改
    print("Sorting")
    for i in res.values():
        i.sort(reverse=True, key=lambda x: x.start_loc)

    # 提取出来的函数放在*_code.c，该文件需要添加include，这个集合用来存放这些文件的名字
    need_add_includes_file_set = set()

    # 在头文件里面修改的interface函数，需要在原头文件里面添加include语句，这个集合用来存放这些头文件的名字
    need_add_include_header_file_set = set()

    # 寻找到所有需要添加include的*_code.c文件
    for i in res.values():
        for j in i:
            file_attribute = j.file_attribute
            if j.handle_way in {"NORMAL", "INTERFACE"}:
                need_add_includes_file_set.add(file_attribute)

    # 所有要添加的include
    angle_includes_list = []
    quote_includes_pairs_dict = defaultdict(list)
    # 给need_add_includes_file_set里的文件添加includes语句
    for code_file in need_add_includes_file_set:
        angle_includes, quote_includes_pairs = add_includes(code_file, module_dir_path)
        for i in angle_includes:
            if i not in angle_includes_list:
                angle_includes_list.append(angle_includes)
        for key, value in quote_includes_pairs.items():
            for func in value:
                if func not in quote_includes_pairs_dict[key]:
                    quote_includes_pairs_dict[key].append(func)
    # 给jmp_interface.h添加include
    add_includes_to_jump_interface(angle_includes_list, quote_includes_pairs_dict, module_dir_path)
    # 给unexport_symbol.h添加include
    add_unexport_symbol_header(angle_includes_list, quote_includes_pairs_dict, module_dir_path)

    # 首先找到未导出函数，然后提取出函数签名然后加入unexport_symbol.h
    # 用来存储该模块中所有函数遇到的未导出函数
    unexport_funcs = set()
    unexport_var = set()
    export_funcs = set()
    for real_file, i in res.items():
        # 遍历该文件中的需要处理的函数列表
        for j in i:
            file_attribute = j.file_attribute
            handle_way = j.handle_way
            func_name = j.func_name
            if handle_way == "NORMAL" or handle_way == "INTERFACE":
                export_funcs.add(func_name)
                unexport_var = unexport_var.union(
                    extract_func_used_gv(file_attribute=file_attribute, func_name=func_name))
                unexport_funcs = unexport_funcs.union(extract_func_used_func(file_attribute, func_name))
    # 给所有未导出函数在unexport_symbol.h文件里添加宏
    print("unexport_funcs:\n")
    unexport_funcs = unexport_funcs.difference(export_funcs)
    print(unexport_funcs)
    unexport_funcs = add_unexport_func_macro(module_dir=module_dir_path, unexport_funcs=unexport_funcs)
    print("unexport_funcs:\n")
    print(unexport_funcs)
    not_handled_vars = set()
    handled_unexport_vars = set()
    for i in unexport_var:
        if "." not in i[0] and "." not in i[1] and len(i[1]):
            add_macro_to_unexport_var_header(i[0], i[1], module_dir_path)
            handled_unexport_vars.add(i[0])
        else:
            not_handled_vars.add(i[0])

    # 依次处理res中的函数
    for real_file, i in res.items():
        # 目前处理头文件还有bug，于是暂时先跳过
        if real_file.endswith(".h"):
            continue

        # 遍历该文件中的需要处理的函数列表
        for j in i:
            file_attribute = j.file_attribute
            handle_way = j.handle_way
            func_name = j.func_name
            real_file = j.real_file
            # files_name.append(file_attribute.split('/')[-1] + "_code") 原代码
            # 修改后的files_name处理
            new_file_name = file_attribute.split('/')[-1] + "_code"
            if new_file_name not in files_name:
                if handle_way == "NORMAL" or handle_way == "INTERFACE":
                    files_name.append(new_file_name)
            else:
                # 如果字符串已存在于列表中，则忽略它。
                pass

            if handle_way == "DELETE":
                handle_delete_funcs(func_name, file_attribute, module_name, module_dir_path)

            elif handle_way == "NORMAL":
                handle_normal_funcs(func_name, file_attribute, module_name, module_dir_path)
                unexport_funcs.union(extract_func_used_func(file_attribute, func_name))

            elif handle_way == "INTERFACE":
                if real_file.endswith(".h"):
                    need_add_include_header_file_set.add(real_file)
                handle_interface_func(func_name, file_attribute, module_name, module_dir_path)
                unexport_funcs.union(extract_func_used_func(file_attribute, func_name))
                pass

    # 处理需要添加宏的原头文件
    for i in need_add_include_header_file_set:
        add_header_file_include_linux_module(i)

    # 给kallsyms_lookup函数添加EXPORT宏
    add_export_kallsyms_look_up_macro()

    # 修改模块函数对未导出符号的引用
    for i in handled_unexport_vars:
        modify_unexport_var_symbol_in_mod_func(i, module_dir_path)

    # 修改drivers下的和模块目录下的makefile
    modify_makefile(module_name, files_name, module_dir_path)

    print("Found global vars but not handled:")
    print(not_handled_vars)


if __name__ == '__main__':
    modular(module_name, res_graph_dot_path)
