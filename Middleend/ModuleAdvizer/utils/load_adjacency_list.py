import os

import pydot

from config.data_path import linux_whole_kernel_dot, \
    kernel_graph_reverse_adjacency_list_cache, kernel_graph_adjacency_list_cache
from graph_ops.adjacency_list import reverse_directed_adjacency_list, directed_adjacency_list
from utils.cache_util import get_obj_else_run_f


def get_whole_linux_kernel_reverse_adjacency_list():
    dot_file_mtime = os.path.getmtime(linux_whole_kernel_dot)

    f = lambda: reverse_directed_adjacency_list(pydot.graph_from_dot_file(linux_whole_kernel_dot)[0])

    return get_obj_else_run_f(kernel_graph_reverse_adjacency_list_cache, f, dot_file_mtime)


def get_whole_linux_kernel_adjacency_list():
    dot_file_mtime = os.path.getmtime(linux_whole_kernel_dot)

    f = lambda: directed_adjacency_list(pydot.graph_from_dot_file(linux_whole_kernel_dot)[0])

    return get_obj_else_run_f(kernel_graph_adjacency_list_cache, f, dot_file_mtime)




def get_pa_reverse_adjacency_list():
    dot_file_mtime = os.path.getmtime(linux_whole_kernel_dot)

    f = lambda: reverse_directed_adjacency_list(pydot.graph_from_dot_file(linux_whole_kernel_dot)[0])

    return get_obj_else_run_f(kernel_graph_reverse_adjacency_list_cache, f, dot_file_mtime)