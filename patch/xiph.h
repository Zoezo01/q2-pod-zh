#ifndef Q2_XIPH_H
#define Q2_XIPH_H
/* Vorbis comments (FLAC's VORBIS_COMMENT block, an Ogg Opus or Vorbis file's comment header) read a
 * field at a time, as stock's TagLib 2.0.1 (Shanling's libtag.so) XiphComment reads them, without
 * holding the comments or their pictures (docs/internals.md#oversized-ogg-tags). */

/* An Ogg comment header over this goes to xiph.c rather than TagLib, which holds the whole header
 * about 4 times over: its packet grows by doubling as it joins the pages, then each picture is
 * decoded from base64 beside it. A 3 MB cover's base64 (4,000,000 bytes) stays under it, so such a
 * file keeps stock's read, at a peak of about 16 MB over demo's 17; the 20 MB header that killed
 * demo (92 MB resident) would have taken 80. */
#define OGG_TAGS_MAX (4 << 20)
#define OGG_PAGES_MAX 0x10000 /* a walk's pages: 4 GB of full ones, a broken file's end sooner */
#define XIPH_TEXT 512         /* a text field's joined values, cut at a whole character */
#define XIPH_VALUE 1024       /* a value's bytes looked at: the rest is skipped */

/* The keys read: the text fields first, in toolsGetMusicInfo's order and then foobar2000's
 * "ALBUM ARTIST", then the numbers (each value's first only) and MQAENCODER, whose presence alone
 * counts. */
enum {
    XIPH_TITLE,
    XIPH_ARTIST,
    XIPH_ALBUM,
    XIPH_GENRE,
    XIPH_COMPOSER,
    XIPH_ALBUMARTIST,
    XIPH_ALBUM_ARTIST,
    XIPH_TEXTS
};
enum {
    XIPH_DATE,
    XIPH_YEAR,
    XIPH_TRACKNUMBER,
    XIPH_TRACKNUM,
    XIPH_DISCNUMBER,
    XIPH_DISC,
    XIPH_MQAENCODER,
    XIPH_NUMBERS
};

typedef struct {
    char text[XIPH_TEXTS][XIPH_TEXT]; /* each key's values joined with " / " (Tag::joinTagValues) */
    unsigned short used[XIPH_TEXTS];  /* bytes in text, XIPH_TEXT once cut */
    int num[XIPH_NUMBERS];            /* String::toInt of each number's first value */
    unsigned char has[XIPH_NUMBERS];
    /* TagLib's: year() DATE else YEAR else 0; track() TRACKNUM else TRACKNUMBER else 0; its disc
     * DISC else DISCNUMBER else 1000; mqaflag() */
    int year, track, disc, mqa;
    int kind;      /* 1 Opus, 2 Vorbis, 0 no tag (a packet its pages cut short, a bad OpusHead) */
    int rate;      /* the audio properties' sample rate: 48000 for Opus, Vorbis's from its header */
    unsigned size; /* the comment header's bytes, as many as its pages held */
} xiph_tags;

/* path's comment header when it is an .opus, .ogg or .oga file's and over OGG_TAGS_MAX: 0 when it
 * is not, and TagLib may read it; else 1, with t (unless 0) its fields read here. */
int ogg_tags(const char *path, xiph_tags *t);
/* A FLAC's first "ALBUM ARTIST" (foobar2000's spelling) into out, 0x100 bytes, when it has one: its
 * first 0xff bytes as they are, UTF-8 or not, as before xiph.c (albums are grouped by it). */
void flac_album_artist(const char *path, char *out);
/* Whether s is UTF-8: 0 not, 1 UTF-8, 2 with every character below U+0100 (String::isLatin1).
 * latin1 (unless 0) takes libtag_c's Latin-1 copy (String::to8Bit(false)): each UTF-16 unit's low
 * byte, up to the first zero one. */
int utf8_latin1(const char *s, char *latin1);
#endif
