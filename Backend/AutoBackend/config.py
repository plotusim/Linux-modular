import os
import re

# 是否把不同联通量合并成一个模块，现在默认为true，因为多模块没有写
merge = True

# 设置模块的名字,注意后面最好跟上_module后缀
# module_name = "auth_gss"
module_name = "netfilter"

# 前端输出的总dot文件
whole_kernel_dot_file = "../../Data/dots_merge/all.dot"

# 终端输出的包含模块化建议的dot文件
# res_graph_dot_path = "/home/plot/hn_working_dir/Linux-modular/Graph/result_pa_new/net/sunrpc/auth_gss/res.dot"
res_graph_dot_path = "/home/plot/build/Frontend_test_v2/Middleend/result/net/netfilter/res.dot"

# 要修改的源代码的根目录
# kernel_source_root_path = "../modify_kernel_auth_gss"

kernel_source_root_path = "/home/plot/build/Frontend_test_v2/Backend/test_hn"

# 包含源代码编译出的LLVM BC文件的根目录
kernel_bc_file_root_path = "../../Frontend/Kernel_src"

# 存放模块化模版文件的目录地址
module_template_files_dir = "../sys_module"

# 要修改的内核源代码目录下的drivers目录，用于存放我们的模块源代码
drivers_dir_path = os.path.join(kernel_source_root_path, "drivers")

# 导出符号文件
export_symbols_list_file = '../../Data/func_list/export_symbols.txt'

# 当前文件的地址
current_file_path = os.path.abspath(__file__)
current_project_dir = os.path.dirname(current_file_path)


# 初始化项目会用到的其他全局变量
# 读取导出符号
print("Read export symbols set")
export_symbols_set = set()
with open(export_symbols_list_file, 'r') as file:
    for line in file:
        export_symbols_set.add(line.strip())

# 读取all.dot
print("Read func-file pairs")
func_file_attribute_pairs = {}
pattern = r'(.*?)\s*\[file=\"(.*?)\"(.*?)\]'

with open(whole_kernel_dot_file, 'r') as file:
    for line in file:
        match = re.search(pattern, line)
        if match:
            part1 = match.group(1)
            part2 = match.group(2)
            func_file_attribute_pairs[part1] = part2
