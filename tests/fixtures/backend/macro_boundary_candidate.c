/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

#define DEFINE_PRIVATE_STATE(name) backend_value_t name = 3
#define CALL_THROUGH_MACRO(expression) (expression)

static DEFINE_PRIVATE_STATE(persistent_state);

static backend_value_t macro_helper(backend_value_t value)
{
    return value + persistent_state;
}

backend_value_t deferred_macro(backend_value_t value)
{
    persistent_state++;
    return CALL_THROUGH_MACRO(macro_helper(value));
}
