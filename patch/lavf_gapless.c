#include "peq.h"

/* Gapless for what libavformat demuxes (docs/internals.md#lossy-gapless-playback): M4A's AAC, Ogg's Vorbis and Opus.
 * mp3_filter calls this on every decoded block, before any filter. A window of stream timestamps says what is
 * music; lavf parsed all of it while opening the file, and the block's own timestamp says where it is, so there's no
 * held tail, no allocation, and a seek or a preload needs nothing done. An end is only believed if no packet follows
 * and it lies inside the last packet, where real padding is: audio after an end proves the end wrong, and nothing goes.
 * ponytail: an end early by less than what's left of a real last packet, from a tag or a granule wrong by that little,
 * can't be told from padding and loses that much. A chained Vorbis keeps every link's padding but the last. HE-AAC
 * plays as stock does, gap and all: its numbers can't be told from LC's before a packet decodes.
 * Matroska and WebM's Vorbis and Opus aren't read. ADTS .aac has no numbers to read and native ASF's WMA none. */
#define AT(p, o, type) (*(type *)((char *)(p) + (o)))
#define LAVF_PREF_DESC 0x8a496cu  /* demuxer_desc_lavf_preferred */
#define LAVF_DESC 0x8a49a0u       /* demuxer_desc_lavf: same functions */
#define LAVF_PRIV_SIZE 0xc38      /* lavf_priv_t; tools/peq.py's LAVF_PRIV makes room for a window after it */
#define MOV_FORMAT ((void *)0xafc030u) /* ff_mov_demuxer */
#define OGG_FORMAT ((void *)0xafc250u) /* ff_ogg_demuxer */
#define CODEC_AAC 0x15002
#define CODEC_VORBIS 0x15005
#define CODEC_OPUS 0x1503d
#define AV_DICT_GET ((char **(*)(void *, const char *, const void *, int))0x6ea608u)
#define NOPTS (-0x7fffffffffffffffLL - 1)
#define DS_GET_NEXT_PTS ((double (*)(void *))0x485ea8u) /* stock calls it at open; fills the queue, sets no EOF */
#define NO_END 0x7fffffffffffffffLL
#define AAC_FRAME 1024 /* padding is less; a tag or edit claiming more isn't this file's end, so try the next number */
#define MAX_PRIMING 16383 /* lavf's own bound on iTunSMPB's */
#define MAX_PRE_SKIP 24000

enum { OFF, ON, ON_AAC };
typedef struct {
    int checked, trim;     /* OFF leaves the track alone */
    long long start, end;  /* frames at timestamps outside these go */
    int head;              /* how far below start a block can start and still be priming; a chained Ogg's block with
                            * the last link's end and the next one's start in it is further */
} window;
_Static_assert(sizeof(window) == 0xc58 - LAVF_PRIV_SIZE, "tools/peq.py's LAVF_PRIV allocates this much");

/* Stream timeline, from the structures of FFmpeg 3.0 as built into hciplayer 9c3f8c6d…: AVFormatContext's iformat
 * +4, priv_data +0xc, nb_streams +0x18, streams +0x1c, metadata +0x484; AVStream's codec +8, priv_data +0xc,
 * time_base +0x28, start_time +0x30, duration +0x38; AVCodecContext's codec_id +0x30, delay +0x78. */
