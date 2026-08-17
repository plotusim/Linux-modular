/* SPDX-License-Identifier: GPL-2.0 */
#include "backend_api.h"

enum local_mode {
    LOCAL_ADD = 2,
};

typedef struct {
    backend_value_t value;
} local_box;

struct local_args;
typedef backend_value_t (local_apply_fn)(struct local_args *args);

struct local_args {
    local_box box;
    enum local_mode mode;
} local_args_template;

#define LOCAL_BASE 3
#define LOCAL_TOTAL(value) ((value) + LOCAL_BASE)

static const backend_value_t local_ro_bias = 1;

static backend_value_t shared_local(struct local_args *args);

static backend_value_t shared_local(struct local_args *args)
{
    return fixture_dependency(args->box.value) + args->mode + local_ro_bias;
}

backend_value_t deferred_local(backend_value_t value)
{
    static const backend_value_t local_actions[] = { LOCAL_ADD };
    local_apply_fn *apply = shared_local;
    struct local_args args = {
        .box = { .value = LOCAL_TOTAL(value) },
        .mode = LOCAL_ADD,
    };

    return apply(&args) + local_actions[0] - LOCAL_ADD;
}
