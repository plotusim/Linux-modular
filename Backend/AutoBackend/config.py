import os
import re
import inspect
from collections import defaultdict


class Config:
    def __init__(self):
        self.merge = True
        self.project_base_path = "../../"
        # self.project_base_path = "/home/plot/build/Frontend_test_v2"
        self.module_name = "dnotify_module"
        self.llvm_bin_path_prefix = os.path.join(self.project_base_path, "Frontend/llvm-project/prefix/bin")
        self.whole_kernel_dot_file = os.path.join(self.project_base_path, "Data/dots_merge/all.dot")
        self.res_graph_dot_path = os.path.join(self.project_base_path, "Middleend/result/fs/notify/dnotify/res.dot")
        self.kernel_source_root_path = os.path.join(self.project_base_path, "Backend/test_hn")
        self.kernel_bc_file_root_path = os.path.join(self.project_base_path, "Frontend/Kernel_src")
        self.module_template_files_dir = os.path.join(self.project_base_path, "Backend/sys_module")
        self.drivers_dir_path = os.path.join(self.kernel_source_root_path, "drivers")
        self.export_symbols_list_file = os.path.join(self.project_base_path, "Data/func_list/export_symbols.txt")
        self.current_file_path = os.path.abspath(__file__)
        self.current_project_dir = os.path.dirname(self.current_file_path)

        # 这些变量将在调用相应的方法时被初始化
        self.export_symbols_set = set()
        self.func_file_attribute_pairs = {}
        self.func_children = defaultdict(set)
        self.read_export_symbols()
        self.read_func_file_pairs()
        self.read_func_children_pairs()

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

    def read_args_and_modify(self, args):
        # 获取当前所有用户自定义的属性
        attributes = inspect.getmembers(self, lambda a: not (inspect.isroutine(a)))
        custom_attributes = [a for a in attributes if not (a[0].startswith('__') and a[0].endswith('__'))]

        for attr_name, _ in custom_attributes:
            # 如果args中存在同名的属性，则更新当前对象的属性
            if args and hasattr(args, attr_name):
                value = getattr(args, attr_name)
                if value is not None:  # 如果参数被明确地传递了，则更新属性
                    setattr(self, attr_name, value)

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
