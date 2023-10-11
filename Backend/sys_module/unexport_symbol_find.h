#ifndef UNEXPORT_SYMBOL_FIND
#define UNEXPORT_SYMBOL_FIND
#include <linux/kallsyms.h>

//定义unexport_symbol
#define UNEXPORT_FUNC(func,ret,...)  ret (*_##func)(__VA_ARGS__);
#define UNEXPORT_VAR(name,type)  type *_##name;
#include "unexport_symbol.h"
#undef UNEXPORT_FUNC
#undef UNEXPORT_VAR

#define NAME(func) #func

//
#define FIND_SYMBOL(var) \
    _##var = (typeof(_##var))kallsyms_lookup_name(NAME(var)); \
    if (!_##var) { \
        printk("error:%s\n",NAME(var)); \
        return -1; \
    }

#define UNEXPORT_FUNC(func,...) FIND_SYMBOL(func);
#define UNEXPORT_VAR(name,...) FIND_SYMBOL(name);
static inline int find_symbol(void){
    #include "unexport_symbol.h"
    return 1;
}
#undef UNEXPORT_FUNC
#undef UNEXPORT_VAR



#endif