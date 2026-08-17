typedef int (*callback_t)(int);

struct demo_ops {
    callback_t first;
    callback_t second;
};

union nested_callback {
    callback_t invoke;
};

struct nested_ops {
    int tag;
    union nested_callback callback;
};

static int local_first(int value)
{
    return value + 1;
}

static int local_second(int value)
{
    return value + 2;
}

int external_callback(int value)
{
    return value + 3;
}

static struct demo_ops callbacks = {
    .first = local_first,
    .second = local_second,
};

static struct nested_ops nested_callbacks = {
    .tag = 1,
    .callback = {
        .invoke = local_first,
    },
};

static callback_t runtime_callback;

__attribute__((used)) static int compiler_retained(int value)
{
    return value;
}

__attribute__((used, section(".discard.addressable")))
static void *retention_metadata = (void *)&compiler_retained;

const char *compiler_string(void)
{
    return "compiler generated string";
}

extern __inline __attribute__((gnu_inline))
int externally_available(int value)
{
    return value + 4;
}

void register_callback(callback_t callback)
{
    runtime_callback = callback;
}

int invoke_runtime_callback(int value)
{
    return runtime_callback(value);
}

int exercise_callbacks(int value)
{
    if (__builtin_expect(value == -1, 0))
        return -1;
    register_callback(external_callback);
    return callbacks.first(value) + callbacks.second(value) +
           invoke_runtime_callback(value) + externally_available(value);
}

int exercise_nested_callback(int value)
{
    return nested_callbacks.callback.invoke(value);
}

int exercise_copied_nested_callback(int value)
{
    struct nested_ops copy = nested_callbacks;

    return copy.callback.invoke(value);
}

int exercise_inline_asm(int value)
{
    __asm__ volatile("" : "+r"(value));
    return value;
}
