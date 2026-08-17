// SPDX-License-Identifier: GPL-2.0
#define _GNU_SOURCE

#include <fcntl.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <unistd.h>

#define IOPRIO_WHO_PROCESS 1
#define IOPRIO_CLASS_BE 2
#define IOPRIO_CLASS_SHIFT 13

static int test_ioprio(void)
{
	long observed;
	int target = (IOPRIO_CLASS_BE << IOPRIO_CLASS_SHIFT) | 4;

	observed = syscall(SYS_ioprio_get, IOPRIO_WHO_PROCESS, 0);
	if (observed < 0) {
		perror("ioprio_get");
		return 1;
	}
	if (syscall(
		    SYS_ioprio_set,
		    IOPRIO_WHO_PROCESS,
		    0,
		    target
	    ) != 0) {
		perror("ioprio_set");
		return 1;
	}
	observed = syscall(SYS_ioprio_get, IOPRIO_WHO_PROCESS, 0);
	if (observed != target) {
		fprintf(
			stderr,
			"ioprio mismatch: expected=%d observed=%ld\n",
			target,
			observed
		);
		return 1;
	}
	return 0;
}

static int test_splice(void)
{
	static const char payload[] = "linux-modular-combined";
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
	return 0;
}

int main(void)
{
	if (test_ioprio() != 0 || test_splice() != 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
