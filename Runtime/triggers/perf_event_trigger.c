/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/perf_event.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

struct read_result {
	uint64_t value;
	uint64_t time_enabled;
	uint64_t time_running;
};

static int open_task_clock(void)
{
	struct perf_event_attr attr;

	memset(&attr, 0, sizeof(attr));
	attr.type = PERF_TYPE_SOFTWARE;
	attr.size = sizeof(attr);
	attr.config = PERF_COUNT_SW_TASK_CLOCK;
	attr.disabled = 1;
	attr.exclude_kernel = 1;
	attr.exclude_hv = 1;
	attr.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED |
		PERF_FORMAT_TOTAL_TIME_RUNNING;

	return (int)syscall(__NR_perf_event_open, &attr, 0, -1, -1,
			    PERF_FLAG_FD_CLOEXEC);
}

static int compat_ioctl_reset(int fd)
{
#if defined(__x86_64__)
	register long nr __asm__("rax") = 54; /* i386 __NR_ioctl */
	register long arg1 __asm__("rbx") = fd;
	register long arg2 __asm__("rcx") = PERF_EVENT_IOC_RESET;
	register long arg3 __asm__("rdx") = 0;

	__asm__ volatile("int $0x80"
			 : "+a"(nr)
			 : "b"(arg1), "c"(arg2), "d"(arg3)
			 : "memory", "cc");
	if (nr < 0 && nr >= -4095) {
		errno = (int)-nr;
		return -1;
	}
	return (int)nr;
#else
	(void)fd;
	return 0;
#endif
}

static int exercise_callbacks(int fd)
{
	struct read_result result;
	struct pollfd descriptor;
	long page_size;
	void *mapping;
	volatile uint64_t accumulator = 0;
	int flags;
	int index;

	if (ioctl(fd, PERF_EVENT_IOC_RESET, 0) < 0 ||
	    ioctl(fd, PERF_EVENT_IOC_ENABLE, 0) < 0) {
		perror("perf ioctl enable");
		return 1;
	}
	for (index = 0; index < 200000; ++index)
		accumulator += (uint64_t)index;
	if (accumulator == 0)
		return 1;
	if (compat_ioctl_reset(fd) < 0) {
		perror("perf compat ioctl");
		return 1;
	}
	if (ioctl(fd, PERF_EVENT_IOC_DISABLE, 0) < 0) {
		perror("perf ioctl disable");
		return 1;
	}
	puts("PERF_IOCTL_OK");

	memset(&result, 0, sizeof(result));
	if (read(fd, &result, sizeof(result)) != (ssize_t)sizeof(result)) {
		perror("perf read");
		return 1;
	}
	puts("PERF_READ_OK");

	descriptor.fd = fd;
	descriptor.events = POLLIN | POLLHUP;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) < 0) {
		perror("perf poll");
		return 1;
	}
	puts("PERF_POLL_OK");

	page_size = sysconf(_SC_PAGESIZE);
	if (page_size <= 0)
		return 1;
	mapping = mmap(NULL, (size_t)page_size * 2,
		       PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (mapping == MAP_FAILED) {
		perror("perf mmap");
		return 1;
	}
	if (munmap(mapping, (size_t)page_size * 2) < 0) {
		perror("perf munmap");
		return 1;
	}
	puts("PERF_MMAP_OK");

	if (fcntl(fd, F_SETOWN, getpid()) < 0) {
		perror("perf fcntl owner");
		return 1;
	}
	flags = fcntl(fd, F_GETFL);
	if (flags < 0 || fcntl(fd, F_SETFL, flags | O_ASYNC | O_NONBLOCK) < 0 ||
	    fcntl(fd, F_SETFL, flags & ~O_ASYNC) < 0) {
		perror("perf fasync");
		return 1;
	}
	puts("PERF_FASYNC_OK");
	return 0;
}

static int write_marker(const char *path, const char *value)
{
	int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	ssize_t length = (ssize_t)strlen(value);
	int saved_errno;

	if (fd < 0)
		return -1;
	if (write(fd, value, (size_t)length) != length) {
		saved_errno = errno;
		close(fd);
		errno = saved_errno;
		return -1;
	}
	return close(fd);
}

static int hold_across_unload(const char *ready, const char *go,
			      const char *result_path)
{
	struct stat status;
	volatile uint64_t metadata_value;
	long page_size;
	void *held_mapping;
	int fd;
	int attempt;
	int result;

	fd = open_task_clock();
	if (fd < 0) {
		perror("perf_event_open");
		return 1;
	}
	puts("PERF_OPEN_OK");
	page_size = sysconf(_SC_PAGESIZE);
	if (page_size <= 0) {
		close(fd);
		return 1;
	}
	held_mapping = mmap(NULL, (size_t)page_size * 2,
			    PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (held_mapping == MAP_FAILED) {
		perror("perf held mmap");
		close(fd);
		return 1;
	}
	puts("PERF_HELD_MMAP_OK");
	if (write_marker(ready, "ready\n") < 0) {
		perror("write ready marker");
		munmap(held_mapping, (size_t)page_size * 2);
		close(fd);
		return 1;
	}
	for (attempt = 0; attempt < 3000; ++attempt) {
		if (stat(go, &status) == 0)
			break;
		usleep(10000);
	}
	if (attempt == 3000) {
		fprintf(stderr, "timed out waiting for %s\n", go);
		munmap(held_mapping, (size_t)page_size * 2);
		close(fd);
		return 1;
	}
	/*
	 * The parent test removes the generated module before creating GO.
	 * The VMA therefore exercises its resident vm_ops table after module
	 * unload: the first read invokes fault and munmap invokes close.
	 */
	metadata_value = *(volatile uint64_t *)held_mapping;
	(void)metadata_value;
	puts("PERF_HELD_FAULT_OK");
	if (munmap(held_mapping, (size_t)page_size * 2) < 0) {
		perror("perf held munmap");
		close(fd);
		return 1;
	}
	puts("PERF_HELD_CLOSE_OK");
	result = exercise_callbacks(fd);
	if (close(fd) < 0) {
		perror("perf close");
		return 1;
	}
	if (result != 0)
		return result;
	if (write_marker(result_path, "RESULT_OK\n") < 0) {
		perror("write result marker");
		return 1;
	}
	puts("HOLD_RESULT_OK");
	return 0;
}

int main(int argc, char **argv)
{
	int fd;
	int result;

	if (argc == 5 && strcmp(argv[1], "--hold") == 0)
		return hold_across_unload(argv[2], argv[3], argv[4]);
	if (argc != 1) {
		fprintf(stderr, "usage: %s [--hold READY GO RESULT]\n", argv[0]);
		return 2;
	}

	fd = open_task_clock();
	if (fd < 0) {
		perror("perf_event_open");
		return 1;
	}
	puts("PERF_OPEN_OK");
	result = exercise_callbacks(fd);
	if (close(fd) < 0) {
		perror("perf close");
		return 1;
	}
	if (result != 0)
		return result;
	puts("RESULT_OK");
	return 0;
}
