/* Card themes (docs/internals.md, Themes: Card themes): /Q2Pod/theme.txt, a Q2 Theme Studio file,
 * checked whole and copied to /mnt/data at a card mount (card_theme_sync, the boot logo's thread),
 * read from there once at the start, before the first frame: its accent, page colour, widget rules
 * and Home's pictures (card_rules.c), under a theme that takes a card theme's colours, and its
 * battery style.
 * Nothing applies live and nothing is written to config.ini or the card: the file is the switch. */
#include "navigation.h"

#define CT_SRC "/mnt/mmc/Q2Pod/theme.txt"
#define CT_DST "/mnt/data/q2pod-theme"
#define CT_BAD CT_DST ".bad"   /* a copy that took two starts down: the card's bytes stay out */
#define CT_TRY CT_DST ".try"   /* the starts that never got CT_TRY_MS into the copy, one digit */
#define CT_SEEN CT_DST ".seen" /* the card file last refused: size, mtime (told_once) */
#define CT_MAX 16384
#define CT_TRY_MS 15000
/* accent, bg (the page colour; its half is st.card_page): 1 << 24 | its 0xRRGGBB, 0 none; battery:
 * 1 + st.battery's value, 0 none; try_at: the start's time while CT_TRY counts it; note: a toast,
 * load's or the worker's (power_poll shows load's on its first pass, before logo_poll starts the
 * worker); row: the live scheme row (base) with tones, the accent's, and paper and ink, the
 * page's. */
static struct {
    unsigned accent, bg, try_at, tones[5];
    int battery;
    const char *note;
    const scheme_t *base;
    scheme_t row;
} ct __attribute__((section(".scratch")));

/* text past a UTF-8 BOM when its n bytes (a NUL after them, as read_all's) are a whole card theme:
 * no NUL (read_all keeps it to CT_MAX), the first line "q2theme 1", the last "end" with only empty
 * lines after it (as the Studio's reader has it), so a file cut short by a pulled card is refused
 * whole; else 0. */
static char *whole(char *text, unsigned n) {
    static const char head[] = "q2theme 1";
    unsigned at = sizeof head - 1;
    if (n >= 3 && !memcmp(text, "\xef\xbb\xbf", 3)) text += 3, n -= 3;
    if (strlen(text) != n || n < at || memcmp(text, head, at)) return 0;
    at += text[at] == '\r';
    if (text[at] != '\n') return 0;
    /* empty lines after end: LFs, and CRs each before an LF or the end */
    while (n && (text[n - 1] == '\n' || (text[n - 1] == '\r' && text[n] != '\r'))) --n;
    return n >= 4 && !memcmp(text + n - 4, "\nend", 4) ? text : 0;
}

static int eol(const char *c) { return c[*c == '\r'] == '\n'; } /* an LF, or a CR and its LF */
static int line(const char *q, const char *s) {
    unsigned k = strlen(s);
    return !strncmp(q, s, k) && eol(q + k);
}
/* 1 << 24 | the colour when the line at q is verb (its k bytes) and six hex digits, either case,
 * ending the line; else 0 */
static unsigned colour(const char *q, const char *verb, unsigned k) {
    unsigned rgb = 0, at = k;
    if (strncmp(q, verb, k)) return 0;
    for (int d; (d = hex(q[k])) >= 0; ++k) rgb = rgb << 4 | (unsigned)d;
    return k == at + 6 && eol(q + k) ? 1u << 24 | rgb : 0;
}
/* text (n bytes) into ct when whole: the lines as Q2 Theme Studio writes them, "accent #rrggbb",
 * "color bg #rrggbb" (hex in either case) and "battery icon|percent|both|inside", each ending the
 * line (CRLF too), the last of each, and the widget rules' (card_rule_line); any other line (name,
 * a newer verb, one with a tab, another space or a CR that isn't before its LF) is skipped, as the
 * Studio's reader skips it. 0 when refused. */
