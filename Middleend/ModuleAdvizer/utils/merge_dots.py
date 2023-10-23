import os
import pydot
from utils.cache_util import get_latest_modification_time
from config.data_path import temp_dir
from utils.load_func_file_pairs import get_func_file_pairs


def get_merge_dots_cache_file_path(folder_path: str, other_str: str):
    filename = other_str + "_" + "_".join(folder_path.split(os.path.sep)) + ".dot"
    return os.path.join(temp_dir, filename)


def get_dot_else_run_f(dot_cache_file_path, f, last_modify_time):
    try:
        cache_file_mtime = os.path.getmtime(dot_cache_file_path)
    except FileNotFoundError:
        cache_file_mtime = 0

    # 如果dot文件的修改时间比缓存文件的修改时间晚，或缓存文件不存在
    if last_modify_time > cache_file_mtime:
        graph: pydot.Graph = f()
        graph.write(dot_cache_file_path)
        return graph
    else:
        # 尝试从缓存中加载graph
        graph = pydot.graph_from_dot_file(dot_cache_file_path)[0]
        return graph


def combine_dots_from_folder(folder_path: str):
    folder_path_latest_modification_time = get_latest_modification_time(folder_path)
    cache_file_path = get_merge_dots_cache_file_path(folder_path, "")
    f = lambda: _combine_dots_from_folder(folder_path)
    return get_dot_else_run_f(cache_file_path, f, folder_path_latest_modification_time)


def _combine_dots_from_folder(folder_path: str):
    merged_graph = pydot.Dot(graph_type='digraph')
    # 创建一个字典，用于保存节点
    nodes_dict = {}
    subdirectories = []
    files = []
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isdir(item_path):
            subdirectories.append(item)
        elif os.path.isfile(item_path):
            files.append(item)

    for subdirectory in subdirectories:
        sub_dir_graph = combine_dots_from_folder(os.path.join(folder_path, subdirectory))
        second_merge_to_first(merged_graph, nodes_dict, sub_dir_graph)

    for filename in files:
        if filename.endswith(".dot") and not filename.endswith("mod.dot") and not filename.startswith("all.dot"):
            # 解析 .dot 文件并获取 Dot 对象
            file_path = os.path.join(folder_path, filename)
            dot_graph = pydot.graph_from_dot_file(file_path)[0]

            second_merge_to_first(merged_graph, nodes_dict, dot_graph)

    return merged_graph


def second_merge_to_first(merged_graph, nodes_dict, sub_dir_graph):
    # 遍历节点并添加到新的 Dot 对象中
    for node in sub_dir_graph.get_nodes():
        if node.get_name() != "\"\\n\"":
            if node.get_name() not in nodes_dict:
                # 如果节点名称不存在，则添加到字典和新的 Dot 对象中
                nodes_dict[node.get_name()] = node
                merged_graph.add_node(node)
            else:
                # 如果节点名称已存在，则合并节点属性
                existing_node = nodes_dict[node.get_name()]
                for attr, value in node.get_attributes().items():
                    existing_node.set(attr, value)
    # 遍历边并添加到新的 Dot 对象中
    for edge in sub_dir_graph.get_edges():
        merged_graph.add_edge(edge)

    func_file_pairs = get_func_file_pairs()
    for node in merged_graph.get_nodes():
        node.set("file", func_file_pairs[node.get_name()])

    return merged_graph
