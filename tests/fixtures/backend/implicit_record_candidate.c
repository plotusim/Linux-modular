/* SPDX-License-Identifier: GPL-2.0 */
#include "implicit_record_api.h"

struct implicit_record {
    int value;
};

int deferred_implicit_record(struct implicit_holder *holder)
{
    return holder->record->value;
}
