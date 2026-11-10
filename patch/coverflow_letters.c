/* Cover Flow's letters (docs/internals.md#coverflow-thumb), as the depth renderer draws them: the
 * row at speed, the entry into letters, the row held going by letters and the fold out of it onto
 * the letter's album. coverflow.c's coverflow_render draws them (draw_row, draw_fold), with its
 * poses and columns. */
#include "coverflow.h"

/* A cover at xc, depth zc, seen from eye (1/16 px), at brightness bright, turned deg16 degrees
 * (16.16, up to 90) as a right-hand cover; negative turns it the other way, as a left-hand one.
 * CF_ANGLE itself takes its own table's end, as pose() does at that tilt. */
static void pose_at(int xc, int zc, int deg16, int eye, int bright, pose_t *p) {
    int a = deg16 < 0 ? -deg16 : deg16, other = a != CF_ANGLE << 16;
    turn(other ? a / 90 : CF_ONE, other, p);
    p->xc = xc, p->zc = zc, p->eye = eye, p->bright = bright;
    if (deg16 < 0) p->s = -p->s;
}

/* smoothstep of x (16.16, clamped to 0..CF_ONE), and a to b over e of it: both exact at the ends */
static int ease16(int x) { return (int)(cf_smooth((float)x / CF_ONE) * CF_ONE); }
static int glide(int a, int b, int e) { return a + (int)((float)(b - a) * (float)e / CF_ONE); }

typedef struct {
    int xl, xr, hl, hr, bright; /* draw_quad's: its edges' x (1/16 px) and heights (1/256 px) */
} cf_quad_t;

/* How far the row at lead has formed (0 to 1), past row_from */
static float row_formed(float lead) {
    float ld = lead < 0 ? -lead : lead;
    return cf_smooth((ld * 100 - cfs.row_from) / (cfs.row_full - cfs.row_from));
}
/* A cover t albums from the facing point: its near edge x (1/16 px), the row formed b, unshrunk */
static float row_near(float t, float b) {
    pose_t p;
    float flat = cfs.row_flat / 100.f, tz = t > flat ? t - (t - flat) * b : t;
    pose((int)(tz * CF_ONE), &p);
    return (float)project(&p, CF_HALF16) + (t - tz) * cfs.row_out * 16;
}

/* How far the shelf goes (1/16 px) as the facing point runs run albums on beyond lead: the row's
 * own spacing out past row_flat, as formed at lead, so the covers away from the facing point keep
 * their places in the row */
static float row_run(float lead, float run) {
    float b = row_formed(lead), t = cfs.row_flat / 100.f + 1;
    return run * (row_near(t + 1, b) - row_near(t, b));
}

/* The shelf with its facing point lead albums on from frac, its cover a albums from frac's centre:
 * its t is its distance from that point, on that side, and the whole shelf slides lead * row_shift
 * px. Past row_from the row forms: shrunk, no receding past row_flat from the focus, only spacing,
 * each cover away from the focus thinner and with less keystone. At lead 0 this is
 * coverflow_render's own path, which draws then. Into letters the facing point runs run albums on
 * beyond lead and the shelf xrun (1/16 px) with it: the row forms as far as the facing point is
 * from it, shrunk as at lead, so the facing point only goes on. Its brightness: a cover is whole
 * for as long as any of it shows, the window it is drawn from reaching past the screen. */
