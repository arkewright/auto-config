/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef KCFG_CONTROL_H
#define KCFG_CONTROL_H

#define KCFG_BOOT_HIT 1
#define KCFG_WORK_HIT 2
#define KCFG_INIT_HIT 4

int kcfg_begin(void);
int kcfg_end(void);
int kcfg_snapshot_ready(void);
unsigned int kcfg_sample(unsigned int id);
unsigned int kcfg_sample_count(void);
const char *kcfg_campaign_id(void);

#endif
