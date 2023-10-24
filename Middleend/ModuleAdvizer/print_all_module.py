from config.data_path import dots_root_folder, all_module_list_file
import os

ALL_MODULE_LIST = []


def dfs(relative_path):
    result_folder = os.path.join(str(dots_root_folder), relative_path)

    files = [entry.name for entry in os.scandir(result_folder) if entry.name.endswith(".dot")]

    subdirectories = [entry.name for entry in os.scandir(result_folder) if
                      entry.is_dir()]
    for subdirectory in subdirectories:
        dfs(os.path.join(relative_path, subdirectory))
    if len(files) > 0:
        print(relative_path)
        ALL_MODULE_LIST.append(relative_path)


if __name__ == '__main__':
    dfs('')
    with open(all_module_list_file, 'w') as file:
        # Iterate through the list and write each item to the file, followed by a newline character
        for item in ALL_MODULE_LIST:
            file.write(f"{item}\n")