static int row_quad_run(int frac, float lead, float run, float xrun, int a, cf_quad_t *q) {
    float at = (float)frac / CF_ONE + lead + run, shift = lead * cfs.row_shift + xrun / 16;
    float b = row_formed(lead + run);
    float sh = 1 - row_formed(lead) * cfs.row_shrink / 100, flat = cfs.row_flat / 100.f;
    float d = (float)a - at, t = d < 0 ? -d : d;
    float tz = t > flat ? t - (t - flat) * b : t, e = b * cf_smooth(t * 100 / cfs.row_edge);
    pose_t p;
    pose((int)(tz * CF_ONE), &p);
    int near = project(&p, CF_HALF16), inner = project(&p, -CF_HALF16);
    float hn = ART_SIZE * 256.f * p.eye / (p.eye + p.zc - (CF_HALF16 * p.s >> 16));
    float hi = ART_SIZE * 256.f * p.eye / (p.eye + p.zc + (CF_HALF16 * p.s >> 16));
    float out = near + (t - tz) * cfs.row_out * 16;
    float width = (near - inner) * (1 - e * cfs.row_width / 100);
    float hf = (hn + (hi - hn) * (1 - e * cfs.row_key / 100)) * sh, right = d >= 0;
    hn *= sh;
    float xl = (right ? out - width : -out) + shift * 16;
    float xr = (right ? out : width - out) + shift * 16;
    q->xl = (int)(xl * sh), q->xr = (int)(xr * sh);
    q->hl = (int)(right ? hf : hn), q->hr = (int)(right ? hn : hf);
    return q->bright = tz < 2 ? p.bright : cfs.bright2;
}

/* The middles of the screen's first and last columns, as draw_quad places them (1/16 px) */
#define CF_COL0 ((1 - 2 * CF_CX) * 8)
#define CF_COLN ((2 * CF_VIEW_W - 1 - 2 * CF_CX) * 8)

/* The entry's speed, albums a ms: the row's as letters began, CF_ENTER_SPEED a second at least */
float coverflow_enter_v(void) {
    float v = ABS(th.vel), least = (float)CF_ENTER_SPEED / 1000;
    return v > least ? v : least;
}

/* The entry into letters, enter of its enter_albums still to go, from the row at speed as it was
 * (frac, lead), leaning lean: against the spin, as the covers behind the facing cover lean. All on
 * one clock, the row goes on enter_albums at the entry's speed, and in the first ENTER_RUN % the
 * facing point runs on ahead of it the way the spin went, from the speed it had in the row (the
 * lead's, lead_v) and faster and faster (the cube of the time), through the covers ahead, each
 * turning as a cover passing it does in the row, till it stops so far past the screen's leading
 * edge that no cover on the screen is within row_flat or row_edge of it: every one there is turned
 * and narrowed as the row's covers away from the facing point are, so none turns towards the eye
 * as the albums pass it. The shelf goes with it at the row's own spacing, so the covers away from
 * it keep their places in the row. In the rest the row only goes on, and letters hold it so
 * (coverflow_hold). */
#define ENTER_RUN 99 /* the share of the entry the facing point runs on in, % */
typedef struct {
    int frac, lean, focus, after;
    float lead, run, xrun, at; /* the facing point's run (albums), the shelf's (1/16 px), and it */
} entry_t;

static void entry_at(entry_t *n, int frac, float lead, float enter, int lean) {
    int dir = -lean, len = cfs.enter_albums, m;
    float v = coverflow_enter_v(), runs = (float)len * ENTER_RUN / 100; /* the row's, running */
    float gone = 1 - enter / (float)len, step = row_run(lead, 1);
    gone = gone < 0 ? 0 : gone > 1 ? 1 : (float)(int)(gone * CF_ONE + .5f) / CF_ONE; /* 16.16 */
    /* The facing point as it stops running, along the spin, and the album it runs to: the first
     * past which the cover turned albums behind it, the last the row turns or narrows (formed, at
     * the shelf's shift, shrunk), is wholly past the screen's leading edge. */
    float f0 = (float)dir * ((float)frac / CF_ONE + lead) + runs;
    float sh = 1 - row_formed(lead) * cfs.row_shrink / 100, edge = dir > 0 ? CF_COLN : -CF_COL0;
    float turned = (float)(cfs.row_flat > cfs.row_edge ? cfs.row_flat : cfs.row_edge) / 100;
    float past = edge / sh + row_near(turned, 1) - (float)dir * lead * cfs.row_shift * 16;
    past = past > 0 ? past : 0, step = step > 16 ? step : 16; /* a px an album at least */
    m = cf_floor(f0 + past / step) + 1;
    /* the run, at the lead's speed to begin with, the rest by the cube: never slower than it was */
    float end = (float)m - f0, l0 = (float)dir * th.lead_v * runs / v, r = gone * 100 / ENTER_RUN;
    l0 = l0 < 0 ? 0 : l0 > end ? end : l0;
    r = r < 1 ? r : 1;
    float run = l0 * r + (end - l0) * r * r * r;
    n->frac = frac + dir * (int)(gone * (float)(len * CF_ONE) + .5f), n->lead = lead,
    n->lean = lean;
    n->run = (float)dir * run, n->xrun = n->run * step; /* row_run, on the guarded spacing */
    n->at = (float)n->frac / CF_ONE + lead + n->run;
    n->focus = cf_round(n->at), n->after = n->at < (float)n->focus;
}

