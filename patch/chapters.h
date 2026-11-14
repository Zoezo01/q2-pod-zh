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
/* The player's mov demuxer (hciplayer, and ffmpeg for Chapters) indexes every sample before it
 * plays. A 47-hour M4B is millions of samples, more than the Q2's 128 MB, and the whole device
 * stalls. ponytail: 16 bytes a sample is the low end of a 16-24 guess, not measured: FFmpeg's own
 * cost (a 24-byte AVIndexEntry and a 4-byte stsz size, 28) refused an 18-hour 44.1 kHz book
 * (2.8 M samples, 75 MB) that played before the check existed. Raise it if a book that starts
 * stalls the Q2. The estimate leaves out chunk tables and the decoder's own buffers
 * (MP4_SPARE_KB covers them). */
#define MP4_SAMPLE_BYTES 16
#define MP4_SPARE_KB (16 * 1024)
/* A start's spare is smaller: the player closes the file it holds before it opens the next, so
 * that file's buffers, counted as used at the check, come back, and its index (mp4_start_fits'
 * held) with them. A preload or Chapters opens a second decoder beside the playing one.
 * ponytail: 8 MB is reasoned, not measured: stalls were seen near 3 MB of MemAvailable. Raise it
 * if a book that starts stalls the Q2; lower it if a book that plays elsewhere is refused. */
#define MP4_START_SPARE_KB (8 * 1024)
#define MP4_EOVERFLOW 79 /* MIPS; a 64-bit host opens any size */
unsigned long long mp4_index_bytes(const char *path); /* 0 not an MP4, ~0 too large to open */
int mp4_fits(const char *path); /* the index fits MemAvailable, less MP4_SPARE_KB */
/* The same for a start, where the player first closes the file it holds (held, "" for none). */
int mp4_start_fits(const char *path, const char *held);
unsigned mem_available_kb(void); /* /proc/meminfo's MemAvailable, ~0 with none */
#endif
