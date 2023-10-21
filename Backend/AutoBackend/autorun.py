import subprocess
import logging
import os
from config import update_config

dots_root_folder = "../../Middleend/result/"


def setup_logger(subsystem):
    # 创建 logger
    logger = logging.getLogger(subsystem)
    logger.setLevel(logging.DEBUG)

    # 创建文件处理程序并设置级别为 debug
    log_directory = 'logs'
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)
    log_file = os.path.join(log_directory, f"{subsystem}.log")
    handler = logging.FileHandler(log_file, 'w')
    handler.setLevel(logging.DEBUG)

    # 创建 formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 添加 formatter 到 handler
    handler.setFormatter(formatter)

    # 添加 handler 到 logger
    logger.addHandler(handler)

    return logger


def dfs(relative_path):
    print("DFS:\t{}".format(relative_path))

    if relative_path.endswith("temp"):
        return

    if not os.path.exists(os.path.join("./logs", relative_path)):
        os.mkdir(os.path.join("./logs", relative_path))

    subdirectories = [entry.name for entry in os.scandir(dots_root_folder + relative_path) if
                      entry.is_dir()]

    for i in subdirectories:
        dfs(os.path.join(relative_path, i))
        pass

    if relative_path != "":
        run_auto_backend(relative_path)


def clean_kernel_folder(dir_path):
    completed_process = subprocess.run(
        ["rm -rf  " + dir_path],
        cwd=".",
        stdout=subprocess.PIPE,  # 捕获输出
        stderr=subprocess.PIPE,  # 捕获错误
        text=True,  # 让 subprocess 处理字符串而非字节
        shell=True,
    )

    completed_process = subprocess.run(
        ["cp -r /home/plot/Linux-modular/cjh/test/linux-5.10.176/  " + dir_path],
        cwd=".",
        stdout=subprocess.PIPE,  # 捕获输出
        stderr=subprocess.PIPE,  # 捕获错误
        text=True,  # 让 subprocess 处理字符串而非字节
        shell=True,
    )


def run_auto_backend(subsystem):
    print("Autobackend:\t{}".format(subsystem))
    logger = setup_logger(subsystem)

    dir_path = "../test_hn/"
    clean_kernel_folder(dir_path)

    update_config(file_path="./config.ini", section="MODULE",
                  key_value_pairs={
                      "ModuleName": subsystem.replace("/", "_"),
                      "ResGraphDotPath": "Middleend/result/" + subsystem + "/res.dot",
                  })

    try:
        completed_process = subprocess.run(
            ["python main.py"],
            cwd=".",
            input="",  # 传递给 grep 的输入
            stdout=subprocess.PIPE,  # 捕获输出
            stderr=subprocess.PIPE,  # 捕获错误
            text=True,  # 让 subprocess 处理字符串而非字节
            shell=True,
        )

        logger.info("Stdout:\n{}".format(completed_process.stdout))
        logger.info("Stderr:\n {}".format(completed_process.stderr))


    except subprocess.CalledProcessError as e:
        logger.error("Return code:\n  {}".format(e.returncode))
        logger.error("Output:\n {}".format(e.output))
        logger.error("Stderr:\n {}".format(e.stderr))
        return

    try:
        completed_process = subprocess.run(
            ["make defconfig"],
            cwd=dir_path,
            input="",  # 传递给 grep 的输入
            stdout=subprocess.PIPE,  # 捕获输出
            stderr=subprocess.PIPE,  # 捕获错误
            text=True,  # 让 subprocess 处理字符串而非字节
            shell=True,
        )

        logger.info("Stdout:\n{}".format(completed_process.stdout))
        logger.info("Stderr:\n {}".format(completed_process.stderr))


    except subprocess.CalledProcessError as e:
        logger.error("Return code:\n  {}".format(e.returncode))
        logger.error("Output:\n {}".format(e.output))
        logger.error("Stderr:\n {}".format(e.stderr))
        return

    try:
        completed_process = subprocess.run(
            ["make -j 32"],
            cwd=dir_path,
            input="",  # 传递给 grep 的输入
            stdout=subprocess.PIPE,  # 捕获输出
            stderr=subprocess.PIPE,  # 捕获错误
            text=True,  # 让 subprocess 处理字符串而非字节
            shell=True,
        )

        logger.info("Stdout:\n{}".format(completed_process.stdout))
        logger.info("Stderr:\n {}".format(completed_process.stderr))


    except subprocess.CalledProcessError as e:
        logger.error("Return code:\n  {}".format(e.returncode))
        logger.error("Output:\n {}".format(e.output))
        logger.error("Stderr:\n {}".format(e.stderr))
        return


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
    directory_path = 'logs'  # 请将此路径替换为你的日志文件所在的目录
    specific_string = 'Kernel: arch/x86/boot/bzImage is ready'  # 你要搜索的字符串

    result = find_string_in_logs(directory_path, specific_string)
    for i in result:
        print(i[4:-4])


if __name__ == '__main__':
    result_aggregation()
