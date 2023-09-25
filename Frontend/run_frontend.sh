#!/bin/bash

# 定义要执行的 Python 文件列表
# python_files=("scripts/generatedCallGraph.py" "scripts/CallGraphToDotWithSuffix.py" "scripts/classify_functions.py")

base_path="../"

# 合并dot文件
combine_python_file="scripts/combine_dots.py"
input_file="${base_path}Data/dots"
output_file="${base_path}Data/dots/all.dot"

python "$combine_python_file" -i "$input_file" -o "$output_file"

if [ $? -ne 0 ]; then
    echo "执行 $python_file 时出现错误"
    exit 1
fi

echo "已合并内核dot文件"


# python_files=("scripts/classify_functions.py")
# # 遍历文件列表并执行每个 Python 文件
# for file in "${python_files[@]}"
# do
#     # 执行 Python 文件
#     python "$file"

#     # 检查上一次命令的返回值
#     if [ $? -ne 0 ]; then
#         echo "执行 $file 时出现错误"
#         exit 1
#     fi
# done

# echo "所有 Python 文件已成功执行"

