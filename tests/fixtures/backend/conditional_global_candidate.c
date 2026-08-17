static const char conditional_text[] =
	"active; text"
#if 0
	"inactive; text"
#endif
	;

int conditional_text_size(void)
{
	return sizeof(conditional_text);
}
