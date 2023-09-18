import pydot

from utils.load_graph import get_whole_linux_graph


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
    external_graph = get_whole_linux_graph()
    external_edges = external_graph.get_edges()

    nodes_set = {}
    for i in graph.get_nodes():
        nodes_set[i.get_name()] = i

    for i in external_edges:
        source: str = i.get_source()
        destination: str = i.get_destination()
        # filter out the external-to-external edge
        if destination in nodes_set and nodes_set[destination].get("type") != "EXTERNAL":
            add_edge(graph, source, destination)

    return graph
