// SPDX-License-Identifier: GPL-2.0-only
/* Hardware-independent oracle for tracking normal callees of a late initcall. */
#include <linux/delay.h>
#include <linux/init.h>
#include <linux/module.h>
#include "../../driver/probe.h"

static noinline void sample_after_sleep(void)
{
	msleep(10);
	kcfg_hit(KCFG_TEST_ID);
}

static int __init sample_module_init(void)
{
	sample_after_sleep();
	return 0;
}
module_init(sample_module_init);
MODULE_DESCRIPTION("Test synchronous initializer probe attribution");
MODULE_LICENSE("GPL");
