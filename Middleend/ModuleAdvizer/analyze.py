import argparse
import os

from graph_ops.build_extern import build_external_edges
from graph_ops.origin_graph import get_origin_graph
from graph_ops.simplify_graph import simplify_graph
from graph_ops.recommendation import generate_recommendation_info
from graph_ops.partition_graph import partition


def parse_args():
    parser = argparse.ArgumentParser(
        description='Color the function nodes in the function call graph based on their reachability types.')

    parser.add_argument('-i', '--dot_file_folder', help='input dot_file folder', required=True)
    parser.add_argument('-o', '--output_folder',
                        help='where to store result', default="./")
    parser.add_argument('-v', '--verbose', help='store intermediate result files', action="store_true")

    return parser.parse_args()


def run_analysis(dot_file_folder_path, temp_folder="./", verbose=False):
    graph = get_origin_graph(dot_file_folder_path)
    if verbose:
        output_path = os.path.join(temp_folder, dot_file_folder_path.split("/")[-1])
        graph.write(output_path + "_origin" + ".dot")
    graph = build_external_edges(graph)
    graph = simplify_graph(graph)
    if verbose:
        output_path = os.path.join(temp_folder, dot_file_folder_path.split("/")[-1])
        graph.write(output_path + "_extern_simplified" + ".dot")
    graph = generate_recommendation_info(graph)
    graph = partition(graph)
    return graph


if __name__ == '__main__':
    args = parse_args()
    dot_file_folder = args.dot_file_folder
    output_folder = args.output_folder
    run_analysis(dot_file_folder, output_folder, args.verbose).write(os.path.join(output_folder, "res.dot"))