/* Cover a (from frac's centre) of the entry n, into r; whether any of it is on the screen. */
static int entry_quad(const entry_t *n, int a, cf_quad_t *r) {
    row_quad_run(n->frac, n->lead, n->run, n->xrun, a, r);
    /* lit, and reaching the middle of a column on the screen, as draw_quad draws it */
    return r->bright > 0 && r->xr > r->xl && r->xr > CF_COL0 && r->xl <= CF_COLN;
}

/* Whether cover a of n shows: on the screen, with the cover CF_SHOW_PAST albums nearer the facing
 * point still having its outer edge there. A cover whose nearer neighbour's outer edge is off the
 * screen is behind nearer covers but for its reflection, which shows through theirs a level or so
 * for three covers more (Classic's barely formed row has covers on the screen far back, all
 * hidden). Not a theme's to tune: it is how far the renderer's reflections let a hidden cover
 * through, measured (test/coverflow.py edge_drawn and enter_frames fail with 3). */
#define CF_SHOW_PAST 4
static int entry_shows(const entry_t *n, int a) {
    cf_quad_t r, o;
    int dir = -n->lean, near = a + CF_SHOW_PAST * dir;
    if (!entry_quad(n, a, &r)) return 0;
    if ((n->focus - near) * dir <= 0) return 1;
    entry_quad(n, near, &o);
    return dir > 0 ? o.xl >= CF_COL0 : o.xr <= CF_COLN;
}

/* The row's window (CF_WIDE albums), its middle from frac's centre: from the farthest cover behind
 * the facing point that shows, so that one joins or leaves it only where it shows nothing (held,
 * the facing point off the screen, the row's covers on it reach about CF_WIDE back from it). */
static int entry_window(const entry_t *n) {
    int dir = -n->lean, a = n->focus - dir * (CF_WIDE + CF_WIDE_REACH);
    while ((n->focus - a) * dir > 0 && !entry_shows(n, a)) a += dir;
    return a + dir * CF_WIDE_REACH;
}
/* The row's window: entry_window's into letters, held, and at speed in Big Library Mode; at speed
 * with it off, 1.7.6's, round the facing cover */
static int row_window(const entry_t *n) {
    return th.jump || ringnav_big_library() ? entry_window(n) : cf_round(n->lead);
}

/* The entry's covers, nearest the facing point first as the row at speed draws them, into q, with
 * each one's album from frac's centre in album and the window's middle in *centre; their count. */
static int entry_quads(const entry_t *n, int *centre, cf_quad_t *q, int *album) {
    int k = 0;
    *centre = row_window(n);
    for (int d = 0; d < 2 * CF_WIDE; ++d)
        for (int i = 0; i < (d ? 2 : 1); ++i) {
            int a = (i == 0) == n->after ? n->focus - d : n->focus + d;
            if (a < *centre - CF_WIDE_REACH || a > *centre + CF_WIDE_REACH) continue;
            entry_quad(n, a, &q[k]);
            album[k++] = a;
        }
    return k;
}

/* Going by letters, the row held as the entry left it (th.hold_*), frac (16.16) of an album on as
 * it slides: the entry's row past its end, with frac's centre the album at the screen's middle. */
