/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define SELINUXFS "/sys/fs/selinux"

static int fail_operation(const char *operation, const char *path)
{
	fprintf(stderr, "%s(%s): %s\n", operation, path, strerror(errno));
	return -1;
}

static int write_marker(const char *path, const char *value)
{
	ssize_t length = (ssize_t)strlen(value);
	int saved_errno;
	int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);

	if (fd < 0)
		return fail_operation("open marker", path);
	if (write(fd, value, (size_t)length) != length) {
		saved_errno = errno;
		close(fd);
		errno = saved_errno;
		return fail_operation("write marker", path);
	}
	if (close(fd) < 0)
		return fail_operation("close marker", path);
	return 0;
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

static int exercise_pipe_fds(int read_fd, int write_fd)
{
	static const char payload[] = "linux-modular-pipe";
	struct pollfd descriptor;
	char observed[sizeof(payload)];
	int available = -1;
	int flags;

	flags = fcntl(read_fd, F_GETFL);
	if (flags < 0 || fcntl(read_fd, F_SETOWN, getpid()) < 0 ||
	    fcntl(read_fd, F_SETFL, flags | O_ASYNC | O_NONBLOCK) < 0 ||
	    fcntl(read_fd, F_SETFL, flags | O_NONBLOCK) < 0)
		return fail_operation("pipe fasync", "pipe");
	if (write(write_fd, payload, sizeof(payload)) !=
	    (ssize_t)sizeof(payload))
		return fail_operation("write", "pipe");
	if (ioctl(read_fd, FIONREAD, &available) < 0)
		return fail_operation("FIONREAD", "pipe");
	if (available != (int)sizeof(payload)) {
		errno = EPROTO;
		return fail_operation("unexpected readable bytes", "pipe");
	}
	descriptor.fd = read_fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    !(descriptor.revents & POLLIN)) {
		errno = ETIMEDOUT;
		return fail_operation("poll", "pipe");
	}
	if (read(read_fd, observed, sizeof(observed)) !=
	    (ssize_t)sizeof(observed))
		return fail_operation("read", "pipe");
	if (memcmp(observed, payload, sizeof(payload)) != 0) {
		errno = EIO;
		return fail_operation("compare", "pipe");
	}
	return 0;
}

static int exercise_pipe(void)
{
	int descriptors[2];
	int result;

	if (pipe2(descriptors, O_CLOEXEC) < 0)
		return fail_operation("pipe2", "pipe");
	result = exercise_pipe_fds(descriptors[0], descriptors[1]);
	close(descriptors[0]);
	close(descriptors[1]);
	if (result < 0)
		return result;
	puts("PIPE_RESULT_OK");
	return 0;
}

static int exercise_vcs_fd(int fd)
{
	struct pollfd descriptor;
	unsigned char value;
	int flags;

	if (lseek(fd, 0, SEEK_SET) < 0 || read(fd, &value, 1) != 1)
		return fail_operation("read", "/dev/vcs");
	if (lseek(fd, 0, SEEK_SET) < 0 || write(fd, &value, 1) != 1)
		return fail_operation("write", "/dev/vcs");
	descriptor.fd = fd;
	descriptor.events = POLLIN | POLLPRI;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) < 0)
		return fail_operation("poll", "/dev/vcs");
	flags = fcntl(fd, F_GETFL);
	if (flags < 0 || fcntl(fd, F_SETOWN, getpid()) < 0 ||
	    fcntl(fd, F_SETFL, flags | O_ASYNC) < 0 ||
	    fcntl(fd, F_SETFL, flags & ~O_ASYNC) < 0)
		return fail_operation("fasync", "/dev/vcs");
	return 0;
}

static int exercise_vcs(void)
{
	int fd = open("/dev/vcs", O_RDWR | O_CLOEXEC);
	int result;

	if (fd < 0)
		return fail_operation("open", "/dev/vcs");
	result = exercise_vcs_fd(fd);
	close(fd);
	if (result < 0)
		return result;
	puts("VCS_RESULT_OK");
	return 0;
}

static int expected_vga_write_error(int error)
{
	return error == EPROTO || error == ENODEV || error == EBUSY ||
	       error == EINVAL;
}

static int exercise_vga_fd(int fd)
{
	static const char target[] = "target default";
	struct pollfd descriptor;
	char buffer[1024];
	ssize_t result;

	result = read(fd, buffer, sizeof(buffer));
	if (result <= 0)
		return fail_operation("read", "/dev/vga_arbiter");
	descriptor.fd = fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    !(descriptor.revents & POLLIN)) {
		errno = ETIMEDOUT;
		return fail_operation("poll", "/dev/vga_arbiter");
	}
	result = write(fd, target, sizeof(target) - 1);
	if (result < 0 && !expected_vga_write_error(errno))
		return fail_operation("write", "/dev/vga_arbiter");
	return 0;
}

