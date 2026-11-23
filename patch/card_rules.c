/* A card theme's widget rules (docs/internals.md, Themes: Card themes): a Q2 Theme Studio file's
 * page, hide, move, size, font and text lines, read in card_theme_parse's pass as the Studio's
 * reader reads them, card_widgets.inc standing in for its catalogue; applied once, as Now Playing
 * (its iPod layout) and the lock screen open, and at each layout of Home, under a theme that takes
 * a card theme's look (card_theme_load drops them under any other). And its homeart line, Home's
 * pictures in Split. */
#include "navigation.h"

enum { CR_HIDE = 1, CR_MOVE = 2, CR_SIZE = 4, CR_FONT = 8, CR_SQUARE = 16, CR_TEXT = 32 };
#define CARD_WIDGET(page, name, acts, font, x0, y0, x1, y1)                                        \
    { name, page, acts, font, { x0, y0, x1, y1 } },
static const struct {
    const char *name;
    unsigned char page, acts, font;
    short box[4];
} rows[] = {
#include "card_widgets.inc"
};
#define ROWS (sizeof rows / sizeof *rows)
#define ART 0 /* rows[ART], the art: its glyph and spinner follow it */
static const char *const pages[] = { "playing_page", "saverscreen_page", "home_page" }; /* CARD_* */
/* rows[HOME] on: Home's main list, in HOME_ROWS' order */
#define HOME (ROWS - 7)
/* a homeart picture, its number at sizeof - 7 */
#define ART_URL "file://" PEQ_ROOT "/mnt/mmc/Q2Pod/images/art00.jpg"

/* Each row's rules, the last line of each kind winning: flags its hide, move, size and words
 * (CR_*), font its px (0: none), box a move's x and y and a size's w and h, words a Home row's
 * (rows[HOME + i]); page: the parse's, its CARD_* + 1, 0 none. homeart's: secs a turn, art its
 * JPEGs' numbers (1 on), arts how many; and its turn: slot the one checked + 1, shown its
 * picture's number (0: refused), url its name. All zero: no rules. */
static struct {
    unsigned char flags[ROWS], font[ROWS], page, arts, art[24], shown;
    short box[ROWS][4], secs;
    char words[ROWS - HOME][41], url[sizeof ART_URL];
    unsigned slot;
} cr __attribute__((section(".scratch")));
int card_home __attribute__((section(".scratch")));

/* v held to lo..hi, lo when hi is less */
static int fit(int v, int lo, int hi) {
    v = v > hi ? hi : v;
    return v < lo ? lo : v;
}
static int is(const char *w, unsigned n, const char *s) { return !strncmp(w, s, n) && !s[n]; }
/* w's n bytes as -?[0-9]+, saturated at 30000 either way, into *v: 0 when they aren't one */
static int whole(const char *w, unsigned n, int *v) {
    unsigned i = *w == '-', a = 0;
    if (i == n) return 0;
    for (; i < n; ++i) {
        if (w[i] < '0' || w[i] > '9') return 0;
        if (a < 30000) a = a * 10 + (unsigned)(w[i] - '0');
    }
    a = a < 30000 ? a : 30000;
    *v = *w == '-' ? -(int)a : (int)a;
    return 1;
}

/* n bytes at s into to (41 bytes), a row's words: 1 only when they are as the Studio's cleanText
 * leaves words, which is how it writes them: valid UTF-8, none of the controls it drops (C0, DEL,
 * C1, U+2028 and U+2029) or the other spaces it makes one ' ' (JS's \s), no space at either end or
 * beside another, at most 24 characters and 40 bytes. Else 0: Q2 Pod doesn't clean them. */
static int clean(char *to, const char *s, unsigned n) {
    /* what cleanText drops or makes a space, but the space */
    static const unsigned short out[][2] = { { 0, 0x1f },        { 0x7f, 0xa0 },
                                             { 0x1680, 0x1680 }, { 0x2000, 0x200a },
                                             { 0x2028, 0x2029 }, { 0x202f, 0x202f },
                                             { 0x205f, 0x205f }, { 0x3000, 0x3000 },
                                             { 0xfeff, 0xfeff } };
    unsigned chars = 0;
    if (!n || n > 40 || *s == ' ' || s[n - 1] == ' ') return 0;
    memcpy(to, s, n), to[n] = 0;
    if (!utf8_text(to)) return 0;
    for (const unsigned char *p = (const unsigned char *)to; *p; ++chars) {
        unsigned c = *p++, k = (c >= 0xc0) + (c >= 0xe0) + (c >= 0xf0), v = k ? c & (0x3f >> k) : c;
        while (k--) v = v << 6 | (*p++ & 63);
        if (v == ' ' && *p == ' ') return 0;
        for (unsigned j = 0; j < sizeof out / sizeof *out; ++j)
            if (v >= out[j][0] && v <= out[j][1]) return 0;
    }
    return chars <= 24;
}

