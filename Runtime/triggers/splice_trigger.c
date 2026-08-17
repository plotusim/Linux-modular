// SPDX-License-Identifier: GPL-2.0
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <unistd.h>

int main(void)
{
	static const char payload[] = "linux-modular-splice";
	struct iovec iov = {
		.iov_base = (void *)payload,
		.iov_len = sizeof(payload) - 1,
	};
	int pipefd[2];
	int sink;
	ssize_t inserted;
	ssize_t moved;

	if (pipe(pipefd) != 0) {
		perror("pipe");
		return 1;
	}
	sink = open("/dev/null", O_WRONLY);
	if (sink < 0) {
		perror("open /dev/null");
		return 1;
	}

	inserted = syscall(SYS_vmsplice, pipefd[1], &iov, 1, 0);
	if (inserted != (ssize_t)iov.iov_len) {
		if (inserted < 0)
			perror("vmsplice");
		else
			fprintf(stderr, "short vmsplice: %zd\n", inserted);
		return 1;
	}
	moved = syscall(
		SYS_splice,
		pipefd[0],
		NULL,
		sink,
		NULL,
		(size_t)inserted,
		0
	);
	if (moved != inserted) {
		if (moved < 0)
			perror("splice");
		else
			fprintf(stderr, "short splice: %zd\n", moved);
		return 1;
	}

	close(sink);
	close(pipefd[0]);
	close(pipefd[1]);
	puts("RESULT_OK");
	return 0;
}
