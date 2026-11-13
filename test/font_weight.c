/* Production weight helper with a portable native-widget/paint boundary. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define Q2_NAVIGATION_H
typedef struct {
    int weight, writes;
    const unsigned *text;
} label_t;
static unsigned mixed[] = { 0x65e5, 0x672c, 0x8a9e, ' ', 'T', 'i', 't', 'l', 'e', 0 };
static int ox = 7, oy = 9, calls;
static void *last_label;
static int widget_get_prop_int(void *w, const char *key, int def) {
    assert(!strcmp(key, "_q2_bold_fallback"));
    return ((label_t *)w)->weight;
}
static int widget_set_prop_int(void *w, const char *key, int v) {
    assert(!strcmp(key, "_q2_bold_fallback"));
    ((label_t *)w)->weight = v;
    ++((label_t *)w)->writes;
    return 0;
}
static const unsigned *widget_get_text(void *w) { return ((label_t *)w)->text; }
static void canvas_translate(void *canvas, int x, int y) {
    ox += x;
    oy += y;
}
static int widget_vtable_on_paint_self(void *w, void *canvas) {
    assert(canvas && ox == 8 && oy == 9);
    ++calls;
    last_label = w;
    return 16; /* failure must still restore origin */
}
#include "../patch/font_weight.c"
static void paint(label_t *w, int extra) {
    int before = calls;
    font_weight_paint(w, &calls);
    assert(calls - before == extra && ox == 7 && oy == 9);
    if (extra) assert(last_label == w);
}
int main(void) {
    label_t title = { 0, 0, mixed }, row = { 0, 0, mixed }, meta = { 0, 0, mixed };
    paint(&title, 0);
    font_weight_mark(&title, 1);
    paint(&title, 1);
    assert(title.writes == 1);
    font_weight_mark(&title, 1);
    assert(title.writes == 1);
    font_weight_mark(&row, 1);
    paint(&row, 1);
    paint(&meta, 0);
    font_weight_mark(&title, 0);
    paint(&title, 0);
    assert(title.writes == 2);
    font_weight_mark(&title, 1);
    title.text = NULL;
    paint(&title, 0);
    unsigned empty[] = { 0 };
    title.text = empty;
    paint(&title, 0);
    title.text = mixed;
    paint(&title, 1);
    font_weight_mark(NULL, 1);
    font_weight_paint(NULL, &calls);
    font_weight_paint(&title, NULL);
    assert(ox == 7 && oy == 9);
    puts("Shared fallback weight: marked title/row repaint, ordinary text excluded, cached marks "
         "write once, recycled label clears, empty text skipped, native error restores origin: "
         "passed");
}
