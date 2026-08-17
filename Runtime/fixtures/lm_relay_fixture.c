// SPDX-License-Identifier: GPL-2.0
/* QEMU-only fixture exposing a real relay_file_operations descriptor. */

#include <linux/debugfs.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/relay.h>

#define LM_RELAY_WRITE _IO('L', 0x51)

static struct rchan *lm_relay_channel;

static int lm_relay_subbuf_start(struct rchan_buf *buffer, void *subbuffer,
				 void *previous, size_t padding)
{
	return 1;
}

static struct dentry *lm_relay_create_file(const char *filename,
					   struct dentry *parent,
					   umode_t mode,
					   struct rchan_buf *buffer,
					   int *is_global)
{
	*is_global = 1;
	return debugfs_create_file(filename, 0400, parent, buffer,
				   &relay_file_operations);
}

static int lm_relay_remove_file(struct dentry *dentry)
{
	debugfs_remove(dentry);
	return 0;
}

static struct rchan_callbacks lm_relay_callbacks = {
	.subbuf_start = lm_relay_subbuf_start,
	.create_buf_file = lm_relay_create_file,
	.remove_buf_file = lm_relay_remove_file,
};

static long lm_relay_ioctl(struct file *file, unsigned int command,
			   unsigned long argument)
{
	static const char payload[] = "linux-modular-relay\n";

	if (command != LM_RELAY_WRITE)
		return -ENOTTY;
	relay_write(lm_relay_channel, payload, sizeof(payload));
	relay_flush(lm_relay_channel);
	return 0;
}

static const struct file_operations lm_relay_control_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = lm_relay_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = lm_relay_ioctl,
#endif
};

static struct miscdevice lm_relay_control = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "lm_relay_fixture",
	.fops = &lm_relay_control_fops,
	.mode = 0600,
};

static int __init lm_relay_init(void)
{
	int result;

	lm_relay_channel = relay_open("lm_relay", NULL, PAGE_SIZE, 4,
				      &lm_relay_callbacks, NULL);
	if (!lm_relay_channel)
		return -ENOMEM;
	result = misc_register(&lm_relay_control);
	if (result) {
		relay_close(lm_relay_channel);
		lm_relay_channel = NULL;
	}
	return result;
}

static void __exit lm_relay_exit(void)
{
	misc_deregister(&lm_relay_control);
	relay_close(lm_relay_channel);
}

module_init(lm_relay_init);
module_exit(lm_relay_exit);

MODULE_DESCRIPTION("Linux modularizer relay QEMU fixture");
MODULE_LICENSE("GPL");
