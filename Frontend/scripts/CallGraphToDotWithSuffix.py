import re
import pydot
import os
import sys

graph_dir = '/home/plot/clang-linux-5.10.176/new_graphs_suffix'
#graph_dir = '/home/plot/clang-linux-5.10.176/graphsWithSuffix'
dot_dir = '/home/plot/clang-linux-5.10.176/new_dots_suffix'
#dot_dir = '/home/plot/clang-linux-5.10.176/dotsForFileCall'

def TxtToDot(source_path, destination_path):
    # 读取文件内容
    with open(source_path, 'r') as f:
        # 解析每一行的函数调用关系
        graph = pydot.Dot(graph_type='digraph')
    
        for line in f:
            # @ 用于读取后缀
            match = re.match(r'([\w@]+) -> (.*)', line)
            if match:
                caller = match.group(1)
                callees = match.group(2).split()
                if caller.split('@')[1] == "isDefinition":
                    graph.add_node(pydot.Node(caller.split('@')[0]))
                for callee in callees:
                    edge = pydot.Edge(caller.split('@')[0], callee.split('@')[0])
                    graph.add_edge(edge)

    # # 添加没有被调用的函数作为孤立节点
    # for node in nodes:
    #     if not any(edge.get_destination() == node for edge in graph.get_edges()):
    #         graph.add_node(pydot.Node(node))

    # 删除图中与llvm有关的函数，即含有"llvm."的行
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
            # 如果文件扩展名为 .c
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


if __name__=='__main__' :
    main()
    #file_path = sys.argv[1]
    #file_path = "a.txt"
    #target_file_path = file_path.replace('.txt', '.dot')
    #TxtToDot(file_path, target_file_path)
