dd if=/dev/zero of=rootfs_ext4.img bs=1M count=8192
mkfs.ext4 rootfs_ext4.img
mkdir -p tmpfs
mount -t ext4 rootfs_ext4.img tmpfs/ -o loop
cp -af _install/* tmpfs/
sudo umount tmpfs
rm -rf tmpfs
chmod 777 rootfs_ext4.img
