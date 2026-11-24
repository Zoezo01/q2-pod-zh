/* Card apps' EQ (addon.c's apps, the caps word "eq"): a running app sets the EQ, or reads it, in
 * /tmp/q2-<id>.eq, written whole (temp + rename): "<seq>" on its first line (1 to EQ_SEQ - 1 bytes
 * of [0-9A-Za-z], new for every request), then "get" (nothing changes), "on" (the PEQ plays, as the
 * EQ page's Advanced switch turned on), "off" (no EQ plays; the PEQ is kept), each with a line end
 * after it or none, or else a preset in the card EQ folder's text (peq_parse, PEQ_FILE_LIMIT bytes
 * at most), which plays at once as the EQ page's Apply does (peq_ui.c eq_set: what EQ per Output
 * files for the output playing). The answer goes to .eq.done, written whole: "<seq> ok", then
 * "mode off|3eq|peq" (what plays) and the PEQ as text (eq_text), whether it plays or not; or
 * "<seq> error <why>", a preset's "line N: <peq_parse's reason>", and nothing changed.
 * addon_eq_poll, in a running app's poll (addon.c poll_one, every ADDON_POLL_MS), reads the file's
 * first EQ_SEQ bytes, and the whole file once when those are new for the app's slot; a seq the
 * .done answers already (demo restarted, /tmp kept) is taken as done, and a first line that isn't a
 * seq is never answered. One request at a time: one replaced before it was read is never answered.
 * ponytail: each change saves PEQ_ACTIVE on the data partition, fsynced, as the page's Apply does,
 * since the player reads only that file: at most once an ADDON_POLL_MS for each app, so a phone's
 * drag of a minute writes it 120 times. A RAM-only preview would need the player to read another
 * file. */
#include "offsets.inc"
#include "peq.h"

#define EQ_SEQ 24 /* a seq's bytes and its line end */
/* The whole request as state_read keeps it: the seq's line, then enough for peq_parse to see a
 * preset over PEQ_FILE_LIMIT, with state_read's newline before and NUL after. The answer is built
 * there after: under 4 KB (30 bands' lines of 107 bytes at most, a Channel line before each). */
#define EQ_RAW (EQ_SEQ + PEQ_FILE_LIMIT + 3)
#define EQ_TMP "/tmp/q2-eq.tmp"

/* Each app's request as last read, its first EQ_SEQ bytes hashed; 0 none. */
static unsigned eq_seen[APPS_MAX] __attribute__((section(".scratch")));

/* body is word, a line end after it or none */
static int is(const char *body, const char *word) {
    unsigned k = strlen(word);
    return !strncmp(body, word, k) &&
           (!body[k] || !strcmp(body + k, "\n") || !strcmp(body + k, "\r\n"));
}

/* v in the fewest digits that read back as v (peq_number), so the text holds the preset exactly:
 * 17 always do. */
static void num(char *out, unsigned size, double v) {
    double back;
    for (int digits = 15; digits < 17; ++digits) {
        snprintf(out, size, "%.*g", digits, v);
        if (peq_number(out, &back) && back == v) return;
    }
    snprintf(out, size, "%.17g", v);
}

/* p as the card EQ folder's text, which peq_parse reads back to the same preset: no Preamp line is
 * Auto; a balance is Channel: L and R preamps after the common one, the side turned down below 0
 * and the other at 0; a band on one side comes under its Channel:, and every band has its Q, so a
 * shelf (LSC, HSC) keeps its frequency as it stands. ponytail: Auto with a balance has no text of
 * its own, as any Preamp line is a picked preamp, so it is written as its 48 kHz cut, the EQ page's
 * figure, and reads back picked; a word for Auto in the format would keep it. */
