//接口函数的宏定义的定义
#ifndef JMP_INTERFACE
#define JMP_INTERFACE
#include <linux/kallsyms.h>
#include "unexport_symbol_dec.h"

#define EXPORT_FUNC(func,ret,...) extern ret mod_##func(__VA_ARGS__);
#undef EXPORT_FUNC


//for arch64
#define JMP_VAR_DEC(func) \
static unsigned long orig_##func_addr; \
static unsigned long mod_##func_addr; \
static u32 store_orig_insn_##func; \			
static u32 store_jump_##func

#define JMP_INIT(func) do{ \
    orig_##func_addr = kallsyms_lookup_name("##func"); \
    mod_##func_addr = (unsigned long)mod_##func;
	memcpy((void *)&store_orig_insn_##func, (void *)orig_##func_addr, AARCH64_INSN_SIZE); \
	store_jump_##func = _aarch64_insn_gen_branch_imm((orig_##func_addr,	\
				  mod_##func_addr, AARCH64_INSN_BRANCH_NOLINK); \
}while(0)

#define JMP_INSTALL(func) \
    _aarch64_insn_patch_text_nosync((void *)orig_##func_addr, store_jump_##func);

#define JMP_REMOVE(func) \
    _aarch64_insn_patch_text_nosync((void *)orig_##func_addr, store_orig_insn_##func);

#define JMP_OPERATION(ops) do {	\
        disable_write_protect(); \
		jump_##ops();	\
        enable_write_protect(); \
	} while(0)
//arch64 end

#define EXPORT_FUNC(func,...) JMP_VAR_DEC(func); //声明接口函数热替换需要的变量
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