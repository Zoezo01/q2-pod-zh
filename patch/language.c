/* Languages (docs/internals.md#languages). Q2 Pod is English. A card app whose q2app says lang=1
 * carries translations, lang/<code>.tsv: a first line "# " and the language's own name, then a line
 * a text, three fields tab apart: its key, the English it was written for and the translation
 * (\n, \t and \\ escaped). System settings' Language row shows while such an app is on the card;
 * its page (stock's, its rows replaced) lists English and each table, and a pick copies the table
 * to LANG_COPY and restarts. Every start reads the copy, so the card can go. Stock's keys, and the
 * assets' tr_text, reach it through locale_info_tr (ringnav_locale_tr); Q2 Pod's own English
 * through tr(), its key the English itself. A line counts only for the English it was written
 * for, with the same printf conversions and a text: a relabelled row, a changed sentence or a key
 * the pack hasn't caught up with shows English. */
#include "navigation.h"

#define LANG_COPY PEQ_ROOT "/mnt/data/q2-lang.tsv"
#define LANG_CARD PEQ_ROOT "/mnt/mmc"
#define LANG_BYTES (1u << 20) /* a table's most */
#define LANG_PICKS 24         /* the page's tables */

extern const char *stock_locale_tr_trampoline(void *, const char *);
extern int stock_language_trampoline(widget_t *, void *), ringnav_reboot(void);

typedef struct {
    const char *key, *en, *text;
} lang_line_t;
static struct {
    int loaded;
    unsigned n;
    char *raw;
    lang_line_t *line;
    int picks; /* the page's tables: code, own name, the card file */
    char code[LANG_PICKS][12], name[LANG_PICKS][48], file[LANG_PICKS][96];
} lg;

/* s's \n, \t and \\ undone in place; any other backslash stays. */
static void unescape(char *s) {
    char *o = s;
    for (; *s; ++s)
        if (*s == '\\' && (s[1] == 'n' || s[1] == 't' || s[1] == '\\'))
            ++s, *o++ = *s == 'n' ? '\n' : *s == 't' ? '\t' : '\\';
        else
            *o++ = *s;
    *o = 0;
}

/* The next printf conversion in s ("%%" is none): where it starts, its length in *n; 0 past the
 * last. */
static const char *conversion(const char *s, unsigned *n) {
    for (; *s; ++s) {
        if (*s != '%') continue;
        if (s[1] == '%') {
            ++s;
            continue;
        }
        const char *e = s + 1;
        while (*e && strstr("-+ #0123456789.*hlLqjzt", (char[]){ *e, 0 })) ++e;
        *n = (unsigned)(e - s) + (*e != 0);
        return s;
    }
    return 0;
}

/* Whether a and b hold the same conversions in the same order: a %s where a %d was crashes. */
static int same_conversions(const char *a, const char *b) {
    unsigned m = 0, n = 0;
    for (;;) {
        a = conversion(a, &m), b = conversion(b, &n);
        if (!a || !b) return a == b;
        if (m != n || memcmp(a, b, m)) return 0;
        a += m, b += n;
    }
}

static int by_key(const lang_line_t *a, const lang_line_t *b) {
    int c = strcmp(a->key, b->key);
    return c ? c : strcmp(a->en, b->en);
}
static int by_key_sort(const void *a, const void *b) { return by_key(a, b); }

/* The translation of key written for en, or 0. */
static const char *find(const char *key, const char *en) {
    lang_line_t want = { key, en, 0 };
    unsigned lo = 0, hi = lg.n;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        int c = by_key(&lg.line[mid], &want);
        if (!c) return lg.line[mid].text;
        if (c < 0)
            lo = mid + 1;
        else
            hi = mid;
    }
    return 0;
}

/* The copy read once, on the first text asked for (the UI thread's, as stock builds Home). A line
 * without three fields, a key or a text, or with other conversions than its English, is skipped.
 * ponytail: the whole file stays in RAM, LANG_BYTES at most. */
