/* SPDX-License-Identifier: GPL-2.0 */

#include "backend_api.h"

#define SYSCALL_DEFINE2(name, type1, arg1, type2, arg2)                  \
	static inline __attribute__((no_instrument_function))             \
	long __do_sys_##name(type1 arg1, type2 arg2)

#define COMPAT_SYSCALL_DEFINE1(name, type1, arg1)                       \
	static inline __attribute__((no_instrument_function))             \
	long __do_compat_sys_##name(type1 arg1)

SYSCALL_DEFINE2(optional_call, int, value, unsigned int, flags)
{
	return fixture_dependency(value) + flags;
}

static inline __attribute__((no_instrument_function))
long resident_inline(unsigned int value)
{
	return fixture_dependency(value);
}

COMPAT_SYSCALL_DEFINE1(compat_optional_call, unsigned int, value)
{
	return resident_inline(value);
}
