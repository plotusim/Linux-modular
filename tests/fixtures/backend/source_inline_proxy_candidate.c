/* SPDX-License-Identifier: GPL-2.0 */

static inline void source_local_proxy(int value)
{
	(void)value;
}

int source_proxy_deferred(int value)
{
	source_local_proxy(value);
	return value + 1;
}
