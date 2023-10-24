import pydot
from config import config
from utils.func_utils import extract_source_location


class Function:
    def __init__(self, handle_way, func_name, file_attribute):
        self.file_attribute = file_attribute
        self.handle_way = handle_way
        self.func_name = func_name
        self.real_file, self.start_loc, self.end_loc, _ = extract_source_location(file_attribute, func_name)


def read_res_graph(path=config.res_graph_dot_path):
    graph = pydot.graph_from_dot_file(path)[0]
    final_graph = pydot.Graph()
    if config.merge:
        for i in graph.get_subgraphs():
            for node in i.get_nodes():
                final_graph.add_node(node)

            for edge in i.get_edges():
                final_graph.add_edge(edge)
    return final_graph


def get_recommendation(graph: pydot.Graph):
    res = {}

    for node in graph.get_nodes():
        file = node.get("file")[1:-1]  # 去掉 ""
        handle_way = node.get("recommendation")
        name = node.get_name()
        function = Function(handle_way=handle_way, func_name=name, file_attribute=file)
        res.setdefault(function.real_file, []).append(function)

    return res


def get_res(path):
    print("Reading res graph")
    res_graph = read_res_graph(path=path)
    print("Generating Function objects")
    res = get_recommendation(res_graph)
    return res
