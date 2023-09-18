from config import non_modular_type
from config.color_mapping import function_color_mapping
from graph_ops.transform import remove_nodes_with_predicate, set_color
from graph_ops.recommendation import update_recommendation_info


def merge_external_edges(graph):
    graph = remove_nodes_with_predicate(graph, lambda x: x.get("type") == "EXTERNAL", to_virtual=True)
    return graph


def remove_non_modular_nodes(graph):
    graph = remove_nodes_with_predicate(graph, lambda x: x.get("type") in non_modular_type, to_virtual=True)
    return graph


def remove_virtual(graph):
    graph = remove_nodes_with_predicate(graph, lambda x: x.get("type") == "VIRTUAL", to_virtual=False)
    return graph


def simplify_graph(graph):
    print("merge extern nodes")
    graph = merge_external_edges(graph)
    print("merge non-modular nodes")
    graph = remove_non_modular_nodes(graph)

    graph = update_recommendation_info(graph)
    graph = remove_virtual(graph)
    graph = set_color(graph, function_color_mapping, "type")
    return graph
