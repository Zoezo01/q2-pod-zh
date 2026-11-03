#ifndef Q2_CHAPTERS_H
#define Q2_CHAPTERS_H
#define CHAPTER_MAX 512
#define CHAPTER_BYTES (256 * 1024)
typedef struct { int sec; char title[256]; } chapter;
typedef struct {
    int pid, fd, used, done, failed, count;
    unsigned long long at;
    chapter rows[CHAPTER_MAX];
    char raw[CHAPTER_BYTES + 1];
} chapters;
int chapters_parse(char *raw, chapter *rows);
chapters *chapters_start(const char *path, unsigned long long now);
int chapters_poll(chapters *c, unsigned long long now); /* 0 running, 1 done */
void chapters_cancel(chapters *c); /* poll until reaped, then free */
/* FFmpeg's mov demuxer (hciplayer, and ffmpeg for Chapters) indexes every sample before it plays:
 * a 24-byte AVIndexEntry and a 4-byte stsz size. A 47-hour M4B is millions of samples, more than
 * the Q2's 128 MB, and the whole device stalls. ponytail: the estimate leaves out chunk tables and
 * the decoder's own buffers (MP4_SPARE_KB covers them); measure on the device to tune it. */
#define MP4_SAMPLE_BYTES 28
#define MP4_SPARE_KB (16 * 1024)
#define MP4_EOVERFLOW 79 /* MIPS; a 64-bit host opens any size */
unsigned long long mp4_index_bytes(const char *path); /* 0 not an MP4, ~0 too large to open */
int mp4_fits(const char *path); /* the index fits MemAvailable, less MP4_SPARE_KB */
#endif
