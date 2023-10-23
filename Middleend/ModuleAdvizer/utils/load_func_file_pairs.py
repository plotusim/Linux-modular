from config.data_path import func_file_pairs_cache, linux_whole_kernel_dot
import re
from utils.cache_util import get_obj_else_run_f, get_latest_modification_time


def load_func_file_pairs_from_scratch():
    funcs = {}
    pattern2 = r'(.*?)\s*\[file=\"(.*?)\"(.*?)\]'
    with open(linux_whole_kernel_dot, 'r') as file:
        for line in file:
            match = re.search(pattern2, line)
            if match:
                part1 = match.group(1)
                part2 = match.group(2)
                funcs[part1] = part2
    return funcs


def get_func_file_pairs():
    dot_file_mtime = get_latest_modification_time(linux_whole_kernel_dot)
    return get_obj_else_run_f(func_file_pairs_cache, load_func_file_pairs_from_scratch, dot_file_mtime)
