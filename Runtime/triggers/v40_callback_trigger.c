/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/sync_file.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define LM_SYNC_CREATE_FENCE _IO('L', 0x40)
#define LM_SYNC_SIGNAL_FENCE _IOW('L', 0x41, int)
#define LM_SYNC_FIXTURE "/dev/lm_sync_fixture"

static int fail_operation(const char *operation, const char *path)
{
	fprintf(stderr, "%s(%s): %s\n", operation, path, strerror(errno));
	return -1;
}

static ssize_t read_path(const char *path, void *buffer, size_t size)
{
	ssize_t result;
	int saved_errno;
	int fd;

	if (!size) {
		errno = EINVAL;
		return fail_operation("zero-sized read", path);
	}
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return fail_operation("open for read", path);
	result = read(fd, buffer, size);
	saved_errno = errno;
	if (close(fd) < 0 && result >= 0)
		return fail_operation("close after read", path);
	errno = saved_errno;
	if (result <= 0)
		return fail_operation("read", path);
	return result;
}

static int write_path(const char *path, const void *buffer, size_t size)
{
	ssize_t result;
	int saved_errno;
	int fd;

	fd = open(path, O_WRONLY | O_CLOEXEC);
	if (fd < 0)
		return fail_operation("open for write", path);
	result = write(fd, buffer, size);
	saved_errno = errno;
	if (close(fd) < 0 && result >= 0)
		return fail_operation("close after write", path);
	errno = saved_errno;
	if (result != (ssize_t)size)
		return fail_operation("write", path);
	return 0;
}

static int rewrite_path(const char *path)
{
	char value[256];
	ssize_t length = read_path(path, value, sizeof(value));

	if (length < 0)
		return -1;
	return write_path(path, value, (size_t)length);
}