static void hold_at(entry_t *n, int frac) {
    n->frac = frac - th.hold_k, n->lean = th.lean < 0 ? -1 : 1;
    n->lead = th.hold_lead, n->run = th.hold_run, n->xrun = th.hold_xrun;
    n->at = (float)n->frac / CF_ONE + n->lead + n->run;
    n->focus = cf_round(n->at), n->after = n->at < (float)n->focus;
}
/* The held row's cover at16 (16.16) albums on from the screen's middle, as draw_quad draws it */
static int hold_quad(int at16, cf_quad_t *q) {
    return row_quad_run(-at16 - th.hold_k, th.hold_lead, th.hold_run, th.hold_xrun, 0, q);
}
/* The row at frac: at speed (its facing point lead albums on, leaning against it), into letters,
 * or once in, held */
static void row_at(entry_t *n, int frac) {
    if (!th.jump) {
        n->frac = frac, n->lead = th.lead, n->lean = th.lead < 0 ? 1 : -1, n->run = n->xrun = 0;
        n->at = (float)frac / CF_ONE + th.lead;
        n->focus = cf_round(n->at), n->after = n->at < (float)n->focus;
    } else if (th.enter > 0)
        entry_at(n, frac, th.lead, th.enter, th.lean < 0 ? -1 : 1);
    else
        hold_at(n, frac);
}

/* The window's middle (coverflow_paint's, fx_window's) of the row at frac as it is drawn now: at
 * speed, into letters or held (draw_row's) */
int coverflow_window(int frac) {
    entry_t n;
    row_at(&n, frac);
    return row_window(&n);
}

/* The row's covers, nearest the facing point first: all of its window. ring[j]: the cover
 * j - CF_REACH from the window's middle (coverflow_paint's). */
void draw_row(shelf_t *f, int frac, const unsigned *const *ring) {
    cf_quad_t q[CF_WIDE];
    entry_t n;
    int album[CF_WIDE], centre;
    row_at(&n, frac);
    for (int i = 0, k = entry_quads(&n, &centre, q, album); i < k; ++i) {
        const unsigned *tex = ring[album[i] - centre + CF_REACH];
        if (tex && q[i].bright > 0)
            draw_quad(f, q[i].xl, q[i].xr, q[i].hl, q[i].hr, tex, q[i].bright);
    }
}

/* The pose, as placed (a left-hand turn's s negative), that draws as q: each edge's x over its
 * height gives xc and the cosine, whatever the eye; for that turn, the edges' heights give the eye,
 * and their size the depth. Answers the turn, as pose_at takes it. The held row's covers, narrow
 * and with little keystone, are seen from far off; an eye past CF_EYE_FAR (none left) stays there,
 * its far edge then a little short. */
#define CF_EYE_FAR (16 * 16384)
/* the farthest eye project's and unproject's sums hold (tools/theme.py) */
#define CF_EYE_NEAR (16 * 2046)
static int quad_pose(const cf_quad_t *q, pose_t *p) {
    float k = ART_SIZE * 256.f, al = (float)q->xl * k / (float)q->hl,
          ar = (float)q->xr * k / (float)q->hr;
    float dh = 1.f / (float)q->hl - 1.f / (float)q->hr, c = (ar - al) / (2 * CF_HALF16);
    int ci = c <= 0 ? 0 : c >= 1 ? CF_ONE : (int)(c * CF_ONE), i = 0;
    while (i < 15 && cf_cos90[i + 1] > ci) ++i; /* turn()'s table backwards */
    int x = (i << 12) + (cf_cos90[i] - ci) * 4096 / (cf_cos90[i] - cf_cos90[i + 1]);
    int deg = (dh < 0 ? -90 : 90) * x;
    pose_at(0, 0, deg, 0, q->bright > 0 ? q->bright : 0, p);
    float e = 2.f * CF_HALF16 * (float)p->s / CF_ONE / (k * dh);
    e = !(e > (float)CF_EYE16) ? (float)CF_EYE16 : e > (float)CF_EYE_FAR ? (float)CF_EYE_FAR : e;
    float dl = k * e / (float)q->hl, dr = k * e / (float)q->hr; /* the edges' depths, eye's too */
    p->xc = cf_round((al + ar) / 2), p->zc = cf_round((dl + dr) / 2 - e), p->eye = (int)e;
    return deg;
}
/* p's quad, as draw_quad draws it: project's edges and their heights, in float (a held cover's eye
 * is far past what project's sums hold); unlit if an edge reaches the eye. */
