/* Now Playing's bar: Classic's times and bar fitted to the track, the bar's title, the theme's
 * progress bar and its fill. */
#include "navigation.h"

static int bar_bold __attribute__((section(".scratch"))); /* the bar's title set bold (bar_title) */

/* Classic's bar's left end for a track sec long: the times' boxes hold "-9:59" under 10:00,
 * "-59:59" under an hour, else "-9:59:59" (NP_IPOD_TIME_W*, tools/ipod.py measures them), their
 * outer edges and the gap kept; the bar takes the rest, mirrored. */
static int ipod_bar_x(int sec) {
    return NP_IPOD_BAR_X - NP_IPOD_TIME_W1 +
           (sec < 600    ? NP_IPOD_TIME_W1
            : sec < 3600 ? NP_IPOD_TIME_W2
                         : NP_IPOD_TIME_W);
}

/* Classic's elapsed and remaining times and bar (with none, the radio's own) fitted to a track sec
 * long (ipod_bar_x); unknown (0 or less) leaves them, so the page keeps the build's "-9:59" boxes
 * and widest bar, or the last track's, whose times hold the 0:00 an unknown length shows. Each
 * keeps its y and height and repaints where it was and where it goes, so the caller is outside a
 * paint, which loses its invalidates (#116). 1 when any moved. */
int ipod_times(void *elapsed, void *remain, void *bar, int sec) {
    if (sec <= 0) return 0;
    int x = ipod_bar_x(sec), tw = x - (NP_IPOD_BAR_X - NP_IPOD_TIME_W1), bw = 375 - 2 * x,
        moved = 0;
    void *w[] = { elapsed, remain, bar ? bar : st.stream_bar };
    const int to[3][2] = { { x - NP_IPOD_TIME_GAP - tw, tw },
                           { x + bw + NP_IPOD_TIME_GAP, tw },
                           { x, bw } };
    for (unsigned i = 0; i < 3; ++i) {
        if (!w[i] || (I(w[i], W_X) == to[i][0] && I(w[i], W_W) == to[i][1])) continue;
        widget_invalidate_force(w[i], (void *)0);
        widget_move_resize(w[i], to[i][0], I(w[i], W_Y), to[i][1], I(w[i], W_H));
        widget_invalidate_force(w[i], (void *)0);
        moved = 1;
    }
    return moved;
}

/* The remaining time, once after a frame whose paint np_sync wrote it in: its invalidate there is
 * lost with the frame's dirty rects (#116). Minimal's and Modern's sit in the rect the slider's and
 * the elapsed time's repaint takes each second; only times beside the bar (np_beside) need it. */
int remain_due(const void *info) {
    (void)info;
    st.np_remain_timer = 0;
    if (st.np_remain) widget_invalidate_force(st.np_remain, (void *)0);
    return 0;
}

/* 1 when Now Playing's times sit on its bar's rows, beside it (Classic's iPod page; its Centred
 * page and the other themes' put them under the bar): outside the slider's repaint. */
int np_beside(void) {
    if (!st.np_remain || !st.np_slider || !st.np_elapsed) return 0;
    int bh = widget_get_prop_int(st.np_slider, "bar_size", 8);
    return I(st.np_elapsed, W_Y) < I(st.np_slider, W_Y) + (I(st.np_slider, W_H) + bh) / 2;
}

/* Classic's times and bar fitted to the playing track's length (the slider's max), off the paint
 * whose np_sync found them stale. */
int np_fit_due(const void *info) {
    (void)info;
    st.np_fit_timer = 0;
    if (np_beside())
        ipod_times(st.np_elapsed, st.np_remain, st.np_slider,
                   widget_get_prop_int(st.np_slider, "max", 0));
    return 0;
}

/* Now Playing's "3 of 12", album and remaining time (slider max less value, stock's seconds, so a
 * drag or scrub previews it). Labels are written only when their source changes; 1 when the
 * remaining time was. Classic's times beside the bar refit when the length's class changes: a
 * seek, a pause or a rate change keeps it. */
