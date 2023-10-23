import os

current_file_path = os.path.abspath(__file__)
current_directory = os.path.dirname(current_file_path)
parent_directory = os.path.dirname(current_directory)
parent_directory = os.path.dirname(parent_directory)
base_directory = os.path.dirname(parent_directory)

kernel_bc_file_root_path = os.path.join(base_directory, "Frontend/Kernel_src")
kernel_source_root_path = os.path.join(base_directory, "Frontend/Kernel_src")
llvm_bin_path_prefix = os.path.join(base_directory, "Frontend/llvm-project/prefix/bin")

# Frontend data path
func_list_files_dir = os.path.join(base_directory, "Data", "func_list")
dots_root_folder = os.path.join(base_directory, "Data", "dots_merge")

# Funcs list file name 
init_funcs_list_file = os.path.join(func_list_files_dir, 'init_funcs.txt')
init_reach_funcs_list_file = os.path.join(func_list_files_dir, 'init_reach_funcs.txt')
export_funcs_list_file = os.path.join(func_list_files_dir, 'export_symbols.txt')
trace_funcs_list_file = os.path.join(func_list_files_dir, 'trace_funcs.txt')
modular_funcs_list_file = os.path.join(func_list_files_dir, 'modular_funcs.txt')
virtual_structs_list_file = os.path.join(func_list_files_dir, 'virtual_structs.txt')
virtual_structs_top_funcs_list_file = os.path.join(func_list_files_dir, 'virtual_structs_top_funcs.txt')
syscall_funcs_list_file = os.path.join(func_list_files_dir, 'syscall_funcs.txt')

inline_funcs_list_file = os.path.join(func_list_files_dir, "inline_funcs_list.txt")
use_syscall_trace_funcs_list_file = os.path.join(func_list_files_dir, 'use_syscall_trace_funcs.txt')

# Cache for kernel graph
linux_whole_kernel_dot = os.path.join(dots_root_folder, 'all.dot')

temp_dir = "./temp/"
if not os.path.exists(temp_dir):
    os.mkdir(temp_dir)

kernel_graph_reverse_adjacency_list_cache = os.path.join(temp_dir, dots_root_folder.split(os.path.sep)[
    -1] + '_kernel_graph_reverse_adjacency_list_cache.pkl')

kernel_graph_adjacency_list_cache = os.path.join(temp_dir, dots_root_folder.split(os.path.sep)[
    -1] + '_kernel_graph_adjacency_list_cache.pkl')
