from collections import defaultdict


def directed_adjacency_list(graph):
    # 获取图中的所有边
    edges = graph.get_edges()

    # 使用字典存储每个节点的相邻节点
    adjacency_list_res = defaultdict(set)

    for edge in edges:
        source = edge.get_source()
        dest = edge.get_destination()

        # 更新源节点的邻接列表
        adjacency_list_res[source].add(dest)

    return adjacency_list_res


def reverse_directed_adjacency_list(graph):
    # 获取图中的所有边
    edges = graph.get_edges()

    # 使用字典存储每个节点的相邻节点
    reverse_adjacency_list_res = defaultdict(set)

    for edge in edges:
        source = edge.get_source()
        dest = edge.get_destination()

        # 更新源节点的邻接列表
        reverse_adjacency_list_res[dest].add(source)

    return reverse_adjacency_list_res


def undirected_adjacency_list(graph):
    # 获取图中的所有边
    edges = graph.get_edges()

    # 使用字典存储每个节点的相邻节点
    adjacency_list_res = defaultdict(set)

    for edge in edges:
        source = edge.get_source()
        dest = edge.get_destination()

        # 更新源节点的邻接列表
        adjacency_list_res[source].add(dest)

        adjacency_list_res[dest].add(source)

    return adjacency_list_res
