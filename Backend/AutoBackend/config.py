import re
from collections import defaultdict
import configparser
import os


def update_config(file_path, section, key_value_pairs):
    """
    更新config.ini文件的函数。

    :param file_path: 配置文件的路径。
    :param section: 要更新的配置文件的部分。
    :param key_value_pairs: 键值对字典，包含要更新的设置。
    """
    # 实例化ConfigParser对象
    config = configparser.ConfigParser()

    # 检查文件是否存在
    if not os.path.exists(file_path):
        print(f"Error: The config file does not exist at {file_path}")
        return

    # 读取配置文件
    config.read(file_path)

    # 检查部分是否存在于配置文件中
    if section not in config.sections():
        print(f"Error: The section {section} does not exist in {file_path}")
        return

    # 更新键值对
    for key, value in key_value_pairs.items():
        config[section][key] = value

    # 写回到文件中
    with open(file_path, 'w') as configfile:
        config.write(configfile)

    print(f"Config file at {file_path} updated successfully.")


class Config:
    def __init__(self, config_file='config.ini'):
        # 加载和解析配置文件
        self.config = configparser.ConfigParser()
        self.config.read(config_file)

        # 从配置文件初始化属性
        self.merge = self.config.getboolean('DEFAULT', 'Merge')
        self.project_base_path = self.config.get('DEFAULT', 'ProjectBasePath')
        self.module_name = self.config.get('MODULE', 'ModuleName')

        # 以下路径依赖于project_base_path, 之后会更新它们
        self.llvm_bin_path_prefix = ''
        self.whole_kernel_dot_file = ''
        self.res_graph_dot_path = ''
        self.kernel_source_root_path = ''
        self.kernel_bc_file_root_path = ''
        self.module_template_files_dir = ''
        self.drivers_dir_path = ''
        self.export_symbols_list_file = ''
        self.export_symbols_set = set()
        self.func_file_attribute_pairs = {}
        self.func_children = defaultdict(set)
        # 初始化路径
        self.update_paths_based_on_base()
        self.current_file_path = os.path.abspath(__file__)
        self.current_project_dir = os.path.dirname(self.current_file_path)

        self.read_export_symbols()
        self.read_func_file_pairs()
        self.read_func_children_pairs()

    def update_paths_based_on_base(self):
        """基于基础路径更新其他路径"""
        self.llvm_bin_path_prefix = self.config.get('LLVM', 'LLVMBinPathPrefix')
        self.whole_kernel_dot_file = self.config.get('DEFAULT', 'WholeKernelDotFile')
        self.whole_kernel_pa_dot_file = self.config.get('DEFAULT', 'wholekernelpadotfile')
        self.res_graph_dot_path = self.config.get('MODULE', 'ResGraphDotPath')
        self.kernel_source_root_path = self.config.get('MODULE', 'KernelSourceRootPath')
        self.kernel_bc_file_root_path = self.config.get('DEFAULT', 'KernelBCFileRootPath')
        self.module_template_files_dir = self.config.get('DEFAULT', 'ModuleTemplateFilesDir')
        self.drivers_dir_path = os.path.join(self.kernel_source_root_path, "drivers")
        self.export_symbols_list_file = self.config.get('DEFAULT', 'ExportSymbolsListFile')

    def read_export_symbols(self):
        print("Read export symbols set")
        with open(self.export_symbols_list_file, 'r') as file:
            for line in file:
                self.export_symbols_set.add(line.strip())

    def read_func_file_pairs(self):
        print("Read func-file pairs")
        pattern2 = r'(.*?)\s*\[file=\"(.*?)\"(.*?)\]'
        with open(self.whole_kernel_pa_dot_file, 'r') as file:
            for line in file:
                match = re.search(pattern2, line)
                if match:
                    part1 = match.group(1)
                    part2 = match.group(2)
                    self.func_file_attribute_pairs[part1] = part2

        folder_path = self.res_graph_dot_path.replace("res.dot", "temp/")
        files = []
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path):
                files.append(item)
        for i in files:
            with open(folder_path + i, 'r') as file:
                for line in file:
                    match = re.search(pattern2, line)
                    if match:
                        part1 = match.group(1)
                        part2 = match.group(2)
                        self.func_file_attribute_pairs[part1] = part2

    def read_func_children_pairs(self):
        print("Read func->func pairs")
        pattern = r'\"?\b([a-zA-Z_]\w*)\b\"?\s*->\s*\"?\b([a-zA-Z_]\w*)\b\"?'
        with open(self.whole_kernel_dot_file, 'r') as file:
            for line in file:
                match = re.search(pattern, line)
                if match:
                    part1 = match.group(1)
                    part2 = match.group(2)
                    self.func_children[part1].add(part2)

    def __str__(self):

        ignore_list = ['export_symbols_set',
                       'func_file_attribute_pairs',
                       'func_children']

        # 这个方法将会被str()调用，并且当你打印一个对象时，也会自动调用它

        # 获取类中定义的所有属性
        attrs = vars(self)
        # 创建一个包含属性名和值的字符串列表
        # 格式：attribute_name: attribute_value
        result = ['{}: {}'.format(attr, value) for attr, value in attrs.items() if attr not in ignore_list]

        # 将所有字符串连接起来，每个一行
        return '\n'.join(result)


config = Config()
