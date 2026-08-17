/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/filter.h>
#include <linux/rtc.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/inotify.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define LM_DMA_BUF_CREATE _IO('L', 0x50)
#define LM_RELAY_WRITE _IO('L', 0x51)

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

static int read_fdinfo(int fd)
{
	char path[64];
	char buffer[1024];
	int info_fd;

	if (snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", fd) < 0)
		return -1;
	info_fd = open(path, O_RDONLY | O_CLOEXEC);
	if (info_fd < 0)
		return fail_operation("open fdinfo", path);
	if (read(info_fd, buffer, sizeof(buffer)) <= 0) {
		close(info_fd);
		return fail_operation("read fdinfo", path);
	}
	return close(info_fd);
}

static int exercise_eventfd_fd(int fd)
{
	struct pollfd descriptor = { .fd = fd, .events = POLLIN };
	uint64_t value = 7;
	uint64_t observed = 0;

	if (write(fd, &value, sizeof(value)) != (ssize_t)sizeof(value))
		return fail_operation("write", "eventfd");
	if (poll(&descriptor, 1, 1000) != 1 ||
	    !(descriptor.revents & POLLIN)) {
		errno = ETIMEDOUT;
		return fail_operation("poll", "eventfd");
	}
	if (read(fd, &observed, sizeof(observed)) !=
	    (ssize_t)sizeof(observed) || observed != value) {
		errno = EIO;
		return fail_operation("read", "eventfd");
	}
	return read_fdinfo(fd);
}

static int exercise_eventfd(void)
{
	int fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
	int result;

	if (fd < 0)
		return fail_operation("eventfd", "eventfd");
	result = exercise_eventfd_fd(fd);
	close(fd);
	if (result < 0)
		return result;
	puts("EVENTFD_RESULT_OK");
	return 0;
}

static int hold_eventfd(const char *ready, const char *go,
			const char *result_path)
{
	int fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
	int result = 1;

	if (fd < 0)
		return fail_operation("eventfd", "held eventfd");
	if (exercise_eventfd_fd(fd) < 0 ||
	    write_marker(ready, "ready\n") < 0 || wait_for_marker(go) < 0 ||
	    exercise_eventfd_fd(fd) < 0 ||
	    write_marker(result_path, "RESULT_OK\n") < 0)
		goto out;
	puts("EVENTFD_HELD_RELOAD_OK");
	result = 0;
out:
	close(fd);
	return result;
}

static int exercise_signalfd(void)
{
	struct signalfd_siginfo info;
	struct pollfd descriptor;
	sigset_t mask;
	int fd;

	sigemptyset(&mask);
	sigaddset(&mask, SIGUSR1);
	if (sigprocmask(SIG_BLOCK, &mask, NULL) < 0)
		return fail_operation("sigprocmask", "signalfd");
	fd = signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK);
	if (fd < 0)
		return fail_operation("signalfd", "signalfd");
	if (kill(getpid(), SIGUSR1) < 0) {
		close(fd);
		return fail_operation("kill", "signalfd");
	}
	descriptor.fd = fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    read(fd, &info, sizeof(info)) != (ssize_t)sizeof(info) ||
	    info.ssi_signo != SIGUSR1 || read_fdinfo(fd) < 0) {
		close(fd);
		errno = EIO;
		return fail_operation("poll/read", "signalfd");
	}
	close(fd);
	puts("SIGNALFD_RESULT_OK");
	return 0;
}

static int exercise_socket(void)
{
	static const char payload[] = "linux-modular-socket";
	struct pollfd descriptor;
	char observed[sizeof(payload)];
	int available = 0;
	int descriptors[2];

	if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, descriptors) < 0)
		return fail_operation("socketpair", "socket");
	if (write(descriptors[0], payload, sizeof(payload)) !=
	    (ssize_t)sizeof(payload))
		goto fail;
	if (ioctl(descriptors[1], FIONREAD, &available) < 0 ||
	    available != (int)sizeof(payload))
		goto fail;
	descriptor.fd = descriptors[1];
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    read(descriptors[1], observed, sizeof(observed)) !=
	    (ssize_t)sizeof(observed) ||
	    memcmp(payload, observed, sizeof(payload)) != 0 ||
	    read_fdinfo(descriptors[1]) < 0)
		goto fail;
	close(descriptors[0]);
	close(descriptors[1]);
	puts("SOCKET_RESULT_OK");
	return 0;
