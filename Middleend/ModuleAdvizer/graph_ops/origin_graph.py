import pydot

from config.color_mapping import function_color_mapping
from config.data_path import init_func_list_file, init_reach_func_list_file, export_func_list_file, \
    virtual_structs_reach_level1_list_file, all_virtual_structs_reach_funcs_list_file
from graph_ops.transform import set_nodes_type, set_color
from utils.merge_dots import combine_dots_from_folder_with_locations
from utils.read_file import read_funcs


def init_funcs_graph(graph):
    nodes = graph.get_nodes()
    edges = graph.get_edges()

    init_func_list = read_funcs(init_func_list_file)
    init_reach_list = read_funcs(init_reach_func_list_file)
    export_func_list = read_funcs(export_func_list_file)
    virtual_structs_reach_level1_list = read_funcs(virtual_structs_reach_level1_list_file)
    trace_func_list = get_trace_func_list(graph)
    virtual_structs_reach = read_funcs(all_virtual_structs_reach_funcs_list_file)
    virtual_structs_list = []
    for i in nodes:
        name = i.get_name()
        if name.endswith('__virtual_init'):
            virtual_structs_list.append(name)

    nodes_types = {"INIT": init_func_list, "INIT_REACH": init_reach_list, "EXPORT": export_func_list,
                   "VIRTUAL_STRUCTS_REACH_LEVEL_1": virtual_structs_reach_level1_list,
                   "VIRTUAL_STRUCTS_REACH": virtual_structs_reach, "TRACE": trace_func_list,
                   "VIRTUAL_STRUCTS": virtual_structs_list}
    graph = set_nodes_type(graph, nodes_types, "PLAIN")
    for edge in edges:
        endpoints = [edge.get_source(), edge.get_destination()]
        for i in endpoints:
            node_in_graph = graph.get_node(i)
            if len(node_in_graph) == 0:
                graph.add_node(pydot.Node(i, type="EXTERNAL"))

    graph.del_node("\"\\n\"")
    return graph


def get_trace_func_list(graph):
    res_list = []
    nodes = graph.get_nodes()
    for node in nodes:
        if "trace" in node.get_name().lower():
            res_list.append(node.get_name())
    return res_list


def get_origin_graph(dot_file_folder):
    print("generate graph")
    graph = combine_dots_from_folder_with_locations(dot_file_folder)
    graph = init_funcs_graph(graph)
    graph = set_color(graph, function_color_mapping, "type")
    return graph
