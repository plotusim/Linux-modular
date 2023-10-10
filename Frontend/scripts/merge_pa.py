import os
import pydot
from collections import defaultdict

base_path = "../"
dot_pa_dir = os.path.join(base_path, "Data/dots_pa")
dot_dir = os.path.join(base_path, "Data/dots")
merged_dot_dir = os.path.join(base_path, "Data/dots_merge")

func_use_gv = defaultdict(set)
gv_use_func = defaultdict(set)
reversed_func_use_gv = defaultdict(set)


class UnionFind:
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, u):
        if u != self.parent.setdefault(u, u):
            self.parent[u] = self.find(self.parent[u])  # 路径压缩
        return self.parent[u]

    def union(self, u, v):
        u = self.find(u)
        v = self.find(v)
        if u == v:
            return

        if self.rank.setdefault(u, 0) < self.rank.setdefault(v, 0):
            u, v = v, u

        self.parent[v] = u

        if self.rank[u] == self.rank[v]:
            self.rank[u] += 1


uf = UnionFind()


def get_gv_use_func(gv_name):
    return gv_use_func[uf.find(gv_name)]


def add_gv_use_func(gv_name, func_name):
    get_gv_use_func(gv_name).add(func_name)


def union_gvs(gv_a, gv_b):
    union_funcs = get_gv_use_func(gv_a).union(get_gv_use_func(gv_b))
    uf.union(gv_a, gv_b)
    get_gv_use_func(gv_a).union(union_funcs)


def add_func_to_gv(func_name, gv_name):
    func_use_gv[func_name].add(uf.find(gv_name))


def is_global(name):
    return name.startswith("__virtual__global__") or name.startswith("\"__virtual__global__")


def gen_use_relation():
    all_dot_pa_graph_path = os.path.join(dot_pa_dir, "all.dot")
    graph = pydot.graph_from_dot_file(all_dot_pa_graph_path)[0]
    for i in graph.get_edges():
        sour = i.get_source()
        dest = i.get_destination()
        if is_global(sour):
            if is_global(dest):
                union_gvs(sour, dest)
            else:
                add_gv_use_func(sour, dest)
        else:
            if is_global(dest):
                add_func_to_gv(sour, dest)

    for key, value in func_use_gv.items():
        for v in value:
            reversed_func_use_gv[uf.find(v)].add(key)


def merge_pa_dots(relative_folder_path: str):
    print(f"begin merge:\t{relative_folder_path}")
    pa_folder_path = os.path.join(dot_pa_dir, relative_folder_path)
    origin_dot_folder_path = os.path.join(dot_dir, relative_folder_path)
    merged_dot_folder_path = os.path.join(merged_dot_dir, relative_folder_path)

    if not os.path.exists(merged_dot_folder_path):
        os.makedirs(merged_dot_folder_path)

    subdirectories = []
    files = []
    for item in os.listdir(origin_dot_folder_path):
        item_path = os.path.join(origin_dot_folder_path, item)
        if os.path.isdir(item_path):
            subdirectories.append(item)
        elif os.path.isfile(item_path):
            files.append(item)

    for filename in files:
        if filename.endswith("dot") and not filename.endswith("mod.dot") and not filename == "all.dot":
            print(f"merge:\t{os.path.join(relative_folder_path, filename)}")
            origin_dot = os.path.join(origin_dot_folder_path, filename)
            dot_graph = pydot.graph_from_dot_file(origin_dot)[0]

            pa_dot = os.path.join(pa_folder_path, filename)
            pa_dot_graph = pydot.graph_from_dot_file(pa_dot)[0]

            merged_dot_file = os.path.join(merged_dot_folder_path, filename)

            for edge in pa_dot_graph.get_edges():
                sour = edge.get_source()
                dest = edge.get_destination()
                if is_global(dest):
                    if is_global(sour):
                        continue
                    else:
                        for i in get_gv_use_func(dest):
                            if len(dot_graph.get_edge(sour, i)) == 0:
                                new_edge = pydot.Edge(sour, i)
                                dot_graph.add_edge(new_edge)
                else:
                    if is_global(sour):
                        for func in reversed_func_use_gv[uf.find(sour)]:
                            if len(dot_graph.get_node(func)):
                                if len(dot_graph.get_edge(func, dest)) == 0:
                                    new_edge = pydot.Edge(func, dest)
                                    dot_graph.add_edge(new_edge)

                    else:
                        if len(dot_graph.get_edge(sour, dest)) == 0:
                            dot_graph.add_edge(edge)

            dot_graph.write(merged_dot_file)

    for subdirectory in subdirectories:
        merge_pa_dots(os.path.join(relative_folder_path, subdirectory))


if __name__ == '__main__':
    print("gen_use_relation")
    gen_use_relation()
    print("begin merge")
    merge_pa_dots("")
