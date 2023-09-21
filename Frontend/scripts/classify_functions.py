from elftools.elf.elffile import ELFFile
import pydot
from simple_parse_makefile import parseconfig

## This scripts used for classifying kernel functions

init_funcs = set()
export_funcs = set()

init_reach_funcs = set()
virtual_structs = set()
virtual_structs_reach = set()

module_funcs = set()

# 存储边
edge_dict = {}

# 识别init函数,export函数
def find_symbols_from_vmlinux(vmlinux_path):
    # 打开 vmlinux 文件
    with open(vmlinux_path, 'rb') as file:
        # 创建 ELFFile 对象
        elf = ELFFile(file)

        # 获取 .symtab 和 .strtab 节（节是 ELF 文件中的一个分段单位）
        symtab = elf.get_section_by_name('.symtab')
        strtab = elf.get_section_by_name('.strtab')

        section_header = elf['e_shstrndx']
        if section_header >= elf.num_sections():
            print("Invalid section header index.")
            return

        init_text_index = None
        export_index = None
        for index, section in enumerate(elf.iter_sections()):
            if section.name == ".init.text":
                init_text_index = index
            if section.name == "__ksymtab_strings":
                export_index = index

        #识别init函数
        if init_text_index is not None:
            for symbol in symtab.iter_symbols():
                if symbol['st_shndx'] == init_text_index:
                    name = symbol.name
                    if strtab is not None:
                        name = strtab.get_string(symbol['st_name'])
                    init_funcs.add(name)
            print("init funcs: " + str(len(init_funcs)))
        else:
          print("init_index None")
        
        #识别export函数
        if export_index is not None:
            for symbol in symtab.iter_symbols():
                if symbol['st_shndx'] == export_index:
                    name = symbol.name
                    if strtab is not None:
                        name = strtab.get_string(symbol['st_name'])
                    if "__kstrtab_" in name:
                        cut_name = name.replace('__kstrtab_', '')
                        export_funcs.add(cut_name)
            print("export funcs :" + str(len(export_funcs)))
        else:
            print("export_index None")

#识别init_reach函数，识别virtual_structs函数，识别virtual_struts_reach函数
def find_symbols_from_kernel_dot(kernel_dot):
    print("read kernel dot")
    graph = pydot.graph_from_dot_file(kernel_dot)[0]
    print("read kernel dot success")
    
    # 识别virtual_structs函数
    nodes = graph.get_nodes()
    for node in nodes:
        name = node.get_name()
        if name.endswith("__virtual_init"):
            virtual_structs.add(name)
    print("virtual structs :" + str(len(virtual_structs)))


    edges = graph.get_edges()
    for edge in edges:
        src = edge.get_source()
        des = edge.get_destination() 
        edge_dict.setdefault(src,set()).add(des)

    # 识别virtual_structs_reach函数
    find = set()
    for i in virtual_structs:
        if i in edge_dict.keys():
            for j in edge_dict[i]:
                if j not in virtual_structs and j not in find:
                    find.add(j)

    while(len(find)>0):
        element = find.pop()
        virtual_structs_reach.add(element)
        if element in edge_dict.keys():
            for child in edge_dict[element]:
                if child not in virtual_structs_reach:
                    find.add(child)
    print("virtual structs reach :" + str(len(virtual_structs_reach)))
    
    #识别init_reach函数
    init_find = set()
    for i in init_funcs:
        if i in edge_dict.keys():
            for j in edge_dict[i]:
                if j not in init_funcs and j not in init_find:
                    init_find.add(j)

    while(len(init_find)>0):
        element = init_find.pop()
        init_reach_funcs.add(element)
        if element in edge_dict.keys():
            for child in edge_dict[element]:
                if child not in init_reach_funcs:
                    init_find.add(child)
    print("init reach funcs :" + str(len(init_reach_funcs)))



# 识别已模块化函数
def find_modular_functions():
    find_module_files = set()
    tempConfig = parseconfig(kernel_path="/home/plot/linux-5.10.176",config=".config")
    find_module_files = tempConfig.modualr_files
    print(find_module_files)

if __name__ == "__main__":
    # vmlinux_path_test = "/home/plot/linux-5.10.176/vmlinux"
    # find_symbols_from_vmlinux(vmlinux_path_test)
    
    # kernel_dot = "/home/plot/hn_working_dir/Linux-modular/Graph/data/new_linux_all_with_file_label.dot"
    # find_symbols_from_kernel_dot(kernel_dot)
    
    find_modular_functions()