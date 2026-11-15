/* Retain a theme's bold intent when its selected face falls back to stock's full Unicode font.
 * Shared by Cover Flow captions, its track panel, themed rows and Now Playing titles. */
#include "navigation.h"

#define WEIGHT_PROP "_q2_bold_fallback"

void font_weight_mark(void *w, int bold) {
    bold = bold != 0;
    if (w && widget_get_prop_int(w, WEIGHT_PROP, 0) != bold)
        widget_set_prop_int(w, WEIGHT_PROP, bold);
}

/* The existing border hook runs after native self painting. Repeat only marked labels' own
 * foreground one pixel right, keeping their native shaping, marquee, clip and style. Calling
 * the vtable self painter does not dispatch paint events or re-enter border painting. */
void font_weight_paint(void *w, void *canvas) {
    if (!w || !canvas || !widget_get_prop_int(w, WEIGHT_PROP, 0)) return;
    const unsigned *text = widget_get_text(w);
    if (!text || !*text) return;
    canvas_translate(canvas, 1, 0);
    widget_vtable_on_paint_self(w, canvas);
    canvas_translate(canvas, -1, 0); /* restore even when the native painter returns an error */
}

/* Vietnamese vowels in NFC, indexed by shape and then tone (none, grave,
 * acute, tilde, hook above, dot below). The source filename or tag stays untouched. */
static const struct {
    unsigned short base, shape, vowel[6];
} vietnamese_vowels[] = {
    { 'a', 0x306, { 0x0103, 0x1eb1, 0x1eaf, 0x1eb5, 0x1eb3, 0x1eb7 } },
    { 'a', 0x302, { 0x00e2, 0x1ea7, 0x1ea5, 0x1eab, 0x1ea9, 0x1ead } },
    { 'e', 0x302, { 0x00ea, 0x1ec1, 0x1ebf, 0x1ec5, 0x1ec3, 0x1ec7 } },
    { 'o', 0x302, { 0x00f4, 0x1ed3, 0x1ed1, 0x1ed7, 0x1ed5, 0x1ed9 } },
    { 'o', 0x31b, { 0x01a1, 0x1edd, 0x1edb, 0x1ee1, 0x1edf, 0x1ee3 } },
    { 'u', 0x31b, { 0x01b0, 0x1eeb, 0x1ee9, 0x1eef, 0x1eed, 0x1ef1 } },
    { 'A', 0x306, { 0x0102, 0x1eb0, 0x1eae, 0x1eb4, 0x1eb2, 0x1eb6 } },
    { 'A', 0x302, { 0x00c2, 0x1ea6, 0x1ea4, 0x1eaa, 0x1ea8, 0x1eac } },
    { 'E', 0x302, { 0x00ca, 0x1ec0, 0x1ebe, 0x1ec4, 0x1ec2, 0x1ec6 } },
    { 'O', 0x302, { 0x00d4, 0x1ed2, 0x1ed0, 0x1ed6, 0x1ed4, 0x1ed8 } },
    { 'O', 0x31b, { 0x01a0, 0x1edc, 0x1eda, 0x1ee0, 0x1ede, 0x1ee2 } },
    { 'U', 0x31b, { 0x01af, 0x1eea, 0x1ee8, 0x1eee, 0x1eec, 0x1ef0 } },
    { 'a', 0, { 0x0061, 0x00e0, 0x00e1, 0x00e3, 0x1ea3, 0x1ea1 } },
    { 'e', 0, { 0x0065, 0x00e8, 0x00e9, 0x1ebd, 0x1ebb, 0x1eb9 } },
    { 'i', 0, { 0x0069, 0x00ec, 0x00ed, 0x0129, 0x1ec9, 0x1ecb } },
    { 'o', 0, { 0x006f, 0x00f2, 0x00f3, 0x00f5, 0x1ecf, 0x1ecd } },
    { 'u', 0, { 0x0075, 0x00f9, 0x00fa, 0x0169, 0x1ee7, 0x1ee5 } },
    { 'y', 0, { 0x0079, 0x1ef3, 0x00fd, 0x1ef9, 0x1ef7, 0x1ef5 } },
    { 'A', 0, { 0x0041, 0x00c0, 0x00c1, 0x00c3, 0x1ea2, 0x1ea0 } },
    { 'E', 0, { 0x0045, 0x00c8, 0x00c9, 0x1ebc, 0x1eba, 0x1eb8 } },
    { 'I', 0, { 0x0049, 0x00cc, 0x00cd, 0x0128, 0x1ec8, 0x1eca } },
    { 'O', 0, { 0x004f, 0x00d2, 0x00d3, 0x00d5, 0x1ece, 0x1ecc } },
    { 'U', 0, { 0x0055, 0x00d9, 0x00da, 0x0168, 0x1ee6, 0x1ee4 } },
    { 'Y', 0, { 0x0059, 0x1ef2, 0x00dd, 0x1ef8, 0x1ef6, 0x1ef4 } },
};

const unsigned *normalize_vietnamese_label(void *label, const unsigned *text) {
    if (!text) return text;
    /* Folder view and local artist tags fit within 511 UTF-8 bytes. */
    unsigned display[512], n = 0;
    int changed = 0;
    static const unsigned short tones[] = { 0, 0x300, 0x301, 0x303, 0x309, 0x323 };
    const unsigned *s = text;
    while (*s && n < sizeof display / sizeof *display - 1) {
        unsigned c = *s++;
        if (*s >= 0x300 && *s <= 0x323) {
            for (unsigned i = 0; i < sizeof vietnamese_vowels / sizeof *vietnamese_vowels; ++i) {
                unsigned base = vietnamese_vowels[i].base, shape = vietnamese_vowels[i].shape;
                const unsigned short *vowel = vietnamese_vowels[i].vowel;
                if (c != base && c != vowel[0]) continue;
                const unsigned *end = s;
                if (shape && c == base && *end == shape) ++end;
                unsigned tone = 1;
                while (tone < sizeof tones / sizeof *tones && *end != tones[tone]) ++tone;
                if (tone < sizeof tones / sizeof *tones) {
                    ++end;
                    if (shape && c == base && end == s + 1) {
                        if (*end != shape) continue;
                        ++end;
                    }
                    c = vowel[tone];
                } else if (end > s)
                    c = vowel[0];
                if (end != s) {
                    s = end, changed = 1;
                    break;
                }
            }
        }
        display[n++] = c;
    }
    if (!changed || *s) return text;
    display[n] = 0;
    widget_set_text(label, display);
    return widget_get_text(label);
}
