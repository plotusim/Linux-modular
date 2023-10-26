import os
import re

from utils.file_utils import append_string_to_file, insert_before_keyword
from config import config


def generate_ifndef_macro(header):
    # 创建一个合法的宏名称，替换掉非字母数字字符如 . 和 /
    macro = re.sub(r'[^a-zA-Z0-9]', '_', header).upper()
    ifndef_string = f"#ifndef MY_MODULE_{macro}\n" \
                    f"#define MY_MODULE_{macro}\n" \
                    f"#include <{header}>\n" \
                    f"#endif\n"
    return ifndef_string


# 使用正则表达式识别2种#include语句
def extract_includes(filename):
    angle_bracket_pattern = re.compile(r'#include <(.*?)>')
    quote_pattern = re.compile(r'#include "(.*?)"')

    angle_bracket_includes = []
    quote_includes = []

    with open(filename, 'r') as f:
        for line in f:
            angle_match = angle_bracket_pattern.match(line.strip())
            quote_match = quote_pattern.match(line.strip())

            if angle_match:
                if angle_match.group(1) not in angle_bracket_includes:
                    angle_bracket_includes.append(angle_match.group(1))
            elif quote_match:
                if quote_match.group(1) not in quote_includes:
                    quote_includes.append(quote_match.group(1))

    return angle_bracket_includes, quote_includes


def add_includes_from_src_to_dest(source_file, dest_file, source_dir):
    angle_includes, quote_includes = extract_includes(source_file)

    add_angle_includes(angle_includes, dest_file)

    add_quote_includes(quote_includes, source_dir, dest_file)

    append_string_to_file(dest_file, "#include \"unexport_symbol_dec.h\"")
    append_string_to_file(dest_file, "\n")
    return angle_includes, {source_dir: quote_includes}


def add_angle_includes(angle_includes, dest_file):
    for i in angle_includes:
        append_string_to_file(dest_file, generate_ifndef_macro(i))


def add_quote_includes(quote_includes, source_dir, dest_file):
    # #include "*"宏需要修改路径
    for i in quote_includes:
        path = os.path.join("../", source_dir, i)
        append_string_to_file(dest_file, generate_ifndef_macro(path))


# 给*_code.c文件添加include宏的入口函数,返回加入的angle_includes与quote_includes
def add_includes(file, module_dir):
    print("Add includes to " + file)
    source_file = config.kernel_source_root_path + file + '.c'
    file_name = file.split('/')[-1]
    source_dir = ""
    for i in file.split('/')[:-1]:
        source_dir = os.path.join(str(source_dir), i)

    dest_file = os.path.join(module_dir, file_name + "_code.c")
    return add_includes_from_src_to_dest(source_file, dest_file, source_dir)


# 给头文件添加#include <linux/module.h>的函数
def add_header_file_include_linux_module(header_file_path):
    print("Add includes to " + header_file_path)
    header_path = config.kernel_source_root_path + header_file_path
    insert_before_keyword(filename=header_path, keyword="#include", content_to_insert="#include <linux/module.h>")


def add_includes_to_jump_interface(angle_includes, quote_includes_pairs, mod_dir):
    print("Add includes to jmp_interface_header_file")
    jmp_interface_header_file = os.path.join(mod_dir, "jmp_interface.h")

    for i in angle_includes:
        insert_before_keyword(filename=jmp_interface_header_file, keyword="#define EXPORT_FUNC(",
                              content_to_insert=generate_ifndef_macro(i))

    for source_dir, quote_includes in quote_includes_pairs.items():
        for i in quote_includes:
            path = os.path.join("../", source_dir, i)
            insert_before_keyword(filename=jmp_interface_header_file, keyword="#define EXPORT_FUNC(",
                                  content_to_insert=generate_ifndef_macro(path))


def add_unexport_symbol_header(angle_includes, quote_includes_pairs, mod_dir):
    print("Add includes to unexport_symbol_header")
    unexport_symbol_header_file = os.path.join(mod_dir, "unexport_symbol.h")
    add_angle_includes(angle_includes, unexport_symbol_header_file)
    for source_dir, quote_includes in quote_includes_pairs.items():
        add_quote_includes(quote_includes, source_dir, unexport_symbol_header_file)
