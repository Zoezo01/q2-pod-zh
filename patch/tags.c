/* Tag reads and tag text: toolsGetMusicInfo (the library scan and the player), a card app's song's
 * tags and a CUE track's, GBK or Latin-1 tag text, CUE sheet text and the folder scan's refresh. */
#include "navigation.h"

extern int stock_music_info_trampoline(int, char *, const char *);

/* toolsGetMusicInfo's genre strcpy (GENRE_COPY): TagLib joins multi-value genres with " / ",
 * but style has only 0x80 bytes before name. Keep whole entries that fit, or as many whole
 * characters of an oversized first one as fit 0x7f bytes; the tag file is untouched.
 * ponytail: only " / " is an entry boundary, so one value joined with ";" or "//" is cut at 127
 * bytes, and an empty first entry before an oversized one stores an empty genre. */
char *ringnav_genre_copy(char *dst, const char *joined) {
    const char *entry = joined;
    unsigned keep = 0;
    for (;;) {
        const char *end = entry;
        while (*end && (end[0] != ' ' || end[1] != '/' || end[2] != ' ')) ++end;
        unsigned n = end - joined;
        if (n > 0x7f) {
            if (entry == joined) keep = 0x7f;
            break;
        }
        keep = n;
        if (!*end) break;
        entry = end + 3;
    }
    while (keep && (joined[keep] & 0xc0) == 0x80) --keep; /* not inside a UTF-8 character */
    memcpy(dst, joined, keep);
    dst[keep] = 0;
    return dst;
}

/* The crash log's records (docs/boot.md), for a library scan's tag reads and Coverflow's art build:
 * file holds the path in progress, and every `every`th call appends "<count> <VmRSS kB> <path>" to
 * log, which each demo starts afresh and stops past CRASH_NOTE_MAX bytes, so an OOM kill names the
 * file and shows whether demo grew. n is the caller's {calls, bytes logged}. tmpfs, no locks: a
 * thread racing on n only shifts a line. */
#define CRASH_NOTE_MAX 65536
void crash_note(const char *path, const char *file, const char *log, unsigned every, unsigned *n) {
    int fd = open(file, 0x80301, 0644); /* O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC */
    if (fd >= 0) write(fd, path, strlen(path)), write(fd, "\n", 1), close(fd);
    if (++n[0] % every || n[1] >= CRASH_NOTE_MAX) return;
    char m[64], line[600];
    unsigned pages = 0;
    fd = open("/proc/self/statm", 0x80000); /* O_RDONLY | O_CLOEXEC: "size resident ..." in pages */
    int got = fd < 0 ? 0 : read(fd, m, sizeof m - 1);
    if (fd >= 0) close(fd);
    m[got > 0 ? got : 0] = 0;
    const char *p = m;
    while (*p && *p != ' ') ++p;
    while (*p == ' ') ++p;
    while (*p >= '0' && *p <= '9') pages = pages * 10 + (*p++ - '0');
    /* O_WRONLY | O_CREAT | O_CLOEXEC, and O_APPEND, or O_TRUNC for this demo's first line */
    fd = open(log, n[1] ? 0x80109 : 0x80301, 0644);
    if (fd < 0) return;
    int len = snprintf(line, sizeof line, "%u %u %.500s\n", n[0], pages * 4, path);
    if (write(fd, line, len) == len) n[1] += len;
    close(fd);
}
static unsigned scan_counts[2];

/* toolsGetMusicInfo's TagLib branch (0x5c6084-0x5c64d0) for an Ogg file whose comment header is
 * over OGG_TAGS_MAX, from xiph.c's fields: each text field through stock's toolsFixId3ErrorCode as
 * libtag_c's pair of copies (Latin-1, UTF-8) and isLatin1 would reach it, then strcpy'd (the genre
 * through GENRE_COPY's ringnav_genre_copy); year, track() split into disc (high half, +0x684) and
 * track (+0x688), mqaflag into +0x68c's 0x100 bit; with a, the audio properties' sample rate and 16
 * bits. A header TagLib would find no tag in leaves info as stock does. */
