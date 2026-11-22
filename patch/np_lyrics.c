/* Now Playing's lyrics: the follow, the wheel's glide and nudge, the hold, and full screen.
 * lyrics.c lays out and paints the lines. */
#include "navigation.h"

/* The lyrics (lyric_poll): lyric_raw while their page holds the wheel's raw travel; lyric_spun once
 * the wheel moved them since it was let go (2: only nudged them, still following); lyric_touch
 * while a finger is on Now Playing, 1 if it came down on the lyrics, lyric_drag_y the offset then;
 * the glide's target lyric_to (1/16 px) and its last step lyric_at; lyric_follow_at the last
 * follow. Full screen's column before it (np_full) and where the last touch on the lyrics came
 * down (np_lyric_touch). navigation.h's st has the hold, the glide and the songs. */
static struct {
    int lyric_raw, lyric_spun, lyric_touch, lyric_drag_y, lyric_to;
    int lyric_push, lyric_home; /* travel short of LYRIC_PUSH_DETENTS, and where it nudged from */
    unsigned lyric_push_at;
    unsigned lyric_at, lyric_follow_at;
    int np_was[9]; /* the column before full screen (np_full) */
    int np_tap_x, np_tap_y;
} nl __attribute__((section(".scratch")));

/* scroll_lrc's rows are 20 px, or 25 a line when wrapped (0x53b838), and its 178 px are no whole
 * number of them, so the view's end (content height - view height), where stock's follow and the
 * wheel stop, cut the top row. The content grows to the first row top at or past that end plus the
 * view: under a row of blank space below the last. A relayout (scroll_view's, 0x604638) puts the
 * height back and clamps the offset; the next follow, tick or glide step pads it again. ponytail:
 * the wheel and a drag stop anywhere, so a row can be cut at the top until the page follows. iPod's
 * lyrics page (lyrics.c) ends with its last line in the middle. */
/* The offset that centres row i of lrc in its view. */
static int lyric_centre(widget_t *lrc, int i) {
    widget_t *row = widget_get_child(lrc, i);
    /* NOLINTNEXTLINE(clang-analyzer-core.NullPointerArithm): callers pass lrc past lyrics_on */
    return I(row, W_Y) + (I(row, W_H) - I(lrc, W_H)) / 2;
}

static void lyric_pad(widget_t *lrc) {
    if (lyrics_on(lrc)) {
        I(lrc, VIEW_CONTENT_H) = lyric_centre(lrc, widget_count_children(lrc) - 1) + I(lrc, W_H);
        return;
    }
    int h = I(lrc, W_H), end = I(lrc, VIEW_CONTENT_H) - h, top = 1 << 30;
    for (int i = widget_count_children(lrc); i-- > 0;) {
        int t = I(widget_get_child(lrc, i), W_Y);
        if (t >= end && t < top) top = t;
    }
    if (end > 0 && top < 1 << 30) I(lrc, VIEW_CONTENT_H) = top + h;
}

/* The lyrics page in front: Now Playing on top, lit, not sliding and untouched, on the lyrics. */
static int lyrics_shown(void) {
    widget_t *wm = window_manager();
    return st.np_lrc && st.np_slide && usable() && window_manager_get_top_window(wm) == st.np_win &&
           !window_manager_is_animating(wm) && !pointer_down() &&
           widget_get_prop_int(st.np_slide, "value", 0) == 1;
}

/* Another song: its lyrics at the top, nothing held or gliding, and stock's animator, which would
 * carry them on to where the last song's follow sent them, stopped. */
static void lyric_top(widget_t *lrc) {
    st.lyric_held = nl.lyric_spun = st.lyric_glide = nl.lyric_to = nl.lyric_push = 0;
    view_still(lrc);
    scroll_view_set_offset(lrc, I(lrc, SCROLL_X), 0);
}

/* The lyrics glide to `to` (1/16 px) within their content; lyric_step moves them, every UI pass. */
static void lyric_glide(widget_t *lrc, int to) {
    view_still(lrc);
    nl.lyric_to = clamp_step(to, (I(lrc, VIEW_CONTENT_H) - I(lrc, W_H)) * 16, 0);
    if (!st.lyric_glide) nl.lyric_at = (unsigned)time_now_ms();
    st.lyric_glide = 1;
}

