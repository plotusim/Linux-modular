import os

current_path = os.path.abspath(__file__)
project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_path))))
data_folder = os.path.join(project_root_path, "Data")

# 存放前端生成的dots
dots_root_folder = os.path.join(data_folder, 'dots')

# 存放前端分析的init函数列表
init_func_list_file = os.path.join(data_folder, 'func_list', 'initfuncs.txt')


init_reach_func_list_file = os.path.join(data_folder, 'func_list', 'init_reach_funcs.txt')
export_func_list_file = os.path.join(data_folder, 'func_list', 'export_symbols.txt')
virtual_structs_reach_level1_list_file = os.path.join(data_folder, 'func_list',
                                                      'virtual_structs_reach_level1_list_file.txt')
all_virtual_structs_reach_funcs_list_file = os.path.join(data_folder, 'func_list',
                                                         'all_virtual_structs_reach_funcs_list_file.txt')

whole_kernel_dot_file = os.path.join(data_folder, 'new_linux_all_with_file_label.dot')

whole_kernel_graph_cache = os.path.join(data_folder, 'whole_kernel_graph_cache.pkl')