static int probe_read_error(const char *path)
{
	char value[256];
	ssize_t result;
	int saved_errno;
	int fd = open(path, O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open probe read", path);
	result = read(fd, value, sizeof(value));
	saved_errno = errno;
	if (close(fd) < 0 && result >= 0)
		return fail_operation("close probe read", path);
	if (result >= 0)
		return 0;
	errno = saved_errno;
	if (errno == EINVAL || errno == EOPNOTSUPP || errno == EACCES ||
	    errno == EPERM)
		return 0;
	return fail_operation("probe read", path);
}

static int probe_write_error(const char *path, const char *value)
{
	ssize_t result;
	int saved_errno;
	int fd = open(path, O_WRONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open probe write", path);
	result = write(fd, value, strlen(value));
	saved_errno = errno;
	if (close(fd) < 0 && result >= 0)
		return fail_operation("close probe write", path);
	if (result >= 0)
		return 0;
	errno = saved_errno;
	if (errno == EINVAL || errno == EOPNOTSUPP || errno == EACCES ||
	    errno == EPERM || errno == EBUSY)
		return 0;
	return fail_operation("probe write", path);
}

static int exercise_proc_mem(void)
{
	char target[16] = "LM-V40-MEM";
	char observed[sizeof(target)];
	off_t address = (off_t)(uintptr_t)target;
	int fd = open("/proc/self/mem", O_RDWR | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open", "/proc/self/mem");
	if (lseek(fd, address, SEEK_SET) != address) {
		close(fd);
		return fail_operation("lseek read", "/proc/self/mem");
	}
	if (read(fd, observed, sizeof(observed)) != (ssize_t)sizeof(observed)) {
		close(fd);
		return fail_operation("read", "/proc/self/mem");
	}
	if (memcmp(target, observed, sizeof(target)) != 0) {
		close(fd);
		errno = EIO;
		return fail_operation("compare", "/proc/self/mem");
	}
	if (lseek(fd, address, SEEK_SET) != address) {
		close(fd);
		return fail_operation("lseek write", "/proc/self/mem");
	}
	if (write(fd, observed, sizeof(observed)) != (ssize_t)sizeof(observed)) {
		close(fd);
		return fail_operation("write", "/proc/self/mem");
	}
	if (close(fd) < 0)
		return fail_operation("close", "/proc/self/mem");
	return 0;
}

static int rewrite_coredump_filter(void)
{
	char current[64];
	char value[68];
	ssize_t length = read_path("/proc/self/coredump_filter", current,
				   sizeof(current) - 1);
	int output_length;

	if (length < 0)
		return -1;
	current[length] = '\0';
	output_length = snprintf(value, sizeof(value), "0x%s", current);
	if (output_length < 0 || (size_t)output_length >= sizeof(value)) {
		errno = EOVERFLOW;
		return fail_operation("format", "/proc/self/coredump_filter");
	}
	return write_path("/proc/self/coredump_filter", value,
			  (size_t)output_length);
}

static int exercise_loginuid(void)
{
	char current[32];
	ssize_t length = read_path("/proc/self/loginuid", current,
				   sizeof(current) - 1);
	const char *value;

	if (length < 0)
		return -1;
	current[length] = '\0';
	value = strcmp(current, "4294967295") == 0 ? "0" : current;
	return write_path("/proc/self/loginuid", value, strlen(value));
}

static int exercise_proc_callbacks(void)
{
	char buffer[512];

	if (read_path("/proc/self/auxv", buffer, sizeof(buffer)) < 0 ||
	    read_path("/proc/self/environ", buffer, sizeof(buffer)) < 0 ||
	    read_path("/proc/self/cmdline", buffer, sizeof(buffer)) < 0) {
		return -1;
	}
	puts("PROC_PROCESS_READS_OK");
	if (exercise_proc_mem() < 0)
		return -1;
	puts("PROC_MEM_RW_LSEEK_OK");
	if (rewrite_path("/proc/self/oom_adj") < 0 ||
	    rewrite_path("/proc/self/oom_score_adj") < 0 ||
	    rewrite_coredump_filter() < 0) {
		return -1;
	}
	puts("PROC_POLICY_RW_OK");
	if (exercise_loginuid() < 0 ||
	    read_path("/proc/self/sessionid", buffer, sizeof(buffer)) < 0) {
		return -1;
	}
	puts("PROC_AUDIT_RW_OK");
	if (write_path("/proc/self/comm", "lm-v40-trigger\n", 15) < 0 ||
	    rewrite_path("/proc/self/timerslack_ns") < 0) {
		return -1;
	}
	puts("PROC_TASK_WRITE_OK");
	if (probe_read_error("/proc/self/attr/current") < 0 ||
	    probe_write_error("/proc/self/attr/current",
			      "linux-modular-invalid") < 0 ||
	    probe_write_error("/proc/self/timens_offsets",
			      "monotonic 0 0\nboottime 0 0\n") < 0) {
		return -1;
	}
	puts("PROC_LSM_TIMENS_CALLBACKS_OK");
	puts("PROC_RESULT_OK");
	return 0;
}

static int create_sync_fence(int control_fd)
{
	int fd = ioctl(control_fd, LM_SYNC_CREATE_FENCE, 0);

	if (fd < 0)
		fail_operation("create fence ioctl", LM_SYNC_FIXTURE);
	return fd;
}

static int signal_sync_fence(int control_fd, int fence_fd)
{
	if (ioctl(control_fd, LM_SYNC_SIGNAL_FENCE, &fence_fd) < 0)
		return fail_operation("signal fence ioctl", LM_SYNC_FIXTURE);
	return 0;
}

static int get_sync_file_info(int fd, unsigned int expected_minimum)
{
	struct sync_fence_info fences[4];
	struct sync_file_info info;

	memset(&info, 0, sizeof(info));
	if (ioctl(fd, SYNC_IOC_FILE_INFO, &info) < 0)
		return fail_operation("SYNC_IOC_FILE_INFO count", "sync_file");
	if (info.num_fences < expected_minimum ||
	    info.num_fences > sizeof(fences) / sizeof(fences[0])) {
		errno = EPROTO;
		return fail_operation("unexpected fence count", "sync_file");
	}
	memset(fences, 0, sizeof(fences));
	info.sync_fence_info = (uint64_t)(uintptr_t)fences;
	if (ioctl(fd, SYNC_IOC_FILE_INFO, &info) < 0)
		return fail_operation("SYNC_IOC_FILE_INFO details", "sync_file");
	if (strcmp(fences[0].driver_name, "linux-modularizer") != 0) {
		errno = EPROTO;
		return fail_operation("fence driver name", "sync_file");
	}
	return 0;
}

static int exercise_sync_callbacks(void)
{
	struct sync_merge_data merge;
	struct pollfd descriptor;
	int merged = -1;
	int fence_a = -1;
	int fence_b = -1;
	int control = open(LM_SYNC_FIXTURE, O_RDWR | O_CLOEXEC);
	int result = -1;

	if (control < 0)
		return fail_operation("open", LM_SYNC_FIXTURE);
	fence_a = create_sync_fence(control);
	fence_b = create_sync_fence(control);
	if (fence_a < 0 || fence_b < 0)
		goto out;
	descriptor.fd = fence_a;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) != 0) {
		errno = EPROTO;
		fail_operation("unsignaled poll", "sync_file");
		goto out;
	}
	if (get_sync_file_info(fence_a, 1) < 0)
		goto out;
	puts("SYNC_POLL_INFO_OK");
	memset(&merge, 0, sizeof(merge));
	memcpy(merge.name, "lm-v40-merged", sizeof("lm-v40-merged"));
	merge.fd2 = fence_b;
	if (ioctl(fence_a, SYNC_IOC_MERGE, &merge) < 0) {
		fail_operation("SYNC_IOC_MERGE", "sync_file");
		goto out;
	}
	merged = merge.fence;
	if (merged < 0 || get_sync_file_info(merged, 2) < 0)
		goto out;
	puts("SYNC_MERGE_INFO_OK");
	if (signal_sync_fence(control, fence_a) < 0 ||
	    signal_sync_fence(control, fence_b) < 0) {
		goto out;
	}
	descriptor.fd = merged;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    !(descriptor.revents & POLLIN)) {
		errno = ETIMEDOUT;
		fail_operation("signaled poll", "sync_file");
		goto out;
	}
	puts("SYNC_SIGNAL_POLL_OK");
	result = 0;
out:
	if (merged >= 0)
		close(merged);
	if (fence_b >= 0)
		close(fence_b);
	if (fence_a >= 0)
		close(fence_a);
	close(control);
	if (!result)
		puts("SYNC_RESULT_OK");
	return result;
}

