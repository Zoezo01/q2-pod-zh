/* Cover Flow's thumb: the wheel's travel, a follower the covers ride on, its letters and landings.
 */
#include "coverflow.h"

cf_thumb_t th __attribute__((section(".scratch")));

/* The thumb (docs/internals.md#coverflow-thumb), every UI pass while the covers are open
 * (navigation.c thumb_poll): the wheel's raw travel, 200 units a turn, moves a goal, and the
 * position follows it, critically damped and exact for any step, so its speed stays continuous
 * whatever the wheel or a slow frame does. */
static void th_follow(float *x, float *v, float to, float tau, float ms) {
    float w = 1 / tau, y = *x - to, b = *v + w * y, q = y + b * ms, e = ff_decay(w * ms);
    *x = to + q * e;
    *v = (b - w * q) * e;
}

/* Over 4 albums a second: the album named has run ahead of the covers, and a letter's change of
 * pictures goes by with the motion. */
static int th_moving(void) { return ABS(th.vel) >= .004f; }

/* Where the wheel has got to, the album the caption names and a stop lands on: ahead of the covers
 * at speed. */
static int th_aim(void) {
    float s = cfs.aim_snap / 100.f;
    if (!th_moving()) return cf_round(th.goal);
    return th.dir > 0 ? -cf_floor(s - th.goal) : cf_floor(th.goal + s);
}

static unsigned th_album(int a, unsigned n) {
    if (!n) return 0;
    int c = (I(cf.slide, SLIDE_INDEX) + a) % (int)n;
    return (unsigned)(c < 0 ? c + (int)n : c);
}

/* At rest on album q: the slide_menu's index moves, as a glide's end does, its offset 0. */
static void th_commit(int q) {
    th.on = th.jump = 0, th.lead = th.lead_v = th.vel = th.pos = th.goal = 0;
    carousel_glide(cf.slide, -q * stride_of(cf.slide), 0, 0);
    widget_invalidate_force(cf.slide, 0);
}

/* The covers on the album named, at once: Centre, or nothing to show a landing on. */
void coverflow_wheel_land(void) {
    if (th.on && cf.slide) th_commit(th_aim());
    th.on = 0; /* no covers left to land: a page's load replaced them */
}
int coverflow_wheel_live(void) { return th.raw || th.on || (cfs.thumb && cf.slide); }
int coverflow_wheel_on(void *w) { return th.on && w == cf.slide; }

/* From rest, or from a touch's glide or a held tick's step, whose goal it takes over. */
static void th_grab(void *s, unsigned now) {
    void *a = P(s, SLIDE_ANIMATOR);
    float stride = (float)stride_of(s);
    th.pos = (float)-I(s, SLIDE_OFFSET) / stride;
    th.goal = a ? (float)-I(a, ANIM_X_TO) / stride : th.pos;
    if (a) {
        widget_animator_pause(a);
        widget_animator_destroy(a);
        P(s, SLIDE_ANIMATOR) = 0;
        th.dir = th.goal < th.pos ? -1 : 1;
    }
    th.vel = th.lead = th.lead_v = th.rate = th.ticks = 0;
    th.capped = th.jump = 0;
    th.tau = cfs.snap;
    th.tick_at = now;
    th.on = 1;
    th.clicked = cf_round(th.goal);
}

/* SLIDE_INDEX on by q albums and the follower back by as many, so nothing moves: its numbers stay
 * small, where a float still resolves the 1e-4 its settling needs. */
static void th_rebase(void *s, int q, int n) {
    int i = (I(s, SLIDE_INDEX) + q) % n;
    I(s, SLIDE_OFFSET) = 0; /* the pass writes it again from pos */
    slide_menu_set_value(s, i < 0 ? i + n : i);
    th.pos -= (float)q, th.goal -= (float)q, th.seen -= (float)q;
    th.clicked -= q;
}
#define TH_REBASE 64   /* albums: a float's step there is 8e-6 */
#define TH_FRAME_MS 16 /* a frame at 60 Hz: the shortest pass th_turn's own rate is taken over */

/* The album named now this one, by SLIDE_INDEX alone: the pictures change, and the follower, the
 * row's shape and the speed go on as they were. */
