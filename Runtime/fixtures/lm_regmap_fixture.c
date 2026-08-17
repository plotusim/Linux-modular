// SPDX-License-Identifier: GPL-2.0
/* QEMU-only fixture exposing a real regmap debugfs descriptor. */

#include <linux/err.h>
#include <linux/module.h>
#include <linux/regmap.h>

static unsigned int lm_regmap_registers[4] = {
	0x4c,
	0x4d,
	0x34,
	0x33,
};
static struct regmap *lm_regmap;

static int lm_regmap_read(void *context, unsigned int reg,
			  unsigned int *value)
{
	unsigned int *registers = context;

	if (reg >= ARRAY_SIZE(lm_regmap_registers))
		return -EINVAL;
	*value = registers[reg];
	return 0;
}

static int lm_regmap_write(void *context, unsigned int reg,
			   unsigned int value)
{
	unsigned int *registers = context;

	if (reg >= ARRAY_SIZE(lm_regmap_registers))
		return -EINVAL;
	registers[reg] = value;
	return 0;
}

static const struct reg_default lm_regmap_defaults[] = {
	{ 0, 0x4c },
	{ 1, 0x4d },
	{ 2, 0x34 },
	{ 3, 0x33 },
};

static const struct regmap_config lm_regmap_config = {
	.name = "lm_v43",
	.reg_bits = 8,
	.val_bits = 8,
	.reg_stride = 1,
	.max_register = 3,
	.reg_read = lm_regmap_read,
	.reg_write = lm_regmap_write,
	.reg_defaults = lm_regmap_defaults,
	.num_reg_defaults = ARRAY_SIZE(lm_regmap_defaults),
	.cache_type = REGCACHE_FLAT,
};

static int __init lm_regmap_fixture_init(void)
{
	lm_regmap = regmap_init(NULL, NULL, lm_regmap_registers,
				&lm_regmap_config);
	return PTR_ERR_OR_ZERO(lm_regmap);
}

static void __exit lm_regmap_fixture_exit(void)
{
	regmap_exit(lm_regmap);
}

module_init(lm_regmap_fixture_init);
module_exit(lm_regmap_fixture_exit);

MODULE_DESCRIPTION("Linux modularizer regmap QEMU fixture");
MODULE_LICENSE("GPL");
