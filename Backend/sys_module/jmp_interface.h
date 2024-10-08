//接口函数的宏定义的定义
#ifndef JMP_INTERFACE
#define JMP_INTERFACE
#include <linux/kallsyms.h>
#include <linux/cpu.h>

#define EXPORT_FUNC(func,ret,...) extern ret mod_##func(__VA_ARGS__);
#include "interface.h"
#undef EXPORT_FUNC

#ifdef CONFIG_X86_64
//for x86
#define HEAD_LEN 5

#define JMP_VAR_DEC(func)\
    static unsigned long orig_##func_addr;\
    static unsigned long mod_##func_addr;\
    static unsigned char store_jump_##func[HEAD_LEN];\
    static unsigned char store_orig_insn_##func[HEAD_LEN];

#define VAR_NAME(func) #func

#define JMP_INIT(func) do{\
    orig_##func_addr = kallsyms_lookup_name(#func);\
    mod_##func_addr = (unsigned long)mod_##func;\
    printk("%lx",orig_##func_addr); \
    printk("%lx",mod_##func_addr); \
    printk("%s",VAR_NAME(func));\
    memcpy(store_orig_insn_##func,orig_##func_addr,HEAD_LEN);\
    store_jump_##func[0] = 0xe9;\
    (*(int *)(store_jump_##func + 1)) = \
        (long)mod_##func_addr - (long)orig_##func_addr - HEAD_LEN;\
}while(0)

#define JMP_INSTALL(func)\
    memcpy((unsigned char *)orig_##func_addr,store_jump_##func,HEAD_LEN)

#define JMP_REMOVE(func)\
    memcpy((unsigned char *)orig_##func_addr,store_orig_insn_##func,HEAD_LEN)

#define JMP_OPERATION(ops) do { 	\
		void *unused = disable_write_protect(NULL); \
		jmp_##ops();	\
		enable_write_protect(); \
	} while(0)

#else
//for arch64
#define JMP_VAR_DEC(func) \
static unsigned long orig_##func_addr; \
static unsigned long mod_##func_addr; \
static u32 store_orig_insn_##func; \			
static u32 store_jump_##func

#define VAR_NAME(func) #func

#define JMP_INIT(func) do{ \
    orig_##func_addr = kallsyms_lookup_name(#func); \
    mod_##func_addr = (unsigned long)mod_##func; \
    printk("%lx",orig_##func_addr); \
    printk("%lx",mod_##func_addr); \
    printk("%s",VAR_NAME(func)); \
	memcpy((void *)&store_orig_insn_##func, (void *)orig_##func_addr, AARCH64_INSN_SIZE); \
	store_jump_##func = _aarch64_insn_gen_branch_imm(orig_##func_addr,	\
				  mod_##func_addr, AARCH64_INSN_BRANCH_NOLINK); \
}while(0)

#define JMP_INSTALL(func) \
    _aarch64_insn_patch_text_nosync((void *)orig_##func_addr, store_jump_##func);

#define JMP_REMOVE(func) \
    _aarch64_insn_patch_text_nosync((void *)orig_##func_addr, store_orig_insn_##func);

#define JMP_OPERATION(ops) do {	\
        void *unused = disable_write_protect(NULL); \
		jmp_##ops();	\
        enable_write_protect(); \
	} while(0)
//arch64 end
#endif

#define EXPORT_FUNC(func,...) JMP_VAR_DEC(func); //声明接口函数热替换需要的变量
#include "interface.h"
#undef EXPORT_FUNC


#define EXPORT_FUNC(func,...) JMP_INSTALL(func); //jmp_install:修改接口函数
static inline void jmp_install(void){
    #include "interface.h"
}
#undef EXPORT_FUNC

#define EXPORT_FUNC(func,...) JMP_REMOVE(func); //jmp_remove:恢复接口函数
static inline void jmp_remove(void){
    #include "interface.h"
}
#undef EXPORT_FUNC

#define EXPORT_FUNC(func,...) JMP_INIT(func); //jmp_init函数
static inline void jmp_init(void){
    #include "interface.h"
}
#undef EXPORT_FUNC

#endif