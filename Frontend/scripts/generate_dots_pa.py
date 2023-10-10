import re
import pydot
import os
import sys

base_path = "../"

graph_dir = os.path.join(base_path, "Data/txts_pa")
dot_dir = os.path.join(base_path, "Data/dots_pa")


def TxtToDot(source_path, destination_path):
    # 读取文件内容
    with open(source_path, 'r') as f:
        # 创建dot
        graph = pydot.Dot(graph_type='digraph')
        lines = f.readlines()
        # 解析每一行
        for line in lines:
            match = re.match(r'([\w@]+) -> (.*)', line)
            if match:
                caller = match.group(1)
                callees = set()
                for i in match.group(2).split():
                    callees.add(i)

                caller_splits = caller.split('@')
                if "llvm." in caller_splits[0]:
                    continue

                # print(caller.split('@'))

                if caller_splits[1] == "isDefinition":
                    new_node = pydot.Node(caller_splits[0])
                    new_node.set("file", source_path[len(graph_dir):-4])
                    graph.add_node(new_node)
                for callee in callees:
                    callee_splits = callee.split('@')
                    if "llvm." in callee_splits[0]:
                        continue
                    edge = pydot.Edge(caller_splits[0], callee_splits[0])
                    graph.add_edge(edge)

    # 遍历所有的节点和边
    for edge in graph.get_edges():
        label = edge.get_label()
        if label and 'llvm.' in label:
            # 如果标签中包含 "llvm."，则删除这条边
            graph.del_edge(edge)
    for node in graph.get_nodes():
        label = node.get_label()
        if label and 'llvm.' in label:
            # 如果标签中包含 "llvm."，则删除这个节点
            graph.del_node(node)

    # 保存为 .dot 文件
    graph.write(destination_path)


def GraphToDot(source_dir, target_dir):
    # 遍历源文件夹下的所有文件和子文件夹
    for root, dirs, files in os.walk(source_dir):
        # 遍历所有文件
        for file in files:
            # 如果文件扩展名为 .txt
            if file.endswith('.txt'):
                # 获取文件的绝对路径
                file_path = os.path.join(root, file)
                target_file_path = file_path.replace(source_dir, target_dir).replace('.txt', '.dot')

                # 创建目标文件夹
                os.makedirs(os.path.dirname(target_file_path), exist_ok=True)

                print("converting " + file_path)
                TxtToDot(file_path, target_file_path)


def main():
    GraphToDot(graph_dir, dot_dir)


if __name__ == '__main__':
    main()
