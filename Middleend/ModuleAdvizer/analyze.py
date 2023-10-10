import argparse
import os

from graph_ops.build_extern import build_external_edges
from graph_ops.origin_graph import get_origin_graph
from graph_ops.simplify_graph import simplify_graph


def parse_args():
    parser = argparse.ArgumentParser(
        description='Color the function nodes in the function call graph based on their reachability types.')

    parser.add_argument('--dot_file_folder', help='input dot_file folder', required=True)
    parser.add_argument('--output_folder',
                        help='where to store result', default="./")

    return parser.parse_args()


def run_analysis(dot_file_folder_path, output_folder_path):
    graph = get_origin_graph(dot_file_folder_path)
    output_path = os.path.join(output_folder_path, dot_file_folder_path.split("/")[-1])
    graph.write(output_path + "_origin" + "_v" + ".dot")
    print("build external edges")
    graph = build_external_edges(graph)
    graph = simplify_graph(graph)
    graph.write(output_path + "_extern_simplified" + "_v" + ".dot")
    return graph


if __name__ == '__main__':
    args = parse_args()
    dot_file_folder = args.dot_file_folder
    output_folder = args.output_folder
    run_analysis(dot_file_folder, output_folder)
