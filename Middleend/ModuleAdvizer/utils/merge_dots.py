import os

import pydot

from config.data_path import dots_root_folder


def combine_dots_from_folder(folder_path: str):
    merged_graph = pydot.Dot(graph_type='digraph')

    # 创建一个字典，用于保存节点
    nodes_dict = {}

    # 遍历文件夹中的所有 .dot 文件
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            if filename.endswith(".dot") and not filename.endswith("mod.dot"):
                # 解析 .dot 文件并获取 Dot 对象
                file_path = os.path.join(root, filename)
                dot_graph = pydot.graph_from_dot_file(file_path)[0]

                # 遍历节点并添加到新的 Dot 对象中
                for node in dot_graph.get_nodes():
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
                for edge in dot_graph.get_edges():
                    merged_graph.add_edge(edge)

    return merged_graph


def combine_dots_from_folder_with_locations(folder_path: str):
    merged_graph = pydot.Dot(graph_type='digraph')

    # 创建一个字典，用于保存节点
    nodes_dict = {}

    file_name_start_index = len(dots_root_folder)

    # 遍历文件夹中的所有 .dot 文件
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            if filename.endswith(".dot") and not filename.endswith("mod.dot"):
                # 解析 .dot 文件并获取 Dot 对象
                file_path = os.path.join(root, filename)
                dot_graph = pydot.graph_from_dot_file(file_path)[0]

                # 遍历节点并添加到新的 Dot 对象中
                for node in dot_graph.get_nodes():
                    if node.get_name() != "\"\\n\"":
                        if node.get_name() not in nodes_dict:
                            node.set('file', file_path[file_name_start_index:-4])
                            # 如果节点名称不存在，则添加到字典和新的 Dot 对象中
                            nodes_dict[node.get_name()] = node
                            merged_graph.add_node(node)
                        else:
                            # 如果节点名称已存在，则合并节点属性
                            existing_node = nodes_dict[node.get_name()]
                            for attr, value in node.get_attributes().items():
                                existing_node.set(attr, value)

    # 遍历边并添加到新的 Dot 对象中
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            if filename.endswith(".dot") and not filename.endswith("mod.dot"):
                # 解析 .dot 文件并获取 Dot 对象
                file_path = os.path.join(root, filename)
                dot_graph = pydot.graph_from_dot_file(file_path)[0]

                for edge in dot_graph.get_edges():
                    merged_graph.add_edge(edge)
                    if edge.get_destination() not in nodes_dict:
                        node = pydot.Node(edge.get_destination(), file=file_path[file_name_start_index:-4])
                        nodes_dict[edge.get_destination()] = node
                        merged_graph.add_node(node)

                    if edge.get_source() not in nodes_dict:
                        node = pydot.Node(edge.get_source(), file=file_path[file_name_start_index:-4])
                        nodes_dict[edge.get_source()] = node
                        merged_graph.add_node(node)

    return merged_graph
