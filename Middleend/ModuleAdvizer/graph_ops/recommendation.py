from graph_ops.adjacency_list import directed_adjacency_list
from graph_ops.simplify_graph import remove_virtual
from graph_ops.transform import set_node_recommendation, set_color
from config.color_mapping import recommendation_color_mapping


def update_recommendation_info(graph):
    adjacency_list = directed_adjacency_list(graph)
    node_attribute_dicts = dict(dict())

    # 被不可模块化函数调用的函数设置为INTERFACE
    for i in adjacency_list["virtual_source"]:
        node_attribute_dicts[i] = "INTERFACE"

    # 没有任何函数调用的函数
    zero_in_degree_node_set = set()
    for node in graph.get_nodes():
        if node.get("type") != "VIRTUAL":
            zero_in_degree_node_set.add(node.get_name())

    zero_in_degree_node_set = set()
    work_set = set()

    for node in graph.get_nodes():
        if node.get("type") != "VIRTUAL":
            work_set.add(node.get_name())

    for next_node_names in adjacency_list.values():
        for node_name in next_node_names:
            work_set.discard(node_name)

    while len(work_set) > 0:
        node_name = work_set.pop()

        zero_in_degree_node_set.add(node_name)
        if node_name in adjacency_list:
            for next_node_name in adjacency_list[node_name]:
                work_set.add(next_node_name)
            adjacency_list.pop(node_name)

        for next_node_names in adjacency_list.values():
            for node_name in next_node_names:
                work_set.discard(node_name)

    for i in zero_in_degree_node_set:
        node_attribute_dicts[i] = "DELETE"

    # 只被可模块化函数调用的函数
    for node in graph.get_nodes():
        node_name = node.get_name()
        if node_name not in node_attribute_dicts:
            node_attribute_dicts[node_name] = "NORMAL"

    graph = set_node_recommendation(graph, node_attribute_dicts)
    return graph


def generate_recommendation_info(graph):
    print("generate recommendation info")
    graph = update_recommendation_info(graph)
    print("remove virtual nodes")
    graph = remove_virtual(graph)
    print("update color")
    graph = set_color(graph, recommendation_color_mapping, attribute="recommendation")
    return graph
