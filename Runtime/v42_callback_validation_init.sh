#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
/bin/busybox mkdir -p /sys/kernel/debug /mnt/tmpfs /mnt/huge
mount -t debugfs debugfs /sys/kernel/debug
mount -t tmpfs tmpfs /mnt/tmpfs
if mount -t hugetlbfs hugetlbfs /mnt/huge; then
	echo LINUX_MODULARIZER_HUGETLBFS_READY
else
	echo LINUX_MODULARIZER_HUGETLBFS_FAILED
fi

modprobe rtc_cmos || echo LINUX_MODULARIZER_RTC_SETUP_FAILED
modprobe sd_mod || echo LINUX_MODULARIZER_SD_SETUP_FAILED
modprobe ata_piix || echo LINUX_MODULARIZER_ATA_SETUP_FAILED
modprobe lm_dma_buf_fixture || echo LINUX_MODULARIZER_DMA_FIXTURE_FAILED
modprobe lm_relay_fixture || echo LINUX_MODULARIZER_RELAY_FIXTURE_FAILED
sleep 1

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
