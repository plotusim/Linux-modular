#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/cpumask.h>

#include "toolfunc.h"
#include "jmp_interface.h"
#include "unexport_symbol_find.h"


MODULE_LICENSE("GPL");
MODULE_AUTHOR("felix");
MODULE_DESCRIPTION("new module");




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