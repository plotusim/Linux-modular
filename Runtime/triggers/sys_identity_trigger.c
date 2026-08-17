/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/times.h>
#include <sys/utsname.h>
#include <unistd.h>

static int restore_name(long number, const char *value, size_t length)
{
	if (syscall(number, value, length) < 0) {
		perror("restore namespace name");
		return -1;
	}
	return 0;
}

int run_sys_identity_tests(void)
{
	static const char probe_host[] = "lm-v28";
	static const char probe_domain[] = "lm-domain";
	struct rlimit limit;
	struct tms times_value;
	struct utsname original;
	struct utsname observed;
	long raw_priority;
	int nice_value;

	errno = 0;
	raw_priority = syscall(SYS_getpriority, PRIO_PROCESS, 0);
	if (raw_priority < 0 && errno) {
		perror("getpriority");
		return 1;
	}
	if (raw_priority < 1 || raw_priority > 40) {
		fprintf(stderr, "unexpected raw priority: %ld\n", raw_priority);
		return 1;
	}
	nice_value = 20 - (int)raw_priority;
	if (syscall(SYS_setpriority, PRIO_PROCESS, 0, nice_value) < 0) {
		perror("setpriority");
		return 1;
	}
	if (syscall(SYS_times, &times_value) < 0) {
		perror("times");
		return 1;
	}
	if (syscall(SYS_getrlimit, RLIMIT_NOFILE, &limit) < 0 ||
	    limit.rlim_cur == 0) {
		perror("getrlimit");
		return 1;
	}
	if (syscall(SYS_setpgid, 0, 0) < 0) {
		perror("setpgid");
		return 1;
	}
	if (uname(&original) < 0) {
		perror("uname");
		return 1;
	}
	if (syscall(SYS_sethostname, probe_host, sizeof(probe_host) - 1) < 0) {
		perror("sethostname");
		return 1;
	}
	if (uname(&observed) < 0 ||
	    strcmp(observed.nodename, probe_host) != 0) {
		fprintf(stderr, "hostname verification failed\n");
		restore_name(
			SYS_sethostname,
			original.nodename,
			strlen(original.nodename));
		return 1;
	}
	if (restore_name(
		    SYS_sethostname,
		    original.nodename,
		    strlen(original.nodename)) < 0)
		return 1;

	if (syscall(
		    SYS_setdomainname,
		    probe_domain,
		    sizeof(probe_domain) - 1) < 0) {
		perror("setdomainname");
		return 1;
	}
	if (uname(&observed) < 0 ||
	    strcmp(observed.domainname, probe_domain) != 0) {
		fprintf(stderr, "domainname verification failed\n");
		restore_name(
			SYS_setdomainname,
			original.domainname,
			strlen(original.domainname));
		return 1;
	}
	if (restore_name(
		    SYS_setdomainname,
		    original.domainname,
		    strlen(original.domainname)) < 0)
		return 1;

	return 0;
}

#ifndef LINUX_MODULARIZER_TRIGGER_LIBRARY
int main(void)
{
	if (run_sys_identity_tests() != 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
#endif