static void lavf_window(window *w, char *fc, int id, int rate, long long first) {
    w->start = NOPTS;
    w->end = NO_END;
    if (id < 0 || id >= AT(fc, 0x18, int)) return;
    char *st = AT(fc, 0x1c, char **)[id], *cc = AT(st, 8, char *);
    void *format = AT(fc, 4, void *);
    int codec = AT(cc, 0x30, int);
    long long from = AT(st, 0x30, long long), length = AT(st, 0x38, long long), end = from + length;
    if (AT(st, 0x28, int) != 1 || AT(st, 0x2c, int) != rate || from == NOPTS || length <= 0) return;
    if (format == OGG_FORMAT && codec == CODEC_VORBIS) {
        /* The last page's granule, which ogg_get_length read: lavf shortens the last packet's duration by it but the
         * decoder hears nothing of that. With all the audio on one page (a short track, or a cut one) the first packet
         * decodes to nothing and the first block doesn't start at the start: its end can't be placed. */
        if (first != from) return;
        w->end = end;
    } else if (format == OGG_FORMAT && codec == CODEC_OPUS) {
        /* RFC 7845's pre-skip (stock's opus_header copies it to delay): lavf starts the first packet that far below 0
         * (start_time says 0). It already tells the decoder the end. */
        int pre_skip = AT(cc, 0x78, int);
        if (pre_skip < 0 || pre_skip > MAX_PRE_SKIP) return;
        w->start = from;
        w->head = pre_skip;
    } else if (format == MOV_FORMAT && codec == CODEC_AAC) {
        /* MOVStreamContext: elst_data +0x38 (duration +0, 24 bytes each; tools/peq.py's MOV_EDITS keeps it until
         * mov_read_close frees it), elst_count +0x3c, start_pad +0x4c8. mov.c keeps start_pad and hands it to the decoder, on a seek to 0 too, so a tag's priming isn't ours to cut. An edit
         * list's only moves the timestamps back, so the frames before 0 are the priming. */
        char *sc = AT(st, 0xc, char *), *edit = AT(sc, 0x38, char *);
        char **tag = AV_DICT_GET(AT(fc, 0x484, void *), "iTunSMPB", 0, 0);
        int priming = AT(sc, 0x4c8, int), timescale = AT(AT(fc, 0xc, char *), 8, int);
        unsigned remainder;
        unsigned long long total;
        long long padding = -1;
        if (!priming && from < 0 && from >= -MAX_PRIMING) {
            w->start = 0;
            w->head = -from;
        }
        /* A tag's priming of 0 on AAC, which always has some, can't be counted from. */
        if (priming && tag && __isoc99_sscanf(tag[1], "%*X %*X %X %llX", &remainder, &total) == 2)
            padding = total ? length - priming - (long long)total : remainder;
        /* Without a tag or an edit, the end is stts's: its short last delta is how FFmpeg writes padding. */
        w->end = end;
        if (padding >= 0 && padding < AAC_FRAME) {
            w->end = end - padding;
        } else if (AT(sc, 0x3c, int) == 1 && edit && timescale > 0) {
            /* ponytail: the edit's duration is in the movie's timescale, so at 1000 or 600 the true end is somewhere in
             * its last tick. Taken from the tick's end, a file with whole last frames keeps up to 45 or 74 frames and
             * loses none. FFmpeg's own end on its stts, which is exact. */
            long long duration = AT(edit, 0, long long);
            if (duration > 0 && duration < 0x7fffffff) {
                double at = timescale == rate ? (int)duration : (double)((int)duration + 1) * rate / timescale;
                long long kept = llrint(at + 0.49999);
                if (kept < end && end - kept < AAC_FRAME) w->end = kept;
            }
        }
    } else {
        return;
    }
    w->trim = codec == CODEC_AAC ? ON_AAC : ON;
}

void lavf_trim(void *d, void *sh, af_data *data) {
    unsigned desc = d ? AT(d, 0, unsigned) : 0;
    char *priv = desc == LAVF_DESC || desc == LAVF_PREF_DESC ? AT(d, 0xc70, char *) : 0;
    if (!priv) return;
    window *w = (window *)(priv + LAVF_PRIV_SIZE);
    int unit = data->bps * data->nch, rate = data->rate;
    if ((w->checked && !w->trim) || unit <= 0 || data->len <= 0 || data->len % unit) return;
    /* sh_audio: pts +0x20 (the last packet's), pts_bytes +0xd0 (decoded since), a_buffer_len +0x6c, of which this block
     * is the first len bytes: what written_audio_pts counts. */
    double pts = AT(sh, 0x20, double);
    if (pts < -1e9) return; /* MP_NOPTS_VALUE */
    long long n = data->len / unit;
    long long last = llrint(pts * rate) + (AT(sh, 0xd0, int) - (AT(sh, 0x6c, int) - data->len)) / unit;
    long long first = last - n, cut;
    if (!w->checked) {
        w->checked = 1;
        lavf_window(w, AT(priv, 4, char *), AT(AT(sh, 0, char *), 0x54, int), rate, first);
        if (!w->trim) return;
    }
    /* A packet of over a frame is HE-AAC's SBR at twice the core's: its timescale and stts can be in either rate's units,
     * so no number here can be placed. */
    if (w->trim == ON_AAC && AT(sh, 0xd0, int) / unit > AAC_FRAME) {
        w->trim = OFF;
        return;
    }
    if (first < w->start && w->start - first <= w->head) {
        cut = w->start - first < n ? w->start - first : n;
        data->audio = (char *)data->audio + cut * unit;
        n -= cut;
    }
    /* Real padding lies inside the last packet, so an end at or before its timestamp is wrong (strictly: a last delta of 0
     * is a whole frame nobody meant to cut). So is one with a packet still to come, queued or half read: ad_ffmpeg puts
     * back what the decoder left. The lookahead only runs on the packet the end is in, and a packet it queues is the one
     * the next decode would read. */
    if (last > w->end && w->end > llrint(pts * rate)) {
        char *ds = AT(sh, 0, char *);
        DS_GET_NEXT_PTS(ds); /* its answer is no use: a packet without a timestamp says what no packet says */
        if (!AT(ds, 0x48, void *) && AT(ds, 0, int) >= AT(ds, 4, int)) {
            cut = last - w->end < n ? last - w->end : n;
            n -= cut;
        }
    }
    data->len = n * unit;
}
