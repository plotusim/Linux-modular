#ifndef TESTS_FAKE_KERNEL_H
#define TESTS_FAKE_KERNEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>

#define __rcu
#define __init
#define __exit
#define GFP_KERNEL 0
#define unlikely(value) (value)
#define likely(value) (value)
#define READ_ONCE(value) (value)
#define WRITE_ONCE(destination, value) ((destination) = (value))
#define smp_load_acquire(pointer) (*(pointer))
#define smp_store_release(pointer, value) (*(pointer) = (value))
#define EINVAL 22
#define ENOMEM 12
#define EBUSY 16
#define ENODEV 19
#define ENOENT 2

enum system_states {
    SYSTEM_BOOTING,
    SYSTEM_RUNNING,
};

static enum system_states system_state = SYSTEM_RUNNING;

struct module {
    int unused;
};

#define THIS_MODULE ((struct module *)1)

static inline bool try_module_get(struct module *owner)
{
    return owner != NULL;
}

static inline void module_put(struct module *owner)
{
    (void)owner;
}

static inline void *__symbol_get(const char *symbol)
{
    (void)symbol;
    return NULL;
}

static inline void __symbol_put(const char *symbol)
{
    (void)symbol;
}

#define __stringify_1(value) #value
#define __stringify(value) __stringify_1(value)
#define symbol_get(symbol) \
    ((__typeof__(&(symbol)))__symbol_get(__stringify(symbol)))
#define symbol_put(symbol) __symbol_put(__stringify(symbol))

struct mutex {
    int unused;
};

#define DEFINE_MUTEX(name) struct mutex name

static inline void mutex_lock(struct mutex *lock)
{
    (void)lock;
}

static inline void mutex_unlock(struct mutex *lock)
{
    (void)lock;
}

typedef struct {
    int unused;
} spinlock_t;

#define DEFINE_SPINLOCK(name) spinlock_t name

static inline void spin_lock(spinlock_t *lock)
{
    (void)lock;
}

static inline void spin_unlock(spinlock_t *lock)
{
    (void)lock;
}

#define lockdep_is_held(lock) (1)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define rcu_dereference(pointer) (pointer)
#define rcu_dereference_protected(pointer, condition) (pointer)
#define rcu_access_pointer(pointer) (pointer)
#define rcu_assign_pointer(pointer, value) ((pointer) = (value))
#define RCU_INIT_POINTER(pointer, value) ((pointer) = (value))
#define synchronize_rcu() ((void)0)

static inline void *kmalloc(size_t size, int flags)
{
    (void)flags;
    return malloc(size);
}

static inline void kfree(void *pointer)
{
    free(pointer);
}

static inline int in_interrupt(void)
{
    return 0;
}

static inline int irqs_disabled(void)
{
    return 0;
}

static inline int in_atomic(void)
{
    return 0;
}

static inline int request_module(const char *name)
{
    (void)name;
    return 0;
}

#define EXPORT_SYMBOL_GPL(symbol)
#define module_init(function)
#define module_exit(function)
#define MODULE_LICENSE(value)
#define MODULE_DESCRIPTION(value)
#define MODULE_ALIAS(value)

#endif
