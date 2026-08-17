/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

static backend_value_t deferred_bias = 7;

static backend_value_t helper_add(backend_value_t value)
{
    return value + deferred_bias;
}

backend_value_t deferred_add(backend_value_t value)
{
    return helper_add(value);
}

void deferred_reset(void)
{
    deferred_bias = 0;
}

backend_value_t collision_interface(
    backend_value_t linux_modular_result,
    backend_value_t linux_modular_operations
)
{
    return linux_modular_result + linux_modular_operations;
}

backend_value_t resident_entry(backend_value_t value)
{
    deferred_reset();
    return deferred_add(value);
}