/* The Now Playing timer's follow (LYRIC_FOLLOW in tools/build.py): stock scrolls to the row three
 * lines above the current one, which past the end clamps to it. iPod: lyrics the wheel or a finger
 * moved stay (lyric_held) until Now Playing goes or another song starts, and a finger on the
 * screen keeps them too; on the page, the follow glides. The lyrics page follows its own playing
 * line (lyric_follow). */
int ringnav_lyric_follow(widget_t *lrc, int x, int y, int ms) {
    lyric_pad(lrc);
    if (lrc == st.np_lrc) {
        if (st.lyric_held || nl.lyric_spun == 2 || lyrics_on(lrc) || pointer_down()) return 0;
        if (lyrics_shown()) {
            lyric_glide(lrc, y * 16);
            return 0;
        }
        st.lyric_glide = 0;
    }
    return scroll_view_scroll_to(lrc, x, y, ms);
}

/* The page's refresh rebuilds scroll_lrc's rows (0x53b67c, LYRIC_ROWS in tools/build.py) whenever
 * g_forcerefresh_flag is set, as Play/Pause (playpause_quick_click) and the page's return to the
 * front do, and the rebuild ends at offset 0: the lyrics flicked to the top until the next follow.
 * The offset stays; the follow moves it if the line did. iPod: rows for another song than the last
 * rows' start at the top (lyric_top), whatever moved the old ones since the song changed, and they
 * become the lyrics page's (lyrics_rows). */
int ringnav_lyric_rows(widget_t *win) {
    widget_t *lrc = widget_lookup(win, "scroll_lrc", 1);
    int y = lrc ? I(lrc, SCROLL_Y) : 0, ret = ((int (*)(void *))0x53b67c)(win);
    if (lrc && lrc == st.np_lrc && lyrics_rows(lrc)) lyric_pad(lrc);
    unsigned song = lrc && lrc == st.np_lrc ? np_track() : st.lyric_rows_song;
    if (song != st.lyric_rows_song) {
        st.lyric_rows_song = st.lyric_song = song;
        lyric_top(lrc);
        return ret;
    }
    if (y) scroll_view_set_offset(lrc, I(lrc, SCROLL_X), y);
    return ret;
}

/* The lyrics: stock's 250 ms timer scrolls scroll_lrc to the current line whenever it is elsewhere.
 * Once the wheel or a finger moves them they stay (ringnav_lyric_follow), while the timer keeps the
 * bar and the highlight going, until Now Playing goes or another song starts, a swipe to another
 * page and back included. A scrub under way ends first. */
static void lyric_hold(void) {
    scrub_end();
    st.lyric_held = 1;
}

/* The glide's step, every UI pass: a dt / (dt + LYRIC_EASE_MS) share of the way left, at least a
 * pixel, so it eases in whatever the loop's pace, and quick wheel travel adds up. */
#define LYRIC_EASE_MS 50
static void lyric_step(widget_t *lrc) {
    unsigned now = (unsigned)time_now_ms();
    int dt = (int)(now - nl.lyric_at);
    nl.lyric_at = now;
    lyric_pad(lrc);
    int y = I(lrc, SCROLL_Y),
        to = (clamp_step(nl.lyric_to, (I(lrc, VIEW_CONTENT_H) - I(lrc, W_H)) * 16, 0) + 8) >> 4;
    if (to == y) {
        st.lyric_glide = 0;
        return;
    }
    if (dt > LYRIC_EASE_MS) dt = LYRIC_EASE_MS; /* a late pass: half the way, no leap */
    int step = (to - y) * dt / (dt + LYRIC_EASE_MS);
    scroll_view_set_offset(lrc, I(lrc, SCROLL_X), y + (step ? step : to > y ? 1 : -1));
}

/* The wheel let go: the glide ends on the row top nearest its target, so no row is cut at the top.
 * The end is one (lyric_pad). */
static void lyric_snap(widget_t *lrc) {
    lyric_pad(lrc);
    int at = st.lyric_glide ? nl.lyric_to : I(lrc, SCROLL_Y) * 16, to = 0;
    for (int i = 0, n = widget_count_children(lrc); i < n; ++i) {
        int top = I(widget_get_child(lrc, i), W_Y) * 16;
        if ((top > at ? top - at : at - top) < (to > at ? to - at : at - to)) to = top;
    }
    lyric_glide(lrc, to);
}

