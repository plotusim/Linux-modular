/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

static backend_value_t shared_resident_helper(backend_value_t value)
{
    return fixture_dependency(value);
}

static backend_value_t (*deferred_callback)(backend_value_t) =
    shared_resident_helper;

backend_value_t deferred_via_callback(backend_value_t value)
{
    return deferred_callback(value);
}

backend_value_t deferred_add(backend_value_t value)
{
    return shared_resident_helper(value) + fixture_dependency(value);
}

backend_value_t resident_entry(backend_value_t value)
{
    return shared_resident_helper(value);
}
