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
KBUILD_USERCFLAGS += -Wno-unused-parameter -Wno-sign-compare -Wno-error -fno-inline -g -Xclang -no-opaque-pointers -Xclang -flegacy-pass-manager -Xclang -load -Xclang $IRDUMPER\nKBUILD_CFLAGS += -Wno-unused-parameter -Wno-sign-compare -Wno-error -fno-inline -g -Xclang -no-opaque-pointers -Xclang -flegacy-pass-manager -Xclang -load -Xclang $IRDUMPER"

if [ ! -f "$KERNEL_SRC/Makefile.bak" ]; then
	# Back up Linux Makefile
	cp $KERNEL_SRC/Makefile $KERNEL_SRC/Makefile.bak
fi

# The new flags better follow "# Add user supplied CPPFLAGS, AFLAGS and CFLAGS as the last assignments"
echo -e $NEW_CMD >$KERNEL_SRC/IRDumper.cmd
cat $KERNEL_SRC/Makefile.bak $KERNEL_SRC/IRDumper.cmd >$KERNEL_SRC/Makefile

cd $KERNEL_SRC && make CC=$CLANG $CONFIG
echo $CLANG
echo $NEW_CMD
make CC=$CLANG -j`nproc` -k -i
cd ..

## 生成.txt
generate_txt_file="scripts/generate_txts.py"

### 函数分析
so_path="./LLVM_PASS/ExtendedFuncGraph.so"
txt_path="../Data/txts"

### 指针分析
pa_so_path="./LLVM_PASS/Steensggard.so"
pa_txt_path="../Data/txts_pa"

echo "generate txts start"

python "$generate_txt_file" --so "$so_path" --output_txt "$txt_path" &
python "$generate_txt_file" --so "$pa_so_path" --output_txt "$pa_txt_path"

echo "generate txts exit"


## 生成.dot
generate_dot_file="scripts/generate_dots.py"
generate_pa_dot_file="scripts/generate_dots_pa.py"

### 函数分析的dots
### 指针分析的dots

echo "generate dots start"

python "$generate_dot_file"  &
python "$generate_pa_dot_file" 

echo "generate dots exit"

base_path="../"

combine_python_file="scripts/combine_dots.py"
input_file="${base_path}Data/dots_pa"
output_file="${base_path}Data/dots_pa/all.dot"

if [ ! -f "$output_file" ]; then
    echo "combine dots_pa to all.dot"
    python "$combine_python_file" -i "$input_file" -o "$output_file"
    echo "combine dots_pa finish"
fi




## 生成增量dots
merge_dot_file="scripts/merge_pa.py"

echo "merge dots_pa to dots"

python "$merge_dot_file"

echo "merge finish"


## 合并dot文件到all.dot
base_path="../"

combine_python_file="scripts/combine_dots.py"
input_file="${base_path}Data/dots_merge"
output_file="${base_path}Data/dots_merge/all.dot"

python "$combine_python_file" -i "$input_file" -o "$output_file"

echo "combine merge dots to all.dot"


## 函数分类
classify_funcs_file="scripts/classify_functions.py"

python "$classify_funcs_file"

echo "classify functions finish"

echo "Frontend finished !"