int np_sync(void *top) {
    if (!top || top != st.np_win) return 0;
    st.np_bold = theme_font(st.np_title, ROLE_NP, st.np_bold); /* the title, as it changes */
    unsigned at, n;
    void *r = queue_now(&at, &n);
    const char *album = now_tag(r, REC_ALBUM);
    char s[24] = "";
    unsigned pos[2] = { at, n };
    unsigned h = hash_bytes(fnv(FNV_SEED, (const unsigned char *)album), (const unsigned char *)pos,
                            sizeof pos);
    if (h != st.np_hash) {
        st.np_hash = h;
        if (r) tk_snprintf(s, sizeof s, "%d of %d", at + 1, n);
        widget_set_text_utf8(st.np_pos, s);
        widget_set_text_utf8(st.np_album, album ? album : "");
        st.np_off = 0;
        stop_timer(&st.np_marquee); /* the next paint restarts it if the album overflows */
    }
    int max = widget_get_prop_int(st.np_slider, "max", 0),
        left = max - widget_get_prop_int(st.np_slider, "value", 0);
    if (max > 0 && !st.np_fit_timer && np_beside() && I(st.np_slider, W_X) != ipod_bar_x(max))
        st.np_fit_timer = timer_add(np_fit_due, (void *)0, 0); /* mid-paint */
    if (left < 0) left = 0;
    if (left == st.np_left) return 0;
    st.np_left = left;
    clock_text(st.np_remain, 1, left);
    return 1;
}

/* Time in Title off: the bar's centre names the page in front, as an iPod's, in Modern's bold:
 * Now Playing or Cover Flow, else stock's title in the page's hidden navbar (scrlabel_title, or a
 * settings page's unnamed first child). Home, a page showing its navbar and an untitled page keep
 * the clock; a pop-up or keyboard leaves the bar as it is. This runs in a paint, which drops the
 * label's invalidate, so bar_repaint follows; the clock waits for its timer. */
