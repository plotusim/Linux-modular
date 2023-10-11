from utils.graph_utils import get_res
from config import res_graph_dot_path, module_name
from handle.init_module_dir import init_module_dir
from handle.add_includes import add_includes, add_header_file_include_linux_module
from utils.func_utils import extract_func_used_global_variable
from handle.interface import handle_interface_func
from handle.delete import handle_delete_funcs
from handle.normal import handle_normal_funcs
from handle.modify_makefile import modify_makefile
from handle.modify_Kconfig import modify_kconfig
from handle.add_export_kallsyms_look_up_macro import add_export_kallsyms_look_up_macro
from handle.add_unexport_var_macro import add_unexport_var_macro


# 自动化模块化的主函数
def modular(module_name, dot_path=res_graph_dot_path):
    # 初始化模块源代码目录，包括了创建目录，复制模版文件
    module_dir_path = init_module_dir(module_name)
    # 修改模块目录下的kconfig
    modify_kconfig(module_dir_path, module_name)
    # 读取中端给出的建议，res是一个字典，key文件，value是该文件里面需要处理的函数
    res = get_res(dot_path)

    files_name = []

    # 用来存储该模块中所有函数遇到的未导出符号
    unexport_vars = set()

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

    # 给need_add_includes_file_set里的文件添加includes语句
    for code_file in need_add_includes_file_set:
        add_includes(code_file, module_dir_path)

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
            #修改后的files_name处理
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
                unexport_vars = extract_func_used_global_variable(file_attribute=file_attribute, func_name=func_name,
                                                                  unexport_vars=unexport_vars)
            elif handle_way == "INTERFACE":
                if real_file.endswith(".h"):
                    need_add_include_header_file_set.add(real_file)
                handle_interface_func(func_name, file_attribute, module_name, module_dir_path)
                unexport_vars = extract_func_used_global_variable(file_attribute=file_attribute, func_name=func_name,
                                                                  unexport_vars=unexport_vars)
                pass

    # 处理需要添加宏的原头文件
    for i in need_add_include_header_file_set:
        add_header_file_include_linux_module(i)

    # 给kallsyms_lookup函数添加EXPORT宏
    add_export_kallsyms_look_up_macro()

    # 给所有未导出符号在模块源文件里添加宏
    add_unexport_var_macro(module_dir_path, unexport_vars)

    # 修改drivers下的和模块目录下的makefile
    modify_makefile(module_name, files_name, module_dir_path)


if __name__ == '__main__':
    modular(module_name, res_graph_dot_path)
