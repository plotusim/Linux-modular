# Unused
def find_struct_definitions(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    definitions = []
    current_definition = ""
    brace_balance = 0  # 用于跟踪括号平衡
    inside_struct = False
    def_not_start = True
    for line in lines:
        stripped_line = line.strip()

        if 'struct' in stripped_line:
            inside_struct = True
            if stripped_line.count('{') == 0:
                current_definition += line  # 添加当前行
                continue
            else:
                def_not_start = False

        if inside_struct and def_not_start:
            current_definition += line  # 添加当前行
            if stripped_line.count('{') == 0:
                current_definition += line  # 添加当前行
                continue
            else:
                def_not_start = False

        # 如果我们在一个结构体定义内部
        if inside_struct:
            current_definition += line  # 添加当前行

            brace_balance += stripped_line.count('{')  # 遇到'{'则增加

            brace_balance -= stripped_line.count('}')  # 遇到'}'则减少

            # 如果括号平衡归零，表示结构体定义结束
            if brace_balance == 0:
                inside_struct = False  # 我们现在不再处于结构体内

                # 判断是否是typedef结构，是否需要提取别名
                if current_definition.strip().startswith('typedef'):
                    parts = stripped_line.split('}')
                    if len(parts) > 1 and parts[stripped_line.count('}')]:
                        alias = parts[stripped_line.count('}')].split(';')[0].strip().split(",")[0].strip()  # 提取别名
                        print(alias)
                        if len(alias):
                            current_definition += alias + ';\n'  # 添加别名
                        else:
                            print("alias is empty")

                definitions.append(current_definition)  # 保存定义
                current_definition = ""  # 重置当前定义

    return definitions