/* card_theme_parse, each line at q: page NAME, hide W, move W X Y, size W W H, font W PX and text W
 * WORDS, as the Studio's reader takes them: words at each single space (its split(' ')) to the
 * line's end (an LF, or a CR and its LF), each verb its exact count of words (text at least
 * three), W a row of the page the last page line named that takes the verb, X, Y, W, H and PX
 * whole numbers; anything else is skipped. A page line out of shape names no page, so the lines
 * under it are skipped too. A size is held to the row's box (the art square, 40 up to its box's
 * smaller side; text 12 up to the box's sides); a font to 8..120, 0 or the row's own size clearing
 * it; a move is kept as written, held to the box as it applies; WORDS, the rest of the line, are
 * the row's when clean, else it has none. And homeart SECONDS KINDS, on any page: SECONDS a whole
 * number held to 3..600, KINDS 1 to 24 of j (a JPEG) and g (a GIF, never read), the turns going
 * over the j letters, each keeping its number among them all. */
void card_rule_line(const char *q) {
    static const struct {
        char verb[5];
        unsigned char words, act;
    } verbs[] = { { "hide", 2, CR_HIDE },
                  { "move", 4, CR_MOVE },
                  { "size", 4, CR_SIZE },
                  { "font", 3, CR_FONT },
                  { "text", 3, CR_TEXT } };
    const char *w[4] = { 0 };
    unsigned n[4] = { 0 }, k = 0;
    for (const char *p = q;; ++p) {
        int end = !*p || *p == '\n' || (*p == '\r' && p[1] == '\n');
        if (*p != ' ' && !end) continue;
        if (k < 4) w[k] = q, n[k] = (unsigned)(p - q);
        ++k, q = p + 1;
        if (end) break;
    }
    if (is(w[0], n[0], "page")) {
        cr.page = 0;
        for (unsigned p = 0; k == 2 && p < sizeof pages / sizeof *pages; ++p)
            if (is(w[1], n[1], pages[p])) cr.page = (unsigned char)(p + 1);
        return;
    }
    int v[2];
    if (k == 3 && is(w[0], n[0], "homeart")) {
        unsigned i = 0;
        while (i < n[2] && (w[2][i] == 'j' || w[2][i] == 'g')) ++i;
        if (!i || i < n[2] || i > 24 || !whole(w[1], n[1], v)) return;
        cr.secs = (short)fit(v[0], 3, 600), cr.arts = 0;
        for (unsigned j = 0; j < i; ++j)
            if (w[2][j] == 'j') cr.art[cr.arts++] = (unsigned char)(j + 1);
        return;
    }
    for (unsigned j = 0; j < sizeof verbs / sizeof *verbs; ++j) {
        unsigned act = verbs[j].act, i = 0;
        if ((act == CR_TEXT ? k < 3 : k != verbs[j].words) || !is(w[0], n[0], verbs[j].verb))
            continue;
        while (i < ROWS && (rows[i].page + 1 != cr.page || !(rows[i].acts & act) ||
                            !is(w[1], n[1], rows[i].name)))
            ++i;
        if (i == ROWS) return;
        if (act == CR_TEXT) { /* WORDS: from w[2] to the line's end, q - 1 */
            cr.flags[i] &= (unsigned char)~CR_TEXT;
            if (clean(cr.words[i - HOME], w[2], (unsigned)(q - 1 - w[2]))) cr.flags[i] |= CR_TEXT;
            return;
        }
        if ((k > 2 && !whole(w[2], n[2], v)) || (k > 3 && !whole(w[3], n[3], v + 1))) return;
        if (act == CR_FONT) {
            int px = v[0] ? fit(v[0], 8, 120) : 0;
            cr.font[i] = (unsigned char)(px == rows[i].font ? 0 : px);
            return;
        }
        if (act == CR_SIZE) {
            const short *b = rows[i].box;
            int bw = b[2] - b[0], bh = b[3] - b[1], least = rows[i].acts & CR_FONT ? 12 : 40;
            if (rows[i].acts & CR_SQUARE)
                v[0] = v[1] = v[0] < v[1] ? v[0] : v[1], bw = bh = bw < bh ? bw : bh;
            v[0] = fit(v[0], least, bw), v[1] = fit(v[1], least, bh);
        }
        if (act != CR_HIDE) {
            short *at = cr.box[i] + (act == CR_SIZE ? 2 : 0);
            at[0] = (short)v[0], at[1] = (short)v[1];
        }
        cr.flags[i] |= (unsigned char)act;
        return;
    }
}

