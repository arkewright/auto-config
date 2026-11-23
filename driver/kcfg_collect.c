// SPDX-License-Identifier: GPL-2.0-only
#include <linux/capability.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/uaccess.h>
#include "control.h"

static int kcfg_show(struct seq_file *seq, void *unused)
{
	unsigned int id, count = kcfg_sample_count();

	if (!kcfg_snapshot_ready())
		return -ENODATA;
	seq_printf(seq, "KCFG-TRACE\t1\t%s\n", kcfg_campaign_id());
	for (id = 1; id <= count; ++id) {
		unsigned int sample = kcfg_sample(id);

		seq_printf(seq, "%u\t%u\t%u\t%u\n", id,
			   !!(sample & KCFG_BOOT_HIT), !!(sample & KCFG_WORK_HIT),
			   !!(sample & KCFG_INIT_HIT));
	}
	seq_printf(seq, "END\t%u\n", count);
	return 0;
}

static int kcfg_open(struct inode *inode, struct file *file)
{
	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;
	/* misc_open leaves the device here; seq_open needs the slot. */
	file->private_data = NULL;
	return single_open(file, kcfg_show, NULL);
}

static ssize_t kcfg_write(struct file *file, const char __user *src,
			  size_t len, loff_t *pos)
{
	char cmd[6];
	int ret;

	if (len != 5 && len != 6)
		return -EINVAL;
	if (copy_from_user(cmd, src, len))
		return -EFAULT;
	if (len == 6 && !memcmp(cmd, "start\n", 6))
		ret = kcfg_begin();
	else if (len == 5 && !memcmp(cmd, "stop\n", 5))
		ret = kcfg_end();
	else
		return -EINVAL;
	return ret ? ret : (ssize_t)len;
}

static const struct file_operations kcfg_fops = {
	.owner = THIS_MODULE,
	.open = kcfg_open,
	.read = seq_read,
	.write = kcfg_write,
	.llseek = seq_lseek,
	.release = single_release,
};

static struct miscdevice kcfg_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "kconfig-trace",
	.mode = 0600,
	.fops = &kcfg_fops,
};
module_misc_device(kcfg_device);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Workload kernel configuration capture control and sealed snapshot");