static int ogg_music_info(int a, char *info, const char *path) {
    static const unsigned short at[] = { 0x280, 0x100, 0, 0x200, 0x480, MUSIC_INFO_ALBUM_ARTIST };
    xiph_tags t;
    if (!ogg_tags(path, &t)) return 0;
    for (unsigned k = 0; t.kind && k < sizeof at / sizeof *at; k++) {
        char latin1[XIPH_TEXT] = { 0 }, out[0x100] = { 0 }; /* utf8_latin1 ends no invalid text */
        int latin = utf8_latin1(t.text[k], latin1) == 2;
        toolsFixId3ErrorCode(latin, latin1, t.text[k], out, k + 1);
        if (at[k] == 0x200)
            ringnav_genre_copy(info + at[k], out);
        else
            memcpy(info + at[k], out, strlen(out) + 1);
    }
    if (t.kind) {
        unsigned track = (unsigned)t.disc << 16 | (unsigned)t.track;
        I(info, 0x680) = t.year, I(info, 0x684) = track >> 16, I(info, 0x688) = track & 0xffff;
        I(info, 0x68c) |= t.mqa << 8;
        if (a) I(info, 0x690) = t.rate, I(info, 0x68c) |= 16;
    }
    return 1;
}

/* toolsGetMusicInfo (the library scan and the player's tag reads): crash_note first, an Ogg file
 * with an oversized comment header read by ogg_music_info, then an empty album artist falls back
 * to flac_album_artist. */
int ringnav_music_info(int a, char *info, const char *path) {
    if (path) crash_note(path, "/tmp/q2-scan-file", "/tmp/q2-scan.log", 500, scan_counts);
    if (path && ogg_music_info(a, info, path)) return 1;
    int r = stock_music_info_trampoline(a, info, path);
    if (path && !info[MUSIC_INFO_ALBUM_ARTIST])
        flac_album_artist(path, info + MUSIC_INFO_ALBUM_ARTIST);
    return r;
}
/* A card app's song (its folder's listing, or an .m3u, reads no tags) in a library queue: stock
 * parses only a folder play's file (player_get_id3info, MCL_TYPE 1) and copies any other queue's
 * record, so its title was its file's name and its artist Unknown. As it starts, the playing
 * record and the parsed tags take its file's, read as the library scan reads them
 * (toolsGetMusicInfo): title, artist, album, album artist, track and disc. A record with an album
 * is left, so a card app's song is read once a queue (a card song without an album tag, played from
 * a list, at each start, as stock reads every folder play's); one without tags keeps its record's,
 * and a CUE track its sheet's (ringnav_id3_path). ponytail: Up Next and an app's lists show the
 * songs not yet played by their file names, and an app's list has no Go to rows (Now Playing's
 * has). */
static void card_tags(char *id3, const char *path) {
    static const unsigned from[] = { 0x280, 0x100, 0, MUSIC_INFO_ALBUM_ARTIST };
    static const unsigned to[] = { REC_TITLE, REC_ARTIST, REC_ALBUM, REC_ALBUM_ARTIST };
    unsigned pos, n;
    void *r = queue_now(&pos, &n);
    const char *album = r ? P(r, REC_ALBUM) : 0;
    char info[0x698] = { 0 };
    if (I(MCL_TYPE, 0) == 1 || mclGetCueFlag() || !r || !P(r, REC_PATH) ||
        strcmp(P(r, REC_PATH), path) || (album && *album) ||
        !tk_str_start_with(path, "/mnt/mmc/") || toolsGetMusicInfo(0, info, path) != 1)
        return;
    for (unsigned k = 0; k < sizeof to / sizeof *to; k++) {
        char *t, **field = (char **)((char *)r + to[k]);
        info[from[k] + 0xff] = 0;
        if (!info[from[k]] || !(t = strdup(info + from[k]))) continue;
        free(*field), *field = t;
        tk_snprintf(id3 + ID3_ALBUM + from[k], 0x100, "%s", t);
    }
    if (I(info, 0x684) > 0) I(r, REC_TRACK) = I(info, 0x684);
    if (I(info, 0x688) > 0) I(r, REC_DISC) = I(info, 0x688);
}
/* player_get_id3info's copy of the parsed path (ID3_PATH in tools/build.py), after the tags and
 * before Now Playing's lines. A CUE track's tags are its record's, the stock parser's TITLE and
 * PERFORMER (the track's, else the sheet's); a sheet without them takes the file's (issue #59).
 * ponytail: the playing track only; the database keeps the sheet's empty fields for Library. */
