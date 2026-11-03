/* Conservative Linux half of cooperative PWM clock ownership.
 * MCU has independent CCF leases (mcu_pwm_clock.c). Linux DMA pins this half
 * awake for the module lifetime; only ordinary CPU-driven PWM is gated.
 * Original config/channel/IRQ instructions remain in the stock module. */
#include "pwm_clock.h"
extern struct clk *q2_stock_pwm_mux_clk, *q2_stock_pwm_div_clk, *q2_stock_pwm_gate_clk;
extern unsigned char pwmdata[16*124];
extern int q2_stock_ingenic_pwm_probe(u32,u32,u32,u32);
extern int q2_stock_ingenic_pwm_remove(u32,u32,u32,u32);
struct q2_mutex q2_pwm_lock;
u32 q2_pwm_lock_key;
struct q2_clocks q2_pwm_clocks;
u32 q2_pwm_calls, q2_pwm_dma_pinned, q2_pwm_irq_off, q2_pwm_initializing;
u32 q2_pwm_cycles, q2_pwm_errors, q2_pwm_mcu_owned;
void q2_pwm_mcu_ready(void) {
    /* One-way handoff after the paired, permanent MCU module owns its clocks.
     * A missing/failed MCU module leaves the bootstrap lease held forever. */
    mutex_lock(&q2_pwm_lock);
    q2_pwm_mcu_owned=1;
    mutex_unlock(&q2_pwm_lock);
}
/* Exact target kernel_symbol layout: three absolute 32-bit pointers, as in
 * this pinned module's seven existing GPL exports. No version CRCs. */
static const char q2_ready_name[] __attribute__((section("__ksymtab_strings"),used,aligned(1)))="q2_pwm_mcu_ready";
static const char q2_ready_namespace[] __attribute__((section("__ksymtab_strings"),used,aligned(1)))="";
static const struct { void (*value)(void); const char *name,*namespace; }
q2_ready_export __attribute__((section("__ksymtab_gpl"),used,aligned(4))) =
    {q2_pwm_mcu_ready,q2_ready_name,q2_ready_namespace};

/* Replace only probe's prepare/enable call targets. Record actual ownership,
 * because the vendor probe ignores clock errors. Never assume it owns a ref. */
static int q2_pwm_index(struct clk *c) {
    if (c==q2_stock_pwm_mux_clk) return 0;
    if (c==q2_stock_pwm_div_clk) return 1;
    if (c==q2_stock_pwm_gate_clk) return 2;
    return -1;
}
int q2_pwm_probe_prepare(struct clk *c) {
    int i=q2_pwm_index(c), r=clk_prepare(c);
    if (!r && i>=0) q2_pwm_clocks.prepared |= 1u<<i;
    return r;
}
int q2_pwm_probe_enable(struct clk *c) {
    int i=q2_pwm_index(c), r=clk_enable(c);
    if (!r && i>=0) q2_pwm_clocks.enabled |= 1u<<i;
    return r;
}
void q2_pwm_probe_unprepare(struct clk *c) {
    int i=q2_pwm_index(c);
    clk_unprepare(c);
    if (i>=0) q2_pwm_clocks.prepared &= ~(1u<<i);
}
static int q2_pwm_enter(u32 dma) {
    int r;
    mutex_lock(&q2_pwm_lock);
    q2_pwm_clocks.clk[0]=q2_stock_pwm_mux_clk;
    q2_pwm_clocks.clk[1]=q2_stock_pwm_div_clk;
    q2_pwm_clocks.clk[2]=q2_stock_pwm_gate_clk;
    r=q2_clocks_up(&q2_pwm_clocks);
    if (!r) {
        if (q2_pwm_irq_off) { q2_pwm_irq_off=0; enable_irq(14); }
        q2_pwm_calls++;
        if (dma) q2_pwm_dma_pinned=1;
    } else q2_pwm_errors++;
    mutex_unlock(&q2_pwm_lock);
    return r;
}
static void q2_pwm_leave(void) {
    u32 i;
    mutex_lock(&q2_pwm_lock);
    q2_pwm_calls--;
    if (q2_pwm_calls || q2_pwm_dma_pinned || q2_pwm_initializing || !q2_pwm_mcu_owned) goto out;
    for (i=0;i<16;i++)
        if (*(u32 *)(pwmdata+i*124+12)) goto out;
    /* No API may enter while this mutex is held; the handler never takes it.
     * Drain its final MMIO after wake_up, and prohibit clock-off reentry. */
    if (!q2_pwm_irq_off) { disable_irq(14); q2_pwm_irq_off=1; }
    /* Retain refs for unexpected hardware activity outside Linux metadata.
     * MCU may be running channel1 under its own independent lease. */
    if ((*(volatile u32 *)0xb34c0018u | *(volatile u32 *)0xb34c0108u |
         *(volatile u32 *)0xb34c0128u) & 0xffffu) goto out;
    q2_clocks_down(&q2_pwm_clocks);
    q2_pwm_cycles++;
out:
    mutex_unlock(&q2_pwm_lock);
}
#define WRAP(name, dma) \
extern int q2_stock_##name(u32,u32,u32,u32); \
int q2_##name(u32 a,u32 b,u32 c,u32 d) { \
    int r=q2_pwm_enter(dma); if (r) return r; \
    r=q2_stock_##name(a,b,c,d); q2_pwm_leave(); return r; \
}
WRAP(pwm2_request,0)
WRAP(pwm2_config,0)
WRAP(pwm2_set_level,0)
WRAP(pwm2_release,0)
WRAP(pwm2_dma_init,1)
WRAP(pwm2_dma_update,1)
WRAP(pwm2_dma_disable_loop,1)
int q2_ingenic_pwm_probe(u32 a,u32 b,u32 c,u32 d) {
    int r;
    __mutex_init(&q2_pwm_lock,"q2_pwm_clock",&q2_pwm_lock_key);
    q2_pwm_initializing=1;
    r=q2_stock_ingenic_pwm_probe(a,b,c,d);
    /* Retain probe references until the first public call. This also covers
     * early boot until soc_mcu establishes its independent clock ownership. */
    mutex_lock(&q2_pwm_lock);
    q2_pwm_initializing=0;
    mutex_unlock(&q2_pwm_lock);
    return r;
}
/* Stock remove frees IRQ and deregisters the misc device before clock puts.
 * Normal module unload only; platform forced unbind remains unsupported. */
void q2_pwm_remove_disable(struct clk *c) {
    int i=q2_pwm_index(c);
    if (i>=0 && (q2_pwm_clocks.enabled & (1u<<i))) {
        clk_disable(c); q2_pwm_clocks.enabled &= ~(1u<<i);
    }
}
void q2_pwm_remove_unprepare(struct clk *c) {
    int i=q2_pwm_index(c);
    if (i>=0 && (q2_pwm_clocks.prepared & (1u<<i))) {
        clk_unprepare(c); q2_pwm_clocks.prepared &= ~(1u<<i);
    }
}
