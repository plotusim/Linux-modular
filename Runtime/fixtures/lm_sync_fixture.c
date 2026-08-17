// SPDX-License-Identifier: GPL-2.0
/* QEMU-only fixture for creating real sync_file descriptors. */

#include <linux/dma-fence.h>
#include <linux/fdtable.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/sync_file.h>
#include <linux/uaccess.h>

#define LM_SYNC_CREATE_FENCE _IO('L', 0x40)
#define LM_SYNC_SIGNAL_FENCE _IOW('L', 0x41, int)

struct lm_sync_fence {
	struct dma_fence base;
	spinlock_t lock;
};

static const char *lm_sync_driver_name(struct dma_fence *fence)
{
	return "linux-modularizer";
}

static const char *lm_sync_timeline_name(struct dma_fence *fence)
{
	return "qemu-fixture";
}

static void lm_sync_release(struct dma_fence *fence)
{
	struct lm_sync_fence *fixture =
		container_of(fence, struct lm_sync_fence, base);

	kfree(fixture);
}

static const struct dma_fence_ops lm_sync_fence_ops = {
	.use_64bit_seqno = true,
	.get_driver_name = lm_sync_driver_name,
	.get_timeline_name = lm_sync_timeline_name,
	.release = lm_sync_release,
};

static long lm_sync_create_fence(void)
{
	struct lm_sync_fence *fixture;
	struct sync_file *sync_file;
	int fd;

	fixture = kzalloc(sizeof(*fixture), GFP_KERNEL);
	if (!fixture)
		return -ENOMEM;
	spin_lock_init(&fixture->lock);
	dma_fence_init(&fixture->base, &lm_sync_fence_ops, &fixture->lock,
		       dma_fence_context_alloc(1), 1);

	sync_file = sync_file_create(&fixture->base);
	dma_fence_put(&fixture->base);
	if (!sync_file)
		return -ENOMEM;

	fd = get_unused_fd_flags(O_CLOEXEC);
	if (fd < 0) {
		fput(sync_file->file);
		return fd;
	}
	fd_install(fd, sync_file->file);
	return fd;
}

static long lm_sync_signal_fence(unsigned long argument)
{
	struct dma_fence *fence;
	int fd;
	int result;

	if (copy_from_user(&fd, (void __user *)argument, sizeof(fd)))
		return -EFAULT;
	fence = sync_file_get_fence(fd);
	if (!fence)
		return -EBADF;
	result = dma_fence_signal(fence);
	dma_fence_put(fence);
	return result;
}

static long lm_sync_ioctl(struct file *file, unsigned int command,
			  unsigned long argument)
{
	switch (command) {
	case LM_SYNC_CREATE_FENCE:
		return lm_sync_create_fence();
	case LM_SYNC_SIGNAL_FENCE:
		return lm_sync_signal_fence(argument);
	default:
		return -ENOTTY;
	}
}

static const struct file_operations lm_sync_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = lm_sync_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = lm_sync_ioctl,
#endif
};

static struct miscdevice lm_sync_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "lm_sync_fixture",
	.fops = &lm_sync_fops,
	.mode = 0600,
};

static int __init lm_sync_init(void)
{
	return misc_register(&lm_sync_device);
}

static void __exit lm_sync_exit(void)
{
	misc_deregister(&lm_sync_device);
}

module_init(lm_sync_init);
module_exit(lm_sync_exit);

MODULE_DESCRIPTION("Linux modularizer sync_file QEMU fixture");
MODULE_LICENSE("GPL");