static int card_theme_parse(char *text, unsigned n) {
    static const char *const styles[] = { "battery icon", "battery percent", "battery both",
                                          "battery inside" };
    const char *q = whole(text, n);
    ct.accent = ct.bg = ct.battery = st.card_page = 0;
    card_rules_clear();
    if (!q) return 0;
    while (*q) {
        unsigned a = colour(q, "accent #", 8), b = colour(q, "color bg #", 10);
        if (a) ct.accent = a;
        if (b) ct.bg = b;
        for (int m = 0; m < 4; ++m)
            if (line(q, styles[m])) ct.battery = m + 1;
        card_rule_line(q);
        while (*q && *q++ != '\n') {}
    }
    return 1;
}
/* What of the file doesn't apply: its colours and rules (a theme without the flag; the battery
 * style stays), or all of it (all, a start the guard runs without it). */
static void drop(int all) {
    ct.accent = ct.bg = 0;
    if (all) ct.battery = 0;
    card_rules_clear();
}

/* c mixed 1/16 a step toward to (black or white) until its luminance, in 1/10000 of 255^2 (WCAG's
 * relative luminance, each channel's sRGB curve taken as 0.8 x^2 + 0.2 x^3, within 0.013), is at
 * most limit (toward black) or at least it (toward white). */
static unsigned readable(unsigned c, unsigned to, unsigned limit) {
    for (;;) {
        unsigned lum = 0;
        for (int k = 0; k < 3; ++k) {
            unsigned v = c >> (16 - 8 * k) & 255;
            lum += (const unsigned short[]){ 2126, 7152, 722 }[k] * (v * v * (1020 + v) / 1275);
        }
        if (to ? lum >= limit * 255 * 255 : lum <= limit * 255 * 255) return c;
        c = mix(c, to, 1, 16);
    }
}
/* settings_read, once, before the first frame: the /mnt/data copy (the card may mount later).
 * CT_TRY counts the starts that never got through the copy's first seconds; at two the copy is set
 * aside and this start runs without it. A start that can't keep count runs without it too. Then the
 * accent, darkened until white text on it reaches 3:1 (luminance at most 0.3, 0.29 for the
 * margin), and its tones by Q2 Theme Studio's rule; and the page: light at the Studio's luma of
 * 125, and deepened (a dark page) or paled (a light one) until the half's quiet line (flat_quiet)
 * reads 3:1 on it (luminance at most 0.0719 or at least 0.5711; 0.07 and 0.583 for the margin). */
void card_theme_load(void) {
    unsigned n = 0;
    char *b = read_all(CT_DST, CT_MAX, &n);
    if (b) card_theme_parse(b, n);
    free(b);
    if (!themes[st.theme].card_colors) drop(0); /* Classic: only a battery style */
    if (!(ct.accent | ct.bg | ct.battery | card_rules_used())) return;
    char raw[4];
    char starts = state_read(CT_TRY, raw, sizeof raw) ? raw[1] : '0';
    if (starts >= '2') {
        unlink(CT_TRY);
        rename(CT_DST, CT_BAD);
        ct.note = N_("Card theme turned off after a failed start");
        drop(1);
        return;
    }
    ++starts;
    if (!blob_io(CT_TRY, CT_TRY ".tmp", &starts, 1, 1)) {
        unlink(CT_TRY ".tmp");
        drop(1);
        return;
    }
    ct.try_at = time_now_ms() | 1;
    if (ct.accent) {
        unsigned c = readable(ct.accent & 0xffffff, 0, 2900);
        ct.tones[0] = ct.tones[1] = c;
        ct.tones[2] = ct.tones[4] = mix(c, 0xffffff, 12, 100);
        ct.tones[3] = neutral(ct.tones[2]) ? 0xd8d8d8 : ct.tones[2]; /* Graphite's silver */
    }
    if (ct.bg) {
        unsigned c = ct.bg & 0xffffff;
        int light = ((c >> 16) * 299 + (c >> 8 & 255) * 587 + (c & 255) * 114) / 1000 >= 125;
        st.card_page = 1 + light;
        ct.bg = 1u << 24 | (light ? readable(c, 0xffffff, 5830) : readable(c, 0, 700));
    }
    if (ct.battery) st.battery = ct.battery - 1;
}

