from graph_ops.adjacency_list import directed_adjacency_list
from utils.read_file import read_funcs
from utils.load_graph import get_whole_linux_graph
from config.data_path import init_func_list_file, init_reach_func_list_file, \
    virtual_structs_reach_level1_list_file, all_virtual_structs_reach_funcs_list_file


def get_successors(adjacency_lists, func_list):
    res = set()
    work_set = []
    for i in func_list:
        for j in adjacency_lists[i]:
            if j not in func_list:
                work_set.append(j)

    while len(work_set) > 0:
        func = work_set.pop()
        if func not in res:
            if func not in func_list:
                res.add(func)
                for i in adjacency_lists[func]:
                    work_set.append(i)
    return res


def analyze_init_reach():
    init_funcs = set()
    graph = get_whole_linux_graph()
    adjacency_lists = directed_adjacency_list(graph=graph)
    if init_func_list_file is not None:
        # find init functions
        init_func_list = read_funcs(init_func_list_file)
        for node in graph.get_nodes():
            name = node.get_name()
            if name in init_func_list:
                init_funcs.add(name)

        # find init_reach funcs:
        init_reach_funcs = get_successors(adjacency_lists, init_func_list)

        output_filename = init_reach_func_list_file

        with open(output_filename, "w") as f:
            for func in init_reach_funcs:
                f.write(func + "\n")


def analyze_virtual_structs_reach_level1():
    virtual_structs = set()
    virtual_structs_reach_level1 = set()
    graph = get_whole_linux_graph()

    for i in graph.get_nodes():
        name: str = i.get_name()
        if name.endswith('__virtual_init'):
            virtual_structs.add(name)

    adjacency_lists = directed_adjacency_list(graph=graph)

    for i in virtual_structs:
        for next_func in adjacency_lists[i]:
            virtual_structs_reach_level1.add(next_func)

    output_filename = virtual_structs_reach_level1_list_file

    with open(output_filename, "w") as f:
        for func in virtual_structs_reach_level1:
            f.write(func + "\n")


def analyze_all_virtual_structs_reach_funcs():
    virtual_structs = set()
    graph = get_whole_linux_graph()

    for i in graph.get_nodes():
        name: str = i.get_name()
        if name.endswith('__virtual_init'):
            virtual_structs.add(name)

    adjacency_lists = directed_adjacency_list(graph=graph)
    all_virtual_structs_reach_funcs = get_successors(adjacency_lists, virtual_structs)
    output_filename = all_virtual_structs_reach_funcs_list_file

    with open(output_filename, "w") as f:
        for func in all_virtual_structs_reach_funcs:
            f.write(func + "\n")


if __name__ == "__main__":
    analyze_init_reach()
    analyze_virtual_structs_reach_level1()
    analyze_all_virtual_structs_reach_funcs()
