from utils.load_adjacency_list import get_whole_linux_kernel_reverse_adjacency_list
from utils.read_file import read_funcs
from config.data_path import trace_funcs_list_file
from config.data_path import syscall_funcs_list_file
from config.data_path import init_funcs_list_file
from config.data_path import use_syscall_trace_funcs_list_file


def find_use_trace_syscall():
    print("Finding funcs which use trace and syscall funcs")
    print("Reading kernel reverse adjacency list")
    reverse_adjacency_list = get_whole_linux_kernel_reverse_adjacency_list()
    print("Reading initial trace or syscall funcs set")
    work_set = read_funcs(trace_funcs_list_file)
    work_set = work_set.union(read_funcs(syscall_funcs_list_file))
    origin_set = work_set.copy()
    inline_set = read_funcs(init_funcs_list_file)

    temp_set = set()

    use_trace_syscall_funcs = set()

    while len(work_set):
        temp_set.clear()
        for i in work_set:
            for j in reverse_adjacency_list[i]:
                if j in use_trace_syscall_funcs or j in origin_set:
                    continue
                else:
                    print(f"Add func: {j}")
                    use_trace_syscall_funcs.add(j)
                if j in inline_set:
                    temp_set.add(j)              
        work_set = temp_set.copy()

    print("Completed finding use_trace_syscall_funcs, writing to file")
    with open(use_syscall_trace_funcs_list_file, 'w') as file:
        # Iterate through the list and write each item to the file, followed by a newline character
        for item in use_trace_syscall_funcs:
            file.write(f"{item}\n")


if __name__ == "__main__":
    find_use_trace_syscall()