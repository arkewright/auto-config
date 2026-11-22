extern int exercise(int), other_alias(int);
extern int early(void), teardown(void), noinstr_path(void), notrace_path(void);
extern int noprofile_path(void), nosanitize_path(void), constant_path(void);
extern int pure_path(int const *), cold_path(void), init_module(void), kcfg_helper(void);

static unsigned hits;
unsigned kcfg_seen(void) { return hits; }
/* Deliberately not the probe's source-level name: exercise campaign-specific linkage. */
void kcfg_hit_test(unsigned owner)
{
	if (owner != 65535)
		__builtin_trap();
	++hits;
}

int main(void)
{
	int n = 19;
	if (early() + teardown() + noinstr_path() + notrace_path() + noprofile_path()
	    + nosanitize_path() + constant_path() + pure_path(&n) + cold_path()
	    + init_module() + kcfg_helper() != 74 || hits != 0)
		return 1;
	return exercise(4) != 10 || other_alias(7) != 6 || hits != 2;
}
