#define UNEXPORT_FUNC(func,ret,...) extern ret (*_##func)(__VA_ARGS__);
#define UNEXPORT_VAR(name,type) extern type *_##name;
#include "unexport_symbol.h"
#undef UNEXPORT_FUNC
#undef UNEXPORT_VAR