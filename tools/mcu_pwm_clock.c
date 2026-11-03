/* MCU owns its own balanced PWM clock references, independently of soc_pwm.
 * Only the exact paired MCU image may relinquish them. All mutating entry
 * points serialize wake/publication/park/reset/image writes with this mutex.
 * Unsupported images, unproven hardware state, or protocol timeouts retain
 * clocks until reboot; they never authorize clock-off by elapsed time. */
#include "pwm_clock.h"
#include "mcu_park_expected.h"
extern int q2_stock_mcu_init(u32,u32,u32,u32);
extern int q2_stock_mcu_bootup(u32,u32,u32,u32);
extern int q2_stock_mcu_reset(u32,u32,u32,u32);
extern int q2_stock_mcu_shutdown(u32,u32,u32,u32);
extern int q2_stock_mcu_write_data(u32,u32,u32,u32);
extern int q2_stock_mcu_ioctl(u32,u32,u32,u32);
extern void q2_pwm_mcu_ready(void);
struct q2_mutex q2_mcu_lock;
u32 q2_mcu_lock_key;
struct q2_clocks q2_mcu_clocks;
u32 q2_mcu_known, q2_mcu_running, q2_mcu_fault, q2_mcu_parks, q2_mcu_wakes;
#define REQ (*(volatile u32 *)0xb3429f00u)
#define ACK (*(volatile u32 *)0xb3429f04u)
#ifdef Q2_MCU_SLEEP
#define WAKE (*(volatile u32 *)0xb3429f10u)
#define SFTINT (*(volatile u32 *)0xb00000bcu)
/* Same latch as exact stock mcu_write_data. No DMA/reset/clock registers. */
static void q2_mcu_kick(void) {
    q2_barrier();
    if (!SFTINT) SFTINT=1;
    q2_barrier();
}
static void q2_mcu_request_wake(void) {
    WAKE=1; q2_barrier(); REQ=0; q2_barrier(); q2_mcu_kick();
}
#endif
#define PWM(off) (*(volatile u32 *)(0xb34c0000u+(off)))

static int q2_mcu_image_matches(int pristine) {
    u32 i,j, count=pristine ? Q2_MCU_PRISTINE_RANGES : Q2_MCU_RANGES;
    /* Mutable initial data is trusted only after a complete pristine image
     * was validated. Every raw upload clears that trust. */
    for (i=0;i<count;i++) {
        const volatile unsigned char *p=(void *)(0xb3422000u+q2_mcu_ranges[i].offset);
        for (j=0;j<q2_mcu_ranges[i].length;j++)
            if (p[j]!=q2_mcu_ranges[i].bytes[j]) return 0;
    }
    return 1;
}
static int q2_mcu_ack(u32 value) {
    u32 i;
    for (i=0;i<1000;i++) {
        if (ACK==value
#ifdef Q2_MCU_SLEEP
            && (value || !WAKE)
#endif
        ) { q2_barrier(); return 0; }
#ifdef Q2_MCU_SLEEP
        /* A coalesced IRQ can be cleared while a kick is published. Retry the
         * idempotent wake token only while no command may be written. */
        if (!value && WAKE) q2_mcu_kick();
#endif
        usleep_range(100,200);
    }
    return -110;
}
static void q2_mcu_abandon(void) {
    q2_mcu_fault=1;
    if (q2_mcu_known && q2_mcu_running) {
        /* Clocks are still owned. Do not leave a timed-out MCU parked. */
#ifdef Q2_MCU_SLEEP
        q2_mcu_request_wake();
#else
        REQ=0; q2_barrier();
#endif
    }
}
static int q2_mcu_wake(void) {
    int r=q2_clocks_up(&q2_mcu_clocks);
    if (r) return r;
    if (!q2_mcu_known || !q2_mcu_running) return 0;
#ifdef Q2_MCU_SLEEP
    /* Once this paired protocol faults, retain clocks and reject additional
     * commands until reboot. Never publish into a possibly sleeping MCU. */
    if (q2_mcu_fault) return -110;
    q2_mcu_request_wake();
#else
    REQ=0; q2_barrier();
    if (q2_mcu_fault) return 0;
#endif
    r=q2_mcu_ack(0);
    if (r) q2_mcu_abandon();
    else q2_mcu_wakes++;
    return r;
}
static void q2_mcu_park(void) {
    if (!q2_mcu_known || !q2_mcu_running || q2_mcu_fault) return;
    /* Publication of data and the doorbell completes before the park request.
     * The helper drains pending work, masks its PWM IRQ, then stays parked. */
    q2_barrier(); REQ=1; q2_barrier();
    if (q2_mcu_ack(1)) { q2_mcu_abandon(); return; }
    /* Reject unexpected residual activity. These reads supplement the exact
     * command-completion proof; zero bits alone never grant park permission. */
    if ((PWM(0x18)|PWM(0x108)|PWM(0x128)) & 2u) {
        /* The exact MCU owns channel1. Retain this lease if its hardware
         * still reports activity; independent Linux channels have their own
         * driver leases and must not prevent MCU park. */
        return;
    }
    q2_clocks_down(&q2_mcu_clocks);
    q2_mcu_parks++;
}
static int q2_mcu_start(u32 a,u32 b,u32 c,u32 d,int reset) {
    int r;
    mutex_lock(&q2_mcu_lock);
    r=q2_mcu_wake();
    if (r) goto out;
    if (q2_mcu_known && q2_mcu_running) q2_mcu_park();
    else if (q2_mcu_running) q2_mcu_fault=1;
    q2_stock_mcu_shutdown(0,0,0,0);
    q2_mcu_running=0;
    q2_mcu_known=q2_mcu_image_matches(!q2_mcu_known);
    if (q2_mcu_known) {
        /* SRAM is uncached and reset is asserted. ACK cannot be inherited
         * across a reboot; this area is outside the stock BSS clear. */
        REQ=1; ACK=0;
#ifdef Q2_MCU_SLEEP
        WAKE=0;
#endif
        q2_barrier();
    } else q2_mcu_fault=1;
    /* park() may have released refs above: acquire before releasing reset. */
    r=q2_clocks_up(&q2_mcu_clocks);
    if (r) goto out;
    if (reset) q2_stock_mcu_reset(a,b,c,d);
    else q2_stock_mcu_bootup(a,b,c,d);
    r=0;
    q2_mcu_running=1;
    q2_mcu_park();
out:
    mutex_unlock(&q2_mcu_lock);
    return r;
}
int q2_mcu_bootup(u32 a,u32 b,u32 c,u32 d) { return q2_mcu_start(a,b,c,d,0); }
int q2_mcu_reset(u32 a,u32 b,u32 c,u32 d) { return q2_mcu_start(a,b,c,d,1); }
int q2_mcu_shutdown(u32 a,u32 b,u32 c,u32 d) {
    int r;
    mutex_lock(&q2_mcu_lock);
    r=q2_mcu_wake();
    if (!r) {
        if (q2_mcu_known && q2_mcu_running) q2_mcu_park();
        else if (q2_mcu_running) q2_mcu_fault=1;
        q2_stock_mcu_shutdown(a,b,c,d);
        r=0;
        q2_mcu_running=0;
        /* Reset alone does not stop independent PWM/DMA. Only park may
         * release the references; unknown firmware remains pinned. */
    }
    mutex_unlock(&q2_mcu_lock);
    return r;
}
/* ponytail: parks before returning, and the MCU acknowledges only after the
 * synchronous buzzer stops, so a caller (Key Tone's system("cmd_mcu ...") on
 * the UI thread) waits out each click. Park from delayed work instead if that
 * latency shows. */
