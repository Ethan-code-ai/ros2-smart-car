/*
 * The CMSIS startup calls newlib's __libc_init_array. With -nostartfiles,
 * supply the empty C runtime hook normally provided by the GCC CRT objects.
 * This C-only application uses no constructors, stdio, or dynamic allocation.
 */
void _init(void)
{
}
