#!/bin/bash

## 生成.bc

## 生成.txt
generate_txt_file="scripts/generate_txts.py"

### 函数分析
so_path="./LLVM_PASS/ExtendedFuncGraph.so"
txt_path="../Data/txts"
python "$generate_txt_file" --so "$so_path" --output_txt "$txt_path"

if [ $? -ne 0 ]; then
    echo "执行 $generate_txt_file 时出现错误 :$so_path"
    exit 1
fi

echo "generate txts exit :$txt_path"

### 指针分析
so_path="./LLVM_PASS/Steensggard.so"
txt_path="../Data/txts_pa"
python "$generate_txt_file" --so "$so_path" --output_txt "$txt_path"

if [ $? -ne 0 ]; then
    echo "执行 $generate_txt_file 时出现错误:$so_path"
    exit 1
fi

echo "generate txts exit :$txt_path"

## 生成.dot
generate_dot_file="scripts/generate_dots.py"

### 函数分析的dots
txt_path="../Data/txts"
dot_path="../Data/dots"

python "$generate_dot_file" --source_txt "$txt_path" --dot_path "$dot_path"

if [ $? -ne 0 ]; then
    echo "执行 $generate_dot_file 时出现错误 :$txt_path -> $dot_path"
    exit 1
fi

echo "generate $dot_path exit"

### 指针分析的dots
txt_path="../Data/txts_pa"
dot_path="../Data/dots_pa"

python "$generate_dot_file" --source_txt "$txt_path" --dot_path "$dot_path"

if [ $? -ne 0 ]; then
    echo "执行 $generate_dot_file 时出现错误 :$txt_path -> $dot_path"
    exit 1
fi

echo "generate $dot_path exit"


## 生成增量dots
merge_dot_file="scripts/merge_pa.py"

python "$merge_dot_file"

if [ $? -ne 0 ]; then
    echo "执行 $gmerge_dot_file 时出现错误"
    exit 1
fi

echo "merge dots_pa to dots"


## 合并dot文件到all.dot
base_path="../"

combine_python_file="scripts/combine_dots.py"
input_file="${base_path}Data/dots_merge"
output_file="${base_path}Data/dots_merge/all.dot"

python "$combine_python_file" -i "$input_file" -o "$output_file"

if [ $? -ne 0 ]; then
    echo "执行 $python_file 时出现错误"
    exit 1
fi

echo "combine all dots to all.dot"


## 函数分类
classify_funcs_file="scripts/classify_functions.py"

python "$classify_funcs_file"

# 检查上一次命令的返回值
if [ $? -ne 0 ]; then
    echo "执行 $classify_funcs_file 时出现错误"
    exit 1
fi

echo "classify functions finish"

echo "Frontend finished !"