static int exercise_vga(void)
{
	int fd = open("/dev/vga_arbiter", O_RDWR | O_CLOEXEC);
	int result;

	if (fd < 0)
		return fail_operation("open", "/dev/vga_arbiter");
	result = exercise_vga_fd(fd);
	close(fd);
	if (result < 0)
		return result;
	puts("VGA_RESULT_OK");
	return 0;
}

static int read_path(const char *path, char *buffer, size_t size)
{
	ssize_t result;
	int saved_errno;
	int fd = open(path, O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open for read", path);
	result = read(fd, buffer, size);
	saved_errno = errno;
	close(fd);
	if (result <= 0) {
		errno = saved_errno;
		return fail_operation("read", path);
	}
	return (int)result;
}

static int probe_read_without_policy(const char *path)
{
	char buffer[512];
	ssize_t result;
	int saved_errno;
	int fd = open(path, O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open read probe", path);
	result = read(fd, buffer, sizeof(buffer));
	saved_errno = errno;
	close(fd);
	if (result >= 0)
		return 0;
	errno = saved_errno;
	if (errno == EINVAL || errno == EOPNOTSUPP || errno == EACCES ||
	    errno == EPERM)
		return 0;
	return fail_operation("read probe", path);
}

static int rewrite_path(const char *path)
{
	char buffer[256];
	ssize_t result;
	int length;
	int saved_errno;
	int fd = open(path, O_RDWR | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open for rewrite", path);
	result = read(fd, buffer, sizeof(buffer) - 1);
	if (result <= 0) {
		saved_errno = errno;
		close(fd);
		errno = saved_errno;
		return fail_operation("read for rewrite", path);
	}
	length = (int)result;
	while (length > 0 && (buffer[length - 1] == '\n' ||
			      buffer[length - 1] == '\0'))
		--length;
	if (lseek(fd, 0, SEEK_SET) < 0) {
		saved_errno = errno;
		close(fd);
		errno = saved_errno;
		return fail_operation("seek for rewrite", path);
	}
	result = write(fd, buffer, (size_t)length);
	saved_errno = errno;
	close(fd);
	if (result == length)
		return 0;
	errno = saved_errno;
	if (errno == EACCES || errno == EPERM || errno == EINVAL ||
	    errno == EBUSY)
		return 0;
	return fail_operation("rewrite", path);
}

static int probe_invalid_write(const char *path)
{
	static const char invalid[] = "linux-modular-invalid";
	ssize_t result;
	int saved_errno;
	int fd = open(path, O_WRONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open write probe", path);
	result = write(fd, invalid, sizeof(invalid) - 1);
	saved_errno = errno;
	close(fd);
	if (result >= 0)
		return 0;
	errno = saved_errno;
	if (errno == EACCES || errno == EPERM || errno == EINVAL ||
	    errno == EPROTO || errno == ENOENT)
		return 0;
	return fail_operation("write probe", path);
}

static int exercise_selinux(void)
{
	static const char *const readable[] = {
		SELINUXFS "/policyvers",
		SELINUXFS "/enforce",
		SELINUXFS "/mls",
		SELINUXFS "/checkreqprot",
		SELINUXFS "/deny_unknown",
		SELINUXFS "/reject_unknown",
		SELINUXFS "/avc/cache_threshold",
		SELINUXFS "/avc/hash_stats",
	};
	char buffer[512];
	size_t index;

	for (index = 0; index < sizeof(readable) / sizeof(readable[0]);
	     ++index) {
		if (read_path(readable[index], buffer, sizeof(buffer)) < 0)
			return -1;
	}
	if (probe_read_without_policy(SELINUXFS "/ss/sidtab_hash_stats") < 0 ||
	    probe_read_without_policy(SELINUXFS "/initial_contexts/kernel") < 0)
		return -1;
	if (rewrite_path(SELINUXFS "/avc/cache_threshold") < 0 ||
	    rewrite_path(SELINUXFS "/checkreqprot") < 0 ||
	    rewrite_path(SELINUXFS "/enforce") < 0 ||
	    probe_invalid_write(SELINUXFS "/access") < 0 ||
	    probe_invalid_write(SELINUXFS "/validatetrans") < 0)
		return -1;
	puts("SELINUX_RESULT_OK");
	return 0;
}

static int hold_pipe(const char *ready, const char *go,
		     const char *result_path)
{
	int descriptors[2];
	int result = 1;

	if (pipe2(descriptors, O_CLOEXEC) < 0)
		return fail_operation("pipe2 held", "pipe");
	if (exercise_pipe_fds(descriptors[0], descriptors[1]) < 0)
		goto out;
	puts("PIPE_HELD_FIRST_OK");
	if (write_marker(ready, "ready\n") < 0 || wait_for_marker(go) < 0)
		goto out;
	if (exercise_pipe_fds(descriptors[0], descriptors[1]) < 0)
		goto out;
	puts("PIPE_HELD_RELOAD_OK");
	if (write_marker(result_path, "RESULT_OK\n") < 0)
		goto out;
	puts("HOLD_RESULT_OK");
	result = 0;
out:
	close(descriptors[0]);
	close(descriptors[1]);
	return result;
}

static int hold_device(const char *kind, const char *path,
		       const char *ready, const char *go,
		       const char *result_path)
{
	int open_flags = strcmp(kind, "SELINUX") == 0 ? O_RDONLY : O_RDWR;
	int fd = open(path, open_flags | O_CLOEXEC);
	int result = 1;
	int first;
	int second;

	if (fd < 0)
		return fail_operation("open held", path);
	if (strcmp(kind, "VCS") == 0) {
		first = exercise_vcs_fd(fd);
	} else if (strcmp(kind, "VGA") == 0) {
		first = exercise_vga_fd(fd);
	} else {
		char buffer[64];
		first = read(fd, buffer, sizeof(buffer)) > 0 ? 0 : -1;
	}
	if (first < 0)
		goto out;
	printf("%s_HELD_FIRST_OK\n", kind);
	if (write_marker(ready, "ready\n") < 0 || wait_for_marker(go) < 0)
		goto out;
	if (strcmp(kind, "VCS") == 0) {
		second = exercise_vcs_fd(fd);
	} else if (strcmp(kind, "VGA") == 0) {
		second = exercise_vga_fd(fd);
	} else {
		char buffer[64];
		second = lseek(fd, 0, SEEK_SET) >= 0 &&
			 read(fd, buffer, sizeof(buffer)) > 0 ? 0 : -1;
	}
	if (second < 0) {
		errno = EIO;
		fail_operation("second held operation", path);
		goto out;
	}
	printf("%s_HELD_RELOAD_OK\n", kind);
	if (write_marker(result_path, "RESULT_OK\n") < 0)
		goto out;
	puts("HOLD_RESULT_OK");
	result = 0;
out:
	close(fd);
	return result;
}

static int exercise_all(void)
{
	if (exercise_pipe() < 0 || exercise_vcs() < 0 ||
	    exercise_vga() < 0 || exercise_selinux() < 0)
		return -1;
	puts("RESULT_OK");
	return 0;
}

int main(int argc, char **argv)
{
	signal(SIGIO, SIG_IGN);
	if (argc == 1)
		return exercise_all() < 0;
	if (argc == 2 && strcmp(argv[1], "--pipe") == 0)
		return exercise_pipe() < 0;
	if (argc == 2 && strcmp(argv[1], "--vcs") == 0)
		return exercise_vcs() < 0;
	if (argc == 2 && strcmp(argv[1], "--vga") == 0)
		return exercise_vga() < 0;
	if (argc == 2 && strcmp(argv[1], "--selinux") == 0)
		return exercise_selinux() < 0;
	if (argc == 5 && strcmp(argv[1], "--hold-pipe") == 0)
		return hold_pipe(argv[2], argv[3], argv[4]);
	if (argc == 5 && strcmp(argv[1], "--hold-vcs") == 0)
		return hold_device("VCS", "/dev/vcs", argv[2], argv[3],
				   argv[4]);
	if (argc == 5 && strcmp(argv[1], "--hold-vga") == 0)
		return hold_device("VGA", "/dev/vga_arbiter", argv[2], argv[3],
				   argv[4]);
	if (argc == 5 && strcmp(argv[1], "--hold-selinux") == 0)
		return hold_device("SELINUX", SELINUXFS "/policyvers", argv[2],
				   argv[3], argv[4]);
	fprintf(stderr,
		"usage: %s [--pipe|--vcs|--vga|--selinux|"
		"--hold-{pipe,vcs,vga,selinux} READY GO RESULT]\n",
		argv[0]);
	return 2;
}