void bar_title(void *bar, void *top) {
    static const unsigned
        cover_flow[] = { 'C', 'o', 'v', 'e', 'r', ' ', 'F', 'l', 'o', 'w', 0 }, none = 0;
    if (st.bar_title) { /* this bar names the page in its own label: off, the clock makes way */
        const unsigned *own = widget_get_text(st.bar_title);
        /* Now Playing's page names nothing (bar_title_of): V1.36's np page writes its own title
         * under us ("▶ 正在播放", the bar's play state says it instead), so whatever lands in the
         * label while that page is on top goes before the paint does. */
        if (top && own && *own) {
            const char *page = widget_get_prop_str(top, "name", "");
            if (tk_str_start_with(page, "playing_page")) {
                widget_set_text_utf8(st.bar_title, "");
                own = widget_get_text(st.bar_title);
                st.clock_key = 0;
            }
        }
        int titled = !st.time_title && own && *own;
        void *clock = titled != st.bar_titled ? widget_lookup(bar, "label_clock", 1) : (void *)0;
        if (!clock) return;
        st.bar_titled = titled;
        st.clock_key = 0;
        if (titled)
            widget_set_text(clock, &none);
        else
            rearm(&st.clock_timer, clock_due, 0); /* clock_show writes it again */
        /* the title takes the clock's room while it makes way, up to EQ's box, parked where the
         * right group's room starts (tools/ipod.py) */
        static int title_w;
        if (!title_w) title_w = I(st.bar_title, W_W);
        void *eq = st.bar_eq ? P(st.bar_eq, W_PARENT) : (void *)0;
        int wide = eq ? I(eq, W_X) + I(st.bar_eq, W_X) - I(st.bar_title, W_X) : title_w;
        widget_resize(st.bar_title, titled && wide > title_w ? wide : title_w,
                      I(st.bar_title, W_H));
        if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
        return;
    }
    /* Minimal/Modern: the page name goes on the LEFT, beside the play-state glyph, in
     * label_playword -- the very label the 正在播放/播放暂停 word uses when there is no
     * name. The two are mutually exclusive, as under Classic, so a long name never
     * reaches the EQ badge or the battery, and the glyph stays where it was. The
     * clock keeps its time; with Time in Title off it makes way instead of being
     * overwritten by the name, which may then use the middle of the bar. */
    if (!top || tk_strcmp(widget_get_type(top), "window")) return; /* a dialog keeps the bar */
    /* bar_word_sync normally created this label before bar_title runs; a paint that reaches here
     * first (or a bar built without a sync) still finds it by name. */
    void *word = st.play_word ? st.play_word : widget_lookup(bar, "label_playword", 1);
    void *clock = widget_lookup(bar, "label_clock", 1);
    if (!word || !clock) return;
    void *nav = widget_lookup(top, "view_navbar", 0), *t = (void *)0;
    if (nav && !widget_get_visible(nav)) {
        t = widget_lookup(nav, "scrlabel_title", 0);
        if (!t && widget_count_children(nav)) t = widget_get_child(nav, 0);
        if (t && tk_strcmp(widget_get_type(t), "hscroll_label")) t = (void *)0;
    }
    /* Now Playing names nothing: the word beside the glyph says playing or paused,
     * as it does under Classic (bar_title_of). */
    const char *page = widget_get_prop_str(top, "name", "");
    const unsigned *title = tk_str_start_with(page, "playing_page") ? (void *)0
                            : !tk_strcmp(page, "coverflow_page")    ? cover_flow
                            : t ? widget_get_text(t)
                                : (void *)0;
    if (title && !*title) title = (void *)0;
    static const unsigned null_name[] = { '(', 'n', 'u', 'l', 'l', ')', 0 };
    const unsigned *a = title, *b = null_name; /* stock's "%s" of a NULL name: no title */
    while (a && *a && *a == *b) ++a, ++b;
    if (a && !*a && !*b) title = (void *)0;
    if (!title) {
        /* No name: hand the label back to bar_word_sync's play-state word. bar_sync already ran
         * before us in this frame, so write the word here too — otherwise the previous page's
         * name would linger until bar_repaint reaches bar_word_sync on the next one. */
        if (st.bar_named || st.bar_titled) {
            st.bar_named = st.bar_titled = 0;
            st.word_key = -1; /* its text is written again on the next pass */
            st.clock_key = 0;
            int status = bar_status(); /* Minimal/Modern: state_own_place is always true */
            widget_set_text_utf8(word, status == 2 ? "正在播放" : status == 3 ? "播放暂停" : "");
            widget_set_visible(word, status == 2 || status == 3, 0);
            /* The name is gone: stock's "Q2" may take the left back, but only when
             * the play state's glyph has not (bar_state_sync decides each frame). */
            void *idle = widget_lookup(bar, "label_idle", 1);
            if (idle) widget_set_visible(idle, !(status == 2 || status == 3), 0);
            rearm(&st.clock_timer, clock_due, 0);
            if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
        }
        bar_bold = 0;
        return;
    }
    int titled = !st.time_title;
    if (titled != st.bar_titled) {
        st.bar_titled = titled;
        st.clock_key = 0;
        if (titled) widget_set_text(clock, &none);
        else rearm(&st.clock_timer, clock_due, 0); /* clock_show writes it again */
    }
    /* The name's box ends before the clock's first ink column, or before the right
     * group's first widget when the clock has made way. hscroll_label keeps the
     * rest of a long name inside its box instead of letting it run into EQ. */
    /* The glyph's slot is only reserved while it shows: with it hidden the name
     * stands at the group's own left, level with stock's idle wordmark and with
     * the page's own rows (V183B photos: an empty box before 播放设置). Bars
     * without the glyph (the machine tests' bare ones) keep the word where it is. */
    int x = I(word, W_X);
    if (st.bar_state) {
        void *left = P(st.bar_state, W_PARENT);
        x = left ? I(left, W_X) + I(st.bar_state, W_X) : I(st.bar_state, W_X);
        if (widget_get_visible(st.bar_state)) x += I(st.bar_state, W_W) + STATUS_GAP;
    }
    if (I(word, W_X) != x)
        widget_move_resize(word, x, I(word, W_Y), I(word, W_W), I(word, W_H));
    int limit;
    if (titled) {
        void *first = st.bar_eq && widget_get_visible(st.bar_eq) ? st.bar_eq : st.bar_wifi,
             *parent = first ? P(first, W_PARENT) : (void *)0;
        limit = parent ? I(parent, W_X) + I(first, W_X) - STATUS_GAP : 375 - 40;
    } else {
        int ink = 80; /* the 16 px clock's widest "12:59 下午", centred in its box */
        limit = I(clock, W_X) + (I(clock, W_W) - ink) / 2 - STATUS_GAP;
    }
    int w = limit - x;
    if (w < 40) w = 40;
    if (w > 375 - x - 8) w = 375 - x - 8;
    if (I(word, W_W) != w) widget_resize(word, w, I(word, W_H));
    const unsigned *had = widget_get_text(word), *want = title;
    while (had && *had && *had == *want) ++had, ++want;
    int changed = !st.bar_named || !had || *had != *want;
    if (changed) widget_set_text(word, title);
    /* The bar's own face at the clock's 16 px, never ROLE_NP's bold: the name reads
     * like the time beside it. label_font writes "default", the face with hanzi,
     * and the weight mark clears anything a theme or a previous page left behind. */
    static const char *const sizes[] = { "style:normal:font_size", "style:focused:font_size",
                                         "style:disable:font_size" };
    for (unsigned i = 0; i < 3; ++i) widget_set_prop_int(word, sizes[i], 16);
    bar_bold = theme_font(word, ROLE_BAR, bar_bold);
    label_font(word, 0);
    font_weight_mark(word, 0);
    if (!widget_get_visible(word)) widget_set_visible(word, 1, 0);
    if (!st.bar_named) st.word_key = -1;
    st.bar_named = 1;
    /* One label on the left: the page name has it now, so stock's idle "Q2"
     * wordmark must not ink over it (V183A device photos: "Q2系统设置"). */
    void *idle = widget_lookup(bar, "label_idle", 1);
    if (idle) widget_set_visible(idle, 0, 0);
    if (changed && !st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}

/* No knob in any state on a plain slider (tools/ipod.py plain_slider): Now Playing's, Quick
 * Settings' brightness and the settings slider pages'. The asset's style name has no theme entry,
 * and stock then reads what the inline style leaves out from its default slider style (0x6b48cc),
 * whose icon is the knob: rounded_n, rounded_o focused or over, rounded_p pressed. With one, stock
 * draws it, sizes the dragger to it and fills and seeks short of the ends by half of it. An icon
 * that's no string reads as none (value_str). An empty one in the asset would too, as on stock's
 * own brightness bar, but stock then looks the image up at every paint. */
void slider_knobless(void *slider) {
    static const char *const knob[] = { "style:normal:icon", "style:pressed:icon",
                                        "style:over:icon", "style:focused:icon",
                                        "style:disable:icon" };
    for (unsigned i = 0; i < sizeof knob / sizeof *knob; ++i)
        widget_set_prop_int(slider, knob[i], 0);
}

/* Scrub (docs/internals.md#scrub-ipod) follows stock's own key seek (0x53e000): the page's 250 ms
 * timer stops, the slider and label_playtime preview the target, player_seek_time commits it once
 * on exit in track seconds (it adds a CUE track's start), and the timer restarts. */
/* The accent's light tone (Minimal's own accent: MINIMAL_FILL) fills the progress bar in every
 * state a touch passes through; a white fill (Minimal: a thicker line) marks the scrub. */
void slider_fill(void *slider, unsigned color) {
    static const char *const fill[] = { "style:normal:fg_color", "style:pressed:fg_color",
                                        "style:over:fg_color", "style:focused:fg_color" };
    for (unsigned i = 0; i < sizeof fill / sizeof *fill; ++i)
        widget_set_prop_int(slider, fill[i], (int)color);
    slider_knobless(slider);
}
void np_fill(int scrub) {
    if (!st.np_slider) return;
    slider_fill(st.np_slider, scrub ? 0xffffffff : RGBA(accent_tone(TONE_LIGHT)));
    /* Minimal's fill is near white already: its scrub thickens the line to Classic's. */
    const theme_t *T = theme();
    if (T->np_scrub != T->np_bar)
        widget_set_prop_int(st.np_slider, "bar_size", scrub ? T->np_scrub : T->np_bar);
    widget_invalidate_force(st.np_slider, (void *)0);
}

/* One part of Classic's bar, w wide at x, y: the stops' rows h high, then mirrored under them from
 * SCRUB_REFLECT_A/256 to the paper over SCRUB_REFLECT_ROWS. */
static void bar_part(void *canvas, const stops_t *s, int x, int y, int w, int h, unsigned paper) {
    paint_stops(canvas, x, y, w, h, s);
    for (int k = 0; k < SCRUB_REFLECT_ROWS; ++k) {
        int a = SCRUB_REFLECT_A * (SCRUB_REFLECT_ROWS - k) / SCRUB_REFLECT_ROWS;
        stock_canvas_set_fill_color(canvas, RGBA(mix(paper, stops_row(s, h - 1 - k, h), a, 256)));
        canvas_fill_rect(canvas, x, y + h + k, w, 1);
    }
}

/* Classic's bar (playing.scrubber.stops), an iPod classic's, bw wide and the theme's np_bar high at
 * x, y: the fill (the theme's own scheme's np_fill, another accent's band) over the first `on`
 * columns, the track's rows after them, a column at each end in the track's bottom line's colour
 * whatever the fill reaches, each mirrored under it. With mark, a scrub's cue on the gloss: a
 * diamond in the ink, NP_SCRUB_MARK each side of its middle row and column, at the fill's end, kept
 * inside the bar. 0 when the theme has none. The canvas's fill colour is left as it was. */
int ipod_bar(void *canvas, int x, int y, int bw, int on, int mark) {
    const theme_t *T = theme();
    const stops_t *track = &T->np_track[ipod_light()];
    if (track->n < 2) return 0;
    unsigned saved = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), paper = scheme()->paper,
             line = track->rgb[track->n - 1];
    const stops_t side = { 2, { 0, 100 }, { line, line } };
    bar_part(canvas, T->np_fill.n && T->schemes[accent()].label ? &T->np_fill : &scheme()->sel, x,
             y, on, T->np_bar, paper);
    bar_part(canvas, track, x + on, y, bw - on, T->np_bar, paper);
    bar_part(canvas, &side, x, y, 1, T->np_bar, paper);
    bar_part(canvas, &side, x + bw - 1, y, 1, T->np_bar, paper);
    stock_canvas_set_fill_color(canvas, RGBA(scheme()->ink));
    int end = bw - 1 - NP_SCRUB_MARK, cx = on < NP_SCRUB_MARK ? NP_SCRUB_MARK : on,
        cy = y + (T->np_bar - 1) / 2;
    if (cx > end) cx = end;
    for (int j = -NP_SCRUB_MARK; mark && j <= NP_SCRUB_MARK; ++j) {
        int half = NP_SCRUB_MARK - (j < 0 ? -j : j);
        canvas_fill_rect(canvas, x + cx - half, cy + j, 2 * half + 1, 1);
    }
    stock_canvas_set_fill_color(canvas, saved);
    return 1;
}