int q2_mcu_write_data(u32 a,u32 b,u32 c,u32 d) {
    int r;
    mutex_lock(&q2_mcu_lock);
    r=q2_mcu_wake();
    if (!r) {
        r=q2_stock_mcu_write_data(a,b,c,d);
        q2_mcu_park();
    }
    mutex_unlock(&q2_mcu_lock);
    return r;
}
int q2_mcu_ioctl(u32 a,u32 command,u32 c,u32 d) {
    int r;
    if (command==0x20004d70u) return q2_mcu_shutdown(0,0,0,0);
    if (command==0x20004d71u) return q2_mcu_reset(0,0,0,0);
    if (command==0x20004d72u) return q2_mcu_bootup(0,0,0,0);
    if (command!=0x80044d73u) return q2_stock_mcu_ioctl(a,command,c,d);
    mutex_lock(&q2_mcu_lock);
    r=q2_mcu_wake();
    if (!r) {
        if (q2_mcu_known && q2_mcu_running) q2_mcu_park();
        else if (q2_mcu_running) q2_mcu_fault=1;
        /* Serialize image writes with all boot/reset/command paths and stop
         * execution while replacing it. A later boot revalidates the image. */
        q2_stock_mcu_shutdown(0,0,0,0);
        q2_mcu_running=0; q2_mcu_known=0;
        r=q2_clocks_up(&q2_mcu_clocks);
        if (!r) r=q2_stock_mcu_ioctl(a,command,c,d);
    }
    mutex_unlock(&q2_mcu_lock);
    return r;
}
int q2_mcu_init(u32 a,u32 b,u32 c,u32 d) {
    const char *names[3]={"mux_pwm","div_pwm","gate_pwm"};
    int i,r;
    __mutex_init(&q2_mcu_lock,"q2_mcu_pwm_clock",&q2_mcu_lock_key);
    for (i=0;i<3;i++) {
        struct clk *p=clk_get((void *)0,names[i]);
        if ((u32)p>=0xfffff001u) { r=(int)p; goto fail; }
        q2_mcu_clocks.clk[i]=p;
    }
    r=q2_clocks_up(&q2_mcu_clocks); if (r) goto fail;
    /* misc_register publishes the device before stock init returns. */
    mutex_lock(&q2_mcu_lock);
    r=q2_stock_mcu_init(a,b,c,d);
    if (!r) {
        /* A software BSS flag is not proof of reset. Unknown inherited MCU
         * execution may have left autonomous DMA on any channel. */
        q2_mcu_running=!( *(volatile u32 *)0xb3421030u & 1u );
        if (q2_mcu_running) q2_mcu_fault=1;
        q2_pwm_mcu_ready();
        mutex_unlock(&q2_mcu_lock);
        return r;
    }
    mutex_unlock(&q2_mcu_lock);
    q2_clocks_down(&q2_mcu_clocks);
fail:
    while (i--) clk_put(q2_mcu_clocks.clk[i]);
    return r;
}
