#ifndef Q2_PLAYBACK_H
#define Q2_PLAYBACK_H
int playback_option(int kind); /* 0 shuffle, 1 repeat, 2 grouping */
unsigned rand_below(unsigned n);
int playback_set(int kind, int value);
int playback_groups(void *all, int folder);
int playback_unshuffle(void);   /* the stock mode Shuffle Songs replaces */
int playback_shuffle(int mode); /* the queue as Shuffle Songs, Repeat All; the next restores mode */
int playback_listable(void);    /* View Queue: a shuffle's order is up (stock Shuffle given one) */
int playback_group_skip(int forward);
int playback_back(void); /* the track stock Shuffle's Previous left, once; else -1 */
unsigned playback_upcoming(unsigned *out, unsigned max); /* Up Next: queue indices, in play order */
/* Play Next / Add: the tracks land at pos+1 (next) or the end; returns that index */
unsigned playback_queue_insert(void *queue, void *add, int next);
void playback_insert(unsigned at, unsigned n, int next); /* the advanced part alone */
int playback_move(unsigned from); /* Up Next's hold: from plays next; its new index, else -1 */
void playback_poll(void);
void playback_speed_start(void);
void playback_track_speed(void); /* a track's start: the folder's speed, to the decoder */
int playback_start_fits(void);   /* 0: the track at MCL_POS is too long to open */
int playback_video(void *record); /* a video file, kept out of audio queues */
void xfade_poll(int next); /* crossfade.c */
void mono_sync(void);      /* crossfade.c */
void mqa_sync(void);       /* crossfade.c */
int playback_resumed(void *r);
void playback_save(void);
void playback_clear(void);
int wheel_pct(void); /* Wheel sensitivity, 50-200% */
void wheel_set_pct(int n);
void wheel_guard_reset(void);
extern int wheel_guard; /* Volume guard: the screen-off wheel's half-turn guard, 0 off */
/* Wheel QS taken back: the pull-down's slide for the next release, plus 1 (ringnav_dispatch);
 * qs_down while its finger is down, so that finger's own late touch down keeps it */
extern volatile int qs_back, qs_down;
/* iPod: while wheel_raw is set the wheel posts no ticks and adds its raw travel to wheel_travel,
 * 200 units a turn, rising anticlockwise (games.c's Vortex, Now Playing's lyrics); wheel_held is
 * set while a finger is on the wheel */
extern volatile int wheel_raw, wheel_travel, wheel_held;
#endif
