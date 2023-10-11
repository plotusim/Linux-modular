
#ifdef CONFIG_X86_64
static unsigned long orig_cr0;

static inline void do_write_cr0(unsigned long val)
{
	asm volatile("mov %0,%%cr0": "+r" (val) : : "memory");
}

static inline void *disable_write_protect(void *addr)
{
	BUG_ON(orig_cr0);

	orig_cr0 = read_cr0();
	do_write_cr0(orig_cr0 & 0xfffeffff);

	return (void *)addr;
}

static inline void enable_write_protect(void)
{
	do_write_cr0(orig_cr0);
	orig_cr0 = 0;
}

#else
#include <asm/insn.h>
#include <asm/fixmap.h>
#include <asm/memory.h>
#include <asm/cacheflush.h>

static void *disable_write_protect(void *addr)
{
	unsigned long uintaddr = (uintptr_t) addr;
	struct page *page;

	page = phys_to_page(__pa_symbol(addr));

	return (void *)set_fixmap_offset(FIX_TEXT_POKE0, page_to_phys(page) +
			(uintaddr & ~PAGE_MASK));
}

static inline void enable_write_protect(void)
{
	clear_fixmap(FIX_TEXT_POKE0);
}

#endif