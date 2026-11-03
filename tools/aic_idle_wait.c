/* Exact Q2 soc_aic.ko ABI, pinned by tools/aic.py; no guessed kernel structs.
 *
 * Stock hw_free uses a 20-byte, 4-byte-aligned wait entry at sp+0x20 and the
 * private stream's queue at +0x34. The exact kernel's schedule_timeout_idle
 * stores 0x402 (TASK_IDLE); schedule_timeout(LONG_MAX) calls schedule without
 * arming a timer. TASK_IDLE retains the uninterruptible wake bit but does not
 * contribute to load average. All functions below are already stock imports.
 */
typedef unsigned int u32;
struct wait_entry { u32 opaque[5]; };
extern void init_wait_entry(struct wait_entry *, int);
extern long prepare_to_wait_event(void *, struct wait_entry *, int);
extern void finish_wait(void *, struct wait_entry *);
extern long schedule_timeout(long);
extern void __wake_up(void *, unsigned int, int, void *);

void q2_aic_wait_stopped(void *private)
{
    struct wait_entry wait;
    char *p = private;
    void *queue = p + 0x34;
    init_wait_entry(&wait, 0); /* non-exclusive, shares stock hw_free's queue */
    for (;;) {
        /* Enqueue/state first, then reread both conditions. A concurrent
         * producer wakes us even between this check and schedule_timeout. */
        (void)prepare_to_wait_event(queue, &wait, 0x402);
        if (*(volatile u32 *)(p + 0x28) || *(volatile u32 *)(p + 0x30))
            break;
        (void)schedule_timeout(0x7fffffffL);
    }
    finish_wait(queue, &wait);
}

void q2_aic_wake_notifier(void *private)
{
    /* The stock running/exit store executes before this hook. Publish it
     * before the waitqueue lock/wakeup, including on weakly ordered MIPS. */
    __asm__ volatile ("sync" ::: "memory");
    __wake_up((char *)private + 0x34, 3, 0, (void *)0);
}
