from elftools.elf.elffile import ELFFile



vmlinux_path = "../kernel_src/vmlinux"
## This scripts used for classfifying kernel functions

init_funcs = set()
export_funcs = set()

init_reach_funcs= set()
virtual_structs = set()
virtual_structs_reach = set()

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

            


if __name__ == "__main__":
    vmlinux_path_test = "/home/plot/linux-5.10.176/vmlinux"
    find_symbols_from_vmlinux(vmlinux_path_test)