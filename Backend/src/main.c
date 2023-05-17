#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/cpumask.h>

#include "toolfunc.h"
#include "jmp_interface.h"
#include "unexport_symbol_dec.h"


MODULE_LICENSE("GPL");
MODULE_AUTHOR("plot");
MODULE_DESCRIPTION("new module");


static int find_symbol(void){
    _text_mutex = (void *)kallsyms_lookup_name("text_mutex");
    if (!_text_mutex) {
		printk("text相关工作失败!");
		return -1;
	}
    _aarch64_insn_gen_branch_imm = (u32 (*)(unsigned long, unsigned long, enum aarch64_insn_branch_type))\
                                                    kallsyms_lookup_name("aarch64_insn_gen_branch_imm");
    if (!_aarch64_insn_gen_branch_imm) {
		printk("arch_gen_branch_imm error");
		return -1;
	}
    _aarch64_insn_patch_text_nosync = (int (*)(void *, u32 ))kallsyms_lookup_name("aarch64_insn_patch_text_nosync");
    if (!_aarch64_insn_patch_text_nosync) {
		printk("arch_patch_text error");
		return -1;
	}
    return 1;
}


static int mod_install(void){
    if(find_symbol()){
        printk("find symbol success");
    }
    else{
        printk("find symbol error");
        return -1;
    }
    jmp_init();
    printk("jmp init success");
    get_online_cpus();
	mutex_lock(_text_mutex);
    JMP_OPERATION(install);
    mutex_unlock(_text_mutex);
	put_online_cpus();

    printk(KERN_INFO "Module install\n");
    return 1;
} 

static void restore_function(void){
    get_online_cpus();
	mutex_lock(_text_mutex);
    JMP_OPERATION(remove);
    mutex_unlock(_text_mutex);
	put_online_cpus();
}

static int __init mod_init(void){
    if(mod_install()){
        printk("module install success");
    }
    else{
        printk("module install error");
    }
    printk(KERN_INFO "Hello module!\n");
    return 0;
}


static void __exit mod_exit(void){
    restore_function();
    printk(KERN_INFO "Cleaning up module.\n");
}


module_init(mod_init);
module_exit(mod_exit);