static int write_marker(const char *path, const char *value)
{
	ssize_t length = (ssize_t)strlen(value);
	int saved_errno;
	int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);

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

static int wait_for_marker(const char *path)
{
	struct stat status;
	int attempt;

	for (attempt = 0; attempt < 3000; ++attempt) {
		if (stat(path, &status) == 0)
			return 0;
		usleep(10000);
	}
	errno = ETIMEDOUT;
	return fail_operation("wait for marker", path);
}

static int hold_proc_across_unload(const char *ready, const char *go,
				   const char *result_path)
{
	char buffer[256];
	int fd = open("/proc/self/cmdline", O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open held", "/proc/self/cmdline");
	if (read(fd, buffer, sizeof(buffer)) <= 0) {
		close(fd);
		return fail_operation("first held read", "/proc/self/cmdline");
	}
	puts("PROC_HELD_FIRST_READ_OK");
	if (write_marker(ready, "ready\n") < 0 || wait_for_marker(go) < 0) {
		close(fd);
		return 1;
	}
	if (lseek(fd, 0, SEEK_SET) < 0 ||
	    read(fd, buffer, sizeof(buffer)) <= 0) {
		close(fd);
		return fail_operation("second held read", "/proc/self/cmdline");
	}
	if (close(fd) < 0)
		return fail_operation("close held", "/proc/self/cmdline");
	puts("PROC_HELD_RELOAD_OK");
	if (write_marker(result_path, "RESULT_OK\n") < 0)
		return fail_operation("write result marker", result_path);
	puts("HOLD_RESULT_OK");
	return 0;
}

static int hold_sync_across_unload(const char *ready, const char *go,
				   const char *result_path)
{
	int control = open(LM_SYNC_FIXTURE, O_RDWR | O_CLOEXEC);
	int fence;
	int result = 1;

	if (control < 0)
		return fail_operation("open held", LM_SYNC_FIXTURE);
	fence = create_sync_fence(control);
	if (fence < 0)
		goto out_control;
	if (get_sync_file_info(fence, 1) < 0)
		goto out;
	puts("SYNC_HELD_FIRST_INFO_OK");
	if (write_marker(ready, "ready\n") < 0 || wait_for_marker(go) < 0)
		goto out;
	if (get_sync_file_info(fence, 1) < 0)
		goto out;
	puts("SYNC_HELD_RELOAD_OK");
	if (write_marker(result_path, "RESULT_OK\n") < 0) {
		fail_operation("write result marker", result_path);
		goto out;
	}
	puts("HOLD_RESULT_OK");
	result = 0;
out:
	close(fence);
out_control:
	close(control);
	return result;
}

int main(int argc, char **argv)
{
	if (argc == 1) {
		if (exercise_proc_callbacks() < 0 ||
		    exercise_sync_callbacks() < 0)
			return 1;
		puts("RESULT_OK");
		return 0;
	}
	if (argc == 2 && strcmp(argv[1], "--proc") == 0)
		return exercise_proc_callbacks() < 0;
	if (argc == 2 && strcmp(argv[1], "--sync") == 0)
		return exercise_sync_callbacks() < 0;
	if (argc == 5 && strcmp(argv[1], "--hold-proc") == 0)
		return hold_proc_across_unload(argv[2], argv[3], argv[4]);
	if (argc == 5 && strcmp(argv[1], "--hold-sync") == 0)
		return hold_sync_across_unload(argv[2], argv[3], argv[4]);
	fprintf(stderr,
		"usage: %s [--proc|--sync|--hold-proc READY GO RESULT|"
		"--hold-sync READY GO RESULT]\n",
		argv[0]);
	return 2;
}