fail:
	close(descriptors[0]);
	close(descriptors[1]);
	return fail_operation("I/O", "socket");
}

static int exercise_inotify(void)
{
	struct pollfd descriptor;
	char events[4096];
	char path[128];
	int available = 0;
	int watch;
	int file;
	int fd = inotify_init1(IN_CLOEXEC | IN_NONBLOCK);

	if (fd < 0)
		return fail_operation("inotify_init1", "inotify");
	watch = inotify_add_watch(fd, "/tmp", IN_CREATE | IN_CLOSE_WRITE);
	if (watch < 0)
		goto fail;
	snprintf(path, sizeof(path), "/tmp/lm-inotify-%ld", (long)getpid());
	file = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (file < 0 || write(file, "x", 1) != 1 || close(file) < 0)
		goto fail;
	descriptor.fd = fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 ||
	    ioctl(fd, FIONREAD, &available) < 0 || available <= 0 ||
	    read(fd, events, sizeof(events)) <= 0)
		goto fail;
	unlink(path);
	close(fd);
	puts("INOTIFY_RESULT_OK");
	return 0;
fail:
	unlink(path);
	close(fd);
	return fail_operation("event", "inotify");
}

static int exercise_shmem(void)
{
	const char *path = "/mnt/tmpfs/lm-shmem";
	char buffer[32] = "linux-modular-shmem";
	int fd = open(path, O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);

	if (fd < 0)
		return fail_operation("open", path);
	if (write(fd, buffer, sizeof(buffer)) != (ssize_t)sizeof(buffer) ||
	    fallocate(fd, 0, 0, 8192) < 0 ||
	    lseek(fd, 0, SEEK_END) != 8192 ||
	    (lseek(fd, 0, SEEK_DATA) < 0 && errno != ENXIO) ||
	    (lseek(fd, 0, SEEK_HOLE) < 0 && errno != ENXIO)) {
		close(fd);
		return fail_operation("fallocate/lseek", path);
	}
	close(fd);
	unlink(path);
	puts("SHMEM_RESULT_OK");
	return 0;
}

static int exercise_hugetlb(void)
{
	const char *path = "/mnt/huge/lm-huge";
	char value;
	ssize_t length;
	int fd = open(path, O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);

	if (fd < 0)
		return fail_operation("open", path);
	if (fallocate(fd, 0, 0, 2 * 1024 * 1024) < 0 &&
	    errno != ENOSPC && errno != EINVAL && errno != EOPNOTSUPP) {
		close(fd);
		return fail_operation("fallocate", path);
	}
	length = pread(fd, &value, 1, 0);
	if (length < 0 && errno != ENOSPC && errno != EINVAL &&
	    errno != ENODATA) {
		close(fd);
		return fail_operation("read", path);
	}
	close(fd);
	unlink(path);
	puts("HUGETLB_RESULT_OK");
	return 0;
}

