import re
from kconfiglib import *
import os
import copy


'''
数据结构说明:
文件节点:file_node
CONFIG节点
configs[CONFIG] = {file_node}
CONFIG依赖图 graph (使用kconfiglib)
'''

subsystem = ["block","certs","crypto","drivers","fs","init","io_uring","ipc","kernel","lib","mm","security"\
    ,"sound","usr","virt","net"]

class myconfig:    
    def __init__(self,name):
        self.config_name = name
        self.config_parents = set()
        self.config_childrens = set()
        self.files = []
        self.compile_files = []


class parseconfig:

    kconf = None

    # 用于解析makefile语句的模式
    # obj-$(CONFIG_xxx) (:|+|)= yyy
    pattern1 = re.compile(r'^obj-\$\((\w+)\)\s*((:|\+|)=) \s*(.+)$')
    # obj-y (:|+)= yyy
    pattern2 = re.compile(r'^obj-y\s*((\+|:|)=) \s*(.+)$')
    # xxx-(y|objs) (:|+)= yyy
    pattern3 = re.compile(r'^([-_\w]+)-(y|objs)\s*((:|\+|)=)\s*(.+)$')
    # xxx-$(CONFIG_xxx)(:|+)=yyy
    pattern4 = re.compile(r'^([-_\w]+)-\$\((\w+)\)\s*((\+|:|)=) \s*(.+)$')
    # xxx(:/+)= zzz
    pattern5 = re.compile(r'^([-\w]+)\s*((\+|:|)=)\s*(.+)')
    # xxx-$(subst m,y,$(CONFIG_xxx))	+= yyy
    
    # 解析makefile        
    def parse_makefile(self,config,file_path):
        temp_dict = {}
        temp_object = {}
        temp_dollar = {}
        temp_compile_object = {}
        temp_compile_dependcy = {}
        temp_core = set()
        try:
            makefilepath = os.path.join(file_path,"Makefile")
            with open(makefilepath, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            kbuildfilepath = os.path.join(file_path,"Kbuild")
            with open(kbuildfilepath, "r") as f:
                lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            match = self.pattern2.match(line)
            if match:
                target_list = match.group(3).split()
                while "\\" in target_list:
                    target_list.remove("\\") 
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i].strip()
                    # 解决“cfdbgl.o\”中文件与“\”没分开问题kconfiglib.KconfigError: init/Kconfig:29: error: couldn't parse 'def_bool $(success,echo "$(CC_VERSION_TEXT)
                    temp_line = line.split()
                    if len(temp_line) > 0 and "\\"!= temp_line[-1] and "\\" in temp_line[-1]:
                        temp_file = temp_line[-1].split('\\')
                        temp_line.remove(temp_line[-1])
                        temp_line.append(temp_file[0])
                        temp_line.append('\\')
                    target_list += temp_line

                if config == "":
                    temp_core.update(target_list)
                else:
                    dict_value = temp_dict.setdefault(config, [])
                    dict_value.extend(target_list)
                i += 1
                continue  
            match = self.pattern1.match(line)
            if match:
                config_name = match.group(1)
                target_list = match.group(4).split()
                while "\\" in target_list:
                    target_list.remove("\\") 
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i].strip()
                    # 解决“cfdbgl.o\”中文件与“\”没分开问题
                    temp_line = line.split()
                    if len(temp_line) > 0 and "\\"!= temp_line[-1] and "\\" in temp_line[-1]:
                        temp_file = temp_line[-1].split('\\')
                        temp_line.remove(temp_line[-1])
                        temp_line.append(temp_file[0])
                        temp_line.append('\\')
                    target_list += temp_line
                dict_value = temp_dict.setdefault(config_name, [])
                dict_value.extend(target_list)
                i += 1
                continue  
            match = self.pattern3.match(line)
            if match:
                object_name = str(match.group(1)) + ".o"
                dollar_name = str(match.group(1)) +'-'+ str(match.group(2))
                target_list = match.group(5).split()
                while "\\" in target_list:
                    target_list.remove("\\") 
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i].strip()
                    # 解决“cfdbgl.o\”中文件与“\”没分开问题
                    temp_line = line.split()
                    if len(temp_line) > 0 and "\\"!= temp_line[-1] and "\\" in temp_line[-1]:
                        temp_file = temp_line[-1].split('\\')
                        temp_line.remove(temp_line[-1])
                        temp_line.append(temp_file[0])
                        temp_line.append('\\')
                    target_list += temp_line
                    
                object_value = temp_object.setdefault(object_name, [])
                object_value.extend(target_list)
                
                dollar_value = temp_dollar.setdefault(dollar_name, [])
                dollar_value.extend(target_list)
                i += 1
                continue  
            match = self.pattern4.match(line)
            if match: #当前忽略该模式下的config dependcy关系
                # 忽略ccflags
                if match.group(1) == "ccflags":
                    i += 1
                    continue
                object_name = str(match.group(1)) + ".o"
                config_name = match.group(2)
                target_list = match.group(5).split()
                while "\\" in target_list:
                    target_list.remove("\\") 
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i].strip()
                    # 解决“cfdbgl.o\”中文件与“\”没分开问题
                    temp_line = line.split()
                    if len(temp_line) > 0 and "\\"!= temp_line[-1] and "\\" in temp_line[-1]:
                        temp_file = temp_line[-1].split('\\')
                        temp_line.remove(temp_line[-1])
                        temp_line.append(temp_file[0])
                        temp_line.append('\\')
                    target_list += temp_line
                dict_value = temp_dict.setdefault(config_name, [])
                dict_value.extend(target_list)
                
                object_value = temp_object.setdefault(object_name, [])

                temp_compile_object.setdefault(object_name, set()).add(config_name)

                i += 1
                continue 
            match = self.pattern5.match(line)
            if match:
                dollar_name = match.group(1)
                target_list = match.group(4).split()
                while "\\" in target_list:
                    target_list.remove("\\") 
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i].strip()
                    # 解决“cfdbgl.o\”中文件与“\”没分开问题
                    temp_line = line.split()
                    if len(temp_line) > 0 and "\\"!= temp_line[-1] and "\\" in temp_line[-1]:
                        temp_file = temp_line[-1].split('\\')
                        temp_line.remove(temp_line[-1])
                        temp_line.append(temp_file[0])
                        temp_line.append('\\')
                    target_list += temp_line
                dollar_value = temp_dollar.setdefault(dollar_name, [])
                dollar_value.extend(target_list)
                i += 1
                continue
            
            i += 1
        #在dict中替换object,dollar,并对xxx/进行迭代
        for key,value in temp_dict.items():
            for object,file in temp_object.items():
                if object in value:
                    value.remove(object) 
                    value += file
                    if object in temp_compile_object.keys():
                        temp_compile_dependcy.setdefault(key, set()).update(temp_compile_object[object])
                        self.compile_dependcy.setdefault(key, set()).update(temp_compile_object[object])
                    
            for dollar_name,file in temp_dollar.items():
                keyvalue = "$(" + str(dollar_name) + ")"
                if keyvalue in value:
                    value.remove(keyvalue)
                    value += file
            delete_i = set()               
            for i in value:
                if i.endswith("/"):
                    new_path = os.path.join(file_path, str(i))
                    self.parse_makefile(key,new_path)
                    delete_i.add(i)
            for i in delete_i:
                value.remove(i)
        # 迭代temp_core
        for object,file in temp_object.items():
            if object in temp_core:
                temp_core.remove(object) 
                temp_core.update(file)
        for dollar_name,file in temp_dollar.items():
            keyvalue = "$(" + str(dollar_name) + ")"
            if keyvalue in temp_core:
                temp_core.remove(keyvalue)
                temp_core.update(file)
        delete_i = set()               
        for i in temp_core:
            if i.endswith("/"):
                new_path = os.path.join(file_path, str(i))
                self.parse_makefile("",new_path)
                delete_i.add(i)
        for i in delete_i:
            temp_core.remove(i)

        # 更新到全局的dict
        for key, value in temp_dict.items():
            abs_value = []
            for i in value:
                abs_file = os.path.join(file_path, str(i))
                temp_abs_file = abs_file.replace(".o", ".c")
                abs_value.append(temp_abs_file)
            if key in self.configs.keys():
                self.configs[key].files += abs_value
            else:
                newconfig = myconfig(name=key)
                newconfig.files = abs_value
                self.configs[key] = newconfig
        
        # 更新compile_dependcy
        for key, value in temp_compile_dependcy.items():
            abs_compile = []
            for i in value:
                for j in temp_dict[i]:
                    abs_compile_file = str(file_path) + str(j)
                    temp_abs_compile_file = abs_compile_file.replace(".o", ".c")
                    abs_compile.append(temp_abs_compile_file)
            self.configs[key].compile_files = self.configs[key].files + abs_compile
        
        # 更新core_file
        for i in temp_core:
            abs_core_file = str(file_path) + str(i)
            temp_core_file = abs_core_file.replace(".o", ".c")
            self.core_files.add(temp_core_file)
    
    # 用于debug, print出makefile中未匹配的var,
    def __fileter_file(self):
        # 过滤config中的$(var)
        for _, value in self.configs.items():   
            temp_files = value.files
            value.files = [item for item in temp_files if "$" not in item]
            temp_compile_files = value.compile_files
            value.compile_files = [it for it in temp_compile_files if "$" not in it]
        # 过滤core中的$(var)
        for i in self.core_files:
            if "$" in i:
                temp_core = self.core_files
                self.core_files = {it for it in temp_core if "$" not in it}
                
    def output_modualr_file(self,kernel_path,config):
        mconfig = list()
        
        config_path = os.path.join(kernel_path, config)
        with open(config_path,"r") as cfile:
            for cf_line in cfile:
                if "=m" in cf_line:
                    mconfig.append(cf_line.split('=')[0])
         
        for mcfg in mconfig:
            if mcfg in self.configs.keys():
                mcfg_config = self.configs[mcfg]
                if mcfg_config.compile_files != []:
                    temp_modualr_files = [f for f in mcfg_config.compile_files if f not in self.core_files]
                else:
                    temp_modualr_files = [f for f in mcfg_config.files if f not in self.core_files]
            self.modualr_files.update(temp_modualr_files)
    

    def __init__(self,kernel_path,config):
        # config类
        self.configs = {}
        # compile 依赖
        self.compile_dependcy = {}
        
        self.core_files = set()
        self.modualr_files = set()

        # makefile相关
            # 对每个子系统进行分析
        for i in subsystem:
            filePath = os.path.join(kernel_path, str(i))
            self.parse_makefile("",filePath)

        self.__fileter_file()  
        
        self.output_modualr_file(kernel_path,config)
        
        

