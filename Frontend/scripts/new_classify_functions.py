from elftools.elf.elffile import ELFFile
import pydot
from simple_parse_makefile import parseconfig
import os
import re
import networkx as nx
import copy

## This scripts used for classifying kernel functions

init_funcs = set()
reserve_init_funcs = set()
delete_init_funcs = set()
release_initcall_funcs = set()

export_symbols = set()
trace_funcs = set()
syscall_funcs = set()


init_reach_funcs = set()
virtual_structs = set()
virtual_structs_top_funcs = set()

modular_funcs = set()

base_path = "../"
# write path
wirte_path = os.path.join(base_path,"Data/new_func_list")
# vmlinux path
vmlinux_path = os.path.join(base_path+"Frontend/Kernel_src","vmlinux")
# kernel path
kernel_path = base_path + "Frontend/Kernel_src"
# config
config = ".config"
# kernel dot
kernel_dot = os.path.join(base_path+"Data/dots_merge","all.dot")

typedict = {
    "init_funcs": init_funcs,
    "reserve_init_funcs": reserve_init_funcs,
    "delete_init_funcs": delete_init_funcs,
    "release_initcall_funcs": release_initcall_funcs,
    "export_symbols":export_symbols,
    "trace_funcs":trace_funcs,
    "syscall_funcs":syscall_funcs,
    "init_reach_funcs":init_reach_funcs,
    "virtual_structs":virtual_structs,
    "virtual_structs_top_funcs":virtual_structs_top_funcs,
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

def filter_initcall(func_name):
    pattern = r'__initcall_(?P<name>[\w_]*)(6|6s|7|7s)' 
    match = re.match(pattern, func_name)
    final_name = None
    if match:
        final_name = match.group('name')
    return final_name

# 处理init函数
def process_init_funcs():
    global release_initcall_funcs
    global delete_init_funcs
    global reserve_init_funcs
    ## 1 识别无任何调用关系的函数, 放入no_caller_init_funcs
    G = nx.nx_agraph.read_dot(kernel_dot)
    no_caller_init_funcs = []
    
    for f in init_funcs:
        if G.has_node(f):
            if not list(G.predecessors(f)):
                no_caller_init_funcs.append(f)

    no_caller_successors = set()
    for f in no_caller_init_funcs:
        for suc in list(G.successors(f)):
            no_caller_successors.add(suc)

    flag = True
    while flag:
        remove = []
        for f in no_caller_successors:
            if set(G.predecessors(f)).issubset(set(no_caller_init_funcs)) and f in init_funcs:
                remove.append(f)
                
        if not remove:  
            flag = False
            break
        
        for re in remove:
            no_caller_successors.remove(re)
            no_caller_init_funcs.append(re)
            
            for suc in list(G.successors(re)):
                if suc in init_funcs:
                    no_caller_successors.add(suc)

    ## 2 处理no_caller_init_funcs, 删除caller in .asm, 生成delete_init_funcs
    # 读取arch/x86/kernel/head64.dot, 生成caller in .asm
    caller_in_asm_funcs = set()
    head64_dot = os.path.join(base_path+"Data/dots_merge","arch/x86/kernel/head64.dot")
    Ghead64 = nx.nx_agraph.read_dot(head64_dot)
    for node in Ghead64.nodes():
        if node in init_funcs:
            caller_in_asm_funcs.add(node)

    delete_init_funcs.update(set(no_caller_init_funcs) - caller_in_asm_funcs)


    ## 3 识别initcall后半部分函数, 生成realse_initcall_funcs
    device_to_end_funcs = set()
    # 打开 vmlinux 文件
    with open(vmlinux_path, 'rb') as f:
        elf = ELFFile(f)

        # 获取符号表节
        symtab = elf.get_section_by_name('.symtab')

        # 获取__initcall_start, __initcall_end, __initcall(x)_start地址
        for symbol in symtab.iter_symbols():
            if symbol.name == "__initcall6_start":
                device_initcall_start_address = symbol.entry.st_value
            if symbol.name == "__initcall_end":
                initcall_end_address = symbol.entry.st_value
        
        for symbol in symtab.iter_symbols():
            symobl_adr = symbol.entry.st_value
            if symobl_adr >= device_initcall_start_address and symobl_adr <= initcall_end_address: 
                device_to_end_funcs.add(symbol.name)

    for f in device_to_end_funcs:
        final_name = filter_initcall(f)
        if final_name:
            release_initcall_funcs.add(final_name)

    ## 4 其余init函数对应以前的init_funcs, 改名为reserve_init_funcs
    reserve_init_funcs.update(init_funcs - delete_init_funcs - release_initcall_funcs)
    write_to_file("reserve_init_funcs")
    write_to_file("release_initcall_funcs")
    write_to_file("delete_init_funcs")
    return

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

            
            if init_text_index is not None and export_index is not None:
                for symbol in symtab.iter_symbols():
                    # 识别init函数
                    if symbol['st_shndx'] == init_text_index:
                        name = symbol.name
                        if strtab is not None:
                            name = strtab.get_string(symbol['st_name'])
                        init_funcs.add(name)

                    # 识别export函数
                    if symbol['st_shndx'] == export_index:
                        name = symbol.name
                        if strtab is not None:
                            name = strtab.get_string(symbol['st_name'])
                        if "__kstrtab_" in name:
                            cut_name = name.replace('__kstrtab_', '')
                            export_symbols.add(cut_name)
                    
                    # 识别trace函数
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
    
    process_init_funcs()
    #保存init函数，export函数
    write_to_file("init_funcs")
    write_to_file("export_symbols")

    
#识别syscall函数，trace函数，virtual_structs函数，virtual_structs_top_funcs函数
def find_symbols_from_kernel_dot():
    global init_reach_funcs
    global virtual_structs
    global virtual_structs_top_funcs
    try:
        print("ready to read kernel dot")
        G = nx.nx_agraph.read_dot(kernel_dot)
        print("read kernel dot success")
    except FileNotFoundError:
        print("请先合并所有的dot文件,并命名为all.dot")
        return
    
    # 识别syscall函数，识别trace函数
    for node in G.nodes():
        if node.endswith("__virtual_init"):
            virtual_structs.add(node)
        if re.match(r'__(ia32_|x64_|se_|do_sys|do_compat_sys)', node):
            syscall_funcs.add(node)
        if "trace" in node or "perf" in node or "__traceiter" in node or "__SCT__tp" in node:
            trace_funcs.add(node)
    print("virtual structs :" + str(len(virtual_structs)))
    print("trace funcs:" + str(len(trace_funcs)))
    
    # 识别virtual_structs_top_funcs函数
    find = set()
    for i in virtual_structs:
        for j in list(G.successors(i)):
            if j not in virtual_structs and j not in find:
                find.add(j)
                virtual_structs_top_funcs.add(j)

    print("virtual structs top funcs:" + str(len(virtual_structs_top_funcs)))
    
    #识别reserve_init_funcs的init_reach函数    
    init_find = set()
    init_find.update(reserve_init_funcs)
    while(len(init_find)>0):
        element = init_find.pop()
        init_reach_funcs.add(element)
        if element in G.nodes():
            for child in G.successors(element):
                if child not in init_reach_funcs:
                    init_find.add(child)

    init_reach_funcs.update(init_reach_funcs - reserve_init_funcs)

    #保存函数
    write_to_file("syscall_funcs")
    write_to_file("trace_funcs")
    write_to_file("virtual_structs")
    write_to_file("virtual_structs_top_funcs")
    write_to_file("init_reach_funcs")


# 识别已模块化函数
def find_modular_functions():

    tempConfig = parseconfig(kernel_path,config)
    
    dot_files = set()
    temp_module_files = tempConfig.modualr_files
    dot_files = [os.path.join(base_path + "Data/dots_merge",it[len(kernel_path)+1:].replace(".c", ".dot")) for it in temp_module_files]

    #打开dot文件，把其中的node节点作为modular函数
    for dotf in dot_files:
        G = nx.nx_agraph.read_dot(dotf)
        for node in G.nodes():
            if node == "\n":
                continue
            modular_funcs.add(node)
    print("modular funcs:" + str(len(modular_funcs)))
    write_to_file("modular_funcs")

def classify_functions():
    find_symbols_from_vmlinux()
    
    find_symbols_from_kernel_dot()
    
    find_modular_functions()


if __name__ == "__main__":
    
    classify_functions()
    