static int exercise_kcore(void)
{
	char buffer[4096];
	ssize_t length;
	int fd = open("/proc/kcore", O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open", "/proc/kcore");
	length = pread(fd, buffer, sizeof(buffer), 0);
	if (length < 0 && errno != EFAULT && errno != EINVAL &&
	    errno != EPERM) {
		close(fd);
		return fail_operation("pread", "/proc/kcore");
	}
	close(fd);
	puts("KCORE_RESULT_OK");
	return 0;
}

static int exercise_mtrr(void)
{
	static const char invalid[] = "linux-modular-invalid\n";
	int result;
	int fd = open("/proc/mtrr", O_WRONLY | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open", "/proc/mtrr");
	result = (int)write(fd, invalid, sizeof(invalid) - 1);
	if (result < 0 && errno != EINVAL && errno != ENOSYS &&
	    errno != EPERM) {
		close(fd);
		return fail_operation("write", "/proc/mtrr");
	}
	if (ioctl(fd, _IO('L', 0x7a), 0) < 0 && errno != ENOTTY &&
	    errno != EINVAL) {
		close(fd);
		return fail_operation("ioctl", "/proc/mtrr");
	}
	close(fd);
	puts("MTRR_RESULT_OK");
	return 0;
}

static int exercise_rtc(void)
{
	struct rtc_time time;
	struct pollfd descriptor;
	unsigned long data;
	int flags;
	int fd = open("/dev/rtc0", O_RDONLY | O_NONBLOCK | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open", "/dev/rtc0");
	if (ioctl(fd, RTC_RD_TIME, &time) < 0)
		goto fail;
	descriptor.fd = fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) < 0)
		goto fail;
	if (read(fd, &data, sizeof(data)) < 0 && errno != EAGAIN)
		goto fail;
	flags = fcntl(fd, F_GETFL);
	if (flags < 0 || fcntl(fd, F_SETOWN, getpid()) < 0 ||
	    fcntl(fd, F_SETFL, flags | O_ASYNC) < 0 ||
	    fcntl(fd, F_SETFL, flags & ~O_ASYNC) < 0)
		goto fail;
	close(fd);
	puts("RTC_RESULT_OK");
	return 0;
fail:
	close(fd);
	return fail_operation("ioctl/read/poll/fasync", "/dev/rtc0");
}

static int exercise_snapshot(void)
{
	char value;
	int fd = open("/dev/snapshot", O_RDONLY | O_NONBLOCK | O_CLOEXEC);

	if (fd < 0)
		return fail_operation("open", "/dev/snapshot");
	if (read(fd, &value, 1) < 0 && errno != ENODATA && errno != EINVAL) {
		close(fd);
		return fail_operation("read", "/dev/snapshot");
	}
	if (ioctl(fd, _IO('L', 0x7b), 0) < 0 && errno != ENOTTY) {
		close(fd);
		return fail_operation("ioctl", "/dev/snapshot");
	}
	close(fd);
	puts("SNAPSHOT_RESULT_OK");
	return 0;
}

static int open_first_entry(const char *directory, char *path, size_t size)
{
	struct dirent *entry;
	DIR *stream = opendir(directory);
	int fd = -1;

	if (!stream)
		return -1;
	while ((entry = readdir(stream)) != NULL) {
		if (entry->d_name[0] == '.')
			continue;
		if (snprintf(path, size, "%s/%s", directory, entry->d_name) < 0)
			break;
		fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
		if (fd >= 0)
			break;
	}
	closedir(stream);
	return fd;
}

static int exercise_pci(void)
{
	char path[256];
	char buffer[256];
	int fd = open_first_entry("/proc/bus/pci/00", path, sizeof(path));

	if (fd < 0)
		return fail_operation("open first entry", "/proc/bus/pci/00");
	if (pread(fd, buffer, sizeof(buffer), 0) <= 0 ||
	    lseek(fd, 0, SEEK_SET) < 0) {
		close(fd);
		return fail_operation("read/lseek", path);
	}
	if (ioctl(fd, _IO('L', 0x7c), 0) < 0 && errno != ENOTTY &&
	    errno != EINVAL) {
		close(fd);
		return fail_operation("ioctl", path);
	}
	close(fd);
	puts("PCI_RESULT_OK");
	return 0;
}

static int exercise_bsg(void)
{
	char path[256];
	int fd = open_first_entry("/dev/bsg", path, sizeof(path));

	if (fd < 0)
		return fail_operation("open first entry", "/dev/bsg");
	if (ioctl(fd, _IO('L', 0x7d), 0) < 0 && errno != ENOTTY &&
	    errno != EINVAL) {
		close(fd);
		return fail_operation("ioctl", path);
	}
	close(fd);
	puts("BSG_RESULT_OK");
	return 0;
}

static int exercise_dma_buf(void)
{
	struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_RW |
						DMA_BUF_SYNC_START };
	struct pollfd descriptor;
	int control = open("/dev/lm_dma_buf_fixture", O_RDWR | O_CLOEXEC);
	int fd;

	if (control < 0)
		return fail_operation("open", "/dev/lm_dma_buf_fixture");
	fd = ioctl(control, LM_DMA_BUF_CREATE, 0);
	close(control);
	if (fd < 0)
		return fail_operation("create", "dma_buf");
	if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0 ||
	    ioctl(fd, DMA_BUF_SET_NAME, "lm-v42") < 0 ||
	    lseek(fd, 0, SEEK_END) != 4096 || lseek(fd, 0, SEEK_SET) != 0 ||
	    read_fdinfo(fd) < 0) {
		close(fd);
		return fail_operation("ioctl/lseek/fdinfo", "dma_buf");
	}
	descriptor.fd = fd;
	descriptor.events = POLLIN | POLLOUT;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 0) < 0) {
		close(fd);
		return fail_operation("poll", "dma_buf");
	}
	sync.flags = DMA_BUF_SYNC_RW | DMA_BUF_SYNC_END;
	if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) {
		close(fd);
		return fail_operation("sync end", "dma_buf");
	}
	close(fd);
	puts("DMA_BUF_RESULT_OK");
	return 0;
}