/* Every UI loop pass (ringnav_sleep). On the lyrics page in front, while the track has lyrics, the
 * wheel posts no ticks (wheel_raw) and its raw travel, 200 units a turn, glides them a pixel at a
 * time: a tick's worth of travel at the Wheel Sensitivity moves them LYRIC_ROW_PX, stock's row, as
 * far as a tick did. No key event comes, so the travel times the loop's idle and the screen's
 * timeout itself. While a finger stays on the wheel they stay where it put them; let go, they glide
 * to the nearest row top (lyric_snap). A finger that came down on the lyrics and dragged them holds
 * them as the wheel does; stock's scroll animation stops as it lands, so only the finger moves
 * them, and a swipe that brings the page in holds nothing. A swipe to another page and back keeps
 * the hold; leaving Now Playing drops it (np_gone). Another song starts at the top, its hold
 * gone. The lyrics page's rows are its lines, a tick's worth a line. */
#define LYRIC_ROW_PX 20

/* Now Playing's lyrics are the lyrics page's (lyrics.c): the rows full width after a blank
 * LYRIC_SLOT. Stock's init built the rows before ringnav_playing knew the page; they become the
 * page's, or, with none or no memory for their table, stay stock's. */
void lyric_init(void) {
    widget_t *lrc = st.np_lrc;
    if (lrc && lyrics_rows(lrc)) lyric_pad(lrc);
}

/* The lyrics page, every pass in front, else every LYRIC_FOLLOW_MS: its playing line centred,
 * gliding there while the page shows, else jumping, unless the wheel or a finger holds them or a
 * finger is on the screen. Its rows repaint while it is in front (front) and lit. */
#define LYRIC_FOLLOW_MS 250
static void lyric_follow(widget_t *lrc, int ms, int front, int shown) {
    int line = lyrics_line(lrc, ms, front), to = lyric_centre(lrc, line > 0 ? line : 0);
    if (to < 0) to = 0;
    if (st.lyric_held || nl.lyric_spun == 2 || pointer_down())
        return; /* lyric_spun 2: a finger on the wheel holds the nudge */
    lyric_pad(lrc);
    if (shown) {
        if (to * 16 != nl.lyric_to || (!st.lyric_glide && I(lrc, SCROLL_Y) != to))
            lyric_glide(lrc, to * 16);
    } else if (I(lrc, SCROLL_Y) != to) {
        view_still(lrc);
        scroll_view_set_offset(lrc, I(lrc, SCROLL_X), to);
    }
}

/* Full-screen lyrics (np_lyric_touch): the column (slide_view_view) goes over the page's other
 * widgets, black and the window's width, its slide_view down to the page dots, and the lines wrap
 * at the new width (lyrics_fit), back on the playing line; off, its place, index and slide_view's
 * height before (np_was) again. Centred's column (tools/ipod.py PLAYING_STOCK) keeps its y and
 * height, its lyrics ending 20 px above its foot. The iPod page's, between the top row and the
 * bar, takes the whole window: its dots NP_FULL_FOOT above the foot, as Centred's, and its
 * view_lrc, whose self_layout fixes its place, from the top down to NP_FULL_FOOT above them; off,
 * both self_layouts are their old place again. A page without a slide_view has none of this. */
#define NP_FULL_FOOT 20
void np_full(int on) {
    if (!st.np_slide || !on == !st.np_full) return; /* without stock's slide_view: no column */
    widget_t *col = P(st.np_slide, W_PARENT), *dots = widget_lookup(col, "slide_indicator1", 1),
             *view = P(st.np_lrc, W_PARENT);
    /* off, the layout it started in: Centred's setting changes only the next Now Playing's */
    int *was = nl.np_was, ipod = on ? np_layout() != NP_CENTRED && dots && view : st.np_full == 2;
    if (on) {
        int now[] = { I(col, W_X), I(col, W_Y),          I(col, W_W),
                      I(col, W_H), widget_index_of(col), I(st.np_slide, W_H) };
        memcpy(was, now, sizeof now);
        if (ipod) was[6] = I(view, W_Y), was[7] = I(view, W_H), was[8] = I(dots, W_Y);
    }
    st.np_full = on ? 1 + ipod : 0;
    widget_restack(col, on ? widget_count_children(st.np_win) : was[4]);
    widget_set_prop_str(col, "style:normal:bg_color", on ? "#000000" : "#00000000");
    int h = I(st.np_win, W_H), foot = on ? h - NP_FULL_FOOT : was[8];
    if (!on)
        widget_move_resize(col, was[0], was[1], was[2], was[3]);
    else
        widget_move_resize(col, 0, ipod ? 0 : was[1], I(st.np_win, W_W), ipod ? h : was[3]);
    if (ipod) {
        char y[12], vy[12], vh[12];
        tk_snprintf(y, sizeof y, "%d", foot);
        tk_snprintf(vy, sizeof vy, "%d", on ? 0 : was[6]);
        tk_snprintf(vh, sizeof vh, "%d", on ? -NP_FULL_FOOT : was[7]);
        widget_set_self_layout_params(dots, "0", y, "100%", "10");
        widget_set_self_layout_params(view, "0", vy, "100%", vh);
    }
    widget_resize(st.np_slide, I(col, W_W), on && dots ? (ipod ? foot : I(dots, W_Y)) : was[5]);
    widget_layout(col);
    if (lyrics_fit(st.np_lrc)) lyric_pad(st.np_lrc);
    /* the follow takes them; the offset the layout left is no drag of the touch (lyric_poll) */
    st.lyric_held = 0, nl.lyric_drag_y = I(st.np_lrc, SCROLL_Y);
}

