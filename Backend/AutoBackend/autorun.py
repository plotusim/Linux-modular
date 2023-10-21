import subprocess
import logging
import os
from config import update_config

# 配置常量
DOTS_ROOT_FOLDER = "../../Middleend/result/"
LOG_DIRECTORY = 'logs'


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
    handler = logging.FileHandler(log_file, 'w')
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    return logger


def dfs(relative_path):
    """
    深度优先搜索遍历目录并处理子系统。

    :param relative_path: 相对路径，用于定位要处理的子系统。
    """
    print("DFS:\t{}".format(relative_path))

    if relative_path.endswith("temp"):
        return

    if not os.path.exists(os.path.join("./logs", relative_path)):
        os.mkdir(os.path.join("./logs", relative_path))

    subdirectories = [entry.name for entry in os.scandir(DOTS_ROOT_FOLDER + relative_path) if
                      entry.is_dir()]

    for i in subdirectories:
        dfs(os.path.join(relative_path, i))
        pass

    if relative_path != "":
        run_auto_backend(relative_path)


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
            "ResGraphDotPath": f"Middleend/result/{subsystem}/res.dot",
        }
    )

    # 启动后端流程
    execute_command(["python", "main.py"], ".", logger)
    execute_command(["make", "defconfig"], dir_path, logger)
    execute_command(["make", "-j", "32"], dir_path, logger)


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


def find_string_in_logs(directory, search_string):
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

                        # 搜索特定字符串
                        if search_string in content:
                            print(f"'{search_string}' found in {file_path}")
                            results.append(file_path)
                except Exception as e:
                    print(f"An error occurred while reading {file_path}: {str(e)}")

    # 如果循环完成后没有找到，则返回 False
    print(f"'{search_string}' was not found in any .log files in {directory}")
    return results


def result_aggregation():
    # 使用函数
    directory_path = LOG_DIRECTORY
    specific_string = 'Kernel: arch/x86/boot/bzImage is ready'

    result = find_string_in_logs(directory_path, specific_string)
    for i in result:
        print(i[4:-4])


if __name__ == '__main__':
    result_aggregation()
