#!/bin/bash

## Pre：编译.so
### 进入LLVM_PASS，编译.so
cd ./LLVM_PASS
make
cd ..

### 进入IRDumper，编译
cd ./IRDumper/
make
cd ..

## Pre：copy kernel src
source_dir="../Kernel"
target_dir="./Kernel_src"

cp -r "$source_dir"/* "$target_dir"

## 生成.bc
KERNEL_SRC="$(pwd)/Kernel_src"
IRDUMPER="$(pwd)/IRDumper/build/lib/libDumper.so"
CLANG="$(pwd)/llvm-project/prefix/bin/clang"
CONFIG="defconfig"


# Use -Wno-error to avoid turning warnings into errors
NEW_CMD="\n\n\
KBUILD_USERCFLAGS += -Wno-error -fno-inline -g -Xclang -no-opaque-pointers -Xclang -flegacy-pass-manager -Xclang -load -Xclang $IRDUMPER\nKBUILD_CFLAGS += -Wno-error -fno-inline -g -Xclang -no-opaque-pointers -Xclang -flegacy-pass-manager -Xclang -load -Xclang $IRDUMPER"



if [ ! -f "$KERNEL_SRC/Makefile.bak" ]; then
	# Back up Linux Makefile
	cp $KERNEL_SRC/Makefile $KERNEL_SRC/Makefile.bak
fi

# The new flags better follow "# Add user supplied CPPFLAGS, AFLAGS and CFLAGS as the last assignments"
echo -e $NEW_CMD >$KERNEL_SRC/IRDumper.cmd
cat $KERNEL_SRC/Makefile.bak $KERNEL_SRC/IRDumper.cmd >$KERNEL_SRC/Makefile

cd $KERNEL_SRC && make $CONFIG
echo $CLANG
echo $NEW_CMD
make CC=$CLANG -j`nproc` -k -i

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
