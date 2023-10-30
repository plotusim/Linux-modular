from config import config
from utils.func_utils import extract_source_location
from utils.file_utils import replace_with_empty_lines


# 处理需要删除的函数
def del_funcs(file, func_name):
    try:
        # 提取函数所定义的文件，起始位置、结束位置
        file, start_loc, end_loc, _ = extract_source_location(file, func_name)
        src_file_path = config.kernel_source_root_path + file
        # 使用空行替换
        replace_with_empty_lines(src_file_path, start_loc, end_loc)
        print("DELETE FUNC:\t" + func_name)
        return True
    except ValueError as e:
        print("Not DELETE FUNC:\t" + func_name)
        print(e)
        return False
    except RuntimeError as e:
        print("Not DELETE FUNC:\t" + func_name)
        print(e)
        return False
    except Exception as e:
        print(e)
        return False


def handle_delete_funcs(func_name, file_attribute, module_name, module_dir_path):
    del_funcs(file_attribute, func_name)


if __name__ == '__main__':
    del_funcs("/net/compat", "get_compat_msghdr")
