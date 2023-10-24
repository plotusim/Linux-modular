import os
from utils.read_file import read_dot
from config.data_path import zero_module_list_file

RES_PATH = "../new_result"
ALL_ZERO_MODULE = []


def is_all_zero(info):
    for key, value in info.items():
        if value != 0:
            return False
    return True


def init_info():
    return {"SUBGRAPH": 0, "INTERFACE": 0, "DELETE": 0, "NORMAL": 0}


def all_func_num(path):
    nums = init_info()
    subdirectories = [entry.name for entry in os.scandir(os.path.abspath(path)) if entry.is_dir()]
    for i in subdirectories:
        sub_path = os.path.join(path, i)
        if os.path.exists(os.path.join(sub_path, "res.dot")):
            info = get_info_from_result_dot(os.path.join(sub_path, "res.dot"))
            for key, value in info.items():
                nums[key] += value
    print("All func\n")
    print(nums)


def dfs(result_folder):
    if os.path.exists(os.path.join(result_folder, "res.dot")):
        info = get_info_from_result_dot(os.path.join(result_folder, "res.dot"))
        if is_all_zero(info):
            ALL_ZERO_MODULE.append(result_folder[len(RES_PATH) + 1:])
        print_info(result_folder, info)
        pass

    subdirectories = [entry.name for entry in os.scandir(result_folder) if
                      entry.is_dir() and not entry.name.endswith("temp")]
    for subdirectory in subdirectories:
        dfs(os.path.join(result_folder, subdirectory))


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
    dfs(RES_PATH)
    all_func_num(RES_PATH)
    print("Zero Module")

    for i in ALL_ZERO_MODULE:
        print(i)

    with open(zero_module_list_file, 'w') as file:
        # Iterate through the list and write each item to the file, followed by a newline character
        for item in ALL_ZERO_MODULE:
            file.write(f"{item}\n")
