import os
import argparse

import pydot


def combine_dots(folder_path: str):
    merged_graph = pydot.Dot(graph_type='digraph')

    nodes_visited = set()
    edge_visited = set()

    # 遍历文件夹中的所有 .dot 文件
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            if filename.endswith(".dot") and not filename.endswith("mod.dot") and not filename.startswith("all.dot") :
                # 解析 .dot 文件并获取 Dot 对象
                print(filename)
                file_path = os.path.join(root, filename)
                try:
                    dot_graph = pydot.graph_from_dot_file(file_path)[0]
                except TypeError:
                    print(file_path)
                    return

                # 遍历节点并添加到新的 Dot 对象中
                for node in dot_graph.get_nodes():
                    if node.get_name() != "\"\\n\"" and node.get_name() not in nodes_visited:
                        nodes_visited.add(node.get_name())
                        merged_graph.add_node(node)
                for edge in dot_graph.get_edges():
                    if edge not in edge_visited:
                        edge_visited.add(edge)
                        merged_graph.add_edge(edge)
                        if edge.get_source() not in nodes_visited:
                            merged_graph.add_node(pydot.Node(edge.get_source()))
                        if edge.get_destination() not in nodes_visited:
                            merged_graph.add_node(pydot.Node(edge.get_destination()))


    return merged_graph


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Merge multiple DOT files from a specified folder.')
    parser.add_argument('-i', '--input_folder', help='Path to the folder containing DOT files', required=True)
    parser.add_argument('-o', '--output', help='Output dot file name', required=True)

    args = parser.parse_args()

    graph = combine_dots(args.input_folder)
    graph.write(args.output)