static void th_name(void *s, int album, int n) {
    I(s, SLIDE_OFFSET) = 0; /* the pass writes it again from pos */
    slide_menu_set_value(s,
                         (int)th_album(album - (int)th_album(th_aim(), (unsigned)n), (unsigned)n));
}

/* A letter on, Minimal's jump (coverflow_jump): the spin names the first album of the letter after
 * the last one named, or the previous letter's turning back, round the ends as the covers go, its
 * letter shown over the covers. From the last one named, not where the flow has got to: that passes
 * over a letter the flow has left since, and every other one turning back, as the flow leaves a
 * letter's first album at once. A letter has no motion of its own, so slowing goes on from here
 * with nothing to catch up. ponytail: a letter of fewer albums than the flow's travel since the
 * last (about three at top_speed) steps the pictures back by that, on a row still moving. */
static void th_letter(void *s, int n, unsigned now) {
    unsigned letter;
    int to = th.jump_to + th.dir * coverflow_jump(s, th.jump_to, th.dir, &letter);
    if (!letter) to = coverflow_round(th.dir, &letter);
    if (to == th.jump_to) return; /* one letter in all: the flow goes round */
    th_name(s, th.jump_to = to, n);
    coverflow_letter(s, letter, now);
}

/* travel raw units: the goal on by a share of an album that grows with the thumb's speed, at most
 * top_speed albums a second; turning back drops what's queued, and the speed with it. Going by
 * letters, the travel counts towards the next one as well. An artist's albums stop at the ends:
 * the first push past one kicks the covers out, and they spring back. */
static void th_turn(void *s, int travel, unsigned now, int n, int ms) {
    int dir = travel > 0 ? -1 : 1; /* x rises anticlockwise: back */
    if (dir != th.dir) {
        float l = th.goal - th.pos, m = cfs.back / 100.f;
        th.goal = th.pos + (l > m ? m : l < -m ? -m : l);
        th.rate = 0;
        th.jump = th.capped = 0; /* letters end: the turn back moves the covers */
    }
    th.dir = dir;
    /* Wheel Sensitivity scales the travel, 50% as the theme has it */
    float ticks = (float)ABS(travel) * cfs.ticks_turn * wheel_pct() / 10000,
          slow = 1.f / cfs.ticks_album;
    float r = th.rate < 1 ? 1 : th.rate, sr = slow * r, top = cfs.top_speed;
    float f = cf_smooth((th.rate - cfs.fast_at) / (cfs.fast_full - cfs.fast_at));
    float step = (sr + (top > sr ? top - sr : 0) * f) / r;
    /* the cap, at the filtered rate or this pass's own, the faster: while the rate is still rising
     * to a spin's, and past top_speed ticks_album ticks/s, the share would outrun it. This pass's
     * own is over a frame at least: a pass a ms or two after the last would read its ticks as a
     * spin many times the cap's and throw them away. */
    float inst = ticks * 1000 / (float)(ms < TH_FRAME_MS ? TH_FRAME_MS : ms),
          cap = top / (inst > r ? inst : r);
    step = step > slow ? step : slow;
    th.ticks += ticks;
    th.tick_at = now;
    th.stretch += ticks;
    th.goal += (float)dir * ticks * (step < cap ? step : cap);
    if (coverflow_bounded(s)) {
        int i = I(s, SLIDE_INDEX);
        float lo = (float)-i, hi = (float)(n - 1 - i);
        float g = th.goal < lo ? lo : th.goal > hi ? hi : th.goal;
        if (g != th.goal) {
            th.goal = g;
            if (!th.bounced && ABS(th.pos - g) < .02f)
                th.pos = g + (float)dir / CF_BOUNCE, th.vel = 0, th.bounced = 1;
        } else
            th.bounced = 0;
    }
}

