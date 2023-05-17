// #ifdef __UNEXPORT_SYMBOL
// #define __UNEXPORT_SYMBOL

// #include"unexport_symbol.h"

// struct mutex _text_mutex;
// u32 (*_aarch64_insn_gen_branch_imm)(unsigned long pc, unsigned long addr,enum aarch64_insn_branch_type type); 
// int (*_aarch64_insn_patch_text_nosync)(void *addr, u32 insn);


// #endif
struct mutex *_text_mutex;
u32 (*_aarch64_insn_gen_branch_imm)(unsigned long pc, unsigned long addr,enum aarch64_insn_branch_type type); 
int (*_aarch64_insn_patch_text_nosync)(void *addr, u32 insn);

//生成unexport_symbol声明