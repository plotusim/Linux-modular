/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <linux/capability.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

int run_capability_tests(void)
{
	struct __user_cap_header_struct header = {
		.version = _LINUX_CAPABILITY_VERSION_3,
		.pid = 0,
	};
	struct __user_cap_data_struct before[_LINUX_CAPABILITY_U32S_3];
	struct __user_cap_data_struct after[_LINUX_CAPABILITY_U32S_3];

	memset(before, 0, sizeof(before));
	memset(after, 0, sizeof(after));
	if (syscall(SYS_capget, &header, before) < 0) {
		perror("capget");
		return 1;
	}
	if (header.version != _LINUX_CAPABILITY_VERSION_3) {
		fprintf(stderr, "capget changed capability ABI version\n");
		return 1;
	}

	/*
	 * Reinstall the current set instead of changing privileges. This still
	 * exercises the complete capset validation, credential and LSM path.
	 */
	if (syscall(SYS_capset, &header, before) < 0) {
		perror("capset");
		return 1;
	}
	if (syscall(SYS_capget, &header, after) < 0) {
		perror("capget after capset");
		return 1;
	}
	if (memcmp(before, after, sizeof(before)) != 0) {
		fprintf(stderr, "capabilities changed after identity capset\n");
		return 1;
	}
	return 0;
}

#ifndef LINUX_MODULARIZER_TRIGGER_LIBRARY
int main(void)
{
	if (run_capability_tests() != 0)
		return 1;
	puts("CAPABILITY_OK");
	return 0;
}
#endif
