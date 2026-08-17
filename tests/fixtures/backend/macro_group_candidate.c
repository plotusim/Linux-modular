/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

#define DEFINE_FORMAT_ATTR(suffix) \
    static backend_value_t format_attr_##suffix = 4; \
    static backend_value_t format_shadow_##suffix = 5

#define DEFINE_REPEATED(type, name) \
    static type name = 6; \
    static type *name##_ptr = &name

DEFINE_FORMAT_ATTR(type);
DEFINE_REPEATED(backend_value_t, repeated_state);

backend_value_t deferred_macro_group(backend_value_t value)
{
    static const backend_value_t local_actions[] = { 1, 2 };

    return value + format_attr_type + format_shadow_type +
           repeated_state + *repeated_state_ptr + local_actions[0];
}
