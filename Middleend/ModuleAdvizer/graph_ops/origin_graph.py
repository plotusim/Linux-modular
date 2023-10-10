import pydot

from config.color_mapping import function_color_mapping
from config.data_path import init_funcs_list_file, init_reach_funcs_list_file, export_funcs_list_file, \
    trace_funcs_list_file, virtual_structs_reach_funcs_list_file, virtual_structs_list_file, \
    virtual_structs_top_funcs_list_file, syscall_funcs_list_file, modular_funcs_list_file
from graph_ops.transform import set_nodes_type, set_color, set_default_type
from utils.merge_dots import combine_dots_from_folder_with_locations
from utils.read_file import read_funcs


def init_funcs_graph(graph):
    edges = graph.get_edges()

    init_func_list = read_funcs(init_funcs_list_file)
    init_reach_list = read_funcs(init_reach_funcs_list_file)
    export_func_list = read_funcs(export_funcs_list_file)
    virtual_structs_reach_level1_list = read_funcs(virtual_structs_top_funcs_list_file)
    trace_func_list = read_funcs(trace_funcs_list_file)
    virtual_structs_reach = read_funcs(virtual_structs_reach_funcs_list_file)
    syscall_func_list = read_funcs(syscall_funcs_list_file)
    virtual_structs_list = read_funcs(virtual_structs_list_file)
    modular_funcs_list = read_funcs(modular_funcs_list_file)

    graph = set_nodes_type(graph, {"INIT": init_func_list, "INIT_REACH": init_reach_list})

    graph = set_nodes_type(graph, {"SYSCALL": syscall_func_list, "TRACE": trace_func_list,
                                   "EXPORT": export_func_list, "MODULAR": modular_funcs_list,
                                   "VIRTUAL_STRUCTS": virtual_structs_list})
    graph = set_nodes_type(graph, {
        "VIRTUAL_STRUCTS_REACH_TOP": virtual_structs_reach_level1_list,
        "VIRTUAL_STRUCTS_REACH": virtual_structs_reach, })

    graph = set_default_type(graph)
    for edge in edges:
        endpoints = [edge.get_source(), edge.get_destination()]
        for i in endpoints:
            node_in_graph = graph.get_node(i)
            if len(node_in_graph) == 0:
                graph.add_node(pydot.Node(i, type="EXTERNAL"))

    graph.del_node("\"\\n\"")
    return graph


def get_origin_graph(dot_file_folder):
    print("generate graph")
    graph: pydot.Graph = combine_dots_from_folder_with_locations(dot_file_folder)
    graph = init_funcs_graph(graph)
    graph = set_color(graph, function_color_mapping, "type")
    return graph
