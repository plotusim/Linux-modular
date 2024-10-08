import os
import argparse

base_path = "../"

source_dir = os.path.join(base_path,'Frontend/Kernel_src')

opt_path = os.path.join(base_path,"Frontend/llvm-project/prefix/bin/opt")

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


def IRToGraph(source_dir, target_dir, library_path):
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
                if os.path.basename(library_path) == "ExtendedFuncGraph.so":
                    os.system(f'{opt_path} -load {library_path} -ExtendedFuncGraph {source_file_path} -enable-new-pm=0 > {target_file_path}')
                if os.path.basename(library_path) == "Steensggard.so":
                    os.system(f'{opt_path} -load {library_path} -Steensggard {source_file_path} -enable-new-pm=0 > {target_file_path}')

                # 由于输出有bug 结尾有乱码，需要稍微处理一下
                modify(target_file_path)


def main():
    parser = argparse.ArgumentParser(description='Classify analyze .so')
    parser.add_argument('--so', help='llvm analyze pass', required=True)
    parser.add_argument('--output_txt', help='output directory path',required=True)
    args = parser.parse_args()

    IRToGraph(source_dir, args.output_txt, args.so)


if __name__=='__main__' :
    main()

