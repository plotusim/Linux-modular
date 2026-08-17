// SPDX-License-Identifier: GPL-2.0
#define _GNU_SOURCE

#include <stdio.h>
#include <sys/quota.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void)
{
	long result;

	result = syscall(
		SYS_quotactl,
		QCMD(Q_SYNC, USRQUOTA),
		NULL,
		0,
		NULL
	);
	if (result != 0) {
		perror("quotactl Q_SYNC");
		return 1;
	}
	puts("RESULT_OK");
	return 0;
}
