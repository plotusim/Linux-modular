/* SPDX-License-Identifier: GPL-2.0 */
#ifndef LINUX_MODULARIZER_FIXTURE_H
#define LINUX_MODULARIZER_FIXTURE_H

/*
 * The compiler barriers make this validation body large enough to prove that
 * code really moved out of vmlinux; they are not part of the modularizer
 * runtime itself.
 */
#define LM_FIXTURE_STEP(value, constant) do {                         \
	(value) ^= (constant);                                         \
	asm volatile("" : "+r" (value));                               \
	(value) = ((value) << 7) | ((unsigned int)(value) >> 25);       \
	(value) += 0x9e3779b9U;                                        \
	asm volatile("" : "+r" (value));                               \
} while (0)

#endif
