/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define TRACE_ROOT "/sys/kernel/tracing"

static int trace_path(char *buffer, size_t size, const char *relative)
{
	int length = snprintf(buffer, size, "%s/%s", TRACE_ROOT, relative);

	if (length < 0 || (size_t)length >= size) {
		errno = ENAMETOOLONG;
		return -1;
	}
	return 0;
}

static int fail_path(const char *operation, const char *relative)
{
	fprintf(stderr, "%s(%s): %s\n", operation, relative, strerror(errno));
	return -1;
}

static ssize_t read_value(const char *relative, char *buffer, size_t size)
{
	char path[256];
	ssize_t result;
	int saved_errno;
	int fd;

	if (size < 2 || trace_path(path, sizeof(path), relative) < 0)
		return -1;
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return fail_path("open", relative);
	result = read(fd, buffer, size - 1);
	saved_errno = errno;
	if (close(fd) < 0 && result >= 0)
		return fail_path("close", relative);
	errno = saved_errno;
	if (result <= 0)
		return fail_path("read", relative);
	buffer[result] = '\0';
	return result;
}

static int write_value(const char *relative, const void *buffer, size_t size)
{
	char path[256];
	ssize_t result;
	int saved_errno;
	int fd;

	if (trace_path(path, sizeof(path), relative) < 0)
		return -1;
	fd = open(path, O_WRONLY | O_CLOEXEC);
	if (fd < 0)
		return fail_path("open", relative);
	result = write(fd, buffer, size);
	saved_errno = errno;
	if (close(fd) < 0 && result >= 0)
		return fail_path("close", relative);
	errno = saved_errno;
	if (result != (ssize_t)size)
		return fail_path("write", relative);
	return 0;
}

static int rewrite_first_line(const char *relative)
{
	char value[512];
	ssize_t length = read_value(relative, value, sizeof(value));
	char *newline;

	if (length < 0)
		return -1;
	newline = memchr(value, '\n', (size_t)length);
	if (newline != NULL)
		length = newline - value + 1;
	return write_value(relative, value, (size_t)length);
}

static int rewrite_first_token(const char *relative)
{
	char value[512];
	ssize_t length = read_value(relative, value, sizeof(value));
	ssize_t token_length = 0;

	if (length < 0)
		return -1;
	while (token_length < length && value[token_length] != ' ' &&
	       value[token_length] != '\t' && value[token_length] != '\n') {
		++token_length;
	}
	if (!token_length) {
		errno = EINVAL;
		return fail_path("parse first token", relative);
	}
	value[token_length++] = '\n';
	return write_value(relative, value, (size_t)token_length);
}

static int rewrite_trace_clock(void)
{
	char value[1024];
	char selected[128];
	ssize_t length = read_value("trace_clock", value, sizeof(value));
	char *begin;
	char *end;
	size_t selected_length;

	if (length < 0)
		return -1;
	begin = memchr(value, '[', (size_t)length);
	if (begin == NULL) {
		errno = EINVAL;
		return fail_path("find selected clock", "trace_clock");
	}
	end = memchr(begin + 1, ']', (size_t)(value + length - begin - 1));
	if (end == NULL) {
		errno = EINVAL;
		return fail_path("find selected clock", "trace_clock");
	}
	selected_length = (size_t)(end - begin - 1);
	if (selected_length + 1 >= sizeof(selected)) {
		errno = EOVERFLOW;
		return fail_path("copy selected clock", "trace_clock");
	}
	memcpy(selected, begin + 1, selected_length);
	selected[selected_length++] = '\n';
	return write_value("trace_clock", selected, selected_length);
}

static int exercise_llseek_and_stub_write(void)
{
	char path[256];
	int fd;

	if (trace_path(path, sizeof(path), "trace") < 0)
		return -1;
	fd = open(path, O_RDWR | O_CLOEXEC);
	if (fd < 0)
		return fail_path("open", "trace");
	if (lseek(fd, 0, SEEK_SET) < 0) {
		close(fd);
		return fail_path("lseek", "trace");
	}
	if (write(fd, "x", 1) != 1) {
		close(fd);
		return fail_path("stub write", "trace");
	}
	if (close(fd) < 0)
		return fail_path("close", "trace");
	return 0;
}

