import pydot

from utils.load_adjacency_list import get_whole_linux_kernel_reverse_adjacency_list


def add_edge(graph, source, destination):
    edges_list = graph.get_edge(source, destination)
    if len(edges_list) == 0:
        graph.add_edge(pydot.Edge(source, destination))
    endpoints = [source, destination]
    for i in endpoints:
        node_in_graph = graph.get_node(i)
        if len(node_in_graph) == 0:
            graph.add_node(pydot.Node(i, type="EXTERNAL"))

    return graph


def build_external_edges(graph):
    reverse_adjacency_list = get_whole_linux_kernel_reverse_adjacency_list()

    nodes_set = {}
    for i in graph.get_nodes():
        nodes_set[i.get_name()] = i

    for node in nodes_set.keys():
        for i in reverse_adjacency_list[node]:
            add_edge(graph, i, node)

    return graph
