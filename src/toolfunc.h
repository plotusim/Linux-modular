
//x86下，打开和关闭写保护相关函数
static unsigned long orig_cr0;

static inline void do_write_cr0(unsigned long val)
{
	asm volatile("mov %0,%%cr0": "+r" (val) : : "memory");
}

static inline void disable_write_protect(void)
{

	orig_cr0 = read_cr0();
	do_write_cr0(orig_cr0 & 0xfffeffff);

}

static inline void enable_write_protect(void)
{
	do_write_cr0(orig_cr0);
	orig_cr0 = 0;
}
