/* Languages (docs/internals.md#languages). Q2 Pod is English. A card app whose q2app says lang=1
 * carries translations, lang/<code>.tsv: a first line "# " and the language's own name, then a line
 * a text, three fields tab apart: its key, the English it was written for and the translation
 * (\n, \t and \\ escaped). System settings' Language row shows while such an app is on the card;
 * its page (stock's, its rows replaced) lists English and each table, and a pick copies the table
 * to /mnt/data, names it in config.ini and restarts. Every start reads the copy, so the card can
 * go. Stock's keys, and the
 * assets' tr_text, reach it through locale_info_tr (ringnav_locale_tr); Q2 Pod's own English
 * through tr(), its key the English itself. A line counts only for the English it was written
 * for, with the same printf conversions and a text: a relabelled row, a changed sentence or a key
 * the pack hasn't caught up with shows English. */
#include "navigation.h"

#define LANG_DATA PEQ_ROOT "/mnt/data" /* the copy, q2-lang-<code>.tsv */
#define LANG_CARD PEQ_ROOT "/mnt/mmc"
#define LANG_BYTES (1u << 20)              /* a table's most */
#define LANG_FONT LANG_DATA "/q2-font.ttf" /* script.c's GLYPH_FONT, a pack's lang/font.ttf */
#define LANG_FONT_BYTES (4u << 20)
#define LANG_PICKS 48 /* the page's tables */

extern const char *stock_locale_tr_trampoline(void *, const char *);
extern int stock_language_trampoline(widget_t *, void *), ringnav_reboot(void);
static int active(char *code, char *copy, unsigned n);

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

static int in(const char *set, char c) {
    for (; *set; ++set)
        if (*set == c) return 1;
    return 0;
}
static int digit(char c) { return (c >= '0' && c <= '9') || c == '*'; }

/* The next printf conversion in s, as tools/langpack.py's CONVERSION: flags, width, precision,
 * length, then a conversion's letter; "%%" and a '%' with no letter are none. Where it starts, its
 * length in *n; 0 past the last. */
