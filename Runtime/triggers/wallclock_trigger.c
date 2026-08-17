/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

/*
 * Linux 5.10 routes the i386 adjtimex syscall through
 * __ia32_sys_adjtimex_time32.  Keep this userspace layout independent of the
 * build host's native struct timex so the compat entry point is exercised by
 * the 64-bit validation binary.
 */
struct old_timex32_user {
	uint32_t modes;
	int32_t offset;
	int32_t freq;
	int32_t maxerror;
	int32_t esterror;
	int32_t status;
	int32_t constant;
	int32_t precision;
	int32_t tolerance;
	int32_t time_sec;
	int32_t time_usec;
	int32_t tick;
	int32_t ppsfreq;
	int32_t jitter;
	int32_t shift;
	int32_t stabil;
	int32_t jitcnt;
	int32_t calcnt;
	int32_t errcnt;
	int32_t stbcnt;
	int32_t tai;
	int32_t reserved[11];
};

_Static_assert(sizeof(struct old_timex32_user) == 128,
	       "unexpected old_timex32 userspace layout");

static long ia32_adjtimex_query(struct old_timex32_user *tx)
{
#if defined(__x86_64__)
	register unsigned long number __asm__("rax") = 124;
	register unsigned long argument __asm__("rbx") = (uintptr_t)tx;

	__asm__ volatile("int $0x80"
			 : "+a" (number)
			 : "b" (argument)
			 : "memory", "cc");
	return (int32_t)number;
#else
	(void)tx;
	return -ENOSYS;
#endif
}

int run_wallclock_tests(void)
{
	struct old_timex32_user *tx;
	long result;

	tx = mmap(NULL, sizeof(*tx), PROT_READ | PROT_WRITE,
		  MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0);
	if (tx == MAP_FAILED) {
		perror("mmap MAP_32BIT");
		return 1;
	}
	if ((uintptr_t)tx > UINT32_MAX) {
		fprintf(stderr, "MAP_32BIT returned an incompatible address\n");
		munmap(tx, sizeof(*tx));
		return 1;
	}
	memset(tx, 0, sizeof(*tx));
	result = ia32_adjtimex_query(tx);
	if (result < 0) {
		errno = (int)-result;
		perror("ia32 adjtimex query");
		munmap(tx, sizeof(*tx));
		return 1;
	}
	if (munmap(tx, sizeof(*tx)) < 0) {
		perror("munmap");
		return 1;
	}

	/*
	 * NULL arguments perform the permission and syscall path without changing
	 * either wall-clock time or timezone.
	 */
	if (syscall(SYS_settimeofday, NULL, NULL) < 0) {
		perror("settimeofday no-op");
		return 1;
	}
	return 0;
}

#ifndef LINUX_MODULARIZER_TRIGGER_LIBRARY
int main(void)
{
	if (run_wallclock_tests() != 0)
		return 1;
	puts("WALLCLOCK_OK");
	return 0;
}
#endif