static void pose_quad(const pose_t *p, cf_quad_t *q) {
    float c = (float)p->c * CF_HALF16 / CF_ONE, s = (float)p->s * CF_HALF16 / CF_ONE,
          e = (float)p->eye, dl = e + (float)p->zc + s, dr = e + (float)p->zc - s;
    q->bright = dl > 0 && dr > 0 ? p->bright : 0;
    if (!q->bright) return;
    q->xl = (int)(((float)p->xc - c) * e / dl), q->xr = (int)(((float)p->xc + c) * e / dr);
    q->hl = (int)(ART_SIZE * 256.f * e / dl), q->hr = (int)(ART_SIZE * 256.f * e / dr);
}

/* Into letters (th_letter): the row held where the entry leaves it, its run done, as th.hold_*,
 * with the cover at the screen's middle (hold_k: as x rises with the album, halving) and a cover's
 * xc an album there as poses (hold_xs, the fold's); answers that cover's album from SLIDE_INDEX,
 * where letters go on from, so every cover stays on its album and keeps its x. */
float coverflow_hold(void) {
    entry_t e;
    cf_quad_t q;
    pose_t a, b;
    int r = cf_round(th.pos);
    entry_at(&e, (int)((th.pos - (float)r) * CF_ONE), th.lead, 0, th.lean < 0 ? -1 : 1);
    th.hold_lead = e.lead, th.hold_run = e.run, th.hold_xrun = e.xrun, th.hold_k = 0;
    int lo = (int)((e.lead + e.run - 64) * CF_ONE), hi = lo + 128 * CF_ONE;
    while (hi - lo > 1) {
        int m = lo + (hi - lo) / 2;
        hold_quad(m, &q);
        *(q.xl + q.xr < 0 ? &lo : &hi) = m;
    }
    th.hold_k = lo;
    hold_quad(0, &q), quad_pose(&q, &a);
    hold_quad(CF_ONE, &q), quad_pose(&q, &b);
    th.hold_xs = (float)(b.xc - a.xc);
    return (float)r + (float)(e.frac + lo) / CF_ONE;
}

/* How far through the fold the clock is, 16.16 */
int fold_t16(void) {
    unsigned ms = (unsigned)time_now_ms() - th.fold_at;
    return ms >= CF_FOLD_MS ? CF_ONE : (int)ms * CF_ONE / CF_FOLD_MS;
}

/* Cover o from the middle t16 (16.16) of the way through the fold out of a row leaning lean, as
 * the cover itself is placed (a left-hand turn's s negative). At 0 it is going by letters: the held
 * row's cover at16 (16.16) albums from the middle, o's own place but for the row's fraction as it
 * slides, as the pose that draws it. At CF_ONE it is pose()'s at rest. Every number glides on one
 * smoothstep, but the side whose lean must turn over swings out on a sin^2 arc and turns between
 * FOLD_FROM and FOLD_TO %, while it is out, so no two covers ever pass through each other
 * (test/coverflow.py fold_clearance). */
#define FOLD_OUT 53  /* how far the first cover turning its lean over swings out, % of a cover */
#define FOLD_STEP 87 /* and each one further from the middle, further still, % of a cover */
#define FOLD_FROM 20 /* the share of the fold it turns from, % */
#define FOLD_TO 80   /* and the share it has turned by, % */
static void fold_pose(int o, int at16, int t16, int lean, pose_t *p) {
    pose_t rest, from, arc;
    cf_quad_t q;
    int n = o < 0 ? -o : o, side = o < 0 ? -1 : 1, e = ease16(t16), turn_e = e;
    hold_quad(at16, &q);
    int deg = quad_pose(&q, &from);
    pose(n * CF_ONE, &rest);
    if (n > CF_REACH) rest.bright = 0; /* it lands on the still shelf, which draws seven */
    int xc = glide(from.xc, side * rest.xc, e);
    if (o * lean < 0) {
        turn(2 * (t16 < CF_ONE / 2 ? t16 : CF_ONE - t16), 1, &arc); /* arc.s: sin(pi t) */
        float sn = (float)arc.s / CF_ONE, out = (float)(FOLD_OUT + (n - 1) * FOLD_STEP);
        xc += side * (int)(sn * sn * out * (2 * CF_HALF16) / 100);
        turn_e = ease16((t16 * 100 - FOLD_FROM * CF_ONE) / (FOLD_TO - FOLD_FROM));
    }
    pose_at(xc, glide(from.zc, rest.zc, e), glide(deg, n ? side * cfs.tilt * CF_ONE : 0, turn_e),
            glide(from.eye, rest.eye, e), glide(from.bright, rest.bright, e), p);
}