/* card_theme_parse's start, and card_theme_load wherever the file's rules don't apply: none. */
void card_rules_clear(void) {
    memset(&cr, 0, sizeof cr);
    st.card_rules = card_home = 0;
}
/* card_theme_load, the file applying: the pages that hold a rule, a bit per CARD_*, into
 * st.card_rules, which the hooks read first, and Home's into card_home, which coverflow.c reads;
 * both, 0 for none. */
int card_rules_used(void) {
    st.card_rules = 0;
    for (unsigned i = 0; i < ROWS; ++i)
        if (cr.flags[i] | cr.font[i]) st.card_rules |= 1 << rows[i].page;
    card_home = (st.card_rules >> CARD_HOME & 1) | (cr.arts ? CARD_HOME_ART : 0); /* CARD_HOME_* */
    return st.card_rules | card_home;
}

static void hide(widget_t *w) {
    if (!w) return;
    widget_set_visible(w, 0, 0);
    widget_set_opacity(w, 0);   /* unpainted (widget_paint) when stock shows it again */
    widget_set_sensitive(w, 0); /* a hidden widget keeps its taps */
}
/* px (0: none) as label w's text size, held to its height: on every state (the labels are
 * disabled) and in _card_px for paint_caps */
static void font(widget_t *w, int px) {
    static const char *const sizes[] = { "style:normal:font_size", "style:focused:font_size",
                                         "style:disable:font_size", "_card_px" };
    int h = I(w, W_H);
    for (unsigned s = 0; px && s < sizeof sizes / sizeof *sizes; ++s)
        widget_set_prop_int(w, sizes[s], px < h ? px : h);
}
/* w centred on art's box, dx, dy across from art's parent to w's */
static void centre(widget_t *w, widget_t *art, int dx, int dy) {
    if (w)
        widget_move_resize(w, dx + I(art, W_X) + (I(art, W_W) - I(w, W_W)) / 2,
                           dy + I(art, W_Y) + (I(art, W_H) - I(w, W_H)) / 2, I(w, W_W), I(w, W_H));
}
/* ringnav_playing, before rate_bind (so the heart's rating badge goes where the heart is), and
 * lockscreen_init, after big_clock: page's rules on its window, once; Now Playing's only in its
 * iPod layout (Centred's stock page has the same names). A size is the rule's; a move, or the place
 * a size alone leaves, is held to the row's box, its parent's laid-out size (the art's parent is
 * still 100x100 here, before the first layout); a font is held to the label's height, on every
 * state (the labels are disabled) and in _card_px for paint_caps; a hide is unseen and untouchable.
 * The art's glyph (its tap) and spinner follow it: centred on it again, the spinner a child of the
 * window, and the glyph hidden with it. */
