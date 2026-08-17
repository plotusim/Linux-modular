// SPDX-License-Identifier: GPL-2.0
#define _GNU_SOURCE

#include <fcntl.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void)
{
	int namespace_fd;

	namespace_fd = open("/proc/self/ns/mnt", O_RDONLY | O_CLOEXEC);
	if (namespace_fd < 0) {
		perror("open mount namespace");
		return 1;
	}
	if (syscall(SYS_setns, namespace_fd, 0) != 0) {
		perror("setns");
		close(namespace_fd);
		return 1;
	}
	close(namespace_fd);
	puts("RESULT_OK");
	return 0;
}