/* Classic's progress bar over stock's plain one, centred in the slider as stock's, the scrub's cue
 * on it while a scrub runs; the times sit beside it (tools/ipod.py np_ipod). */
void paint_scrubber(void *w, void *canvas) {
    if (w && w == st.stream_bar && P(canvas, CANVAS_LCD))
        ipod_bar(canvas, 0, 0, I(w, W_W), I(w, W_W), 0);
    if (!w || w != st.np_slider || !P(canvas, CANVAS_LCD)) return;
    int bw = I(w, W_W), lo = widget_get_prop_int(w, "min", 0),
        n = widget_get_prop_int(w, "max", 100) - lo,
        v = clamp_step(0, n, widget_get_prop_int(w, "value", 0) - lo);
    ipod_bar(canvas, 0, (I(w, W_H) - theme()->np_bar) / 2, bw, n > 0 ? bw * v / n : 0, st.scrub);
}

/* The rows painter's bar (the volume panel, a card app's progress): the track's rows, then the
 * fill's, the theme's own scheme's (Classic Blue) or another accent's band, as Now Playing's bar
 * (ipod_bar). */
void paint_rows_bar(void *canvas, int x, int y, int w, int h, int fill_w) {
    const theme_t *T = theme();
    const unsigned (*rows)[NP_ROWS] = T->np_rows + 2 * ipod_light();
    const stops_t *band = T->schemes[accent()].label ? (const stops_t *)0 : &scheme()->sel;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    for (int j = 0; j < h && j < NP_ROWS; ++j) {
        scrub_row(canvas, rows[0][j], x, y + j, w);
        if (fill_w > 0)
            scrub_row(canvas, band ? 0xff000000u | stops_row(band, j, h) : rows[1][j], x, y + j,
                      fill_w < w ? fill_w : w);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* The Theme's progress bar over the asset's (Minimal's square 4px line): its np_bar, Modern's
 * NP_BAR_CLASSIC capsule or Classic's NP_BAR_IPOD (paint_scrubber draws it), and stock's play-state
 * glyph, which Minimal and Classic hide (playing.state.opacity). */
void np_theme(void) {
    static const char *const radius[] = { "style:normal:round_radius", "style:pressed:round_radius",
                                          "style:over:round_radius", "style:disable:round_radius",
                                          "style:focused:round_radius" };
    void *glyph = st.np_win ? widget_lookup(st.np_win, "img_playstate", 1) : (void *)0;
    const theme_t *T = theme();
    int h = T->np_bar;
    for (unsigned i = 0; st.np_slider && i < sizeof radius / sizeof *radius; ++i)
        widget_set_prop_int(st.np_slider, radius[i], h / 2);
    if (st.np_slider) widget_set_prop_int(st.np_slider, "bar_size", h);
    if (glyph) widget_set_opacity(glyph, T->np_state_op);
    /* Minimal: the album is paint_caps' capitals. */
    if (st.np_album) widget_set_opacity(st.np_album, T->np_album_op);
}