void card_rules_apply(widget_t *win, int page) {
    if (!win || !(st.card_rules >> page & 1) || (page == CARD_PLAYING && np_layout() != NP_IPOD))
        return;
    for (unsigned i = 0; i < ROWS; ++i) {
        unsigned f = cr.flags[i];
        int px = cr.font[i];
        widget_t *w =
            (f || px) && rows[i].page == page ? widget_lookup(win, rows[i].name, 1) : (void *)0;
        if (!w) continue;
        const short *b = rows[i].box, *r = cr.box[i];
        int ww = f & CR_SIZE ? r[2] : I(w, W_W), h = f & CR_SIZE ? r[3] : I(w, W_H);
        if (f & (CR_MOVE | CR_SIZE))
            widget_move_resize(w, fit(f & CR_MOVE ? r[0] : I(w, W_X), b[0], b[2] - ww),
                               fit(f & CR_MOVE ? r[1] : I(w, W_Y), b[1], b[3] - h), ww, h);
        font(w, px);
        if (f & CR_HIDE) hide(w);
        if (i != ART) continue;
        widget_t *glyph = widget_lookup(win, "img_playstate", 1);
        int dx = 0, dy = 0;
        for (widget_t *p = P(w, W_PARENT); p && p != win; p = P(p, W_PARENT))
            dx += I(p, W_X), dy += I(p, W_Y);
        if (f & (CR_MOVE | CR_SIZE))
            centre(glyph, w, 0, 0), centre(widget_lookup(win, "image_wait", 1), w, dx, dy);
        if (f & CR_HIDE) hide(glyph);
    }
}

/* coverflow_home_layout, while a Home row has a rule (card_home), after its own hides and the
 * Shortcut row's label and before home_width lays the labels out for their words: list's rows in
 * HOME_ROWS' order, then each hidden one last (Folder, the Shortcut and Streaming hidden there, or
 * by a rule), so the order holds across layouts; a rule's words and font on a row's label (its
 * first child), and its hide on the row. */
void card_rules_home(widget_t *list) {
    widget_t *row[ROWS - HOME];
    for (unsigned i = 0; i < ROWS - HOME; ++i) {
        unsigned f = cr.flags[HOME + i];
        row[i] = widget_lookup(list, rows[HOME + i].name, 1); /* not the Carousel's cards */
        widget_t *label = row[i] ? widget_get_child(row[i], 0) : (void *)0;
        if (!label) continue;
        widget_restack(row[i], i);
        if (f & CR_TEXT) widget_set_text_utf8(label, cr.words[i]);
        font(label, cr.font[HOME + i]);
        if (f & CR_HIDE) hide(row[i]);
    }
    for (unsigned i = 0; i < ROWS - HOME; ++i)
        if (row[i] && !widget_get_visible(row[i])) widget_restack(row[i], ROWS - HOME - 1);
}

/* paint_caps, for Minimal's capitals over label w (Now Playing's album, Home's labels): the px a
 * font rule gave it, 0 when a rule hid it, else def. */
int card_rule_px(widget_t *w, int def) {
    if (!st.card_rules) return def;
    return widget_get_visible(w) ? widget_get_prop_int(w, "_card_px", def) : 0;
}
/* np_theme, as Now Playing opens and at a Theme change: 1 while a rule hides the open page's art,
 * so its glyph stays clear; not on Centred's page, whose art shows. */
int card_rules_art_hidden(void) {
    return cr.flags[ART] & CR_HIDE && st.np_cover && !widget_get_visible(st.np_cover);
}

/* coverflow_home_art, on each paint of Home in Split while homeart names a JPEG (card_home): this
 * turn's picture in *cover and its own key, or key while the turn's is refused. A turn is secs of
 * the clock (time_now_ms), and the turns go round the JPEGs. As a turn starts its picture is
 * checked, once: a JPEG frame header with each side at most 1024 (picture_size) and at most 1 MB,
 * so nothing else reaches the decoder; and the last turn's, when another or none shows now, is
 * unloaded by name, so one picture is held. */
unsigned card_rules_art(const char **cover, unsigned key) {
    unsigned slot = time_now_ms() / 1000 / (unsigned)cr.secs, item = cr.art[slot % cr.arts];
    if (slot + 1 != cr.slot) {
        char url[sizeof ART_URL] = ART_URL;
        int w = 0, h = 0;
        url[sizeof url - 7] = (char)('0' + item / 10);
        url[sizeof url - 6] = (char)('0' + item % 10);
        if (!(picture_size(url + 7, &w, &h) >> 4 == 0xc && w <= 1024 && h <= 1024 &&
              (unsigned long)file_size(url + 7) <= 1 << 20))
            item = 0;
        if (cr.shown && cr.shown != item)
            image_manager_unload_bitmap_by_name(image_manager(), cr.url);
        memcpy(cr.url, url, sizeof url);
        cr.slot = slot + 1, cr.shown = (unsigned char)item;
    }
    if (!cr.shown) return key;
    *cover = cr.url;
    return 1u << 31 | cr.shown;
}
