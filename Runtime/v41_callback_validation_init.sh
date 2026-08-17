#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
/bin/busybox mkdir -p /sys/fs/selinux
if mount -t selinuxfs selinuxfs /sys/fs/selinux; then
	echo LINUX_MODULARIZER_SELINUXFS_READY
else
	echo LINUX_MODULARIZER_SELINUXFS_MOUNT_FAILED
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