static int exercise_relay(void)
{
	char buffer[4096];
	struct pollfd descriptor;
	ssize_t spliced;
	int pipes[2];
	int control = open("/dev/lm_relay_fixture", O_RDWR | O_CLOEXEC);
	int fd;

	if (control < 0)
		return fail_operation("open", "/dev/lm_relay_fixture");
	if (ioctl(control, LM_RELAY_WRITE, 0) < 0) {
		close(control);
		return fail_operation("write ioctl", "relay");
	}
	fd = open("/sys/kernel/debug/lm_relay0", O_RDONLY | O_NONBLOCK |
		  O_CLOEXEC);
	if (fd < 0) {
		close(control);
		return fail_operation("open", "/sys/kernel/debug/lm_relay0");
	}
	descriptor.fd = fd;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	if (poll(&descriptor, 1, 1000) != 1 || read(fd, buffer, sizeof(buffer)) <= 0)
		goto fail;
	if (ioctl(control, LM_RELAY_WRITE, 0) < 0 || pipe2(pipes, O_CLOEXEC) < 0)
		goto fail;
	spliced = splice(fd, NULL, pipes[1], NULL, sizeof(buffer), 0);
	if (spliced < 0 && errno != EINVAL && errno != EAGAIN) {
		close(pipes[0]);
		close(pipes[1]);
		goto fail;
	}
	if (spliced > 0 && read(pipes[0], buffer, sizeof(buffer)) <= 0) {
		close(pipes[0]);
		close(pipes[1]);
		goto fail;
	}
	if (spliced < 0)
		puts("RELAY_SPLICE_EXPECTED_ERROR_OK");
	close(pipes[0]);
	close(pipes[1]);
	close(fd);
	close(control);
	puts("RELAY_RESULT_OK");
	return 0;
fail:
	close(fd);
	close(control);
	return fail_operation("poll/read/splice", "relay");
}

