import pickle
from config.data_path import whole_kernel_dot_file, whole_kernel_graph_cache

import pydot


def load_graph_from_cache(cache_filename):
    """尝试从缓存中加载graph，如果失败则返回None"""
    try:
        with open(cache_filename, 'rb') as cache_file:
            return pickle.load(cache_file)
    except (FileNotFoundError, pickle.UnpicklingError):
        return None


def save_graph_to_cache(graph, cache_filename):
    """将graph对象保存到缓存文件"""
    with open(cache_filename, 'wb') as cache_file:
        pickle.dump(graph, cache_file)


def get_graph(dot_filename, cache_filename):
    """从缓存或dot文件中获取graph"""

    # 尝试从缓存中加载graph
    graph = load_graph_from_cache(cache_filename)

    # 如果没有从缓存中获取到graph
    if graph is None:
        # 从dot文件中读取graph
        with open(dot_filename, 'r') as file:
            graph_data = file.read()
        graph, = pydot.graph_from_dot_data(graph_data)

        # 将graph保存到缓存
        save_graph_to_cache(graph, cache_filename)

    return graph


def get_whole_linux_graph():
    return get_graph(whole_kernel_dot_file, whole_kernel_graph_cache)
