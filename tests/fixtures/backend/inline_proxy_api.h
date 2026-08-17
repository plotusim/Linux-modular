/* SPDX-License-Identifier: GPL-2.0 */
#ifndef INLINE_PROXY_API_H
#define INLINE_PROXY_API_H

typedef int proxy_value_t;

void __traceiter_fixture_proxy(proxy_value_t value);
extern int __tracepoint_fixture_proxy;

static inline void trace_fixture_proxy(proxy_value_t value)
{
	if (__tracepoint_fixture_proxy)
		__traceiter_fixture_proxy(value);
}

#endif
