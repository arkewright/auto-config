/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef KCFG_PROBE_H
#define KCFG_PROBE_H

/* The compiler sees a stable declaration; the linker sees a campaign-specific name. */
extern void kcfg_hit(unsigned int id) __asm__(KCFG_LINK_NAME);

#endif
