import os
from config import kernel_source_root_path
from utils.file_utils import replace_specific_word_with, insert_before_keyword


def modify_drivers_kconfig(module_name):
    print("Modify Drivers Kconfig")
    drivers_kconfig_path = os.path.join(kernel_source_root_path, "drivers", "Kconfig")
    insert_before_keyword(drivers_kconfig_path, "endmenu", f"\nsource \"drivers/{module_name}/Kconfig\"")


def modify_kconfig(module_dir, module_name):
    print("Modify Module Kconfig")
    kconfig_path = os.path.join(module_dir, "Kconfig")
    replace_specific_word_with("SYS_MODULE", module_name.upper(), kconfig_path)
    modify_drivers_kconfig(module_name)
    pass