char *ringnav_id3_path(char *id3, const char *path) {
    static const int fields[] = { ID3_ALBUM, ID3_ARTIST, ID3_ALBUM_ARTIST };
    if (mclGetCueFlag() && (!id3[ID3_ALBUM] || !id3[ID3_ARTIST] || !id3[ID3_ALBUM_ARTIST])) {
        char info[0x698] = { 0 };
        if (toolsGetMusicInfo(0, info, path) == 1)
            for (unsigned i = 0; i < sizeof fields / sizeof *fields; i++) {
                char *f = id3 + fields[i];
                if (*f) continue;
                tk_snprintf(f, 0x100, "%s", info + fields[i] - ID3_ALBUM);
                toolsTrimLeft(f); /* as stock did to its fields before this copy */
            }
    }
    card_tags(id3, path);
    /* last: now_tag and marked_empty read a matching path as the tags being complete */
    tk_snprintf(id3, 0x400, "%s", path); /* +0x400: the CUE start */
    return id3;
}
/* The library scan's folder walk (SCAN_DIRS in tools/build.py): its first exclusion, strcmp(name,
 * "."), here skips every hidden folder, as Folder view's list does, so a card app's own
 * (q2-deezer's stream cache in .deezer/stream) never joins the Library. */
int ringnav_scan_dir(const char *name, const char *dot) {
    return name[0] == '.' ? 0 : strcmp(name, dot);
}

static int ascii_letter(unsigned c) { return (c | 32) >= 'a' && (c | 32) <= 'z'; }

/* Whether s reads as GBK rather than Latin-1 or Windows-1252: not UTF-8, and every byte from 0x80
 * a two-byte character's, lead 0x81-0xFE then trail 0x80-0xFE, none of them between two ASCII
 * letters. Western text has an accented letter beside an ASCII one ("mú-s-ica" is FA 73), or a
 * pair inside a word ("Can-çã-o", "Beyonc-é’-s"); Chinese text runs of high-byte pairs. ponytail: a
 * GBK character with an ASCII-range trail (0x40-0x7E, rarer GBK/3-4 ones), or one alone inside a
 * Latin word, reads as Latin-1, and accented pairs at a word's edge only ("ñó" alone) as GBK; a
 * real charset guesser if either shows up. */
static int gbk_text(const char *s) {
    int high = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p < 0x80) continue;
        if (*p == 0x80 || *p == 0xff || p[1] < 0x80 || p[1] == 0xff ||
            (p > (const unsigned char *)s && ascii_letter(p[-1]) && ascii_letter(p[2])))
            return 0;
        high = 1, ++p;
    }
    return high && !utf8_text(s);
}

/* Tag text (TAG_TEXT in tools/build.py): stock toolsFixId3ErrorCode took a field TagLib found all
 * Latin-1 for GBK when its Latin-1 bytes (latin1) went to UTF-8 and back unchanged (round, the
 * strcmp this replaces), and "Lo que la música me enseñó" does. It is GBK now only if it also reads
 * as GBK; anything else (nonzero) keeps TagLib's UTF-8 copy. */
int ringnav_gbk_tag(const char *round, const char *latin1) {
    return strcmp(round, latin1) || !gbk_text(latin1);
}

/* The CUE sheet parser's fields (CUE_TEXT in tools/build.py), up to 500 bytes each in buffers of
 * 512 or more: stock took any field that wasn't Chinese UTF-8 for GBK ("Café" became Chinese).
 * UTF-8 stays as it is, GBK (gbk_text) goes through stock's conversion as before; anything else is
 * Windows-1252, the code page Western rippers write, re-encoded as UTF-8 in place and cut at a
 * whole character to 511 bytes. Stock's returned 1 for kept text; so does this. */
int ringnav_cue_text(char *s, const char *to, const char *from) {
    if (!s || utf8_text(s)) return 1;
    /* stock's conversion writes back over s unbounded: GBK grows by half as UTF-8, so 340 bytes at
     * most fill the 512; a longer field reads as Windows-1252 */
    if (strlen(s) <= 340 && gbk_text(s))
        return ((int (*)(char *, const char *, const char *))0x5bccac)(s, to, from);
    static const unsigned short cp1252[32] = {
        0x20ac, '?',    0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160,
        0x2039, 0x0152, '?',    0x017d, '?',    '?',    0x2018, 0x2019, 0x201c, 0x201d, 0x2022,
        0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, '?',    0x017e, 0x0178
    };
    char out[512];
    unsigned n = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        unsigned c = *p >= 0x80 && *p < 0xa0 ? cp1252[*p - 0x80] : *p, k = c < 0x80    ? 1
                                                                           : c < 0x800 ? 2
                                                                                       : 3;
        if (n + k >= sizeof out) break;
        if (k == 1)
            out[n++] = (char)c;
        else if (k == 2)
            out[n++] = (char)(0xc0 | c >> 6), out[n++] = (char)(0x80 | (c & 63));
        else
            out[n++] = (char)(0xe0 | c >> 12), out[n++] = (char)(0x80 | (c >> 6 & 63)),
            out[n++] = (char)(0x80 | (c & 63));
    }
    out[n] = 0;
    memcpy(s, out, n + 1);
    return 1;
}