void lyric_poll(void) {
    widget_t *lrc = st.np_lrc, *wm = window_manager();
    unsigned song = lrc ? np_track() : st.lyric_song;
    if (song != st.lyric_song) {
        st.lyric_song = song;
        lyric_top(lrc);
    }
    int page = lrc && st.np_slide && widget_get_prop_int(st.np_slide, "value", 0) == 1;
    if (st.np_full && !page) np_full(0); /* full-screen lyrics end off their page */
    int touch = lrc && window_manager_get_top_window(wm) == st.np_win && pointer_down();
    int shown = page && lyrics_shown(),
        /* not during a scrub: its ticks are np_key's, the lyrics' again once it ends */
        on = shown && !st.scrub && widget_count_children(lrc) && mclGetLyricSize() > 0;
    if (touch && !nl.lyric_touch) {
        nl.lyric_touch = page ? 1 : 2;
        nl.lyric_drag_y = page ? I(lrc, SCROLL_Y) : 0;
        if (page) view_still(lrc);
    } else if (!touch && nl.lyric_touch) {
        if (nl.lyric_touch == 1 && page && I(lrc, SCROLL_Y) != nl.lyric_drag_y) lyric_hold();
        nl.lyric_touch = 0;
    }
    if (on != nl.lyric_raw) {
        __sync_lock_test_and_set(&wheel_travel, 0);
        nl.lyric_raw = on;
        if (on || wheel_raw == WHEEL_RAW) wheel_raw = on; /* never the thumb's, handed over */
    }
    int travel = on ? __sync_lock_test_and_set(&wheel_travel, 0) : 0;
    /* Following, the wheel meets the resistance of a list's end: travel short of
     * LYRIC_PUSH_DETENTS within LYRIC_PUSH_MS only nudges the lyrics BUMP_PX from the follow's
     * place, and they spring back when it is let go, still following; a stray touch keeps the
     * follow. More takes over. */
    if (travel && !st.lyric_held) {
        unsigned at = (unsigned)time_now_ms();
        if (!nl.lyric_push || at - nl.lyric_push_at > LYRIC_PUSH_MS) {
            lyric_pad(lrc);
            nl.lyric_push = 0; /* a nudge still held keeps its home: no walking off tick by tick */
            if (nl.lyric_spun != 2)
                nl.lyric_home = st.lyric_glide ? nl.lyric_to : I(lrc, SCROLL_Y) * 16;
        }
        nl.lyric_push_at = at;
        nl.lyric_push += travel;
        /* the clicks Wheel Sensitivity makes: fewer raw units each as it rises */
        int push = LYRIC_PUSH_DETENTS * WHEEL_TICK_UNITS * 50 / wheel_pct();
        if (nl.lyric_push > -push && nl.lyric_push < push) {
            st.last_input = at;
            reset_poweroptions_timer(1, 1, 1);
            /* x rises anticlockwise: back up the lyrics */
            lyric_glide(lrc,
                        nl.lyric_home - ((nl.lyric_push > 0) - (nl.lyric_push < 0)) * BUMP_PX * 16);
            nl.lyric_spun = 2;
            travel = 0;
        }
    }
    if (travel) {
        st.last_input = time_now_ms();
        reset_poweroptions_timer(1, 1, 1);
        lyric_hold();
        nl.lyric_push = 0;
        lyric_pad(lrc);
        int y = I(lrc, SCROLL_Y),
            from = st.lyric_glide || (nl.lyric_to + 8) >> 4 == y ? nl.lyric_to : y * 16;
        /* x rises anticlockwise: back up the lyrics */
        int row = lyrics_on(lrc) ? LYRIC_SLOT : LYRIC_ROW_PX;
        lyric_glide(lrc, from - travel * row * 16 * wheel_pct() / (WHEEL_TICK_UNITS * 50));
        nl.lyric_spun = 1;
    }
    if (!page)
        nl.lyric_spun = nl.lyric_push = 0;
    else if (nl.lyric_spun == 2 && !wheel_held) {
        /* a nudge springs back, shown or not, unless a tick took them meanwhile; the follow moves
         * them on if the line changed. The next touch starts its push afresh. */
        if (shown && !st.lyric_held) lyric_glide(lrc, nl.lyric_home);
        nl.lyric_spun = nl.lyric_push = 0;
    } else if (shown && nl.lyric_spun && !wheel_held) {
        nl.lyric_spun = 0;
        lyric_snap(lrc);
    }
    /* The page in front and usable (lit): the clock and the follow every pass (it narrows on each
     * second's change, lyrics_now). Else the follow, which only jumps, at stock's timer's pace on
     * whole seconds (lyrics_sec), so a dark or covered page reads the clock only that often. */
    int front = page && usable() && window_manager_get_top_window(wm) == st.np_win;
    unsigned now = (unsigned)time_now_ms();
    if (lyrics_on(lrc) && (front || now - nl.lyric_follow_at >= LYRIC_FOLLOW_MS)) {
        nl.lyric_follow_at = now;
        lyric_follow(lrc, front ? lyrics_now() : lyrics_sec(), front, shown);
    }
    if (!shown)
        st.lyric_glide = 0;
    else if (st.lyric_glide)
        lyric_step(lrc);
}

