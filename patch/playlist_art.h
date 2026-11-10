#ifndef Q2_PLAYLIST_ART_H
#define Q2_PLAYLIST_ART_H
/* playlist_art.c: a playlist's art, for its rows. All on the UI thread. */

/* The file:// url of cd_num's ready art (small: the 52 px one) in url, and 1; else 0, its build
 * queued unless it is known to have none. */
int playlist_art(int cd_num, const char *name, int small, char *url, int size);
/* Every playlist's songs read again, in one pass, for the asks that follow: once per Playlists
 * list built; a finished build notes the songs it read itself. */
void playlist_art_refresh(void);
/* cd_num's songs, as the last refresh, or a build since, counted them. */
unsigned playlist_songs(int cd_num);
/* From the build's own timer: calls playlist_art_ready for each playlist whose art was made. */
void playlist_art_poll(void);
/* The build stopped and joined, its queue dropped (a page closed, the card gone). */
void playlist_art_stop(void);
/* A clean end (power-off, Restart): the build stops before its next cover, its note gone. */
void playlist_art_clean_end(void);
extern void (*playlist_art_ready)(int cd_num);

#endif
