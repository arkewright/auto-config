#include "sibling.h"

/* These definitions remain callable, but an entry probe is not appropriate. */
__attribute__((section(".init.text"))) int early(void) { return 1; }
__attribute__((section(".exit.text"))) int teardown(void) { return 2; }
__attribute__((section(".noinstr.text"))) int noinstr_path(void) { return 3; }
__attribute__((no_instrument_function)) int notrace_path(void) { return 4; }
__attribute__((no_profile_instrument_function)) int noprofile_path(void) { return 5; }
__attribute__((no_sanitize("address"))) int nosanitize_path(void) { return 6; }
__attribute__((const)) int constant_path(void) { return 7; }
__attribute__((pure)) int pure_path(int const *v) { return *v; }
__attribute__((cold)) int cold_path(void) { return 8; }
__attribute__((patchable_function_entry(0, 0))) int patch_notrace(void) { return 0; }
#if defined(__x86_64__) || defined(__aarch64__)
__attribute__((naked)) void raw_entry(void) { __asm__(""); }
#endif
static inline int inline_path(int n) { return n + 1; }
int init_module(void) { return 9; }
int kcfg_helper(void) { return 10; }

extern unsigned kcfg_seen(void);
__attribute__((noinline)) int exercise(int value)
{
	return kcfg_seen() == 1 ? header_helper(inline_path(value)) : -1;
}

/* A prototype, a later declaration, and an alias must not duplicate the probe. */
int other(int);
int other(int n) { return n - 1; }
int other(int);
extern __typeof(other) other_alias __attribute__((alias("other")));
