/* SPDX-License-Identifier: GPL-2.0 */
#include "inline_proxy_api.h"

proxy_value_t deferred_traced(proxy_value_t value)
{
	trace_fixture_proxy(value);
	return value + 1;
}
