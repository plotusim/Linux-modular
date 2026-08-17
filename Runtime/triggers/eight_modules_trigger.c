/* SPDX-License-Identifier: GPL-2.0 */
#define LINUX_MODULARIZER_TRIGGER_LIBRARY

#include "ioprio_splice_setns_quotactl_trigger.c"
#include "stat_trigger.c"
#include "sys_identity_trigger.c"
#include "capability_trigger.c"
#include "wallclock_trigger.c"

int main(void)
{
	if (run_ioprio_splice_setns_quotactl_tests() != 0 ||
	    run_stat_tests() != 0 ||
	    run_sys_identity_tests() != 0 ||
	    run_capability_tests() != 0 ||
	    run_wallclock_tests() != 0)
		return 1;
	puts("RESULT_OK");
	return 0;
}