/* The row's speed as the fold began (fold_vel) carried on through it: fold_off goes to 0 on the
 * fold's smoothstep, which is the Hermite curve's start term, and this adds its speed term, s (1 -
 * s)^2 of the speed over CF_FOLD_MS, as x (1/16 px, the held row's hold_xs an album) shared by
 * every cover, so none moves against another; the covers go on at the row's speed and stop at the
 * end. */
static int fold_push(int t16) {
    float s = (float)t16 / CF_ONE;
    return (int)(-th.hold_xs * (float)CF_FOLD_MS * th.fold_vel * s * (1 - s) * (1 - s));
}

/* Whether draw_fold draws screen column col of p (as placed; q its quad), and its height there */
static int fold_h(const pose_t *p, const cf_quad_t *q, int col, int *h) {
    pose_t m = *p;
    int side = m.s < 0 ? -1 : 1, v = side > 0 ? col : 2 * CF_CX - 1 - col, u;
    if (p->eye > CF_EYE_NEAR) { /* as draw_quad */
        int dx = (2 * col + 1 - 2 * CF_CX) * 8 - q->xl;
        if (dx < 0 || dx >= q->xr - q->xl) return 0;
        *h = q->hl + (int)((float)(q->hr - q->hl) * (float)dx / (float)(q->xr - q->xl));
        return 1;
    }
    m.xc *= side, m.s *= side;
    return unproject(&m, (2 * v + 1 - 2 * CF_CX) * 8, &u, h);
}

/* The fold's covers still lit at t16, nearest first, into order and their poses, as placed, into
 * p. Where two draw a column in common, the one taller there goes first: nearer the eye, as each
 * is seen from its own. A topological sort, which covers that never cross seen from above always
 * allow. */
static int fold_order(int t16, int lean, int off16, signed char *order, pose_t *p) {
    cf_quad_t q[CF_WIDE];
    pose_t ps[CF_WIDE];
    int lo[CF_WIDE], hi[CF_WIDE], n = 0;
    unsigned after[CF_WIDE] = { 0 }, left = 0; /* after[i]: the covers i must follow */
    for (int o = -CF_WIDE_REACH; o <= CF_WIDE_REACH; ++o) {
        fold_pose(o, o * CF_ONE - off16, t16, lean, &ps[n]);
        ps[n].xc += fold_push(t16);
        pose_quad(&ps[n], &q[n]);
        if (q[n].bright <= 0 || q[n].xr <= q[n].xl) continue;
        lo[n] = ((16 * CF_CX + q[n].xl) >> 4) - 1, hi[n] = ((16 * CF_CX + q[n].xr) >> 4) + 1;
        lo[n] = lo[n] < 0 ? 0 : lo[n], hi[n] = hi[n] >= CF_VIEW_W ? CF_VIEW_W - 1 : hi[n];
        order[n++] = (signed char)o;
    }
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < i; ++j) {
            int col = lo[i] > lo[j] ? lo[i] : lo[j], to = hi[i] < hi[j] ? hi[i] : hi[j], ti, tj;
            while (col <= to &&
                   !(fold_h(&ps[i], &q[i], col, &ti) && fold_h(&ps[j], &q[j], col, &tj)))
                ++col;
            if (col > to) continue;
            if (ti > tj)
                after[j] |= 1u << i;
            else
                after[i] |= 1u << j;
        }
    signed char o[CF_WIDE];
    memcpy(o, order, (unsigned)n);
    for (int k = 0; k < n; ++k) {
        int pick = -1;
        for (int i = 0; i < n && pick < 0; ++i)
            if (!(left >> i & 1) && !(after[i] & ~left)) pick = i;
        for (int i = 0; i < n && pick < 0; ++i) /* never with no two crossing: any one, to end */
            if (!(left >> i & 1)) pick = i;
        left |= 1u << pick, order[k] = o[pick], p[k] = ps[pick];
    }
    return n;
}

