import pydot
import os

from config.data_path import dots_root_folder, init_funcs_list_file
from utils.load_adjacency_list import get_whole_linux_kernel_reverse_adjacency_list
from utils.merge_dots import combine_dots_from_folder_with_locations
from utils.read_file import read_funcs
from utils.cache_util import get_obj_else_run_f, get_latest_modification_time
from graph_ops.adjacency_list import reverse_directed_adjacency_list


def _get_subsystem_all_func(subsystem_name):
    dot_file = os.path.join(dots_root_folder, subsystem_name)
    graph = combine_dots_from_folder_with_locations(dot_file)
    func_set = set()
    for i in graph.get_nodes():
        func_name = i.get_name()
        func_set.add(func_name)
    return func_set


class SubsystemInfo:
    def __init__(self, subsystem_name):
        self.funcs_set = _get_subsystem_all_func(subsystem_name)
        self.init_funcs_set = self._get_subsystem_init_func()

    def _get_subsystem_init_func(self):
        init_func_list = read_funcs(init_funcs_list_file)
        init_funcs_set = set()
        for i in init_func_list:
            if i in self.funcs_set:
                init_funcs_set.add(i)

        return init_funcs_set

    def find_call_stack_of_func(self, func_name: str, depth: int):
        pa_all_dot = "/home/plot/lls_test_frontend/Data/dots_pa/all.dot"
        f = lambda: reverse_directed_adjacency_list(pydot.graph_from_dot_file(pa_all_dot)[0])
        reverse_adjacency_list = get_obj_else_run_f("./temp/pa_dots_all_dot.pkl", f, get_latest_modification_time(
            "/home/plot/lls_test_frontend/Data/dots_pa/all.dot"))

        res_graph = pydot.Graph()

        work_set = set()
        work_set.add(func_name)
        node_set = set()
        node_set.add(func_name)

        for i in range(depth):
            temp_set = set()
            for func in work_set:
                for caller in reverse_adjacency_list[func]:
                    if caller in node_set:
                        res_graph.add_edge(pydot.Edge(caller, func))
                    else:
                        node_set.add(caller)
                        temp_set.add(caller)
                        res_graph.add_edge(pydot.Edge(caller, func))
            work_set = temp_set

        for i in node_set:
            res_graph.add_node(pydot.Node(i))

        return res_graph.to_string()


if __name__ == '__main__':
    net_ipv6_info = SubsystemInfo("net/ipv6")
    for i in net_ipv6_info.init_funcs_set:
        print(net_ipv6_info.find_call_stack_of_func(i, 5))
