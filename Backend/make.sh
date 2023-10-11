sudo bash gen_fs.sh

sudo make modules_install INSTALL_MOD_PATH=_install

sudo bash fs.sh


qemu-system-x86_64 -machine pc-q35-2.9 -smp 4 -m 4G -kernel \
arch/x86_64/boot/bzImage -nographic -drive file=rootfs_ext4.img,format=raw -\
append "root=/dev/sda noinitrd console=ttyS0"\