from utils.read_file import read_dot
from transform import set_color
from config.color_mapping import recommendation_color_mapping
import pydot
import argparse
from collections import defaultdict


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, u):
        if u != self.parent.setdefault(u, u):
            self.parent[u] = self.find(self.parent[u])
        return self.parent[u]

    def union(self, u, v):
        u = self.find(u)
        v = self.find(v)
        if u == v:
            return
        self.parent[u] = v

    def subgraphs(self):
        subgraphs_dict = defaultdict(list)
        for i in self.parent.keys():
            subgraphs_dict[self.find(i)].append(i)
        subgraphs = list(subgraphs_dict.values())
        subgraphs.sort(key=lambda x: len(x), reverse=True)
        return subgraphs


def partition(origin_graph: pydot.Graph):
    uf = UnionFind()

    for i in origin_graph.get_nodes():
        uf.union(i.get_name(), i.get_name())

    for i in origin_graph.get_edges():
        uf.union(i.get_source(), i.get_destination())

    graph = pydot.Dot(graph_type="digraph")

    index = 0
    for subgraph in uf.subgraphs():
        # 创建第一个子图 (Cluster)
        cluster = pydot.Cluster(graph_name="cluster_" + str(index), label="Subgraph " + str(index))
        index += 1
        for node_name in subgraph:
            node = origin_graph.get_node(node_name)
            cluster.add_node(node[0])
        graph.add_subgraph(cluster)

    for edge in origin_graph.get_edges():
        graph.add_edge(edge)

    return graph


def main(graph, output):
    res = partition(graph)
    for cluster in res.get_subgraphs():
        set_color(cluster, recommendation_color_mapping, attribute="recommendation")
    res.write(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Partition a graph according to connectivity.')
    parser.add_argument('-i', '--input_dot', help='Path to the DOT file', required=True)
    parser.add_argument('-o', '--output', help='Output filename', required=True)

    args = parser.parse_args()

    main(read_dot(args.input_dot), args.output)
