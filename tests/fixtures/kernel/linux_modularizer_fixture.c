// SPDX-License-Identifier: GPL-2.0
#include <linux/init.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>

#include "linux_modularizer_fixture.h"

static noinline __noclone int modularizer_heavy(unsigned int value)
{
	LM_FIXTURE_STEP(value, 0x00000001U);
	LM_FIXTURE_STEP(value, 0x00000002U);
	LM_FIXTURE_STEP(value, 0x00000004U);
	LM_FIXTURE_STEP(value, 0x00000008U);
	LM_FIXTURE_STEP(value, 0x00000010U);
	LM_FIXTURE_STEP(value, 0x00000020U);
	LM_FIXTURE_STEP(value, 0x00000040U);
	LM_FIXTURE_STEP(value, 0x00000080U);
	LM_FIXTURE_STEP(value, 0x00000100U);
	LM_FIXTURE_STEP(value, 0x00000200U);
	LM_FIXTURE_STEP(value, 0x00000400U);
	LM_FIXTURE_STEP(value, 0x00000800U);
	LM_FIXTURE_STEP(value, 0x00001000U);
	LM_FIXTURE_STEP(value, 0x00002000U);
	LM_FIXTURE_STEP(value, 0x00004000U);
	LM_FIXTURE_STEP(value, 0x00008000U);
	LM_FIXTURE_STEP(value, 0x00010000U);
	LM_FIXTURE_STEP(value, 0x00020000U);
	LM_FIXTURE_STEP(value, 0x00040000U);
	LM_FIXTURE_STEP(value, 0x00080000U);
	LM_FIXTURE_STEP(value, 0x00100000U);
	LM_FIXTURE_STEP(value, 0x00200000U);
	LM_FIXTURE_STEP(value, 0x00400000U);
	LM_FIXTURE_STEP(value, 0x00800000U);
	LM_FIXTURE_STEP(value, 0x01000000U);
	LM_FIXTURE_STEP(value, 0x02000000U);
	LM_FIXTURE_STEP(value, 0x04000000U);
	LM_FIXTURE_STEP(value, 0x08000000U);
	LM_FIXTURE_STEP(value, 0x10000000U);
	LM_FIXTURE_STEP(value, 0x20000000U);
	LM_FIXTURE_STEP(value, 0x40000000U);
	LM_FIXTURE_STEP(value, 0x80000000U);
	LM_FIXTURE_STEP(value, 0x13579bdfU);
	LM_FIXTURE_STEP(value, 0x2468ace0U);
	LM_FIXTURE_STEP(value, 0x55aa55aaU);
	LM_FIXTURE_STEP(value, 0xaa55aa55U);
	LM_FIXTURE_STEP(value, 0xdeadbeefU);
	LM_FIXTURE_STEP(value, 0xc001d00dU);
	LM_FIXTURE_STEP(value, 0x31415926U);
	LM_FIXTURE_STEP(value, 0x27182818U);
	LM_FIXTURE_STEP(value, 0x10203040U);
	LM_FIXTURE_STEP(value, 0x50607080U);
	LM_FIXTURE_STEP(value, 0x89abcdefU);
	LM_FIXTURE_STEP(value, 0xfedcba98U);
	LM_FIXTURE_STEP(value, 0x0f0f0f0fU);
	LM_FIXTURE_STEP(value, 0xf0f0f0f0U);
	LM_FIXTURE_STEP(value, 0x33333333U);
	LM_FIXTURE_STEP(value, 0xccccccccU);
	LM_FIXTURE_STEP(value, 0x5a5a5a5aU);
	LM_FIXTURE_STEP(value, 0xa5a5a5a5U);
	LM_FIXTURE_STEP(value, 0x11223344U);
	LM_FIXTURE_STEP(value, 0x55667788U);
	LM_FIXTURE_STEP(value, 0x99aabbccU);
	LM_FIXTURE_STEP(value, 0xddeeff00U);
	LM_FIXTURE_STEP(value, 0x6a09e667U);
	LM_FIXTURE_STEP(value, 0xbb67ae85U);
	LM_FIXTURE_STEP(value, 0x3c6ef372U);
	LM_FIXTURE_STEP(value, 0xa54ff53aU);
	LM_FIXTURE_STEP(value, 0x510e527fU);
	LM_FIXTURE_STEP(value, 0x9b05688cU);
	LM_FIXTURE_STEP(value, 0x1f83d9abU);
	LM_FIXTURE_STEP(value, 0x5be0cd19U);
	LM_FIXTURE_STEP(value, 0x7f4a7c15U);
	LM_FIXTURE_STEP(value, 0x94d049bbU);
	return value;
}

static int modularizer_fixture_show(struct seq_file *file, void *unused)
{
	seq_printf(file, "%08x\n", modularizer_heavy(7));
	return 0;
}

static int __init modularizer_fixture_init(void)
{
	proc_create_single("linux_modularizer_fixture", 0, NULL,
			   modularizer_fixture_show);
	return 0;
}
fs_initcall(modularizer_fixture_init);
