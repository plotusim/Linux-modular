import pydot


def set_nodes_type(graph: pydot.Graph, node_types):
    for node_type, func_name_set in node_types.items():
        for func_name in func_name_set:
            func_node = graph.get_node(func_name)
            if func_node:
                func_node = func_node[0]
                if func_node.get("type") is None:
                    func_node.set("type", node_type)

    return graph


def set_default_type(graph: pydot.Graph):
    for func_node in graph.get_nodes():
        if func_node.get("type") is None:
            func_node.set("type", "PLAIN")
    return graph


def set_color(graph, color_mapping, attribute):
    for node in graph.get_nodes():
        value = node.get(attribute)
        if value is not None:
            node.set("style", "filled")
            node.set("fillcolor", color_mapping.get(value, 'white'))
    return graph


def set_node_recommendation(graph, node_values):
    for node_name, value in node_values.items():
        graph.get_node(node_name)[0].set("recommendation", value)
    return graph


def remove_nodes_with_predicate(graph, func, to_virtual: bool = False):
    nodes_dict = {}
    edge_dict = {}

    for node in graph.get_nodes():
        if func(node):
            continue
        else:
            nodes_dict[node.get_name()] = node

    for edge in graph.get_edges():
        source = edge.get_source()
        destination = edge.get_destination()
        name = source + "#" + destination
        if source in nodes_dict or destination in nodes_dict:
            if source in nodes_dict and destination in nodes_dict:
                edge_dict[name] = edge
            elif to_virtual:
                if source in nodes_dict:
                    destination = "virtual_destination"
                    name = source + "#" + destination
                    if "virtual_destination" not in nodes_dict:
                        nodes_dict["virtual_destination"] = pydot.Node("virtual_destination", type="VIRTUAL")
                else:
                    if "virtual_source" not in nodes_dict:
                        nodes_dict["virtual_source"] = pydot.Node("virtual_source", type="VIRTUAL")

                    source = "virtual_source"
                    name = source + "#" + destination
                edge_dict[name] = pydot.Edge(source, destination)

    res_graph = pydot.Dot(graph_type=graph.get_type())
    for i in nodes_dict.values():
        res_graph.add_node(i)
    for i in edge_dict.values():
        res_graph.add_edge(i)

    return res_graph
