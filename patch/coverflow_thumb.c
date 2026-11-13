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

/* The album the caption names, from SLIDE_INDEX: in letters, once in, the one the row passes */
static int th_shown(void) { return th.jump && th.enter <= 0 ? cf_round(th.pos) : th_aim(); }

/* At rest on album q: the slide_menu's index moves, as a glide's end does, its offset 0. */
static void th_commit(int q) {
    th.on = th.jump = th.beat = th.lean = th.fold = 0, th.enter = th.fold_off = th.fold_vel = 0;
    th.lead = th.lead_v = th.vel = th.pos = th.goal = 0;
    carousel_glide(cf.slide, -q * stride_of(cf.slide), 0, 0);
    widget_invalidate_force(cf.slide, 0);
}

/* The covers on the album named, at once: Centre, or nothing to show a landing on. */
void coverflow_wheel_land(void) {
    unsigned n = cf.albums ? deque_size(cf.albums) : 0;
    /* landed, painted or not: the letters' slots go, the landing album's window kept */
    fx_unletter(cf.slide ? (int)th_album(th.on ? th_shown() : 0, n) : 0);
    if (th.jump) coverflow_letter(cf.slide, 0, 0); /* the held letter goes with its letters */
    if (th.on && cf.slide) th_commit(th_shown());
    th.jump = th.lean = th.fold = 0;
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
    if (th.jump) coverflow_letter(s, 0, now); /* letters a load cut short: their letter goes too */
    th.capped = th.jump = th.beat = th.lean = th.fold = 0, th.enter = th.fold_off = th.fold_vel = 0;
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
/* The album named is the one kept, here: closed() may come mid-destroy, with no slide_menu. In
 * letters, once in, the one the row passes. */
static void th_named(int n) {
    unsigned c = th_album(th_shown(), (unsigned)n);
    int was = cf.titled;
    titles(c);
    if (cf.titled != was && c + CARDS < (unsigned)n && !cf.scoped)
        cf.saved_album = album_key(deque_at(cf.albums, c));
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

/* Fast Navigation off, 1.7.6's letters: a letter on, Minimal's jump (coverflow_letter_next): the
 * spin names the first album of the letter after the last one named, or the previous letter's
 * turning back, round the ends as the covers go, its letter shown over the covers for LETTER_MS.
 * From the last one named, not where the flow has got to: that passes over a letter the flow has
 * left since, and every other one turning back, as the flow leaves a letter's first album at once.
 * A letter has no motion of its own, so slowing goes on from here with nothing to catch up.
 * ponytail: a letter of fewer albums than the flow's travel since the last (about three at
 * top_speed) steps the pictures back by that, on a row still moving. */
static void th_name_letter(void *s, int n, unsigned now) {
    unsigned letter;
    int to = coverflow_letter_next(s, th.jump_to, th.dir, n, &letter);
    if (to == th.jump_to) return; /* one letter in all: the flow goes round */
    th_name(s, th.jump_to = to, n);
    coverflow_letter(s, letter, now);
}

/* A letter on, Minimal's jump (coverflow_letter_next): the next letter's first album, or the
 * previous letter's turning back, its letter held over the row. The first of letters goes on from
 * the entry with the row held as the entry left it (coverflow_hold), every cover on its album, at
 * the entry's speed v, to the first letter whose first album is v slide_ms or more on from the one
 * at the screen's middle, where the pass's slide_ms follow from v lands with no overshoot, and none
 * before the letter after the album the wheel had got to; each after sets the follower's goal
 * there, from the letter it was going to, and the row slides on through the albums between from
 * wherever it is. Past the last letter it goes on round the seam to the first letter's first
 * album, and back from the first to the last's, as the albums wrap; with one letter there is no
 * step, so no click. Answers whether it stepped; the first of letters always does, to show its
 * letter. */
static int th_letter(void *s, int n, int first) {
    unsigned letter;
    int from = first ? th_aim() : cf_round(th.goal), dir = first ? -th.lean : th.dir;
    float at = first ? coverflow_hold() : 0, v = coverflow_enter_v();
    float near = at + (float)dir * v * (float)cfs.slide_ms;
    if (first &&
        dir > 0) { /* on: the letter after the album before near, if the wheel's is short */
        int before = -cf_floor(-near) - 1;
        from = before > from ? before : from;
    }
    int f = (int)th_album(from, (unsigned)n), to = coverflow_letter_next(s, f, dir, n, &letter);
    if (to == f && !first) return 0;
    /* the short way in the step's direction, through the seam if it's there */
    int go = from + (dir > 0 ? (to - f + n) % n : -((f - to + n) % n));
    if (first &&
        dir < 0) { /* back: near's own letter's first album, if before the wheel's letter */
        unsigned l;
        int b = cf_floor(near), a = (int)th_album(b, (unsigned)n),
            a0 = coverflow_letter_first(a, &l);
        if (b - (a - a0) < go) go = b - (a - a0), letter = l;
    }
    if (first) {
        while (((float)go - near) * (float)dir < 0) go += dir * n; /* round a small library */
        th_rebase(s, go, n);
        th.pos = at - (float)go, th.vel = (float)dir * v, th.goal = th.lead = th.lead_v = 0;
    } else
        th.goal = (float)go;
    th.clicked = cf_round(th.goal);
    coverflow_letter_hold(s, letter);
    widget_invalidate_force(s, 0);
    return 1;
}

/* travel raw units: the goal on by a share of an album that grows with the thumb's speed, at most
 * top_speed albums a second; turning back drops what's queued, and the speed with it. Going by
 * letters, the travel counts towards the next one instead, or the one before from a turn back's
 * first jump_ticks; the row keeps its lean. 1.7.6's letters count it too, and a turn back ends
 * them. An artist's albums stop at the ends:
 * the first push past one kicks the covers out, and they spring back. */
static void th_turn(void *s, int travel, unsigned now, int n, int ms) {
    int dir = travel > 0 ? -1 : 1; /* x rises anticlockwise: back */
    if (dir != th.dir) {
        float l = th.goal - th.pos, m = cfs.back / 100.f;
        if (!th.jump) /* going by letters the goal is the letter's, wherever the row is */
            th.goal = th.pos + (l > m ? m : l < -m ? -m : l);
        th.rate = 0;
        th.stretch = 0;
        if (!ringnav_big_library()) th.beat = th.capped = 0; /* 1.7.6's: the covers move back */
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
    if (th.jump || th.beat) th.stretch += ticks;
    if (th.jump) return;
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

/* Album by album, since ms after the last tick: part-way is for a slow thumb that stays; a lift,
 * the hold's timeout or a stop from speed goes to the album named, in 1.7.6's letters the letter's
 * first album. The row follows the goal, and the facing point leads it. */
static void th_albums(void *s, int n, int since, int ms) {
    int idle = since > cfs.hold_ms;
    float left = ABS(th.goal - th.pos);
    if (th.goal != (float)cf_round(th.goal) &&
        (!th.finger || idle || (since > cfs.stop_ms && left * 100 > cfs.stop_left))) {
        th.goal = (float)th_aim(), left = ABS(th.goal - th.pos);
        if (th.beat && th_moving()) th_name(s, th.jump_to, n); /* onto its letter's first album */
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
}

/* Fast Navigation off, 1.7.6's letters, since ms after the last tick: time at jump_at or more
 * counts towards jump_ms, at most that, and time under it comes off, so a dip costs only its own
 * ms. Then the spin goes by letters, where the sort has them: the covers flow on as any fast turn's
 * do, and each jump_ticks of travel, the first at once and none sooner than jump_pace after the
 * last, names the next letter (th_name_letter), while the covers are moving, so the pictures never
 * change on a still row. Only a turning thumb starts them or moves them on. A lift keeps them for
 * as long as a letter shows, so a re-grip goes on from there; a thumb turning under fast_at, a turn
 * back (th_turn) or that long with no tick ends them, and the album named carries on under it.
 * ponytail: short arcs with a lift each don't reach jump_ms, as on ticks: the rate falls in the
 * lift and rises again. Carry it over the lift for the count too if arcs show on the wheel. */
static void th_beat(void *s, int n, int travel, unsigned now, int ms, int since) {
    th.capped = th.rate >= cfs.jump_at
                    ? (th.capped + ms < cfs.jump_ms ? th.capped + ms : cfs.jump_ms)
                : th.capped > ms ? th.capped - ms
                                 : 0;
    if (th.beat && (since > LETTER_MS || (travel && th.rate < cfs.fast_at)))
        th.beat = 0;
    else if (travel && !th.beat && th.capped >= cfs.jump_ms && cfs.jump_ticks && lettered(s))
        th.beat = 1, th.stretch = cfs.jump_ticks, th.letter_at = now - cfs.jump_pace,
        th.jump_to = (int)th_album(th_aim(), (unsigned)n);
    if (travel && th.beat && th_moving() && th.stretch >= cfs.jump_ticks &&
        (int)(now - th.letter_at) >= cfs.jump_pace) {
        th.stretch = 0, th.letter_at = now;
        th_name_letter(s, n, now);
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
        if (!raw && (th.jump || th.fold)) coverflow_wheel_land(); /* a touch: at once, no fold */
    }
    if (raw) wheel_raw = WHEEL_THUMB; /* decided afresh each pass: lyric_poll may have handed it */
    if (!shown) {
        coverflow_wheel_land();
        return 0;
    }
    if (fx.todo) widget_invalidate_force(s, 0); /* covers still to load, a frame's budget each */
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
    /* Into letters (enter_albums): the row goes on at the speed it had, CF_ENTER_SPEED albums
     * a second at least, the wheel's travel the entry's own, the facing point running on ahead of
     * it off the screen; then the first letter, which always shows, as a change of art. */
    int entered = 0;
    float follow = (float)ms; /* the letters' follow this pass */
    if (th.on && th.jump && th.enter > 0) {
        float v = coverflow_enter_v();
        th.enter -= v * (float)ms;
        if (th.enter > 0) {
            widget_invalidate_force(s, 0);
            return THUMB_MOVED;
        }
        int n = (int)widget_count_children(s);
        follow = -th.enter / v; /* only what is left of the pass past the entry's end */
        th.enter = 0, th.stretch = 0;
        entered = th_letter(s, n, 1) ? THUMB_CLICK : 0;
    }
    if (th.on && th.fold) { /* not after a load or a close dropped the covers mid-fold */
        if ((int)(now - th.fold_at) >= CF_FOLD_MS)
            th.early = 0, th_commit(0); /* a touch while folding steered nothing: it lands too */
        else
            widget_invalidate_force(s, 0); /* a frame each pass, so the loop never idles */
        return THUMB_MOVED;
    }
    /* its shake, a unit or so, moves nothing: travel runs loose in a band of dead units each way */
    th.dead += travel;
    travel = th.dead > cfs.dead ? th.dead - cfs.dead : th.dead < -cfs.dead ? th.dead + cfs.dead : 0;
    th.dead -= travel;
    if (!th.on && !travel) { /* at rest, no thumb on the wheel: an idle pass makes a half copy */
        if (raw && !held && !fx.todo && !P(s, SLIDE_ANIMATOR)) coverflow_halves();
        return 0;
    }
    if (!th.on) th_grab(s, now);
    int n = (int)widget_count_children(s), r = THUMB_MOVED | entered;
    if (travel) {
        th_turn(s, travel, now, n, ms);
        r |= THUMB_TURNED;
    }
    /* the thumb's rate, ticks/s: quick to fall, so slowing down gives control back at once. In
     * 1.7.6's letters, a lifted thumb leaves it: a re-grip isn't a slow thumb. */
    float inst = th.ticks * 1000 / (float)ms, tc = inst > th.rate ? cfs.rate_up : cfs.rate_down;
    if (!th.beat || (th.finger && th.held))
        th.rate += (inst - th.rate) * (1 - ff_decay((float)ms / tc));
    th.ticks = 0;
    /* At big_jump_at or more for big_jump_ms the spin goes by letters, where the sort has them: one
     * each jump_ticks of travel at any rate and either way, the first once the row is in (above),
     * the row leaning against the way the spin went, as the covers the facing point left behind do.
     * Only a lift ends them (above): slowing, resting on the wheel or turning back is reading
     * them, so a false start costs more than 1.7.6's and takes a faster spin of its own. That is
     * Fast Navigation's; off, 1.7.6's (th_beat), at jump_at for jump_ms. */
    int since = (int)(now - th.tick_at), big = ringnav_big_library();
    /* big_jump_at counts towards big_jump_ms; a dip under it, still at fast_at (a thumb
     * re-gripping), drains the count rather than dropping it; a lift or under fast_at drops it. */
    if (big)
        th.capped = !th.finger || th.rate < cfs.fast_at ? 0
                    : th.rate >= cfs.big_jump_at        ? th.capped + ms
                    : th.capped > ms                    ? th.capped - ms
                                                        : 0;
    else
        th_beat(s, n, travel, now, ms, since);
    if (big && !th.jump && th.capped && th.capped >= cfs.big_jump_ms && cfs.jump_ticks &&
        lettered(s)) {
        th.jump = 1, th.lean = -th.dir, th.stretch = 0;
        th.enter = (float)cfs.enter_albums;
    }
    for (; th.jump && th.enter <= 0 && th.stretch >= cfs.jump_ticks; th.stretch -= cfs.jump_ticks)
        if (th_letter(s, n, 0)) r |= THUMB_CLICK;
    if (th.jump && th.enter <= 0) { /* the row slides to the letter, slide_ms; there to half a px */
        /* Speeding up, it grows by e^(ms / slide_ms) a pass at most, from the entry's speed at
         * least (coverflow_enter_v) going on, from CF_ENTER_SPEED turning round (a step back behind
         * it), moving by the mean of the two: a letter's whole slide in a slide_ms would take the
         * spin's speed many times over in a few passes. Slowing isn't held, so it lands where the
         * follow does. */
        float p0 = th.pos, v0 = th.vel, on = coverflow_enter_v(),
              e = ff_decay(follow / cfs.slide_ms);
        th_follow(&th.pos, &th.vel, th.goal, cfs.slide_ms, follow);
        float most = (th.vel * v0 > 0 ? on : (float)CF_ENTER_SPEED / 1000) / e;
        if (ABS(th.vel) > most)
            th.vel = th.vel > 0 ? most : -most, th.pos = p0 + (v0 + th.vel) / 2 * follow;
        if (ABS(th.pos - th.goal) * cfs.row_out < .5f) th.pos = th.goal, th.vel = 0;
        /* The lift: once the row is within fold_near of the letter's first album, it folds back
         * out onto it for CF_FOLD_MS, from where it is and at the speed it has (fold_off,
         * fold_vel), so the follower's slow tail doesn't hold it back; the wheel steers nothing
         * till it ends, then counts again from there. A lift on the way in waits for the entry's
         * end, so the fold starts from the held row. */
        float left = th.goal - th.pos;
        if (!th.finger && ABS(left) * 100 <= cfs.fold_near) {
            th_rebase(s, cf_round(th.goal), n);
            th.fold_off = th.pos, th.fold_vel = th.vel, th.pos = th.vel = 0;
            th.jump = 0, th.fold = 1, th.fold_at = now;
            coverflow_letter(s, 0, now);
        } else if (!left && th.goal)
            th_rebase(s, cf_round(th.goal), n); /* at rest on it */
    } else
        th_albums(s, n, since, ms);
    int q = cf_round(th.pos);
    if (q >= TH_REBASE || q <= -TH_REBASE) th_rebase(s, q, n);
    I(s, SLIDE_OFFSET) = -cf_round(th.pos * (float)stride_of(s));
    if (th.fold || ABS(th.pos - th.seen) >= 1 / 4096.f ||
        ABS(th.lead - th.seen_lead) >= 1 / 4096.f ||
        !th.lead != !th.seen_lead) { /* the fold's first frame too */
        th.seen = th.pos, th.seen_lead = th.lead;
        widget_invalidate_force(s, 0);
    } else if (!(r & THUMB_TURNED))
        r = 0; /* a thumb holding still: the loop may idle */
    th_named(n);
    if (cf_round(th.goal) != th.clicked) {
        th.clicked = cf_round(th.goal);
        r |= THUMB_CLICK;
    }
    if (!th.jump && !th.beat && !th.fold && !th.lead && th.pos == th.goal &&
        th.goal == (float)cf_round(th.goal))
        th_commit(cf_round(th.goal));
    return r;
}
