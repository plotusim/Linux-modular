import argparse

import os
from analyze import run_analysis
from config.data_path import dots_root_folder


def dfs(relative_path, result_folder):
    print(relative_path)
    subdirectories = [entry.name for entry in os.scandir(os.path.join(dots_root_folder, relative_path)) if
                      entry.is_dir()]
    for i in subdirectories:
        if not os.path.exists(os.path.join(result_folder, relative_path, i)):
            os.mkdir(os.path.join(result_folder, relative_path, i))

    main(relative_path, result_folder)

    for i in subdirectories:
        dfs(os.path.join(relative_path, i), result_folder)
        pass


def run_all(result_folder):
    if not os.path.exists(result_folder):
        os.makedirs(result_folder)
    subdirectories = [entry.name for entry in os.scandir(dots_root_folder) if entry.is_dir()]
    for i in subdirectories:
        if not os.path.exists(os.path.join(result_folder, i)):
            os.mkdir(os.path.join(result_folder, i))
        dfs(i, result_folder)


def main(subsystem, result_folder):
    dot_file_folder = os.path.join(dots_root_folder, subsystem)
    temp_folder = os.path.join(result_folder, subsystem, "temp")
    if not os.path.exists(temp_folder):
        os.mkdir(temp_folder)
    graph = run_analysis(dot_file_folder, temp_folder, True)
    graph.write(os.path.join(result_folder, subsystem, "res.dot"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Give recommendations to modular function')
    parser.add_argument('-i', '--subsystem', help='subsystem')
    parser.add_argument('-o', '--result_folder', help='folder to store result', required=True)
    parser.add_argument('-a', '--all', help='run for all subsystem', action="store_true")
    args = parser.parse_args()
    if args.all:
        run_all(args.result_folder)
    else:
        main(args.subsystem, args.result_folder)
