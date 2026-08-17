/* SPDX-License-Identifier: GPL-2.0 */
static int late_include_helper(int value)
{
	return value + 1;
}

#ifdef CONFIG_LATE_INCLUDE_FIXTURE
#include "backend_api.h"

int late_include_deferred(int value)
{
	return late_include_helper(value);
}
#endif
