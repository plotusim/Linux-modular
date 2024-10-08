import subprocess
import logging
import os
from config import update_config

# 配置常量
DOTS_ROOT_FOLDER = "../../Middleend/result/"
LOG_DIRECTORY = 'logs'
ALL_MODULE_FILE = '../../Data/module_list/all_module_list.txt'
ZEOS_MODULE_FILE = '../../Data/module_list/zero_module_list.txt'


def read_files(list_file):
    res = set()
    try:
        with open(list_file, 'r') as file:
            lines = file.readlines()
            for i in lines:
                i = i.strip()
                if len(i) > 0:
                    res.add(i)
    except FileNotFoundError:
        print(f"File '{list_file}' not found. Returning an empty set.")

    return res


def setup_logger(subsystem):
    """
    配置和获取日志记录器。

    :param subsystem: 子系统的名称，将用于日志文件的命名。
    :return: 配置后的日志记录器。
    """
    logger = logging.getLogger(subsystem)
    logger.setLevel(logging.DEBUG)

    if not os.path.exists(LOG_DIRECTORY):
        os.makedirs(LOG_DIRECTORY)

    log_file = os.path.join(LOG_DIRECTORY, f"{subsystem}.log")
    if not os.path.exists(os.path.dirname(log_file)):
        os.makedirs(os.path.dirname(log_file))

    handler = logging.FileHandler(log_file, 'w')
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    return logger


def run():
    all_modules = read_files(ALL_MODULE_FILE)
    zero_modules = read_files(ZEOS_MODULE_FILE)

    work_set = all_modules.difference(zero_modules)
    print("Need TO run the following")
    for i in work_set:
        print(i)

    for i in work_set:
        run_auto_backend(i)


def clean_kernel_folder(dir_path):
    """
    清理和准备内核文件夹。

    :param dir_path: 内核文件夹的路径。
    """
    subprocess.run(["rm", "-rf", dir_path], check=True)  # 检查命令是否引发异常
    subprocess.run(["cp", "-r", "/home/plot/Linux-modular/cjh/test/linux-5.10.176/", dir_path], check=True)


def run_auto_backend(subsystem):
    """
    运行后端自动化流程。

    :param subsystem: 要处理的子系统。
    """
    print(f"Autobackend:\t{subsystem}")
    logger = setup_logger(subsystem)

    dir_path = "../test_hn/"
    clean_kernel_folder(dir_path)

    # 更新配置
    update_config(
        file_path="./config.ini",
        section="MODULE",
        key_value_pairs={
            "ModuleName": subsystem.replace("/", "_"),
            "ResGraphDotPath": DOTS_ROOT_FOLDER + f"{subsystem}/res.dot",
        }
    )

    # 启动后端流程
    execute_command(["python", "main.py"], ".", logger)
    execute_command(["make", "defconfig"], dir_path, logger)
    execute_command(["make", "-j", "24"], dir_path, logger)


def execute_command(command, cwd, logger):
    """
    执行命令并记录输出。

    :param command: 要执行的命令列表。
    :param cwd: 命令的工作目录。
    :param logger: 用于记录输出的日志记录器。
    """
    try:
        completed_process = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True  # 如果命令失败，立即引发异常
        )

        logger.info(f"Stdout:\n{completed_process.stdout}")
        logger.info(f"Stderr:\n{completed_process.stderr}")

    except subprocess.CalledProcessError as e:
        logger.error(f"Return code:\n{e.returncode}")
        logger.error(f"Output:\n{e.output}")
        logger.error(f"Stderr:\n{e.stderr}")


def find_string_in_logs(directory):
    auto_ok_num = 0
    make_ok_num = 0
    all_num = 0
    results = []
    # 遍历指定目录下的所有文件
    for foldername, _, filenames in os.walk(directory):
        for filename in filenames:
            # 检查文件扩展名是否为 .log
            if filename.endswith('.log'):
                # 拼接文件路径
                file_path = os.path.join(foldername, filename)

                try:
                    # 以只读方式打开文件
                    with open(file_path, 'r') as file:
                        # 读取文件内容
                        content = file.read()
                        all_num += 1

                        if "Found global vars but not handled" in content:
                            auto_ok_num += 1

                            # print(f"'Found global vars but not handled'  found in {file_path}")

                            # 搜索特定字符串
                            if 'Kernel: arch/x86/boot/bzImage is ready' in content:
                                # print(f"'Kernel: arch/x86/boot/bzImage is ready' found in {file_path}")
                                results.append(file_path)
                                make_ok_num += 1
                        else:
                            print(f"Auto not ok {file_path}")

                except Exception as e:
                    print(f"An error occurred while reading {file_path}: {str(e)}")
    print(f"There are {all_num}")
    print(f"auto_ok  {auto_ok_num}")
    print(f"make_ok_num  {make_ok_num}")
    return results


def result_aggregation():
    # 使用函数
    directory_path = LOG_DIRECTORY
    result = find_string_in_logs(directory_path)
    for i in result:
        print(i[len(LOG_DIRECTORY) + 1:-4])

    print()


if __name__ == '__main__':
    # run()
    result_aggregation()
