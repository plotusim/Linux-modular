// SPDX-License-Identifier: GPL-2.0
/* QEMU-only fixture for creating a real dma_buf descriptor. */

#include <linux/dma-buf.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/slab.h>

#define LM_DMA_BUF_CREATE _IO('L', 0x50)

struct lm_dma_buf_allocation {
	unsigned long cookie;
};

static struct sg_table *
lm_dma_buf_map(struct dma_buf_attachment *attachment,
	       enum dma_data_direction direction)
{
	return ERR_PTR(-EOPNOTSUPP);
}

static void lm_dma_buf_unmap(struct dma_buf_attachment *attachment,
			     struct sg_table *table,
			     enum dma_data_direction direction)
{
}

static void lm_dma_buf_release(struct dma_buf *buffer)
{
	kfree(buffer->priv);
}

static int lm_dma_buf_cpu_access(struct dma_buf *buffer,
				 enum dma_data_direction direction)
{
	return 0;
}

static const struct dma_buf_ops lm_dma_buf_ops = {
	.map_dma_buf = lm_dma_buf_map,
	.unmap_dma_buf = lm_dma_buf_unmap,
	.release = lm_dma_buf_release,
	.begin_cpu_access = lm_dma_buf_cpu_access,
	.end_cpu_access = lm_dma_buf_cpu_access,
};

static long lm_dma_buf_create(void)
{
	struct lm_dma_buf_allocation *allocation;
	struct dma_buf *buffer;
	DEFINE_DMA_BUF_EXPORT_INFO(info);
	int fd;

	allocation = kzalloc(sizeof(*allocation), GFP_KERNEL);
	if (!allocation)
		return -ENOMEM;
	allocation->cookie = 0x4c4d444d41425546UL;
	info.ops = &lm_dma_buf_ops;
	info.size = PAGE_SIZE;
	info.flags = O_RDWR;
	info.priv = allocation;
	buffer = dma_buf_export(&info);
	if (IS_ERR(buffer)) {
		kfree(allocation);
		return PTR_ERR(buffer);
	}

	fd = dma_buf_fd(buffer, O_CLOEXEC);
	if (fd < 0)
		dma_buf_put(buffer);
	return fd;
}

static long lm_dma_buf_ioctl(struct file *file, unsigned int command,
			     unsigned long argument)
{
	if (command == LM_DMA_BUF_CREATE)
		return lm_dma_buf_create();
	return -ENOTTY;
}

static const struct file_operations lm_dma_buf_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = lm_dma_buf_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = lm_dma_buf_ioctl,
#endif
};

static struct miscdevice lm_dma_buf_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "lm_dma_buf_fixture",
	.fops = &lm_dma_buf_fops,
	.mode = 0600,
};

static int __init lm_dma_buf_init(void)
{
	return misc_register(&lm_dma_buf_device);
}

static void __exit lm_dma_buf_exit(void)
{
	misc_deregister(&lm_dma_buf_device);
}

module_init(lm_dma_buf_init);
module_exit(lm_dma_buf_exit);

MODULE_DESCRIPTION("Linux modularizer dma_buf QEMU fixture");
MODULE_LICENSE("GPL");