static const char *conversion(const char *s, unsigned *n) {
    for (; *s; ++s) {
        if (*s != '%') continue;
        if (s[1] == '%') {
            ++s;
            continue;
        }
        const char *e = s + 1;
        while (*e && in("-+ #0", *e)) ++e;
        while (digit(*e)) ++e;
        if (*e == '.' && digit(e[1]))
            for (++e; digit(*e); ++e) {}
        if ((*e == 'h' || *e == 'l') && e[1] == *e)
            e += 2;
        else if (*e && in("hlLqjzt", *e))
            ++e;
        if (*e && in("diouxXeEfFgGaAcspn", *e)) {
            *n = (unsigned)(e + 1 - s);
            return s;
        }
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
    char code[256], copy[96];
    lg.loaded = 1;
    char *raw = active(code, copy, sizeof copy) ? read_all(copy, LANG_BYTES, &n) : 0;
    if (!raw) return;
    for (unsigned i = 0; i < n; ++i) lines += raw[i] == '\n';
    lang_line_t *line = calloc(lines, sizeof *line);
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

/* How many of s's first characters make a code: letters and '_', 2 to 11 of them; 0 for fewer
 * or more. */
static unsigned code_len(const char *s) {
    unsigned k = 0;
    while ((s[k] >= 'a' && s[k] <= 'z') || (s[k] >= 'A' && s[k] <= 'Z') || s[k] == '_') ++k;
    return k >= 2 && k <= 11 ? k : 0;
}

/* A table name's code, a code and ".tsv". */
static int table_code(const char *name, char *code) {
    unsigned k = code_len(name);
    if (!k || strcmp(name + k, ".tsv")) return 0;
    memcpy(code, name, k), code[k] = 0;
    return 1;
}

/* The language in use: LANG under Q2POD names it (code holds 256, stock's config line) and its
 * copy, /mnt/data/q2-lang-<code>.tsv, into copy, is there. A config.ini replaced (Reset) or
 * emptied, or a copy gone (Remove), is English: 0, code "". */
static int active(char *code, char *copy, unsigned n) {
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "LANG", code, "");
    code[255] = 0;
    if (*code && code_len(code) == strlen(code)) {
        tk_snprintf(copy, n, LANG_DATA "/q2-lang-%s.tsv", code);
        if (!access(copy, 0)) return 1;
    }
    *code = 0;
    return 0;
}

/* Each card app whose q2app says lang=1: fn(its lang folder, ctx), until fn says stop (nonzero). */
static void lang_apps(int (*fn)(const char *dir, void *ctx), void *ctx) {
    char raw[512], path[96];
    void *d = opendir(LANG_CARD);
    for (struct dirent *e; d && (e = readdir(d));) {
        if (e->d_name[0] != '.' || !valid_id(e->d_name + 1)) continue;
        tk_snprintf(path, sizeof path, LANG_CARD "/%s/q2app", e->d_name);
        if (!state_read(path, raw, sizeof raw) || number(raw, "lang") != 1) continue;
        tk_snprintf(path, sizeof path, LANG_CARD "/%s/lang", e->d_name);
        if (fn(path, ctx)) break;
    }
    if (d) closedir(d);
}

/* A lang folder's tables into lg's picks (the UI thread's: the page's rows), a code once, the
 * first app's; the name its first line's, after "# ", else the code. */
static int picks_add(const char *dir, void *unused) {
    void *t = opendir(dir);
    for (struct dirent *f; t && (f = readdir(t)) && lg.picks < LANG_PICKS;) {
        int i = lg.picks, dup = 0;
        if (!table_code(f->d_name, lg.code[i])) continue;
        for (int j = 0; j < i; ++j) dup |= !strcmp(lg.code[j], lg.code[i]);
        tk_snprintf(lg.file[i], sizeof lg.file[i], "%s/%s", dir, f->d_name);
        char head[sizeof lg.name[i] + 2] = "";
        void *h = dup ? 0 : fopen(lg.file[i], "rb");
        if (!h) continue;
        head[fread(head, 1, sizeof head - 1, h)] = 0;
        fclose(h);
        for (char *c = head; *c; ++c)
            if (*c == '\n' || *c == '\r') *c = 0;
        tk_snprintf(lg.name[i], sizeof lg.name[i], "%s",
                    !strncmp(head, "# ", 2) && head[2] ? head + 2 : lg.code[i]);
        lg.picks++;
    }
    if (t) closedir(t);
    return 0;
}

/* System settings' Language row (navigation.c ringnav_systemset): shown while the card has a
 * table to pick. */
int language_offered(void) {
    lg.picks = 0;
    lang_apps(picks_add, 0);
    return lg.picks > 0;
}

/* b (n bytes) into dst whole, through a .new and a rename, so a cut leaves the old copy or none; an
 * empty b too, which blob_io's one-item write counts as failed. */
static int copy_buf(const char *dst, void *b, unsigned n) {
    char tmp[100];
    tk_snprintf(tmp, sizeof tmp, "%s.new", dst);
    if (blob_io(dst, tmp, b, n, 1) || (!n && !rename(tmp, dst))) return 1;
    unlink(tmp);
    return 0;
}

/* src (max bytes at most) into dst whole, as copy_buf. */
static int copy_in(const char *src, const char *dst, unsigned max) {
    unsigned n = 0;
    char *b = read_all(src, max, &n);
    int ok = b && copy_buf(dst, b, n);
    free(b);
    return ok;
}

/* A pick: the new table's copy, then LANG, then the old copy gone, so a cut keeps the old
 * language, or English. */
static int lang_pick(void *ctx, event_t *event) {
    int i = (int)(long)ctx; /* -1 English */
    char was[256], old[96], copy[96];
    int had = active(was, old, sizeof old);
    if (i >= lg.picks || !strcmp(was, i < 0 ? "" : lg.code[i])) return 0;
    if (!restart_asked()) return 0;
    if (i >= 0) {
        tk_snprintf(copy, sizeof copy, LANG_DATA "/q2-lang-%s.tsv", lg.code[i]);
        if (!copy_in(lg.file[i], copy, LANG_BYTES)) {
            toast("无法复制语言文件");
            return 0;
        }
    }
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", "LANG", i < 0 ? "" : lg.code[i]);
    if (had) unlink(old);
    ringnav_reboot();
    return 0;
}

/* systemset_language_page_init: stock's list of its own tables gives way to English and the
 * card's, the one in use ticked, as Theme's rows are. */
int ringnav_language(widget_t *win, void *ctx) {
    int result = stock_language_trampoline(win, ctx);
    widget_t *view = win ? widget_lookup(win, "scroll_view_language", 1) : (void *)0;
    if (!view) return result;
    char was[256], copy[96];
    active(was, copy, sizeof copy);
    language_offered();
    widget_destroy_children(view);
    for (int i = -1; i < lg.picks; ++i)
        widget_set_text_utf8(list_row(view,
                                      !strcmp(was, i < 0 ? "" : lg.code[i]) ? "select" : "unselect",
                                      lang_pick, (void *)(long)i),
                             i < 0 ? "English" : lg.name[i]);
    return result;
}

typedef struct {
    char name[24], file[96];
} lang_find_t;
static int file_in(const char *dir, void *ctx) {
    lang_find_t *f = ctx;
    tk_snprintf(f->file, sizeof f->file, "%s/%s", dir, f->name);
    if (!access(f->file, 0)) return 1;
    *f->file = 0;
    return 0;
}

/* The first lang folder's name (the table or the font) copied to dst when it differs; none on the
 * card changes nothing: the copy is the Q2's. */
static void refresh(lang_find_t *f, const char *dst, unsigned max) {
    unsigned a = 0;
    lang_apps(file_in, f);
    char *card = *f->file ? read_all(f->file, max, &a) : 0;
    if (card && !file_holds(dst, card, a, max)) copy_buf(dst, card, a);
    free(card);
}

/* The card mounted (system.c's logo thread) or an app installed (netupdate.c's worker): the table
 * in use and a pack's font, for right-to-left titles in any language (script.c), copied again when
 * the card's differ, so a pack's update shows from the next start. Its own scan: lg's picks are
 * the UI thread's. */
void language_sync(void) {
    char code[256], copy[96];
    lang_find_t f = { "font.ttf", "" };
    refresh(&f, LANG_FONT, LANG_FONT_BYTES);
    if (!active(code, copy, sizeof copy)) return;
    tk_snprintf(f.name, sizeof f.name, "%s.tsv", code);
    refresh(&f, copy, LANG_BYTES);
}

/* The App Store's Remove of the app in the card folder dir (netupdate.c's worker, before its
 * files go): when its q2app says lang=1, every copy and the font go, so the next start is English.
 * Only the copies: config.ini is the UI thread's to write, and LANG without its copy reads as
 * English. */
void language_removing(const char *dir) {
    char raw[512], path[128];
    tk_snprintf(path, sizeof path, LANG_CARD "/%s/q2app", dir);
    if (!state_read(path, raw, sizeof raw) || number(raw, "lang") != 1) return;
    unlink(LANG_FONT);
    void *d = opendir(LANG_DATA);
    for (struct dirent *e; d && (e = readdir(d));) {
        unsigned k = strlen(e->d_name);
        if (strncmp(e->d_name, "q2-lang-", 8) || k < 12 || strcmp(e->d_name + k - 4, ".tsv"))
            continue;
        tk_snprintf(path, sizeof path, LANG_DATA "/%s", e->d_name);
        unlink(path);
    }
    if (d) closedir(d);
}
