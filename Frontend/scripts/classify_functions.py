from elftools.elf.elffile import ELFFile
import pydot
from simple_parse_makefile import parseconfig
import os
import re
import copy

## This scripts used for classifying kernel functions

init_funcs = set()
export_symbols = set()
trace_funcs = set()
syscall_funcs = set()


init_reach_funcs = set()
virtual_structs = set()
virtual_structs_top_funcs = set()
virtual_structs_reach_funcs = set()

modular_funcs = set()

# 存储边
edge_dict = {}

base_path = "../"
# write path
wirte_path = os.path.join(base_path,"Data/func_list")
# vmlinux path
vmlinux_path = os.path.join(base_path+"Frontend/kernel_src","vmlinux")
# kernel path
kernel_path = base_path + "Frontend/kernel_src"
# config
config = ".config"
# kernel dot
kernel_dot = os.path.join(base_path+"Data/dots","all.dot")

typedict = {
    "init_funcs": init_funcs,
    "export_symbols":export_symbols,
    "trace_funcs":trace_funcs,
    "syscall_funcs":syscall_funcs,
    "init_reach_funcs":init_reach_funcs,
    "virtual_structs":virtual_structs,
    "virtual_structs_top_funcs":virtual_structs_top_funcs,
    "virtual_structs_reach_funcs":virtual_structs_reach_funcs,
    "modular_funcs":modular_funcs,
}


def write_to_file(type):
    try:
        write_file_path = os.path.join(wirte_path,str(type)+".txt")
        with open(write_file_path,"w") as wfile:
            typeset = typedict[type]
            if len(typeset) == 0:
                print(f"type dict error:{type}")
                
            for f in typeset:
                wfile.write(f+"\n")
    except FileNotFoundError:
        print("写入文件路径错误")

    
# 识别init函数,export函数 
def find_symbols_from_vmlinux():
    # 打开 vmlinux 文件
    try:
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
            text_index = None
            for index, section in enumerate(elf.iter_sections()):
                if section.name == ".init.text":
                    init_text_index = index
                if section.name == "__ksymtab_strings":
                    export_index = index
                if section.name == ".text":
                    text_index = index

            #识别init函数
            if init_text_index is not None and export_index is not None:
                for symbol in symtab.iter_symbols():
                    if symbol['st_shndx'] == init_text_index:
                        name = symbol.name
                        if strtab is not None:
                            name = strtab.get_string(symbol['st_name'])
                        init_funcs.add(name)

                    if symbol['st_shndx'] == export_index:
                        name = symbol.name
                        if strtab is not None:
                            name = strtab.get_string(symbol['st_name'])
                        if "__kstrtab_" in name:
                            cut_name = name.replace('__kstrtab_', '')
                            export_symbols.add(cut_name)
                    
                    if symbol['st_shndx'] == text_index:
                        name = symbol.name
                        if strtab is not None:
                            name = strtab.get_string(symbol['st_name'])
                        if "trace" in name:
                            trace_funcs.add(name)
                    
                    name = symbol.name
                    if re.match(r'__(ia32_|x64_|se_|do_sys)', name):
                        syscall_funcs.add(name)

                print("init funcs: " + str(len(init_funcs)))
                print("export funcs: " + str(len(export_symbols)))
                print("syscall_funcs: " + str(len(syscall_funcs)))
            else:
                if init_text_index is not None:
                    print("init_index None")
                else:
                    print("export_index None")
            
    except FileNotFoundError:
        print("请先编译内核!确保生成vmlinux")
    
    #保存init函数，export函数
    write_to_file("init_funcs")
    write_to_file("export_symbols")


    
#识别init_reach函数，识别virtual_structs函数，识别virtual_struts_reach函数
def find_symbols_from_kernel_dot():
    try:
        print("ready to read kernel dot")
        graph = pydot.graph_from_dot_file(kernel_dot)[0]
        print("read kernel dot success")
    except FileNotFoundError:
        print("请先合并所有的dot文件,并命名为all.dot")
        return
    
    # 识别virtual_structs函数
    nodes = graph.get_nodes()
    for node in nodes:
        name = node.get_name()
        if name.endswith("__virtual_init"):
            virtual_structs.add(name)
        if re.match(r'__(ia32_|x64_|se_|do_sys|do_compat_sys)', name):
            syscall_funcs.add(name)
        if "trace" in name or "perf" in name or "__traceiter" in name or "__SCT__tp" in name:
            trace_funcs.add(name)
    print("virtual structs :" + str(len(virtual_structs)))

    edges = graph.get_edges()
    for edge in edges:
        src = edge.get_source()
        des = edge.get_destination() 
        edge_dict.setdefault(src,set()).add(des)

    # 识别trace函数
    print("trace funcs:" + str(len(trace_funcs)))
    
    # 识别virtual_structs_reach函数
    find = set()
    for i in virtual_structs:
        if i in edge_dict.keys():
            for j in edge_dict[i]:
                if j not in virtual_structs and j not in find:
                    find.add(j)
                    virtual_structs_top_funcs.add(j)

    print("virtual structs top funcs:" + str(len(virtual_structs_top_funcs)))
    
    while(len(find)>0):
        element = find.pop()
        virtual_structs_reach_funcs.add(element)
        if element in edge_dict.keys():
            for child in edge_dict[element]:
                if child not in virtual_structs_reach_funcs:
                    find.add(child)
    print("virtual structs reach funcs:" + str(len(virtual_structs_reach_funcs)))
    
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

    #保存virtual_structs函数，virtual_structs_reach函数，init_reach函数
    write_to_file("syscall_funcs")
    write_to_file("virtual_structs")
    write_to_file("virtual_structs_top_funcs")
    write_to_file("virtual_structs_reach_funcs")
    write_to_file("trace_funcs")
    write_to_file("init_reach_funcs")


# 识别已模块化函数
def find_modular_functions():

    tempConfig = parseconfig(kernel_path,config)
    
    dot_files = set()
    temp_module_files = tempConfig.modualr_files
    dot_files = [os.path.join(base_path + "Data/dots",it[len(kernel_path)+1:].replace(".c", ".dot")) for it in temp_module_files]

    #打开dot文件，把其中的node节点作为modular函数
    for dotf in dot_files:
        graph = pydot.graph_from_dot_file(dotf)[0]
        for node in graph.get_nodes():
            name = node.get_name()
            if name == "\n":
                continue
            modular_funcs.add(node.get_name())
    print("modular funcs:" + str(len(modular_funcs)))
    write_to_file("modular_funcs")


def classify_functions():
    find_symbols_from_vmlinux()
    
    find_symbols_from_kernel_dot()
    
    find_modular_functions()


if __name__ == "__main__":
    
    classify_functions()
    