/* Quote bounded scanner paths/tags into one UPDATE; never execute a truncated statement. */
static int scan_quote(char *sql, unsigned cap, unsigned *n, const char *s) {
    if (*n + 3 >= cap) return 0;
    sql[(*n)++] = '\'';
    for (; s && *s; ++s) {
        if (*n + 3 >= cap) return 0;
        if (*s == '\'') sql[(*n)++] = '\'';
        sql[(*n)++] = *s;
    }
    sql[(*n)++] = '\'';
    sql[*n] = 0;
    return 1;
}
/* Only toolsLoadAllFile's folder-scan duplicate check calls this. Keep the row's ID, CUE bounds,
 * creation time and library count, so saved queues/favourites still refer to the same song. */
int ringnav_scan_refresh(void *row, const char *table, int type) {
    int exists = toolsCheckItemExist(row, table, type);
    if (!table || strcmp(table, "songtable")) return exists;
    if (!exists) return toolsGetMusicNum() >= 65000;
    const char *path = P(row, REC_PATH), *name = P(row, REC_NAME);
    if (!path || !name) return exists;
    char info[0x698] = { 0 }, sql[8192];
    int cue = I(row, REC_CUE_START) || I(row, REC_CUE_END);
    if (!cue) {
        /* Stock returns success even when TagLib cannot open/read a tag; keep the old tags then.
         * An oversized Ogg header is xiph.c's to read (ogg_music_info), not TagLib's. */
        xiph_tags t;
        int big = ogg_tags(path, &t);
        void *file = big ? 0 : taglib_file_new(path, 0);
        int valid = big ? t.kind : file && taglib_file_tag(file);
        if (file) taglib_file_free(file);
        /* Refresh tags without the audio-properties probe that can walk the whole file. */
        if (!valid || toolsGetMusicInfo(0, info, path) != 1) return exists;
    }
    static const char *const fields[] = { "album", "songer",   "style",
                                          "name",  "composer", "albumsonger" };
    static const unsigned offsets[] = { 0, 0x100, 0x200, 0x280, 0x480, 0x580 };
    static const unsigned records[] = { REC_ALBUM, REC_ARTIST, 0x1c,
                                        REC_TITLE, 0x20,       REC_ALBUM_ARTIST };
    unsigned n = tk_snprintf(sql, sizeof sql, "update songtable set ");
    for (unsigned i = 0; i < sizeof fields / sizeof *fields; ++i) {
        n += tk_snprintf(sql + n, sizeof sql - n, "%s%s=", i ? "," : "", fields[i]);
        if (n >= sizeof sql ||
            !scan_quote(sql, sizeof sql, &n, cue ? P(row, records[i]) : info + offsets[i]))
            return exists;
    }
    /* Stock indexes the trimmed title's pinyin initial, falling back to the filename. */
    char title[128] = "", key[128] = "";
    tk_snprintf(title, 31, "%s",
                cue && P(row, REC_TITLE) ? (char *)P(row, REC_TITLE) : info + 0x280);
    toolsTrimLeft(title);
    toolsTrimRight(title);
    if (!*title || !strcmp(title, "(null)")) tk_snprintf(title, 31, "%s", name);
    toolsHanziToPinyin(title, key);
    unsigned initial = (unsigned char)*key;
    if (initial >= 'a' && initial <= 'z') initial -= 'a' - 'A';
    initial = initial >= 'A' && initial <= 'Z'   ? initial
              : initial >= '0' && initial <= '9' ? '*'
                                                 : '#';
    n += tk_snprintf(sql + n, sizeof sql - n,
                     ",Initial=%u,year=%d,track=%d,cd_num=%d where filename=", initial,
                     cue ? I(row, 0x30) : I(info, 0x680), cue ? I(row, REC_TRACK) : I(info, 0x684),
                     cue ? I(row, REC_DISC) : I(info, 0x688));
    if (n >= sizeof sql || !scan_quote(sql, sizeof sql, &n, name)) return exists;
    n += tk_snprintf(sql + n, sizeof sql - n, " and fileurl=");
    if (n >= sizeof sql || !scan_quote(sql, sizeof sql, &n, path)) return exists;
    n += tk_snprintf(sql + n, sizeof sql - n, " and time=%d and time_end=%d", I(row, REC_CUE_START),
                     I(row, REC_CUE_END));
    if (n < sizeof sql) toolsUpdateDbTable("/mnt/data/database.db", sql);
    return exists;
}