/* The fold t16 of the way through out of a row leaning lean, onto ring's centre, from off16 (16.16)
 * albums on from it, where the row was as it began: each cover as the shelf draws its pose, the
 * right-hand cover it mirrors when turned left, so it lands as the shelf at rest; seen from an eye
 * past what project's sums hold (a held row with almost no keystone), as its quad. */
void draw_fold(shelf_t *f, const unsigned *const *ring, int t16, int lean, int off16) {
    signed char order[CF_WIDE];
    pose_t p[CF_WIDE];
    for (int i = 0, n = fold_order(t16, lean, off16, order, p); i < n; ++i) {
        const unsigned *tex = ring[order[i] + CF_REACH];
        int side = p[i].s < 0 ? -1 : 1;
        cf_quad_t q;
        if (!tex) continue;
        if (p[i].eye > CF_EYE_NEAR) {
            pose_quad(&p[i], &q);
            draw_quad(f, q.xl, q.xr, q.hl, q.hr, tex, q.bright);
            continue;
        }
        p[i].xc *= side, p[i].s *= side;
        draw_posed(f, &p[i], side, tex);
    }
}

#ifdef PEQ_HOST
/* For test/coverflow.py: the entry's covers, as entry_quads, each a, xl, xr, hl, hr, bright. */
int cf_enter_quads(int frac, float lead, float enter, int lean, int *centre, int (*out)[6]) {
    cf_quad_t q[CF_WIDE];
    int album[CF_WIDE], n;
    cf_restyle();
    entry_t e;
    entry_at(&e, frac, lead, enter, lean);
    n = entry_quads(&e, centre, q, album);
    for (int i = 0; i < n; ++i)
        out[i][0] = album[i], out[i][1] = q[i].xl, out[i][2] = q[i].xr, out[i][3] = q[i].hl,
        out[i][4] = q[i].hr, out[i][5] = q[i].bright;
    return n;
}
/* For test/coverflow.py: any one cover a of the entry, in or out of the window. */
void cf_enter_quad(int frac, float lead, float enter, int lean, int a, int *out) {
    cf_quad_t q;
    entry_t e;
    cf_restyle();
    entry_at(&e, frac, lead, enter, lean);
    entry_quad(&e, a, &q);
    out[0] = a, out[1] = q.xl, out[2] = q.xr, out[3] = q.hl, out[4] = q.hr, out[5] = q.bright;
}
/* For test/coverflow.py: the entry's facing point, albums from frac's centre; its x on the screen
 * (1/16 px). */
float cf_enter_face(int frac, float lead, float enter, int lean, int *x) {
    entry_t e;
    cf_restyle();
    entry_at(&e, frac, lead, enter, lean);
    *x = (int)((lead * cfs.row_shift * 16 + e.xrun) *
               (1 - row_formed(lead) * cfs.row_shrink / 100)); /* row_quad_run's shift */
    return e.at;
}
/* For test/coverflow.py: entering letters, the row leaning lean, from the row at lead, enter
 * albums to go; lean 0: that row at speed, not letters. */
void cf_thumb_enter(int lean, float lead, float enter) {
    th.on = 1, th.jump = lean != 0, th.fold = 0, th.lean = lean, th.lead = lead, th.enter = enter;
}
/* For test/coverflow.py: the row's and the facing point's speeds as letters began, albums a ms. */
void cf_thumb_speed(float vel, float lead_v) { th.vel = vel, th.lead_v = lead_v; }
/* For test/coverflow.py: the row held as an entry leaning lean from the row at speed at (frac,
 * lead) leaves it (th.vel and th.lead_v as they are); the album then at the screen's middle, from
 * frac's centre. */
