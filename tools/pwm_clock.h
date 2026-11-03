/* ABI shim for the pinned Q2 5.10.21+ modules. No invented kernel structures:
 * sizeof(mutex)=28 comes from both exact modules' named objects/field spacing.
 * Everything else uses opaque clock handles and exported function calls. */
typedef unsigned int u32;
typedef unsigned long long u64;
struct q2_mutex { u32 opaque[7]; };
struct clk;
extern void __mutex_init(void *, const char *, void *);
extern void mutex_lock(void *), mutex_unlock(void *);
extern struct clk *clk_get(void *, const char *);
extern void clk_put(struct clk *);
extern int clk_prepare(struct clk *), clk_enable(struct clk *);
extern void clk_disable(struct clk *), clk_unprepare(struct clk *);
extern void disable_irq(u32), enable_irq(u32);
extern void usleep_range(u32, u32);
struct q2_clocks { struct clk *clk[3]; u32 prepared, enabled; };
static void q2_barrier(void) { __asm__ volatile("sync" ::: "memory"); }
static void q2_clocks_down(struct q2_clocks *g) {
    int i;
    for (i=2;i>=0;i--) if (g->enabled & (1u<<i)) {
        clk_disable(g->clk[i]); g->enabled &= ~(1u<<i);
    }
    for (i=2;i>=0;i--) if (g->prepared & (1u<<i)) {
        clk_unprepare(g->clk[i]); g->prepared &= ~(1u<<i);
    }
}
static int q2_clocks_up(struct q2_clocks *g) {
    u32 before_p=g->prepared, before_e=g->enabled;
    int i, r;
    for (i=0;i<3;i++) {
        if (!(g->prepared & (1u<<i))) {
            r=clk_prepare(g->clk[i]); if (r) goto fail;
            g->prepared |= 1u<<i;
        }
        if (!(g->enabled & (1u<<i))) {
            r=clk_enable(g->clk[i]); if (r) goto fail;
            g->enabled |= 1u<<i;
        }
    }
    return 0;
fail:
    for (i=2;i>=0;i--) if ((g->enabled & ~before_e) & (1u<<i)) {
        clk_disable(g->clk[i]); g->enabled &= ~(1u<<i);
    }
    for (i=2;i>=0;i--) if ((g->prepared & ~before_p) & (1u<<i)) {
        clk_unprepare(g->clk[i]); g->prepared &= ~(1u<<i);
    }
    return r;
}
