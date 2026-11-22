// SPDX-License-Identifier: GPL-2.0-only
/* Compiled into one built-in object of the profiling kernel, never into the collector. */
#include <linux/atomic.h>
#include <linux/export.h>
#include <linux/hash.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/mutex.h>
#include <linux/sched.h>
#include <linux/spinlock.h>
#include <trace/events/initcall.h>
#include "control.h"

#ifndef CONFIG_TRACEPOINTS
#error "The profiling kernel needs CONFIG_TRACEPOINTS"
#endif

/* Each slot advances independently. A delayed writer cannot cross its stop fence. */
#define KCFG_RECORDING 8
#define KCFG_FROZEN 16
static atomic_t kcfg_slots[KCFG_COUNT + 1];
static DEFINE_MUTEX(kcfg_control_lock);
static int kcfg_phase;
static bool kcfg_ready, kcfg_incomplete;

/* Track tasks, not CPUs: an initializer may sleep, nest, or migrate. Interrupts
 * borrowing its task must not be mistaken for the synchronous initializer. */
struct kcfg_initializer {
	struct task_struct *task;
	unsigned int depth;
};
#define KCFG_BUCKET_BITS 8
static struct kcfg_initializer kcfg_tasks[1 << KCFG_BUCKET_BITS][4];
static DEFINE_RAW_SPINLOCK(kcfg_tasks_lock);
static atomic_t kcfg_active;

static void kcfg_init_start(void *data, initcall_t fn)
{
	struct kcfg_initializer *bucket = kcfg_tasks[hash_ptr(current, KCFG_BUCKET_BITS)];
	struct kcfg_initializer *slot = NULL;
	unsigned long flags;
	int i;

	raw_spin_lock_irqsave(&kcfg_tasks_lock, flags);
	for (i = 0; i < ARRAY_SIZE(kcfg_tasks[0]); ++i) {
		if (bucket[i].task == current) {
			++bucket[i].depth;
			goto out;
		}
		if (!bucket[i].task)
			slot = &bucket[i];
	}
	if (slot) {
		slot->depth = 1;
		WRITE_ONCE(slot->task, current);
		atomic_inc(&kcfg_active);
	} else {
		WRITE_ONCE(kcfg_incomplete, true);
	}
out:
	raw_spin_unlock_irqrestore(&kcfg_tasks_lock, flags);
}

static void kcfg_init_finish(void *data, initcall_t fn, int result)
{
	struct kcfg_initializer *bucket = kcfg_tasks[hash_ptr(current, KCFG_BUCKET_BITS)];
	unsigned long flags;
	int i;

	raw_spin_lock_irqsave(&kcfg_tasks_lock, flags);
	for (i = 0; i < ARRAY_SIZE(kcfg_tasks[0]); ++i) {
		if (bucket[i].task != current)
			continue;
		if (!--bucket[i].depth) {
			WRITE_ONCE(bucket[i].task, NULL);
			atomic_dec(&kcfg_active);
		}
		break;
	}
	raw_spin_unlock_irqrestore(&kcfg_tasks_lock, flags);
}

static int __init kcfg_watch_initializers(void)
{
	int ret = register_trace_initcall_finish(kcfg_init_finish, NULL);

	if (ret)
		return ret;
	ret = register_trace_initcall_start(kcfg_init_start, NULL);
	if (!ret)
		smp_store_release(&kcfg_ready, true);
	return ret;
}
early_initcall(kcfg_watch_initializers);

static bool kcfg_in_initializer(void)
{
	struct kcfg_initializer *bucket;
	int i;

	if (!atomic_read(&kcfg_active) || !in_task())
		return false;
	bucket = kcfg_tasks[hash_ptr(current, KCFG_BUCKET_BITS)];
	for (i = 0; i < ARRAY_SIZE(kcfg_tasks[0]); ++i)
		if (READ_ONCE(bucket[i].task) == current)
			return true;
	return false;
}

void notrace KCFG_LINK_SYMBOL(unsigned int id);
void notrace KCFG_LINK_SYMBOL(unsigned int id)
{
	int old, bit;
	bool init;

	if (unlikely(!id || id > KCFG_COUNT)) {
		WRITE_ONCE(kcfg_incomplete, true);
		return;
	}
	old = atomic_read(&kcfg_slots[id]);
	if (old & KCFG_FROZEN)
		return;
	init = kcfg_in_initializer();
	do {
		bit = init ? KCFG_INIT_HIT :
			(old & KCFG_RECORDING ? KCFG_WORK_HIT : KCFG_BOOT_HIT);
		if (old & (KCFG_FROZEN | bit))
			return;
	} while (!atomic_try_cmpxchg_relaxed(&kcfg_slots[id], &old, old | bit));
}
EXPORT_SYMBOL_GPL(KCFG_LINK_SYMBOL);

static int kcfg_transition(int from, int to)
{
	unsigned int id;
	int ret = 0;

	mutex_lock(&kcfg_control_lock);
	if (!smp_load_acquire(&kcfg_ready) || READ_ONCE(kcfg_incomplete)) {
		ret = -ENODATA;
	} else if (kcfg_phase != from) {
		ret = -EBUSY;
	} else {
		for (id = 1; id <= KCFG_COUNT; ++id)
			atomic_or(to, &kcfg_slots[id]);
		smp_store_release(&kcfg_phase, to);
	}
	mutex_unlock(&kcfg_control_lock);
	return ret;
}

int kcfg_begin(void) { return kcfg_transition(0, KCFG_RECORDING); }
int kcfg_end(void) { return kcfg_transition(KCFG_RECORDING, KCFG_FROZEN); }
int kcfg_snapshot_ready(void)
{
	return smp_load_acquire(&kcfg_phase) == KCFG_FROZEN && !READ_ONCE(kcfg_incomplete);
}
unsigned int kcfg_sample(unsigned int id)
{
	return id && id <= KCFG_COUNT ? atomic_read(&kcfg_slots[id]) : 0;
}
unsigned int kcfg_sample_count(void) { return KCFG_COUNT; }
const char *kcfg_campaign_id(void) { return KCFG_ID; }
EXPORT_SYMBOL_GPL(kcfg_begin);
EXPORT_SYMBOL_GPL(kcfg_end);
EXPORT_SYMBOL_GPL(kcfg_snapshot_ready);
EXPORT_SYMBOL_GPL(kcfg_sample);
EXPORT_SYMBOL_GPL(kcfg_sample_count);
EXPORT_SYMBOL_GPL(kcfg_campaign_id);