/* A tick on the lyrics page while the track has lyrics, which comes only before lyric_poll takes
 * the wheel raw, glides them to the next or previous row's top, ahead of the volume; never during
 * a scrub, whose ticks move its target. */
int np_lyrics(unsigned key) {
    widget_t *lrc = st.np_lrc;
    if (st.scrub || !st.np_slide || !lrc || widget_get_prop_int(st.np_slide, "value", 0) != 1 ||
        !widget_count_children(lrc) || mclGetLyricSize() <= 0)
        return 0;
    lyric_hold();
    lyric_pad(lrc);
    int y = st.lyric_glide ? (nl.lyric_to + 8) >> 4 : I(lrc, SCROLL_Y),
        max = I(lrc, VIEW_CONTENT_H) - I(lrc, W_H), to = key == KEY_NEXT ? max : 0;
    for (int i = 0, n = widget_count_children(lrc); i < n; ++i) {
        int top = I(widget_get_child(lrc, i), W_Y);
        if (key == KEY_NEXT ? top > y && top < to : top < y && top > to) to = top;
    }
    lyric_glide(lrc, clamp_step(to, max, 0) * 16);
    return 1;
}

/* Now Playing's lyrics page (ringnav_playing): a touch that lifts within TAP_PX of where it came
 * down, so neither a swipe nor a drag of the lines, switches them to full screen and back
 * (np_full), once there are lyrics to show. The slide_view or scroll_lrc taking the touch (its
 * abort) ends full screen at once, so the pages slide in their column. Returns 0: a STOP would
 * keep the slide_view's grab (rate_fav_click). */
#define TAP_PX 10 /* AWTK's drag threshold */
int np_lyric_touch(void *ctx, event_t *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    int tap = (unsigned)(x - nl.np_tap_x + TAP_PX) <= 2 * TAP_PX &&
              (unsigned)(y - nl.np_tap_y + TAP_PX) <= 2 * TAP_PX;
    if (type == EVT_POINTER_DOWN) {
        nl.np_tap_x = x, nl.np_tap_y = y;
        return 0;
    }
    nl.np_tap_x = 1 << 20; /* used or taken for a swipe: a later lift is no tap */
    if (type == EVT_POINTER_ABORT) {
        if (st.np_full) np_full(0);
    } else if (tap && (st.np_full || lyrics_on(st.np_lrc)))
        np_full(!st.np_full);
    return 0;
}