/* s, a row of the live theme, or, while the card theme sets the accent or the page (load keeps them
 * only under a theme that takes a card theme's colours, and a Theme pick across that restarts,
 * which the build checks), a copy of it: with an accent, its tones and no flags (not Ivory's card,
 * not Crimson's reds); with a page, its colour as the paper and its ink (LIGHT_INK on a light page,
 * white on a dark one); made again when s changes (a Theme pick between Minimal and Modern,
 * another accent).
 * ponytail: one copy, which holds because a theme with the flag has one array of rows for both
 * halves; the build refuses the flag on a theme with two. */
const scheme_t *card_theme_row(const scheme_t *s) {
    if (!(ct.accent | ct.bg)) return s;
    if (ct.base != s) {
        ct.row = *s;
        if (ct.accent) ct.row.tones = ct.tones, ct.row.flags = 0;
        if (ct.bg)
            ct.row.paper = ct.bg & 0xffffff,
            ct.row.ink = st.card_page > 1 ? LIGHT_INK * 0x010101u : 0xffffff;
        ct.base = s;
    }
    return &ct.row;
}

/* Whether the card theme sets Display row i: Accent and Light Mode while load kept an accent or a
 * page (only under a theme that takes its colours), Battery in every theme. Its value is
 * settings_read's: st.battery holds the style, BATTERY untouched. */
int card_theme_sets(int i) {
    return i == SETTING_ACCENT  ? ct.accent != 0
           : i == SETTING_LIGHT ? ct.bg != 0
                                : i == SETTING_BATTERY && ct.battery;
}

/* The count off: the copy has run CT_TRY_MS, or the run ends cleanly (ringnav_clean_end:
 * power-off, Restart, a firmware update, Rockbox), so those are no failed start. */
void card_theme_ran(void) {
    if (ct.try_at) unlink(CT_TRY), ct.try_at = 0;
}
/* power_poll: the count off once due, and a note. */
void card_theme_poll(void) {
    if (ct.try_at && time_now_ms() - ct.try_at >= CT_TRY_MS) card_theme_ran();
    const char *said = ct.note;
    if (said) ct.note = 0, toast(tr(said)); /* first: a toast's dialog polls here again */
}

/* 1 when path holds these n bytes. */
static int same(const char *path, const char *b, unsigned n) {
    unsigned m = 0;
    char *old = read_all(path, CT_MAX, &m);
    int is = old && m == n && !memcmp(old, b, n);
    free(old);
    return is;
}
/* logo_sync's thread at a card mount: the card's file copied whole to CT_DST, unless the copy or
 * the set-aside one holds the same bytes; no file, and the copy and its marks go. A refused file
 * keeps the copy there was and is told once (CT_SEEN). Never a write to the card. */
void card_theme_sync(void) {
    const char *note = 0;
    if (access(CT_SRC, 0)) {
        if (!access(CT_DST, 0) || !access(CT_BAD, 0))
            note = N_("Card theme removed. Restart to finish");
        unlink(CT_DST), unlink(CT_BAD), unlink(CT_TRY), unlink(CT_SEEN);
    } else {
        unsigned n = 0;
        char *b = read_all(CT_SRC, CT_MAX, &n);
        if (!b || !whole(b, n)) {
            note = told_once(CT_SRC, CT_SEEN, CT_SEEN ".tmp") ? 0
                                                              : N_("Card theme file can't be used");
        } else {
            unlink(CT_SEEN);
            /* the same bytes are not written again each mount */
            if (!same(CT_DST, b, n) && !same(CT_BAD, b, n)) {
                if (blob_io(CT_DST, CT_DST ".tmp", b, n, 1))
                    unlink(CT_BAD), unlink(CT_TRY), note = N_("Card theme saved. Restart to apply");
                else
                    unlink(CT_DST ".tmp"), note = N_("Could not save card theme");
            }
        }
        free(b);
    }
    if (note) ct.note = note;
}
