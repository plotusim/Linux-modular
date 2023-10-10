import pydot


def read_dot(filepath):
    input_graph = pydot.graph_from_dot_file(filepath)
    if len(input_graph) > 0:
        input_graph = input_graph[0]
        return input_graph
    else:
        print("No graph found in the DOT file.")
        return None


def read_funcs(func_list_file):
    funcs_list = set()
    try:
        with open(func_list_file, 'r') as file:
            funcs = file.readlines()
            for func in funcs:
                func = func.strip()
                if len(func) > 0:
                    funcs_list.add(func)
    except FileNotFoundError:
        print(f"File '{func_list_file}' not found. Returning an empty set.")

    return funcs_list