float cf_thumb_hold(int lean, float lead, int frac) {
    cf_restyle();
    th.lean = lean, th.lead = lead, th.pos = (float)frac / CF_ONE;
    float x = coverflow_hold();
    th.lead = th.pos = 0;
    return x;
}
/* For test/patch.py: the row held as given, the MIPS build's own */
void cf_thumb_held(float lead, float run, float xrun, float xs, int k) {
    th.hold_lead = lead, th.hold_run = run, th.hold_xrun = xrun, th.hold_xs = xs, th.hold_k = k;
}
/* For test/coverflow.py: the covers of the frame at frac as drawn now (the row at speed, the entry
 * or the held row), each its album from frac's centre, xl, xr, hl, hr, bright; their count. */
int cf_frame_quads(int frac, int (*out)[6]) {
    cf_quad_t q[CF_WIDE];
    int album[CF_WIDE], n = 0, centre;
    cf_restyle();
    if (th.on && (th.jump || th.lead)) {
        entry_t e;
        row_at(&e, frac);
        n = entry_quads(&e, &centre, q, album);
    } else
        for (int o = -CF_WIDE_REACH; o <= CF_WIDE_REACH; ++o, ++n)
            album[n] = o, row_quad_run(frac, 0, 0, 0, o, &q[n]);
    for (int i = 0; i < n; ++i)
        out[i][0] = album[i], out[i][1] = q[i].xl, out[i][2] = q[i].xr, out[i][3] = q[i].hl,
        out[i][4] = q[i].hr, out[i][5] = q[i].bright;
    return n;
}
/* For test/coverflow.py: any one cover a (from frac's centre) of the frame at frac as drawn now, in
 * or out of its window: a, xl, xr, hl, hr, bright. */
void cf_frame_quad(int frac, int a, int *out) {
    cf_quad_t q;
    entry_t e;
    cf_restyle();
    if (th.on && (th.jump || th.lead))
        row_at(&e, frac), entry_quad(&e, a, &q);
    else
        row_quad_run(frac, 0, 0, 0, a, &q);
    out[0] = a, out[1] = q.xl, out[2] = q.xr, out[3] = q.hl, out[4] = q.hr, out[5] = q.bright;
}
/* For test/coverflow.py: the window's middle, from the album at the screen's middle, of the frame
 * at frac as drawn now (coverflow_paint's). */
int cf_frame_window(int frac) {
    cf_restyle();
    return !th.on || th.fold || (!th.jump && !th.lead) ? 0 : coverflow_window(frac);
}
/* For test/coverflow.py: the fold's cover o, placed, and its brightness; its paint order, and
 * each one's quad (unless out is 0). */
int cf_fold_pose(int o, int t16, int lean, int *xc, int *zc, int *c, int *s, int *eye) {
    pose_t p;
    cf_restyle();
    fold_pose(o, o * CF_ONE, t16, lean, &p);
    *xc = p.xc, *zc = p.zc, *c = p.c, *s = p.s, *eye = p.eye;
    return p.bright;
}
int cf_fold_order(int t16, int lean, signed char *order, int (*out)[6]) {
    pose_t p[CF_WIDE];
    cf_quad_t q;
    cf_restyle();
    int n = fold_order(t16, lean, 0, order, p);
    for (int i = 0; out && i < n; ++i)
        pose_quad(&p[i], &q), out[i][0] = order[i], out[i][1] = q.xl, out[i][2] = q.xr,
                              out[i][3] = q.hl, out[i][4] = q.hr, out[i][5] = q.bright;
    return n;
}
/* For test/coverflow.py: cover o from the ring's middle as the held row (at frac) or the fold
 * place it now: its middle's x on the screen, 1/16 px. */
int cf_cover_x(int o, int frac) {
    pose_t p;
    cf_quad_t q;
    if (th.fold) {
        fold_pose(o, o * CF_ONE - (int)(th.fold_off * CF_ONE), fold_t16(), th.lean < 0 ? -1 : 1,
                  &p);
        p.xc += fold_push(fold_t16());
        pose_quad(&p, &q);
    } else
        hold_quad(o * CF_ONE - frac, &q);
    return (q.xl + q.xr) / 2;
}
#endif
