import os
import pydot
from collections import defaultdict
from datetime import datetime

base_path = "../"
dot_pa_dir = os.path.join(base_path, "Data/dots_pa")
dot_dir = os.path.join(base_path, "Data/dots")
merged_dot_dir = os.path.join(base_path, "Data/dots_merge")

func_use_gv = defaultdict(set)
gv_use_func = defaultdict(set)
gv_use_gv = defaultdict(set)
reversed_func_use_gv = defaultdict(set)
reversed_gv_use_gv = defaultdict(set)



def get_gv_use_func(gv_name):
    return gv_use_func[gv_name]


def add_gv_use_func(gv_name, func_name):
    get_gv_use_func(gv_name).add(func_name)


def add_gv_use_gv(gv_s, gv_d):
    gv_use_gv[gv_s].add(gv_d)


def add_func_use_gv(func_name, gv_name):
    func_use_gv[func_name].add(gv_name)


def is_global(name):
    return name.startswith("__virtual__global__") or name.startswith("\"__virtual__global__")


def gen_use_relation():
    all_dot_pa_graph_path = os.path.join(dot_pa_dir, "all.dot")
    graph = pydot.graph_from_dot_file(all_dot_pa_graph_path)[0]
    current_time = datetime.now()
    print("当前时间：", current_time)
    print("finish read all.dot")
    for i in graph.get_edges():
        sour = i.get_source()
        dest = i.get_destination()
        if is_global(sour):
            if is_global(dest):
                add_gv_use_gv(sour, dest)
            else:
                add_gv_use_func(sour, dest)
        else:
            if is_global(dest):
                add_func_use_gv(sour, dest)

    for key, value in gv_use_gv.items():
        for v in value:
            reversed_gv_use_gv[v].add(key)    

    visited = set()
    for leaf in reversed_gv_use_gv.keys():
        if leaf not in gv_use_gv.keys():
            visited.add(leaf)

    temp = dict(gv_use_gv)  # 创建gv_use_gv的副本

    while temp:
        delete = None  # 初始化delete变量

        for key, value in temp.items():
            if set(value).issubset(visited):
                delete = key
                visited.add(key)
                for i in value:
                    for j in gv_use_func[i]:
                        add_gv_use_func(key, j)
                break
        
        if delete is not None:
            del temp[delete]
        else:
            break  # 如果没有找到满足条件的key，退出循环

    for key, value in func_use_gv.items():
        for v in value:
            reversed_func_use_gv[v].add(key)
    current_time = datetime.now()
    print("当前时间：", current_time)
    print("gen finish")

def merge_pa_dots(relative_folder_path: str):
    print(f"begin merge:\t{relative_folder_path}")
    pa_folder_path = os.path.join(dot_pa_dir, relative_folder_path)
    origin_dot_folder_path = os.path.join(dot_dir, relative_folder_path)
    merged_dot_folder_path = os.path.join(merged_dot_dir, relative_folder_path)

    if not os.path.exists(merged_dot_folder_path):
        os.makedirs(merged_dot_folder_path)

    subdirectories = []
    files = []
    for item in os.listdir(origin_dot_folder_path):
        item_path = os.path.join(origin_dot_folder_path, item)
        if os.path.isdir(item_path):
            subdirectories.append(item)
        elif os.path.isfile(item_path):
            files.append(item)

    for filename in files:
        if filename.endswith("dot") and not filename.endswith("mod.dot") and not filename == "all.dot":
            print(f"merge:\t{os.path.join(relative_folder_path, filename)}")
            origin_dot = os.path.join(origin_dot_folder_path, filename)
            dot_graph = pydot.graph_from_dot_file(origin_dot)[0]

            pa_dot = os.path.join(pa_folder_path, filename)
            pa_dot_graph = pydot.graph_from_dot_file(pa_dot)[0]

            merged_dot_file = os.path.join(merged_dot_folder_path, filename)

            for edge in pa_dot_graph.get_edges():
                sour = edge.get_source()
                dest = edge.get_destination()
                if is_global(dest):
                    if is_global(sour):
                        continue
                    else:
                        for i in get_gv_use_func(dest):
                            if len(dot_graph.get_edge(sour, i)) == 0:
                                new_edge = pydot.Edge(sour, i)
                                dot_graph.add_edge(new_edge)
                else:
                    if is_global(sour):
                        for func in reversed_func_use_gv[sour]:
                            if len(dot_graph.get_node(func)):
                                if len(dot_graph.get_edge(func, dest)) == 0:
                                    new_edge = pydot.Edge(func, dest)
                                    dot_graph.add_edge(new_edge)

                    else:
                        if len(dot_graph.get_edge(sour, dest)) == 0:
                            dot_graph.add_edge(edge)

            dot_graph.write(merged_dot_file)

    for subdirectory in subdirectories:
        merge_pa_dots(os.path.join(relative_folder_path, subdirectory))


if __name__ == '__main__':
    current_time = datetime.now()
    print("当前时间：", current_time)
    print("gen_use_relation")
    gen_use_relation()


    print("begin merge")
    merge_pa_dots("")
    current_time = datetime.now()
    print("当前时间：", current_time)
    print("merge finish")
