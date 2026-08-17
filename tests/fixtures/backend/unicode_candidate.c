/* SPDX-License-Identifier: GPL-2.0 */
/* UTF-8 offset regression: 内核源码中的非 ASCII 注释。 */
#include "backend_api.h"

static backend_value_t unicode_helper(backend_value_t value)
{
    return fixture_dependency(value);
}

backend_value_t unicode_deferred(backend_value_t value)
{
    return unicode_helper(value);
}