static void lang_load(void) {
    unsigned n = 0, lines = 1, m = 0;
    lg.loaded = 1;
    char *raw = read_all(LANG_COPY, LANG_BYTES, &n);
    for (unsigned i = 0; raw && i < n; ++i) lines += raw[i] == '\n';
    lang_line_t *line = raw ? calloc(lines, sizeof *line) : 0;
    for (char *p = raw, *end = raw + n; line && p < end;) {
        char *e = p, *f[3] = { p, 0, 0 };
        while (e < end && *e != '\n') ++e;
        *e = 0;
        if (e > p && e[-1] == '\r') e[-1] = 0;
        int k = 1;
        for (char *c = p; *c; ++c)
            if (*c == '\t') {
                *c = 0;
                if (k == 3) break;
                f[k++] = c + 1;
            }
        if (k == 3 && *p != '#') {
            for (int i = 0; i < 3; ++i) unescape(f[i]);
            if (*f[0] && *f[2] && same_conversions(f[1], f[2]))
                line[m++] = (lang_line_t){ f[0], f[1], f[2] };
        }
        p = e + 1;
    }
    if (!m) {
        free(line), free(raw);
        return;
    }
    qsort(line, m, sizeof *line, by_key_sort);
    lg.raw = raw, lg.line = line, lg.n = m;
}

/* Q2 Pod's own English, translated: the same pointer when the language has no line for it. */
const char *tr(const char *en) {
    if (!lg.loaded) lang_load();
    const char *t = en && lg.n ? find(en, en) : 0;
    return t ? t : en;
}

/* locale_info_tr: stock's text for key (its en_US table's, or key itself when the table has
 * none, as an asset's English tr_text), then the copy's line for that key and that English. */
const char *ringnav_locale_tr(void *info, const char *key) {
    const char *en = stock_locale_tr_trampoline(info, key);
    if (!lg.loaded) lang_load();
    const char *t = key && en && lg.n ? find(key, en) : 0;
    return t ? t : en;
}

/* A table name's code: letters and '_', 2 to 11 of them, then ".tsv". */
static int table_code(const char *name, char *code) {
    unsigned k = 0;
    while ((name[k] >= 'a' && name[k] <= 'z') || (name[k] >= 'A' && name[k] <= 'Z') ||
           name[k] == '_')
        ++k;
    if (k < 2 || k > 11 || strcmp(name + k, ".tsv")) return 0;
    memcpy(code, name, k), code[k] = 0;
    return 1;
}

/* Every table of every card app whose q2app says lang=1, into lg's picks, a code once (the first
 * app's); how many. */
static int picks_read(void) {
    char raw[512], path[96];
    lg.picks = 0;
    void *d = opendir(LANG_CARD);
    for (struct dirent *e; d && (e = readdir(d));) {
        if (e->d_name[0] != '.' || !valid_id(e->d_name + 1)) continue;
        tk_snprintf(path, sizeof path, LANG_CARD "/%s/q2app", e->d_name);
        if (!state_read(path, raw, sizeof raw) || number(raw, "lang") != 1) continue;
        tk_snprintf(path, sizeof path, LANG_CARD "/%s/lang", e->d_name);
        void *t = opendir(path);
        for (struct dirent *f; t && (f = readdir(t)) && lg.picks < LANG_PICKS;) {
            int i = lg.picks, dup = 0;
            if (!table_code(f->d_name, lg.code[i])) continue;
            for (int j = 0; j < i; ++j) dup |= !strcmp(lg.code[j], lg.code[i]);
            tk_snprintf(lg.file[i], sizeof lg.file[i], "%s/%s", path, f->d_name);
            char head[sizeof lg.name[i] + 2] = "";
            void *h = dup ? 0 : fopen(lg.file[i], "rb");
            if (!h) continue;
            head[fread(head, 1, sizeof head - 1, h)] = 0;
            fclose(h);
            for (char *c = head; *c; ++c)
                if (*c == '\n' || *c == '\r') *c = 0;
            /* no "# " name: the code */
            tk_snprintf(lg.name[i], sizeof lg.name[i], "%s",
                        !strncmp(head, "# ", 2) && head[2] ? head + 2 : lg.code[i]);
            lg.picks++;
        }
        if (t) closedir(t);
    }
    if (d) closedir(d);
    return lg.picks;
}

