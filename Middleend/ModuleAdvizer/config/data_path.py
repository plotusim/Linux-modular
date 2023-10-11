import os.path

# Frontend data path
func_list_files_dir = '/home/plot/lls_test_frontend/Data/func_list_pa/'
dots_root_folder = '/home/plot/lls_test_frontend/Data/merged_dots'

# Funcs list file name 
init_funcs_list_file = os.path.join(func_list_files_dir, 'init_funcs.txt')
init_reach_funcs_list_file = os.path.join(func_list_files_dir, 'init_reach_funcs.txt')
export_funcs_list_file = os.path.join(func_list_files_dir, 'export_symbols.txt')
trace_funcs_list_file = os.path.join(func_list_files_dir, 'trace_funcs.txt')
modular_funcs_list_file = os.path.join(func_list_files_dir, 'modular_funcs.txt')
# virtual_structs_reach_funcs_list_file = os.path.join(func_list_files_dir, 'virtual_structs_reach_funcs.txt')
virtual_structs_list_file = os.path.join(func_list_files_dir, 'virtual_structs.txt')
virtual_structs_top_funcs_list_file = os.path.join(func_list_files_dir, 'virtual_structs_top_funcs.txt')
syscall_funcs_list_file = os.path.join(func_list_files_dir, 'syscall_funcs.txt')

# Cache for kernel graph
linux_whole_kernel_dot = os.path.join(dots_root_folder, 'all.dot')

temp_dir = "./temp/"
if not os.path.exists(temp_dir):
    os.mkdir(temp_dir)

kernel_graph_reverse_adjacency_list_cache = os.path.join(temp_dir, dots_root_folder.split(os.path.sep)[
    -1] + '_kernel_graph_reverse_adjacency_list_cache.pkl')

kernel_graph_adjacency_list_cache = os.path.join(temp_dir, dots_root_folder.split(os.path.sep)[
    -1] + '_kernel_graph_adjacency_list_cache.pkl')