/* usable: navigation.c's; gated: travel is to be dropped (its gates). Answers THUMB_*. */
int coverflow_wheel_poll(int usable, int gated) {
    void *s = cf.slide, *wm = window_manager();
    int shown = usable && cfs.thumb && fx.frame && !fl.panel && covers_live() &&
                widget_count_children(s) && window_manager_get_top_window(wm) == cf.page &&
                !window_manager_is_animating(wm);
    int raw = shown && !window_manager_get_pointer_pressed(wm);
    if (raw != th.raw) {
        __sync_lock_test_and_set(&wheel_travel, 0);
        th.raw = raw, th.held = th.finger = th.early = 0;
        if (!raw && wheel_raw == WHEEL_THUMB) wheel_raw = WHEEL_TICKS;
    }
    if (raw) wheel_raw = WHEEL_THUMB; /* decided afresh each pass: lyric_poll may have handed it */
    if (!shown) {
        coverflow_wheel_land();
        return 0;
    }
    if (fx.todo) widget_invalidate_force(s, 0); /* covers still to decode, FX_DECODES a frame */
    unsigned now = (unsigned)time_now_ms();
    int ms = (int)(now - th.at), travel = raw ? __sync_lock_test_and_set(&wheel_travel, 0) : 0;
    if (gated) travel = 0;
    ms = ms < 1 ? 1 : ms > 250 ? 250 : ms;
    th.at = now;
    /* A still thumb sends no frames, for seconds: only wheel_held says it's there. A press only
     * anchors (wheel_sample); a contact under contact_ms is nothing, and a lift that short changes
     * nothing. */
    int held = raw && wheel_held;
    if (held != th.held) {
        th.held = held, th.edge_at = now;
        if (held)
            th.dead = th.bounced = 0;
        else if (!th.finger)
            th.early = 0;
    }
    if ((int)(now - th.edge_at) >= cfs.contact_ms && th.finger != held) {
        th.finger = held;
        travel += th.early;
        th.early = 0;
    }
    if (!th.finger) {
        if (held) th.early += travel;
        travel = 0;
    }
    /* its shake, a unit or so, moves nothing: travel runs loose in a band of dead units each way */
    th.dead += travel;
    travel = th.dead > cfs.dead ? th.dead - cfs.dead : th.dead < -cfs.dead ? th.dead + cfs.dead : 0;
    th.dead -= travel;
    if (!th.on && !travel) return 0;
    if (!th.on) th_grab(s, now);
    int n = (int)widget_count_children(s), r = THUMB_MOVED;
    if (travel) {
        th_turn(s, travel, now, n, ms);
        r |= THUMB_TURNED;
    }
    /* the thumb's rate, ticks/s: quick to fall, so slowing down gives control back at once. Going
     * by letters, a lifted thumb leaves it: a re-grip isn't a slow thumb. */
    float inst = th.ticks * 1000 / (float)ms, tc = inst > th.rate ? cfs.rate_up : cfs.rate_down;
    if ((th.finger && th.held) || !th.jump)
        th.rate += (inst - th.rate) * (1 - ff_decay((float)ms / tc));
    th.ticks = 0;
    /* Time at jump_at or more counts towards jump_ms, at most that, and time under it comes off, so
     * a dip costs only its own ms. Then the spin goes by letters, where the sort has them: the
     * covers flow on as any fast turn's do, and each jump_ticks of travel, the first at once and
     * none sooner than jump_pace after the last, names the next letter (th_letter), while the
     * covers are moving, so the pictures never change on a still row. Only a turning thumb starts
     * them or moves them on. A lift keeps them for as long as a letter shows, so a re-grip goes on
     * from there; a thumb turning under fast_at, a turn back (th_turn) or that long with no tick
     * ends them, and the album named carries on under it.
     * ponytail: short arcs with a lift each don't reach jump_ms, as on ticks: the rate falls in the
     * lift and rises again. Carry it over the lift for the count too if arcs show on the wheel. */
    int since = (int)(now - th.tick_at);
    th.capped = th.rate >= cfs.jump_at
                    ? (th.capped + ms < cfs.jump_ms ? th.capped + ms : cfs.jump_ms)
                : th.capped > ms ? th.capped - ms
                                 : 0;
    if (th.jump && (since > LETTER_MS || (travel && th.rate < cfs.fast_at)))
        th.jump = 0;
    else if (travel && !th.jump && th.capped >= cfs.jump_ms && cfs.jump_ticks && lettered(s))
        th.jump = 1, th.stretch = cfs.jump_ticks, th.letter_at = now - cfs.jump_pace,
        th.jump_to = (int)th_album(th_aim(), (unsigned)n);
    if (travel && th.jump && th_moving() && th.stretch >= cfs.jump_ticks &&
        (int)(now - th.letter_at) >= cfs.jump_pace) {
        th.stretch = 0, th.letter_at = now;
        th_letter(s, n, now);
    }
    /* Part-way is for a slow thumb that stays: a lift, the hold's timeout or a stop from speed goes
     * to the album named. */
    int idle = since > cfs.hold_ms;
    float left = ABS(th.goal - th.pos);
    if (th.goal != (float)cf_round(th.goal) &&
        (!th.finger || idle || (since > cfs.stop_ms && left * 100 > cfs.stop_left))) {
        th.goal = (float)th_aim(), left = ABS(th.goal - th.pos);
        if (th.jump && th_moving()) th_name(s, th.jump_to, n); /* onto its letter's first album */
    }
    float f = cf_smooth((th.rate - cfs.fast_at) / (cfs.fast_full - cfs.fast_at));
    float base = idle || !th.finger ? cfs.snap : cfs.track + (float)(cfs.spin - cfs.track) * f;
    float rest = cfs.snap + (float)(cfs.spin - cfs.snap) * cf_smooth(left * 100 / cfs.far_left);
    /* only the thumb raises it, and how far is left only lets it fall: lag raising it would add lag
     * and snap the row out at one speed */
    th.tau = th.tau < rest ? th.tau : rest;
    th.tau = th.tau > base ? th.tau : base;
    /* its soft detent, slow and finger down: towards the albums, never against the thumb */
    int g0 = cf_floor(th.goal);
    float r0 = th.goal - (float)g0, k = th.finger ? (1 - f) * cfs.detent / 100 : 0;
    float to = (float)g0 + (1 - k) * r0 + k * r0 * r0 * (3 - 2 * r0);
    th_follow(&th.pos, &th.vel, to, th.tau, (float)ms);
    if (ABS(th.pos - th.goal) < 1e-4f && ABS(th.vel) < 1e-5f) th.pos = th.goal, th.vel = 0;
    /* The facing point is the wheel's target, where the covers will land: off under lead_from, so
     * slow control is exact, and its short follow only smooths a reversal. */
    float l = th.goal - th.pos;
    float want = l * cf_smooth((ABS(l) * 100 - cfs.lead_from) / (cfs.lead_full - cfs.lead_from));
    th_follow(&th.lead, &th.lead_v, want, cfs.lead_ms, (float)ms);
    if (!want && ABS(th.lead) < 1e-4f && ABS(th.lead_v) < 1e-5f) th.lead = th.lead_v = 0;
    /* Once the wheel's stopped, the facing point's follow, lagging as it shrinks, never passes the
     * album the covers land on, or the next one faces for a moment and turns back. While it turns,
     * the gap jumps with each tick, so it's left alone then. */
    if (since > cfs.stop_ms && th.lead * l > 0 && ABS(th.lead) > ABS(l))
        th.lead = l, th.lead_v = -th.vel;
    int q = cf_round(th.pos);
    if (q >= TH_REBASE || q <= -TH_REBASE) th_rebase(s, q, n);
    I(s, SLIDE_OFFSET) = -cf_round(th.pos * (float)stride_of(s));
    if (ABS(th.pos - th.seen) >= 1 / 4096.f || ABS(th.lead - th.seen_lead) >= 1 / 4096.f ||
        !th.lead != !th.seen_lead) {
        th.seen = th.pos, th.seen_lead = th.lead;
        widget_invalidate_force(s, 0);
    } else if (!(r & THUMB_TURNED))
        r = 0; /* a thumb holding still: the loop may idle */
    /* the album named is the one kept, here: closed() may come mid-destroy, with no slide_menu */
    unsigned c = th_album(th_aim(), (unsigned)n);
    int was = cf.titled;
    titles(c);
    if (cf.titled != was && c + CARDS < (unsigned)n && !cf.scoped)
        cf.saved_album = album_key(deque_at(cf.albums, c));
    if (cf_round(th.goal) != th.clicked) {
        th.clicked = cf_round(th.goal);
        r |= THUMB_CLICK;
    }
    if (!th.jump && !th.lead && th.pos == th.goal && th.goal == (float)cf_round(th.goal))
        th_commit(cf_round(th.goal));
    return r;
}
