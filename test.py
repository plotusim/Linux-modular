import os

# 获取当前工作路径并打印
print(os.getcwd())

# 获取当前脚本文件所在目录的上一级目录
parent_dir = os.path.dirname(os.path.abspath(__file__)) + '/../linux-5.10.156-1/drivers/user'
now_dir = os.path.join(os.getcwd(),'src')

# 使用上一级目录执行其它操作，例如列出其下所有文件
for file in os.listdir(now_dir):
    print(file)