static int exercise_stream(const char *relative, int raw)
{
	char path[256];
	char byte;
	struct pollfd descriptor;
	ssize_t result;
	int pipe_fds[2];
	int fd;

	if (trace_path(path, sizeof(path), relative) < 0)
		return -1;
	fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0)
		return fail_path("open", relative);
	descriptor.fd = fd;
	descriptor.events = POLLIN | POLLHUP;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) < 0) {
		close(fd);
		return fail_path("poll", relative);
	}
	result = read(fd, &byte, sizeof(byte));
	if (result < 0 && errno != EAGAIN) {
		close(fd);
		return fail_path("nonblocking read", relative);
	}
	if (pipe2(pipe_fds, O_CLOEXEC | O_NONBLOCK) < 0) {
		close(fd);
		return fail_path("pipe2", relative);
	}
	result = splice(fd, NULL, pipe_fds[1], NULL, raw ? 4096 : 1,
			SPLICE_F_NONBLOCK);
	if (result < 0 && errno != EAGAIN) {
		close(pipe_fds[0]);
		close(pipe_fds[1]);
		close(fd);
		return fail_path("nonblocking splice", relative);
	}
	close(pipe_fds[0]);
	close(pipe_fds[1]);
	if (close(fd) < 0)
		return fail_path("close", relative);
	return 0;
}

static int exercise_callbacks(void)
{
	static const char *const rewrite_files[] = {
		"buffer_percent",
		"current_tracer",
		"options/overwrite",
		"saved_cmdlines_size",
		"trace_options",
		"tracing_cpumask",
		"tracing_on",
		"tracing_thresh",
	};
	char buffer[256];
	uint32_t raw_marker[2] = { UINT32_C(0x4c4d5452), 0 };
	size_t index;

	if (read_value("README", buffer, sizeof(buffer)) < 0 ||
	    read_value("buffer_total_size_kb", buffer, sizeof(buffer)) < 0) {
		return -1;
	}
	puts("TRACE_READS_OK");
	for (index = 0; index < sizeof(rewrite_files) /
					   sizeof(rewrite_files[0]); ++index) {
		if (rewrite_first_line(rewrite_files[index]) < 0)
			return -1;
	}
	if (rewrite_first_token("buffer_size_kb") < 0)
		return -1;
	if (rewrite_trace_clock() < 0)
		return -1;
	puts("TRACE_CONFIG_RW_OK");
	if (write_value("error_log", "x\n", 2) < 0 ||
	    write_value("trace_marker_raw", raw_marker,
			sizeof(raw_marker)) < 0) {
		return -1;
	}
	if (exercise_llseek_and_stub_write() < 0)
		return -1;
	puts("TRACE_WRITE_LSEEK_OK");
	if (exercise_stream("trace_pipe", 0) < 0 ||
	    exercise_stream("per_cpu/cpu0/trace_pipe_raw", 1) < 0) {
		return -1;
	}
	puts("TRACE_STREAM_CALLBACKS_OK");
	return 0;
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

static int hold_across_unload(const char *ready, const char *go,
			      const char *result_path)
{
	char path[256];
	char buffer[64];
	struct stat status;
	int attempt;
	int fd;

	if (trace_path(path, sizeof(path), "README") < 0)
		return 1;
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return fail_path("open held", "README");
	if (read(fd, buffer, sizeof(buffer)) <= 0) {
		close(fd);
		return fail_path("first held read", "README");
	}
	puts("TRACE_HELD_FIRST_READ_OK");
	if (write_marker(ready, "ready\n") < 0) {
		perror("write ready marker");
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
		close(fd);
		return 1;
	}
	if (read(fd, buffer, sizeof(buffer)) <= 0) {
		close(fd);
		return fail_path("second held read", "README");
	}
	puts("TRACE_HELD_RELOAD_READ_OK");
	if (close(fd) < 0)
		return fail_path("close held", "README");
	if (exercise_callbacks() < 0)
		return 1;
	if (write_marker(result_path, "RESULT_OK\n") < 0) {
		perror("write result marker");
		return 1;
	}
	puts("HOLD_RESULT_OK");
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 5 && strcmp(argv[1], "--hold") == 0)
		return hold_across_unload(argv[2], argv[3], argv[4]);
	if (argc != 1) {
		fprintf(stderr, "usage: %s [--hold READY GO RESULT]\n", argv[0]);
		return 2;
	}
	if (exercise_callbacks() < 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