/* LANG under Q2POD: the code picked, "" for English, as it is without the copy (the App Store's
 * Remove takes the copy alone: language_removing). */
static void lang_code(char *code, unsigned n) {
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "LANG", code, "");
    code[n - 1] = 0;
    if (access(LANG_COPY, 0)) *code = 0;
}

/* System settings' Language row (navigation.c ringnav_systemset): shown while the card has a
 * table to pick. */
int language_offered(void) { return picks_read() > 0; }

/* file into LANG_COPY whole, through a .new and a rename, so a cut leaves the old copy or none. */
static int copy_in(const char *file) {
    unsigned n = 0;
    char *b = read_all(file, LANG_BYTES, &n);
    void *f = b ? fopen(LANG_COPY ".new", "wb") : 0;
    int ok = f && fwrite(b, 1, n, f) == n;
    if (f && (fflush(f) || fsync(fileno(f)))) ok = 0;
    if (f && fclose(f)) ok = 0;
    free(b);
    if (ok && !rename(LANG_COPY ".new", LANG_COPY)) return 1;
    if (f) unlink(LANG_COPY ".new");
    return 0;
}

static int lang_pick(void *ctx, event_t *event) {
    int i = (int)(long)ctx; /* -1 English */
    char was[12];
    lang_code(was, sizeof was);
    if (i >= lg.picks || !strcmp(was, i < 0 ? "" : lg.code[i])) return 0;
    struct {
        int kind, ms;
        char text[0x400];
    } prompt = { 1, 0, "" };
    tk_snprintf(prompt.text, sizeof prompt.text, "%s", tr("Restart to apply?"));
    if (navigator_to_with_context("dialog/confirminfo_dialog", &prompt) != 10) return 0;
    if (i < 0)
        unlink(LANG_COPY);
    else if (!copy_in(lg.file[i])) {
        toast(tr("Couldn't copy the language"));
        return 0;
    }
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", "LANG", i < 0 ? "" : lg.code[i]);
    ringnav_reboot();
    return 0;
}

/* systemset_language_page_init: stock's list of its own tables gives way to English and the
 * card's, the one picked ticked, as Theme's rows are. */
int ringnav_language(widget_t *win, void *ctx) {
    int result = stock_language_trampoline(win, ctx);
    widget_t *view = win ? widget_lookup(win, "scroll_view_language", 1) : (void *)0;
    if (!view) return result;
    char was[12];
    lang_code(was, sizeof was);
    picks_read();
    widget_destroy_children(view);
    for (int i = -1; i < lg.picks; ++i)
        widget_set_text_utf8(list_row(view,
                                      !strcmp(was, i < 0 ? "" : lg.code[i]) ? "select" : "unselect",
                                      lang_pick, (void *)(long)i),
                             i < 0 ? "English" : lg.name[i]);
    return result;
}

/* The card mounted (system.c's logo thread) or an app installed (netupdate.c's worker): the
 * language picked copied again when its table on the card differs, so a pack's update shows
 * from the next start. A card without it changes nothing: the copy is the Q2's. */
void language_sync(void) {
    char code[12];
    unsigned a = 0, b = 0;
    lang_code(code, sizeof code);
    if (!*code) return;
    picks_read();
    for (int i = 0; i < lg.picks; ++i) {
        if (strcmp(lg.code[i], code)) continue;
        char *card = read_all(lg.file[i], LANG_BYTES, &a),
             *have = read_all(LANG_COPY, LANG_BYTES, &b);
        if (card && (!have || a != b || memcmp(card, have, a))) copy_in(lg.file[i]);
        free(card), free(have);
        return;
    }
}

/* The App Store's Remove of the app in the card folder dir (netupdate.c's worker, before its
 * files go): when its q2app says lang=1, the copy goes, so the next start is English. Only the
 * copy: config.ini is the UI thread's to write, and LANG without a copy reads as English. */
void language_removing(const char *dir) {
    char raw[512], path[96];
    tk_snprintf(path, sizeof path, LANG_CARD "/%s/q2app", dir);
    if (state_read(path, raw, sizeof raw) && number(raw, "lang") == 1) unlink(LANG_COPY);
}
