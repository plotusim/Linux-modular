import os
from utils.read_file import read_dot


def init_info():
    return {"SUBGRAPH": 0, "INTERFACE": 0, "DELETE": 0, "NORMAL": 0}


def dfs(result_folder, depth):
    if os.path.exists(os.path.join(result_folder, "res.dot")):
        info = get_info_from_result_dot(os.path.join(result_folder, "res.dot"))
        print_info(result_folder, info)

    if depth < 3:
        subdirectories = [entry.name for entry in os.scandir(result_folder) if entry.is_dir()]
        for subdirectory in subdirectories:
            dfs(os.path.join(result_folder, subdirectory), depth + 1)


def get_info_from_result_dot(dotfile):
    graph = read_dot(dotfile)
    res_info = init_info()
    for subgraph in graph.get_subgraphs():
        res_info["SUBGRAPH"] += 1
        for node in subgraph.get_nodes():
            if node.get_name() == "\"\\n\"":
                continue
            else:
                res_info[node.get("recommendation")] += 1

    return res_info


def print_info(subs_name, res_info):
    print(subs_name)
    for key, value in res_info.items():
        print(key + ":\t" + str(value))


def get_info_from_folder(result_folder):
    subdirectories = [entry.name for entry in os.scandir(result_folder) if entry.is_dir()]
    all_func_count = init_info()
    for subdirectory in subdirectories:
        print(subdirectory)
        dot_file = os.path.join(result_folder, subdirectory, "res.dot")
        if os.path.exists(dot_file):
            func_count, subgraph_count = get_info_from_result_dot(dot_file)
            all_func_count["SUBGRAPH"] += subgraph_count
    print_info(result_folder, all_func_count)


if __name__ == '__main__':
    dfs("./result", 0)
