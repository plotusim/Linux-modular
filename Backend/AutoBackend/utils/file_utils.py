# 把content添加到filename文件后面
def append_string_to_file(filename, content):
    with open(filename, 'a') as f:
        f.write(content)
        # print(f"Appended content to {filename}")


# 在keyword出现的前一行插入content
def insert_before_keyword(filename, keyword, content_to_insert):
    # 打开文件并读取所有行
    with open(filename, 'r') as file:
        lines = file.readlines()

    # 查找包含关键词的行并在其前面插入内容
    for index, line in enumerate(lines):
        if keyword in line:
            lines.insert(index, content_to_insert + '\n')
            break

    # 将修改后的内容写回文件
    with open(filename, 'w') as file:
        file.writelines(lines)


# 在特定行插入content
def insert_content_to_file(file_path, line_number, content_to_insert):
    """
    Insert content to a file at the specified line number.

    :param file_path: Path to the file
    :param line_number: Line number to insert content before
    :param content_to_insert: Content to be inserted
    """
    with open(file_path, 'r') as file:
        lines = file.readlines()

    if line_number < 0:
        line_number = 0
    elif line_number > len(lines):
        while line_number > len(lines):
            lines.append("\n")
            line_number -= 1

    lines.insert(line_number - 1, content_to_insert + '\n')

    with open(file_path, 'w') as file:
        file.writelines(lines)


# 删除start到end行
def remove_lines(file_path, start, end):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Adjusting for 0-based index
    del lines[start - 1:end]

    with open(file_path, 'w') as file:
        file.writelines(lines)


# start到end行替换成空行
def replace_with_empty_lines(file_path, start, end):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Adjusting for 0-based index
    for i in range(start - 1, end):
        lines[i] = '\n'

    with open(file_path, 'w') as file:
        file.writelines(lines)


# 把文件中出现的old替换成new
def replace_specific_word_with(old, new, file_path):
    with open(file_path, 'r') as file:
        content = file.read()

    # 替换字符串
    new_content = content.replace(old, new)

    # 将新内容写回文件
    with open(file_path, 'w') as file:
        file.write(new_content)


# 在最后出现“#include”的行后添加content
def insert_after_last_include(filename, content_to_insert):
    # 打开文件并读取所有行
    with open(filename, 'r') as file:
        lines = file.readlines()

    # 反向查找最后一个包含 #include 的行
    for index in reversed(range(len(lines))):
        if '#include' in lines[index]:
            # 在找到的行之后插入内容
            lines.insert(index + 1, content_to_insert + '\n')
            break

    # 将修改后的内容写回文件
    with open(filename, 'w') as file:
        file.writelines(lines)


# 返回文件start到end行的内容
def extract_lines(file_path, start, end):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    return lines[start - 1:end]
