#!/bin/sh

PATH=/bin:/sbin
export PATH

fail()
{
    echo "LINUX_MODULARIZER_PROFILE_SMOKE_FAILED step=$1"
    poweroff -f
    exit 1
}

require_unloaded()
{
    for module in "$@"; do
        if grep -q "^${module} " /proc/modules; then
            fail "unexpected-module-${module}"
        fi
    done
}

mount -t proc proc /proc || fail mount-proc
mount -t sysfs sysfs /sys || fail mount-sysfs
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true

require_unloaded loop ext4 jbd2 mbcache crc16 ipv6 crc_ccitt \
    af_packet binfmt_misc e1000
echo LINUX_MODULARIZER_PROFILE_MODULES_ABSENT_AT_READY

modprobe loop || fail modprobe-loop
test -b /dev/loop0 || mknod /dev/loop0 b 7 0 || fail mknod-loop0
losetup /dev/loop0 /testdata/ext4.img || fail losetup
modprobe ext4 || fail modprobe-ext4
/bin/busybox mkdir -p /mnt || fail mkdir-mnt
mount -t ext4 -o rw /dev/loop0 /mnt || fail mount-ext4
echo v32-profile-proof > /mnt/proof.txt || fail write-ext4
/bin/busybox sync
test "$(/bin/busybox cat /mnt/proof.txt)" = v32-profile-proof || \
    fail read-ext4
umount /mnt || fail umount-ext4
losetup -d /dev/loop0 || fail detach-loop
rmmod ext4 || fail rmmod-ext4
rmmod jbd2 || fail rmmod-jbd2
rmmod mbcache || fail rmmod-mbcache
rmmod crc16 || fail rmmod-crc16
rmmod loop || fail rmmod-loop
echo LINUX_MODULARIZER_PROFILE_EXT4_LOOP_OK

modprobe ipv6 || fail modprobe-ipv6
modprobe af_packet || fail modprobe-af-packet
/bin/modularizer-trigger || fail socket-trigger
rmmod af_packet || fail rmmod-af-packet
if rmmod ipv6 2>/dev/null; then
    rmmod crc_ccitt || fail rmmod-crc-ccitt
    echo LINUX_MODULARIZER_PROFILE_IPV6_UNLOADED
else
    grep -q '^ipv6 ' /proc/modules || fail verify-sticky-ipv6
    echo LINUX_MODULARIZER_PROFILE_IPV6_STICKY_AFTER_USE
fi
echo LINUX_MODULARIZER_PROFILE_NETWORK_STACK_OK

modprobe binfmt_misc || fail modprobe-binfmt-misc
test -d /proc/sys/fs/binfmt_misc || fail binfmt-misc-directory
mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc || \
    fail mount-binfmt-misc
grep -q binfmt_misc /proc/mounts || fail verify-binfmt-misc
umount /proc/sys/fs/binfmt_misc || fail umount-binfmt-misc
rmmod binfmt_misc || fail rmmod-binfmt-misc
echo LINUX_MODULARIZER_PROFILE_BINFMT_MISC_OK

modprobe e1000 || fail modprobe-e1000
test -d /sys/module/e1000 || fail verify-e1000
rmmod e1000 || fail rmmod-e1000
echo LINUX_MODULARIZER_PROFILE_E1000_OK

require_unloaded loop ext4 jbd2 mbcache crc16 af_packet binfmt_misc e1000
echo LINUX_MODULARIZER_PROFILE_UNLOADABLE_MODULES_UNLOADED
echo LINUX_MODULARIZER_PROFILE_SMOKE_PASSED
poweroff -f
