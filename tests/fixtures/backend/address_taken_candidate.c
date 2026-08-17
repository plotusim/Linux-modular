/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

static const backend_value_t published_state = 9;

backend_value_t deferred_address(backend_value_t value)
{
    const backend_value_t *pointer = &published_state;

    return value + *pointer;
}
