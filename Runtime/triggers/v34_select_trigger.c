/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

static int fail(const char *operation)
{
	fprintf(stderr, "%s: %s\n", operation, strerror(errno));
	return -1;
}

static int make_ready_pipe(int descriptors[2])
{
	static const char byte = 'x';

	if (pipe(descriptors) < 0)
		return fail("pipe");
	if (write(descriptors[1], &byte, 1) != 1) {
		close(descriptors[0]);
		close(descriptors[1]);
		return fail("write(pipe)");
	}
	return 0;
}

static int consume_pipe(int descriptors[2])
{
	char byte;
	int result = 0;

	if (read(descriptors[0], &byte, 1) != 1)
		result = fail("read(pipe)");
	if (close(descriptors[0]) < 0)
		result = fail("close(pipe-read)");
	if (close(descriptors[1]) < 0)
		result = fail("close(pipe-write)");
	return result;
}

static int test_select(void)
{
	struct timeval timeout = {.tv_sec = 1, .tv_usec = 0};
	fd_set readers;
	int descriptors[2];

	if (make_ready_pipe(descriptors) != 0)
		return -1;
	FD_ZERO(&readers);
	FD_SET(descriptors[0], &readers);
	if (select(descriptors[0] + 1, &readers, NULL, NULL, &timeout) != 1 ||
	    !FD_ISSET(descriptors[0], &readers)) {
		errno = EIO;
		return fail("select(pipe)");
	}
	return consume_pipe(descriptors);
}

static int test_pselect(void)
{
	struct timespec timeout = {.tv_sec = 1, .tv_nsec = 0};
	sigset_t mask;
	fd_set readers;
	int descriptors[2];

	if (make_ready_pipe(descriptors) != 0)
		return -1;
	sigemptyset(&mask);
	FD_ZERO(&readers);
	FD_SET(descriptors[0], &readers);
	if (pselect(descriptors[0] + 1, &readers, NULL, NULL, &timeout,
	            &mask) != 1 || !FD_ISSET(descriptors[0], &readers)) {
		errno = EIO;
		return fail("pselect(pipe)");
	}
	return consume_pipe(descriptors);
}

static int test_ppoll(void)
{
	struct timespec timeout = {.tv_sec = 1, .tv_nsec = 0};
	struct pollfd descriptor;
	sigset_t mask;
	int descriptors[2];

	if (make_ready_pipe(descriptors) != 0)
		return -1;
	descriptor.fd = descriptors[0];
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	sigemptyset(&mask);
	if (ppoll(&descriptor, 1, &timeout, &mask) != 1 ||
	    !(descriptor.revents & POLLIN)) {
		errno = EIO;
		return fail("ppoll(pipe)");
	}
	return consume_pipe(descriptors);
}

int main(void)
{
	if (test_select() != 0 || test_pselect() != 0 || test_ppoll() != 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
