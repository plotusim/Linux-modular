import os
from config import kernel_source_root_path
from utils.file_utils import append_string_to_file


def modify_drivers_makefile(module_name):
    print("Modify Drivers Makefile")
    drivers_makefile_path = os.path.join(kernel_source_root_path, "drivers", "Makefile")
    append_string_to_file(drivers_makefile_path, f"\nobj-$(CONFIG_{module_name.upper()})		+= {module_name}/\n")


def modify_makefile(module_name: str, files_name, module_dir):
    print("Create Makefile")

    content = f"{module_name}-y := main.o "
    for i in files_name:
        content += i + ".o "
    content += "\n"
    content += f"\nobj-$(CONFIG_{module_name.upper()}) += {module_name}.o"

    content += "\n"
    makefile_path = os.path.join(module_dir, "Makefile")
    with open(makefile_path, 'w') as file:
        file.write(content)

    modify_drivers_makefile(module_name)