static int eq_text(const peq_preset *p, char *out, unsigned size) {
    static const char *const types[] = { "PK", "LSC", "HSC" };
    static const char *const sides[] = { 0, "all", "L", "R" }; /* by peq_band.enabled */
    char a[32], b[32], c[32];
    int n = 0, scope = 1;
    if (p->automatic == 2 || p->balance) {
        num(a, sizeof a, p->automatic == 2 ? p->preamp : peq_auto(p, 48000));
        n += snprintf(out + n, size - n, "Preamp: %s dB\n", a);
    }
    if (p->balance) {
        num(a, sizeof a, p->balance > 0 ? -p->balance : 0);
        num(b, sizeof b, p->balance < 0 ? p->balance : 0);
        n += snprintf(out + n, size - n,
                      "Channel: L\nPreamp: %s dB\nChannel: R\nPreamp: %s dB\nChannel: all\n", a, b);
    }
    for (int i = 0; i < p->count; ++i) {
        const peq_band *x = &p->bands[i];
        if (x->enabled && x->enabled != scope) /* an OFF band is no side's */
            n += snprintf(out + n, size - n, "Channel: %s\n", sides[scope = x->enabled]);
        num(a, sizeof a, x->frequency), num(b, sizeof b, x->gain), num(c, sizeof c, x->q);
        n += snprintf(out + n, size - n, "Filter %d: %s %s Fc %s Hz Gain %s dB Q %s\n", i + 1,
                      x->enabled ? "ON" : "OFF", types[x->type], a, b, c);
    }
    return n;
}

void addon_eq_poll(int slot, const char *id) {
    char path[48], done[56], head[EQ_SEQ + 2], old[EQ_SEQ + 2], seq[EQ_SEQ], why[96], *raw;
    snprintf(path, sizeof path, "/tmp/q2-%s.eq", id);
    unsigned n = state_read(path, head, sizeof head),
             key = hash_bytes(FNV_SEED, (const unsigned char *)head + 1, n) | 1, k = 0;
    if (!n || key == eq_seen[slot] || !(raw = calloc(1, EQ_RAW))) return; /* no memory: next poll */
    eq_seen[slot] = key;
    n = state_read(path, raw, EQ_RAW);
    while (k < EQ_SEQ - 1 &&
           ((raw[1 + k] >= '0' && raw[1 + k] <= '9') || (raw[1 + k] >= 'A' && raw[1 + k] <= 'Z') ||
            (raw[1 + k] >= 'a' && raw[1 + k] <= 'z')))
        ++k;
    snprintf(seq, sizeof seq, "%.*s", (int)k, raw + 1);
    snprintf(done, sizeof done, "%s.done", path);
    state_read(done, old, sizeof old);
    if (!k || raw[1 + k] != '\n' || (!strncmp(old + 1, seq, k) && old[1 + k] == ' ')) {
        free(raw);
        return;
    }
    const char *body = raw + 2 + k, *bad = 0;
    int on = is(body, "on"), get = is(body, "get");
    peq_preset p;
    peq_error e = { 0, 0 };
    if (on || is(body, "off"))
        bad = eq_set(0, on ? EQ_PEQ : EQ_OFF);
    else if (!get && !peq_parse(body, n - 1 - k, &p, &e))
        snprintf(why, sizeof why, "line %u: %s", e.line, e.reason), bad = why;
    else if (!get)
        bad = eq_set(&p, EQ_PEQ);
    int m;
    if (bad)
        m = snprintf(raw, EQ_RAW, "%s error %s\n", seq, bad);
    else {
        eq_state s;
        eq_load(&s);
        static const char *const modes[] = { "off", "3eq", "peq" }; /* by EQ_ */
        /* the flag set: the mode saved plays (peq_ui.c load) */
        m = snprintf(raw, EQ_RAW, "%s ok\nmode %s\n", seq,
                     modes[g_equalizer_flag ? s.mode : EQ_OFF]);
        m += eq_text(&s.peq, raw + m, EQ_RAW - m);
    }
    if (!blob_io(done, EQ_TMP, raw, m, 1)) unlink(EQ_TMP); /* /tmp full: the app's wait runs out */
    free(raw);
}
