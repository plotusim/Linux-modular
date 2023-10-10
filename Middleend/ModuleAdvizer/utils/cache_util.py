import pickle
import os


def load_cache(cache_filename):
    """尝试加载缓存，如果失败则返回None"""
    try:
        with open(cache_filename, 'rb') as cache_file:
            return pickle.load(cache_file)
    except (FileNotFoundError, pickle.UnpicklingError):
        return None


def save_obj_to_cache(obj, cache_filename):
    """将graph对象保存到缓存文件"""
    with open(cache_filename, 'wb') as cache_file:
        pickle.dump(obj, cache_file)


def get_obj_else_run_f(cache_file_path, f, last_modify_time):
    try:
        cache_file_mtime = os.path.getmtime(cache_file_path)
    except FileNotFoundError:
        cache_file_mtime = 0

    # 如果dot文件的修改时间比缓存文件的修改时间晚，或缓存文件不存在
    if last_modify_time > cache_file_mtime:
        obj = f()
        save_obj_to_cache(obj, cache_file_path)
    else:
        # 尝试从缓存中加载graph
        obj = load_cache(cache_file_path)

    return obj


def get_latest_modification_time(path):
    if not os.path.exists(path):
        return 0

    if os.path.isdir(path):
        # 如果是目录，则遍历目录下的所有文件和子目录
        latest_time = os.path.getmtime(path)
        for root, _, files in os.walk(path):
            for file in files:
                file_path = os.path.join(root, file)
                file_time = os.path.getmtime(file_path)
                if file_time > latest_time:
                    latest_time = file_time
        return latest_time
    elif os.path.isfile(path):
        # 如果是文件，则返回文件的修改时间
        return os.path.getmtime(path)
    else:
        # 无效路径
        return 0
