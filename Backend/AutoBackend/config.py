import os

# 是否把不同联通量合并成一个模块，现在默认为true，因为多模块没有写
merge = True

# 设置模块的名字
module_name = "name_module"

# 终端输出的包含模块化建议的dot文件
res_graph_dot_path = "/home/plot/Linux-modular/cjh/Linux-modular/result/net/netfilter/res.dot"

# 要修改的源代码的根目录
kernel_source_root_path = "/home/plot/Linux-modular/cjh/Linux-modular/linux-name"


# 包含源代码编译出的LLVM BC文件的根目录
kernel_bc_file_root_path = "../../Frontend/Kernel_src"

# 存放模块化模版文件的目录地址
module_template_files_dir = "../sys_module"

# 要修改的内核源代码目录下的drivers目录，用于存放我们的模块源代码
drivers_dir_path = os.path.join(kernel_source_root_path, "drivers")

# 导出符号文件
export_symbols_list_file = '/home/plot/build/funcs_analyze_5_10_176/export_symbols.txt'

# 当前文件的地址
current_file_path = os.path.abspath(__file__)

# 提取函数中使用到的全局符号的程序的相对地址
ExtractGlobalVarExe = os.path.dirname(current_file_path) + "/cpp/ExtractGlobalVar"

# 读取导出符号
export_symbols_set = set()
with open(export_symbols_list_file, 'r') as file:
    for line in file:
        export_symbols_set.add(line.strip())
