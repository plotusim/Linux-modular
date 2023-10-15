# **测试方法**

在ubuntu22下配置qemux下x86环境，并进行request_module测试


# **测试步骤**

1. ubuntu下载qemu

2. 下载Linux内核

3. 下载busybox，制作文件系统。

   - 下载busybox

   - ```shell
     cd busybox
     make menuconfig
     ```

   - 将Build static binary选项设置为"Y"

     ```
     setting ----> Build static binary
     ```

   - ```shell
     sudo make -j32
     make install
     ```

   - 查看_install已生成目录下的bin/busybox

   - ```shell
     file bin/busybox
     ```

     查看是否为x86架构下的文件

   - 在_install的目录下创建其他目录：sudo mkdir dev etc lib sys proc tmp var home root mnt

   - 在etc目录下创建文件fstab,profile，inittab，init.d/rcS

   - 制作dev下必要文件。

     ```shell
     cd dev
     sudo mknod console c 5 1
     sudo mknod null c 1 3
     sudo mknod tty1 c 4 1
     ```

   - 将生成的_install文件拷贝到下载的linux内核文件中，命名为 _install

4. 在文件系统中装载模块进行测试

   ```shell
   su make modules_install INSTALL_MOD_PATH=_install
   ```

5. 使用fs.sh生成模拟磁盘

6. 执行qemu命令

   ```shell
   qemu-system-x86_64 -machine pc-q35-2.9 -smp 4 -m 4G -kernel \
   arch/x86_64/boot/bzImage -nographic -drive file=rootfs_ext4.img,format=raw -\
   append "root=/dev/sda noinitrd console=ttyS0"\
   ```
# 测试结果



   

|                           | INTERFACE | NORMAL | DELETE | bzImage  |       | vmlinux  |       |
| ------------------------- | --------- | ------ | ------ | -------- | ----- | -------- | ----- |
| linux-drivers-connector   | 5         | 2      | 0      | 10315584 | 1184  | 67891256 | -1952 |
| linux-fs-exportfs         | 4         | 3      | 0      | 10315456 | 1056  | 67891720 | -1488 |
| linux-fs-nfs_common       | 4         | 1      | 1      | 10314272 | -128  | 67893408 | 200   |
| linux-fs-nls              | 4         | 0      | 0      | 10314240 | -160  | 67893832 | 624   |
| linux-hw_random           | 9         | 1      | 0      | 10313056 | -1344 | 67892112 | -1096 |
| linux-net-sunrpc-auth_gss | 6         | 1      | 0      | 10314752 | 352   | 67894200 | 992   |
| linux-5.10.176            | \         | \      | \      | 10314400 | 0     | 67893208 | 0     |

