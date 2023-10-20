import re
from collections import defaultdict
import configparser
import os


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
        self.llvm_bin_path_prefix = os.path.join(self.project_base_path,
                                                 self.config.get('DEFAULT', 'LLVMBinPathPrefix'))
        self.whole_kernel_dot_file = os.path.join(self.project_base_path,
                                                  self.config.get('DEFAULT', 'WholeKernelDotFile'))
        self.res_graph_dot_path = os.path.join(self.project_base_path, self.config.get('MODULE', 'ResGraphDotPath'))
        self.kernel_source_root_path = os.path.join(self.project_base_path,
                                                    self.config.get('MODULE', 'KernelSourceRootPath'))
        self.kernel_bc_file_root_path = os.path.join(self.project_base_path,
                                                     self.config.get('DEFAULT', 'KernelBCFileRootPath'))
        self.module_template_files_dir = os.path.join(self.project_base_path,
                                                      self.config.get('DEFAULT', 'ModuleTemplateFilesDir'))
        self.drivers_dir_path = os.path.join(self.kernel_source_root_path, "drivers")  # 这个是基于另一个路径的
        self.export_symbols_list_file = os.path.join(self.project_base_path,
                                                     self.config.get('DEFAULT', 'ExportSymbolsListFile'))

    def read_export_symbols(self):
        print("Read export symbols set")
        with open(self.export_symbols_list_file, 'r') as file:
            for line in file:
                self.export_symbols_set.add(line.strip())

    def read_func_file_pairs(self):
        print("Read func-file pairs")
        pattern2 = r'(.*?)\s*\[file=\"(.*?)\"(.*?)\]'
        with open(self.whole_kernel_dot_file, 'r') as file:
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
