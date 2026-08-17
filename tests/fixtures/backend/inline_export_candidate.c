/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

#define notrace __attribute__((no_instrument_function))
#define EXPORT_SYMBOL_GPL(symbol)

static inline notrace backend_value_t traced_inline_helper(
    backend_value_t value)
{
    return value + 1;
}

backend_value_t exported_resident_helper(backend_value_t value)
{
    return fixture_dependency(value);
}
EXPORT_SYMBOL_GPL(exported_resident_helper);

backend_value_t deferred_inline_call(backend_value_t value)
{
    return traced_inline_helper(value) + exported_resident_helper(value);
}