static int exercise_seccomp(void)
{
	struct sock_filter filter[] = {
		BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
			 offsetof(struct seccomp_data, nr)),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_getppid, 0, 1),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
	};
	struct sock_fprog program = {
		.len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
		.filter = filter,
	};
	struct seccomp_notif request;
	struct seccomp_notif_resp response;
	struct pollfd descriptor;
	uint64_t id;
	pid_t child;
	int status;
	int listener;

	if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
		return fail_operation("PR_SET_NO_NEW_PRIVS", "seccomp");
	listener = (int)syscall(SYS_seccomp, SECCOMP_SET_MODE_FILTER,
				SECCOMP_FILTER_FLAG_NEW_LISTENER, &program);
	if (listener < 0)
		return fail_operation("NEW_LISTENER", "seccomp");
	child = fork();
	if (child < 0) {
		close(listener);
		return fail_operation("fork", "seccomp");
	}
	if (child == 0) {
		long value = syscall(SYS_getppid);
		_exit(value == 4242 ? 0 : 3);
	}
	descriptor.fd = listener;
	descriptor.events = POLLIN;
	descriptor.revents = 0;
	memset(&request, 0, sizeof(request));
	memset(&response, 0, sizeof(response));
	if (poll(&descriptor, 1, 1000) != 1 ||
	    ioctl(listener, SECCOMP_IOCTL_NOTIF_RECV, &request) < 0)
		goto fail;
	id = request.id;
	if (ioctl(listener, SECCOMP_IOCTL_NOTIF_ID_VALID, &id) < 0)
		goto fail;
	response.id = request.id;
	response.val = 4242;
	if (ioctl(listener, SECCOMP_IOCTL_NOTIF_SEND, &response) < 0)
		goto fail;
	if (waitpid(child, &status, 0) != child || !WIFEXITED(status) ||
	    WEXITSTATUS(status) != 0)
		goto fail;
	close(listener);
	puts("SECCOMP_RESULT_OK");
	return 0;
fail:
	kill(child, SIGKILL);
	waitpid(child, NULL, 0);
	close(listener);
	return fail_operation("poll/ioctl", "seccomp");
}

int main(int argc, char **argv)
{
	signal(SIGIO, SIG_IGN);
	if (argc == 2 && strcmp(argv[1], "--eventfd") == 0)
		return exercise_eventfd() < 0;
	if (argc == 2 && strcmp(argv[1], "--signalfd") == 0)
		return exercise_signalfd() < 0;
	if (argc == 2 && strcmp(argv[1], "--socket") == 0)
		return exercise_socket() < 0;
	if (argc == 2 && strcmp(argv[1], "--inotify") == 0)
		return exercise_inotify() < 0;
	if (argc == 2 && strcmp(argv[1], "--shmem") == 0)
		return exercise_shmem() < 0;
	if (argc == 2 && strcmp(argv[1], "--hugetlb") == 0)
		return exercise_hugetlb() < 0;
	if (argc == 2 && strcmp(argv[1], "--kcore") == 0)
		return exercise_kcore() < 0;
	if (argc == 2 && strcmp(argv[1], "--mtrr") == 0)
		return exercise_mtrr() < 0;
	if (argc == 2 && strcmp(argv[1], "--rtc") == 0)
		return exercise_rtc() < 0;
	if (argc == 2 && strcmp(argv[1], "--snapshot") == 0)
		return exercise_snapshot() < 0;
	if (argc == 2 && strcmp(argv[1], "--pci") == 0)
		return exercise_pci() < 0;
	if (argc == 2 && strcmp(argv[1], "--bsg") == 0)
		return exercise_bsg() < 0;
	if (argc == 2 && strcmp(argv[1], "--dma-buf") == 0)
		return exercise_dma_buf() < 0;
	if (argc == 2 && strcmp(argv[1], "--relay") == 0)
		return exercise_relay() < 0;
	if (argc == 2 && strcmp(argv[1], "--seccomp") == 0)
		return exercise_seccomp() < 0;
	if (argc == 5 && strcmp(argv[1], "--hold-eventfd") == 0)
		return hold_eventfd(argv[2], argv[3], argv[4]);
	fprintf(stderr, "usage: %s --{eventfd,signalfd,socket,inotify,shmem,"
		"hugetlb,kcore,mtrr,rtc,snapshot,pci,bsg,dma-buf,relay,seccomp}"
		" | --hold-eventfd READY GO RESULT\n", argv[0]);
	return 2;
}
