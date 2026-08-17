/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <linux/keyctl.h>
#include <linux/mount.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static int fail(const char *operation)
{
	fprintf(stderr, "%s: %s\n", operation, strerror(errno));
	return -1;
}

static int test_fsopen(void)
{
	long fd;

	fd = syscall(SYS_fsopen, "tmpfs", FSOPEN_CLOEXEC);
	if (fd < 0)
		return fail("fsopen(tmpfs)");
	if (syscall(SYS_fsconfig, fd, FSCONFIG_SET_STRING,
		    "size", "4096", 0) < 0) {
		close((int)fd);
		return fail("fsconfig(size)");
	}
	if (syscall(SYS_fsconfig, fd, FSCONFIG_CMD_CREATE,
		    NULL, NULL, 0) < 0) {
		close((int)fd);
		return fail("fsconfig(create)");
	}
	if (close((int)fd) < 0)
		return fail("close(fscontext)");
	return 0;
}

static int test_keyctl(void)
{
	static const char description[] = "linux-modular-v33";
	static const char payload[] = "closure-ok";
	char buffer[32] = {0};
	long ring, key, found, bytes;

	ring = syscall(SYS_keyctl, KEYCTL_GET_KEYRING_ID,
		       KEY_SPEC_SESSION_KEYRING, 1, 0, 0);
	if (ring < 0)
		return fail("keyctl(GET_KEYRING_ID)");
	key = syscall(SYS_add_key, "user", description, payload,
		      sizeof(payload), ring);
	if (key < 0)
		return fail("add_key(user)");
	found = syscall(SYS_request_key, "user", description, NULL, ring);
	if (found < 0)
		return fail("request_key(user)");
	bytes = syscall(SYS_keyctl, KEYCTL_READ, key, buffer,
			sizeof(buffer), 0);
	if (bytes != (long)sizeof(payload) ||
	    memcmp(buffer, payload, sizeof(payload)) != 0) {
		errno = EIO;
		return fail("keyctl(READ)");
	}
	if (syscall(SYS_keyctl, KEYCTL_UNLINK, key, ring, 0, 0) < 0)
		return fail("keyctl(UNLINK)");
	return 0;
}

static int test_signal(void)
{
	unsigned long mask = 1UL << (SIGUSR1 - 1);
	struct timespec timeout = {.tv_sec = 1, .tv_nsec = 0};
	siginfo_t information;
	long pidfd, received;

	if (syscall(SYS_rt_sigprocmask, SIG_BLOCK, &mask, NULL,
		    sizeof(mask)) < 0)
		return fail("rt_sigprocmask(SIG_BLOCK)");
	if (syscall(SYS_kill, getpid(), SIGUSR1) < 0)
		return fail("kill(SIGUSR1)");
	memset(&information, 0, sizeof(information));
	received = syscall(SYS_rt_sigtimedwait, &mask, &information,
			   &timeout, sizeof(mask));
	if (received != SIGUSR1) {
		if (received >= 0)
			errno = EIO;
		return fail("rt_sigtimedwait(SIGUSR1)");
	}
	if (syscall(SYS_rt_sigprocmask, SIG_UNBLOCK, &mask, NULL,
		    sizeof(mask)) < 0)
		return fail("rt_sigprocmask(SIG_UNBLOCK)");

	pidfd = syscall(SYS_pidfd_open, getpid(), 0);
	if (pidfd < 0)
		return fail("pidfd_open(self)");
	if (syscall(SYS_pidfd_send_signal, pidfd, 0, NULL, 0) < 0) {
		close((int)pidfd);
		return fail("pidfd_send_signal(0)");
	}
	if (close((int)pidfd) < 0)
		return fail("close(pidfd)");
	return 0;
}

int main(void)
{
	if (test_fsopen() != 0 || test_keyctl() != 0 ||
	    test_signal() != 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
