/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

int run_stat_tests(void)
{
	const char *path = "/tmp/linux-modular-stat-probe";
	const char payload[] = "linux-modular";
	struct stat by_path;
	struct stat by_link;
	struct stat by_fd;
	int fd;

	fd = open(path, O_CREAT | O_TRUNC | O_RDWR, 0600);
	if (fd < 0) {
		perror("open");
		return 1;
	}
	if (write(fd, payload, sizeof(payload) - 1) !=
	    (ssize_t)(sizeof(payload) - 1)) {
		perror("write");
		close(fd);
		return 1;
	}
	if (syscall(SYS_stat, path, &by_path) < 0) {
		perror("stat");
		close(fd);
		return 1;
	}
	if (syscall(SYS_lstat, path, &by_link) < 0) {
		perror("lstat");
		close(fd);
		return 1;
	}
	if (syscall(SYS_fstat, fd, &by_fd) < 0) {
		perror("fstat");
		close(fd);
		return 1;
	}
	if (by_path.st_size != (off_t)(sizeof(payload) - 1) ||
	    by_link.st_size != by_path.st_size ||
	    by_fd.st_size != by_path.st_size ||
	    by_link.st_ino != by_path.st_ino ||
	    by_fd.st_ino != by_path.st_ino ||
	    !S_ISREG(by_path.st_mode)) {
		fprintf(stderr, "stat result mismatch\n");
		close(fd);
		return 1;
	}
	if (close(fd) < 0) {
		perror("close");
		return 1;
	}
	if (unlink(path) < 0 && errno != ENOENT) {
		perror("unlink");
		return 1;
	}
	return 0;
}

#ifndef LINUX_MODULARIZER_TRIGGER_LIBRARY
int main(void)
{
	if (run_stat_tests() != 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
#endif
