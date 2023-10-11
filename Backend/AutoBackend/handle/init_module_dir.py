import os
from config import drivers_dir_path, module_template_files_dir
import shutil


# 把src_dir目录下的所有东西复制到dest_dir里面
def copy_files_from_src_to_dest(src_dir, dest_dir):
    # Ensure the destination directory exists, if not, create it
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    # Iterate over all files in the source directory
    for filename in os.listdir(src_dir):
        file_path = os.path.join(src_dir, filename)

        # Check if it's a file (not a directory)
        if os.path.isfile(file_path):
            shutil.copy(file_path, dest_dir)
            print(f"Copied {filename} to {dest_dir}")


# 在path下创建一个目录dir_name
def create_directory(path, dir_name):
    # Combine the path and directory name to form the full directory path
    full_dir_path = os.path.join(path, dir_name)

    # Check if the directory already exists
    if not os.path.exists(full_dir_path):
        os.makedirs(full_dir_path)
        print(f"Directory '{full_dir_path}' created successfully!")
    else:
        print(f"Directory '{full_dir_path}' already exists!")
    return full_dir_path


def create_module_dir(module_name):
    return create_directory(drivers_dir_path, module_name)


def add_template_files(module_dir):
    copy_files_from_src_to_dest(module_template_files_dir, module_dir)


def init_module_dir(module_name):
    module_dir_path = create_module_dir(module_name)
    add_template_files(module_dir_path)
    return module_dir_path


if __name__ == "__main__":
    init_module_dir("auth_gss_module")
