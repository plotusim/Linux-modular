#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
/bin/busybox mkdir -p /sys/kernel/debug /sys/kernel/tracing
/bin/busybox mkdir -p /dev/mqueue
mount -t debugfs debugfs /sys/kernel/debug
mount -t tracefs tracefs /sys/kernel/tracing
mount -t mqueue mqueue /dev/mqueue

modprobe lm_regmap_fixture || \
	echo LINUX_MODULARIZER_REGMAP_FIXTURE_FAILED

initially_absent=1
for module in \
	deferred_auto_cb_kernel_trace_trace_events_a9a7b715fd35 \
	deferred_auto_cb_drivers_base_regmap_regma_a8eb410686ea \
	deferred_auto_cb_drivers_tty_tty_io_7ab4f7a6c4c8 \
	deferred_auto_cb_fs_kernfs_file_9247db54417a \
	deferred_auto_cb_fs_proc_page_620ea7b2acb7 \
	deferred_auto_cb_kernel_profile_1dbe7829132f \
	deferred_auto_cb_ipc_mqueue_2e1a2714473a \
	deferred_auto_cb_fs_timerfd_f898da10e2db
do
	test ! -d "/sys/module/$module" || initially_absent=0
done
if test "$initially_absent" -eq 1 && \
	test -r /sys/kernel/debug/regmap/dummy-lm_v43/name && \
	test -r /sys/kernel/tracing/events/sched/sched_switch/id && \
	test -r /proc/kpagecount && test -r /proc/profile
then
	/bin/busybox touch /tmp/v43-initial-absent
	echo LINUX_MODULARIZER_V43_INITIAL_ABSENT
else
	echo LINUX_MODULARIZER_V43_INITIAL_STATE_FAILED
fi

read uptime_seconds _ < /proc/uptime
awk -v uptime_seconds="$uptime_seconds" '
    /^MemAvailable:/ { available = $2 }
    /^MemFree:/ { free = $2 }
    /^Slab:/ { slab = $2 }
    /^SReclaimable:/ { sreclaimable = $2 }
    /^SUnreclaim:/ { sunreclaim = $2 }
    END {
        printf "LINUX_MODULARIZER_METRICS uptime_seconds=%s mem_available_kb=%d mem_free_kb=%d slab_kb=%d sreclaimable_kb=%d sunreclaim_kb=%d\n", uptime_seconds, available, free, slab, sreclaimable, sunreclaim
    }
' /proc/meminfo
echo LINUX_MODULARIZER_READY
setsid cttyhack sh
poweroff -f
