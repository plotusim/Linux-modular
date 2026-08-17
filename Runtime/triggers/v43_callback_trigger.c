/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <mqueue.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

static int fail_operation(const char *operation)
{
	fprintf(stderr, "%s: %s\n", operation, strerror(errno));
	return 1;
}

static int exercise_timerfd(void)
{
	struct itimerspec timer = {
		.it_value = {
			.tv_nsec = 1000000,
		},
	};
	struct pollfd descriptor;
	uint64_t expirations;
	int fd;

	fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
	if (fd < 0)
		return fail_operation("timerfd_create");
	if (timerfd_settime(fd, 0, &timer, NULL) < 0) {
		close(fd);
		return fail_operation("timerfd_settime");
	}
	descriptor.fd = fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    read(fd, &expirations, sizeof(expirations)) !=
		    (ssize_t)sizeof(expirations) ||
	    expirations == 0) {
		close(fd);
		errno = EIO;
		return fail_operation("timerfd poll/read");
	}
	if (close(fd) < 0)
		return fail_operation("close timerfd");
	puts("V43_TIMERFD_RESULT_OK");
	return 0;
}

static int exercise_mqueue(void)
{
	static const char queue_name[] = "/linux_modular_v43";
	struct mq_attr attributes = {
		.mq_maxmsg = 4,
		.mq_msgsize = 32,
	};
	struct pollfd descriptor;
	char status[256];
	mqd_t queue;
	ssize_t length;

	mq_unlink(queue_name);
	queue = mq_open(queue_name, O_CREAT | O_RDWR | O_NONBLOCK | O_CLOEXEC,
			0600, &attributes);
	if (queue == (mqd_t)-1)
		return fail_operation("mq_open");
	descriptor.fd = (int)queue;
	descriptor.events = POLLIN | POLLOUT;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) < 0) {
		mq_close(queue);
		mq_unlink(queue_name);
		return fail_operation("poll mqueue");
	}
	length = read((int)queue, status, sizeof(status));
	if (length <= 0) {
		mq_close(queue);
		mq_unlink(queue_name);
		return fail_operation("read mqueue status");
	}
	if (mq_close(queue) < 0 || mq_unlink(queue_name) < 0)
		return fail_operation("close/unlink mqueue");
	puts("V43_MQUEUE_RESULT_OK");
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[1], "--timerfd") == 0)
		return exercise_timerfd();
	if (argc == 2 && strcmp(argv[1], "--mqueue") == 0)
		return exercise_mqueue();
	fprintf(stderr, "usage: %s --{timerfd,mqueue}\n", argv[0]);
	return 2;
}
