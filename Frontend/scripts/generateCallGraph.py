import os

source_dir = '/home/plot/clang-linux-5.10.176/linux-5.10.176/net/netlabel'
# ir_dir = '/home/plot/clang-linux-5.10.176/IRs'
#graph_dir = '/home/plot/clang-linux-5.10.176/graphsWithSuffix'
graph_dir = '/home/plot/clang-linux-5.10.176/testDef'
#library_path = '/home/plot/clang-linux-5.10.176/ExtendedCallGraph.so'
library_path = '/home/plot/clang-linux-5.10.176/ExtendedCallGraphDef.so'

# def cToIR(source_dir, target_dir):
#     # 遍历源文件夹下的所有文件和子文件夹
#     for root, dirs, files in os.walk(source_dir):
#         # 遍历所有文件
#         for file in files:
#             # 如果文件扩展名为 .c
#             if file.endswith('.c'):
#                 # 构造源文件路径和目标文件路径
#                 source_file_path = os.path.join(root, file)
#                 target_file_path = source_file_path.replace(source_dir, target_dir).replace('.c', '.ll')
                
#                 # 创建目标文件夹
#                 os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
                
#                 # 编译源文件
#                 os.system(f'clang -S -emit-llvm -O0 {source_file_path} -o {target_file_path}')

def modify(file_name):
    lines = []
    with open(file_name, 'r', encoding='utf-8', errors='ignore') as f:
        flag = False
        for line in f:
            #if '__virtual_init' in line:
              #  continue
            if '->' not in line:
                flag = True
            if not flag:
                lines.append(line)
            else:
                break

    with open(file_name, 'w', encoding='utf-8', errors='ignore') as f:
        f.truncate(0)

    with open(file_name, 'w') as f:
        f.writelines(lines)


def IRToGraph(source_dir, target_dir):
    # 遍历源文件夹下的所有文件和子文件夹
    for root, dirs, files in os.walk(source_dir):
        # 遍历所有文件
        for file in files:
            # 如果文件扩展名为 .c
            if file.endswith('.bc'):
                # 构造源文件路径和目标文件路径
                source_file_path = os.path.join(root, file)
                target_file_path = source_file_path.replace(source_dir, target_dir).replace('.bc', '.txt')
                
                # 创建目标文件夹
                os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
                
                # 编译源文件
                #os.system(f'opt -load {library_path} -ExtendedCallGraph {source_file_path} -enable-new-pm=0 > {target_file_path}')
                os.system(f'opt -load {library_path} -ExtendedFuncGraph {source_file_path} -enable-new-pm=0 > {target_file_path}')

                # 由于输出有bug 结尾有乱码，需要稍微处理一下
                modify(target_file_path)


def main():
    # cToIR(source_dir, ir_dir)
    IRToGraph(source_dir, graph_dir)


if __name__=='__main__' :
    main()

