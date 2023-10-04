#!/bin/bash

## 生成.bc

## 生成.txt
# generate_txt_file="scripts/generate_txts.py"

# python "$generate_txt_file"

# if [ $? -ne 0 ]; then
#     echo "执行 $generate_txt_file 时出现错误"
#     exit 1
# fi

# echo "generate .txts exit"

# ## 生成.dot

# generate_dot_file="scripts/generate_dots.py"

# python "$generate_dot_file"

# if [ $? -ne 0 ]; then
#     echo "执行 $generate_dot_file 时出现错误"
#     exit 1
# fi

# echo "generate .dots exit"

# 合并dot文件

base_path="../"

combine_python_file="scripts/combine_dots.py"
input_file="${base_path}Data/dots"
output_file="${base_path}Data/dots/all.dot"

python "$combine_python_file" -i "$input_file" -o "$output_file"

if [ $? -ne 0 ]; then
    echo "执行 $python_file 时出现错误"
    exit 1
fi

echo "combine all dots to all.dot"


python_files=("scripts/classify_functions.py")
# 遍历文件列表并执行每个 Python 文件
for file in "${python_files[@]}"
do
    # 执行 Python 文件
    python "$file"

    # 检查上一次命令的返回值
    if [ $? -ne 0 ]; then
        echo "执行 $file 时出现错误"
        exit 1
    fi
done

echo "所有 Python 文件已成功执行"
