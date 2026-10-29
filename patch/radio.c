/* Internet Radio (docs/internals.md#internet-radio): Streaming's Internet Radio row lists the
 * card's favourites (RADIO_DIR's .m3u and .pls files) and radio-browser.info's directory, fetched
 * on a pthread through demo's libcurl. A station plays in q2video -r (video.c), which writes the
 * stream's tags to RADIO_STATE and takes keys on RADIO_SOCK. Like Spotify, while it plays it holds
 * the standby timers and the DAC, and it hands the output over with local music, Videos and
 * Spotify both ways. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif

#define RADIO_DIR PEQ_ROOT "/mnt/mmc/Radio"
#define RADIO_FAVS RADIO_DIR "/favourites.m3u"
#define RADIO_LAST RADIO_DIR "/.last"    /* the station last played, as an .m3u */
#define RADIO_STATE "/tmp/q2radio.state" /* video.c's */
#define RADIO_SOCK "/tmp/q2radio.sock"
#define RADIO_API "https://all.api.radio-browser.info" /* the directory's round-robin name */
#define RADIO_QUERY "?order=votes&reverse=true&limit=100&hidebroken=true"
#define RADIO_CA "/etc/scrobble-ca.pem" /* scrobble.c's; ISRG Root X1 signs the directory */
#define RADIO_MAX 200                   /* stations, countries or genres a list holds */
#define FETCH_MAX (256 << 10)
#define RADIO_POLL_MS 500
#define RADIO_QUIT_MS 1500 /* q2video's own exit, before it is killed */

/* A list's text, cut into strings in place; name and url (a country's code or a genre for those
 * lists) are offsets into it. */
typedef struct {
    char *buf;
    unsigned len;
    int n, truncated;
    unsigned name[RADIO_MAX + 1], url[RADIO_MAX + 1];
} list_t;

/* The line at s cut off; returns the next one's start. */
static char *line(char *s) {
    while (*s && *s != '\n' && *s != '\r') ++s;
    if (*s) *s++ = 0;
    return s;
}

/* An .m3u's or .pls's stations in buf, at most max: #EXTINF names the next URL, a .pls's FileN= is
 * a URL and TitleN= names FileN regardless of their order. A line without "://" is no station. */
int playlist(char *buf, unsigned *name, unsigned *url, int max) {
    int n = 0;
    char *end = buf + strlen(buf);
    unsigned named = 0; /* 1 + the pending #EXTINF name's offset */
    for (char *s = buf, *next; *s; s = next) {
        next = line(s);
        while (*s == ' ' || *s == '\t') ++s;
        char *v = strstr(s, "="), *comma = strstr(s, ",");
        int file = v && !strncasecmp(s, "File", 4);
        if (!strncasecmp(s, "#EXTINF:", 8))
            named = comma && comma[1] ? (unsigned)(comma + 1 - buf) + 1 : 0;
        else if (n < max && *s != '#' && !(v && !strncasecmp(s, "Title", 5)) &&
                 strstr(file ? v : s, "://")) {
            url[n] = (unsigned)((file ? v + 1 : s) - buf);
            name[n] = named ? named - 1 : url[n];
            named = 0, ++n;
        }
    }
    for (char *s = buf; s < end; s += strlen(s) + 1) {
        while (*s == ' ' || *s == '\t') ++s;
        char *v = strstr(s, "=");
        if (!v || strncasecmp(s, "Title", 5) || v <= s + 5 || !v[1]) continue;
        for (int i = 0; i < n; ++i) {
            char *f = buf + url[i];
            while (f > buf && f[-1]) --f;
            while (*f == ' ' || *f == '\t') ++f;
            if (!strncasecmp(f, "File", 4) && !strncasecmp(f + 4, s + 5, (unsigned)(v - s - 4)))
                name[i] = (unsigned)(v + 1 - buf); /* through the '=': FileN= for TitleN= */
        }
    }
    return n;
}

/* radio-browser's CSV in buf (a header, then name,stationcount or name,iso_3166_1,stationcount;
 * a name may be quoted and hold commas), at most max: name, and key the code (coded) or the name.
 */
int choices(char *buf, unsigned *name, unsigned *key, int max, int coded) {
    int n = 0;
    for (char *s = line(buf) /* past the header */, *next; *s && n < max; s = next) {
        next = line(s);
        char *c = strrchr(s, ','), *k = s;
        if (!c) continue;
        *c = 0; /* the count */
        if (coded && (c = strrchr(s, ','))) *c = 0, k = c + 1;
        if (*s == '"' && (c = strrchr(s + 1, '"'))) *c = 0, ++s;
        if (!coded) k = s;
        if (!*s || !*k) continue;
        name[n] = (unsigned)(s - buf);
        key[n++] = (unsigned)(k - buf);
    }
    return n;
}

/* s %-encoded into out (n bytes) as a URL's path segment. */
void url_escape(const char *s, char *out, unsigned n) {
    static const char hex[] = "0123456789ABCDEF";
    for (; *s && n > 3; ++s) {
        unsigned char c = (unsigned char)*s;
        if (((c | 32) >= 'a' && (c | 32) <= 'z') || (c >= '0' && c <= '9') || c == '-' ||
            c == '_' || c == '.' || c == '~')
            *out++ = (char)c, --n;
        else
            *out++ = '%', *out++ = hex[c >> 4], *out++ = hex[c & 15], n -= 3;
    }
    *out = 0;
}

#ifndef PEQ_HOST
static const char STARTERS[] =
    "#EXTM3U\n"
    "#EXTINF:-1,Radio Paradise\nhttp://stream.radioparadise.com/mp3-128\n"
    "#EXTINF:-1,SomaFM Groove Salad\nhttp://ice1.somafm.com/groovesalad-128-mp3\n"
    "#EXTINF:-1,BBC World Service\nhttp://stream.live.vc.bbcmedia.co.uk/bbc_world_service\n"
    "#EXTINF:-1,KEXP\nhttps://kexp-mp3-128.streamguys1.com/kexp128.mp3\n"
    "#EXTINF:-1,FIP\nhttp://icecast.radiofrance.fr/fip-midfi.mp3\n"
    "#EXTINF:-1,NTS 1\nhttps://stream-relay-geo.ntslive.net/stream\n";

/* The player; kept for the life of demo. active: Internet Radio was the last thing played, so the
 * media keys are its, stopped or not. */
static struct {
    int pid, sock, active, at;
    unsigned polled, volume; /* volume: g_volume + 1 as q2video last had it */
    list_t list;             /* the list played from, side buttons step through it */
    char state[12], title[256], codec[16], bitrate[16], error[64]; /* error: q2video's ALSA reason */
    unsigned long long since; /* q2video's at=: when the sound started, 0 before */
    unsigned serial;          /* bumped by each start and favourites change: Now Playing's heart */
} rd __attribute__((section(".scratch")));

/* The directory fetch, on its pthread. */
static struct {
    unsigned long thread;
    int running;
    volatile int cancel, done;
    long code;
    char *body;
    unsigned n;
    char url[320];
} fx __attribute__((section(".scratch")));

enum { MENU, PICK, STATIONS };
enum { R_PLAYING, R_FAVS, R_FEATURED, R_TOP, R_COUNTRY, R_GENRE, A_PICK, A_SEARCH, A_SHOW };

/* The lists page and the Now Playing page, while open. note: a caption over no rows (loading or a
 * failed fetch) stands in for level's list; want is the level a fetch fills. */
static struct {
    void *page, *view, *edit; /* edit: the pull-down's search box */
    unsigned timer, go, leave;
    int level, next, arg, want, note, genre, favs, from, rows[6], sel[3];
    list_t pick, list;
    char caption[64];
} ui __attribute__((section(".scratch")));

static struct {
    void *page, *station, *artist, *song, *info, *elapsed, *heart;
    unsigned timer, leave, seen; /* seen: rd.serial the heart shows */
} np __attribute__((section(".scratch")));

static void list_free(list_t *l) {
    free(l->buf);
    memset(l, 0, sizeof *l);
}

/* len bytes of text into l, a copy, parsed as a playlist or (pick) radio-browser's CSV. */
static void list_set(list_t *l, const char *text, unsigned len, int pick, int coded) {
    list_free(l);
    if (!(l->buf = calloc(len + 1, 1))) return;
    memcpy(l->buf, text, len);
    l->len = len;
    l->n = pick ? choices(l->buf, l->name, l->url, RADIO_MAX, coded)
                : playlist(l->buf, l->name, l->url, RADIO_MAX + 1);
    l->truncated = l->n > RADIO_MAX;
    if (l->truncated) l->n = RADIO_MAX;
}

static void list_copy(list_t *to, const list_t *from) {
    list_free(to);
    *to = *from;
    if ((to->buf = calloc(from->len + 1, 1)))
        memcpy(to->buf, from->buf, from->len);
    else
        to->n = 0;
}

static const char *name_of(const list_t *l, int i) { return l->buf + l->name[i]; }
static const char *url_of(const list_t *l, int i) { return l->buf + l->url[i]; }

/* path's bytes appended to buf at *n, below cap, then a newline. */
static int slurp(const char *path, char *buf, unsigned *n, unsigned cap) {
    void *f = *n + 2 <= cap ? fopen(path, "rb") : 0; /* full: cap - *n - 2 would wrap */
    if (!f) return access(path, 0) != 0;
    *n += fread(buf + *n, 1, cap - *n - 2, f);
    char extra;
    int ok = !fread(&extra, 1, 1, f) && !ferror(f);
    buf[(*n)++] = '\n';
    fclose(f);
    return ok;
}

/* Favourites into l: favourites.m3u, then (all) every other .m3u and .pls in RADIO_DIR; 0 when
 * there was no memory to read them. */
static int favs_read(list_t *l, int all) {
    char *buf = calloc(FETCH_MAX, 1), path[320];
    unsigned n = 0;
    list_free(l);
    if (!buf) return 0;
    int ok = slurp(RADIO_FAVS, buf, &n, FETCH_MAX);
    void *dir = all ? opendir(RADIO_DIR) : (void *)0;
    for (struct dirent *e; dir && (e = readdir(dir));) {
        const char *dot = strrchr(e->d_name, '.');
        if (e->d_name[0] == '.' || !dot || (strcasecmp(dot, ".m3u") && strcasecmp(dot, ".pls")) ||
            !strcasecmp(e->d_name, "favourites.m3u"))
            continue;
        tk_snprintf(path, sizeof path, RADIO_DIR "/%s", e->d_name);
        slurp(path, buf, &n, FETCH_MAX);
    }
    if (dir) closedir(dir);
    ok = ok && strlen(buf) == n; /* an embedded NUL would hide the remaining favourites */
    list_set(l, buf, n, 0, 0);
    free(buf);
    return l->buf != 0 && ok;
}

/* An .m3u at path, written aside and renamed: l's stations but skip, then more's station at. */
static int m3u_write(const char *path, const list_t *l, int skip, const list_t *more, int at) {
    char tmp[320], line[1024];
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    void *f = fopen(tmp, "wb");
    int ok = f && fwrite("#EXTM3U\n", 8, 1, f) == 1;
    for (int i = 0; ok && i <= l->n; ++i) {
        const list_t *from = i < l->n ? l : more;
        int k = i < l->n ? i : at, len = 0;
        if (i == skip || !from) continue;
        len = tk_snprintf(line, sizeof line, "#EXTINF:-1,%s\n%s\n", name_of(from, k),
                          url_of(from, k));
        ok = len > 0 && len < (int)sizeof line && fwrite(line, (unsigned)len, 1, f) == 1;
    }
    if (f && (fflush(f) || fsync(fileno(f)))) ok = 0;
    if (f && fclose(f)) ok = 0;
    return ok && !rename(tmp, path);
}


static void send_key(const char *c, unsigned n) {
    dgram_send(&rd.sock, RADIO_SOCK, c, n);
}

/* q2video quit, and killed if it does not go within RADIO_QUIT_MS. The media keys stay radio's. */
static void radio_halt(void) {
    if (!rd.pid) return;
    int status, done;
    unsigned start = time_now_ms();
    send_key("q", 1);
    while (!(done = waitpid(rd.pid, &status, 1)) && time_now_ms() - start < RADIO_QUIT_MS)
        sleep_ms(20);
    if (!done) {
        char kill[24]; /* this helper only: librespot's sink is a q2video too (demo imports no kill) */
        tk_snprintf(kill, sizeof kill, "kill -9 %d", rd.pid);
        system(kill);
        waitpid(rd.pid, &status, 0);
    }
    rd.pid = 0;
    unlink(RADIO_STATE);
    tk_snprintf(rd.state, sizeof rd.state, "stopped");
}

/* Local music, Videos or Spotify takes the output: radio stops, and the keys are theirs. */
void radio_stop(void) {
    radio_halt();
    rd.active = 0;
}

/* rd.list's station i (round the ends): q2video started on the output in use, after librespot let
 * go, and the station kept in RADIO_LAST. */
static void radio_play(int i) {
    headset_manual();
    int n = rd.list.n;
    if (!n) return;
    rd.at = (i % n + n) % n;
    radio_halt();
    spot_yield();
    rd.pid = video_start(url_of(&rd.list, rd.at), 1);
    rd.active = 1;
    rd.volume = headset_gain() + 1u;
    rd.since = 0;
    rd.title[0] = rd.codec[0] = rd.bitrate[0] = rd.error[0] = 0;
    ++rd.serial;
    tk_snprintf(rd.state, sizeof rd.state, rd.pid ? "connecting" : "error");
    list_t none = { 0 };
    m3u_write(RADIO_LAST, &none, -1, &rd.list, rd.at);
}

/* ringnav_sleep, every UI loop pass: nothing while no station plays; then, paced to RADIO_POLL_MS,
 * its state and its end. While it runs, standby and auto power-off (not the screen) are held, the
 * DAC kept on, and the volume passed on for Bluetooth's and USB's soft volume. */
void radio_poll(void) {
    unsigned now = time_now_ms();
    if (!rd.pid || now - rd.polled < RADIO_POLL_MS) return;
    rd.polled = now;
    int status, ended = waitpid(rd.pid, &status, 1) != 0;
    char raw[512]; /* behind a '\n', as field() reads */
    if (state_read(RADIO_STATE, raw, sizeof raw)) {
        field(raw, "state", rd.state, sizeof rd.state);
        field(raw, "title", rd.title, sizeof rd.title);
        field(raw, "codec", rd.codec, sizeof rd.codec);
        field(raw, "bitrate", rd.bitrate, sizeof rd.bitrate);
        field(raw, "error", rd.error, sizeof rd.error);
        rd.since = number(raw, "at");
    }
    if (ended) {
        rd.pid = 0;
        if (tk_strcmp(rd.state, "error")) tk_snprintf(rd.state, sizeof rd.state, "stopped");
        return;
    }
    reset_poweroptions_timer(1, 1, 0);
    if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
    if (rd.volume != headset_gain() + 1u) {
        char v[2] = { 'v', (char)headset_gain() };
        rd.volume = headset_gain() + 1u;
        send_key(v, 2);
    }
}

/* ringnav(), any page, after stock's key-lock filter: while Internet Radio was the last thing
 * played, Play/Pause stops or restarts the station and the side buttons step through its list. */
int radio_media(unsigned key) {
    if (!rd.active || (key != KEY_PLAY && key != KEY_FWD_BTN && key != KEY_BACK_BTN)) return 0;
    headset_manual();
    if (key == KEY_PLAY && rd.pid)
        radio_halt();
    else
        radio_play(rd.at + (key == KEY_FWD_BTN) - (key == KEY_BACK_BTN));
    return 1;
}

/* A label's text, written only when it changes, so a scrolling one keeps its place. */
static void set(void *w, const char *s) {
    if (text_differs(w, s)) widget_set_text_utf8(w, s);
}

static void later(int next, int arg);

/* url's index in favs, else -1. */
static int fav_index(const list_t *favs, const char *url) {
    for (int k = 0; k < favs->n; ++k)
        if (!tk_strcmp(url_of(favs, k), url)) return k;
    return -1;
}

/* l's station i out of favourites.m3u when it is there, else appended; the toast's text. */
static const char *fav_toggle(const list_t *l, int i) {
    list_t favs = { 0 };
    int ok = favs_read(&favs, 0), found = fav_index(&favs, url_of(l, i));
    if (favs.truncated || (found < 0 && favs.n == RADIO_MAX)) ok = 0;
    ok = ok && m3u_write(RADIO_FAVS, &favs, found, found < 0 ? l : 0, i);
    list_free(&favs);
    ++rd.serial;
    if (ok && ui.favs) later(R_FAVS, 0);
    return !ok         ? "无法保存收藏"
           : found < 0 ? "已加入收藏"
                       : "已取消收藏";
}

/* The Now Playing page from rd: the station, the StreamTitle as artist - title, the stream, and the
 * heart, favourites.m3u read only when rd.serial says the station or the file changed. */
static void np_refresh(void) {
    char artist[256], t[96];
    const char *dash = strstr(rd.title, " - "), *song = dash ? dash + 3 : rd.title;
    unsigned a = dash ? (unsigned)(dash - rd.title) : 0;
    memcpy(artist, rd.title, a);
    artist[a] = 0;
    set(np.station, rd.list.n ? name_of(&rd.list, rd.at) : "");
    set(np.artist, artist);
    if (np.seen != rd.serial && rd.list.n) {
        list_t favs = { 0 };
        favs_read(&favs, 0);
        np.seen = rd.serial;
        image_base_set_image(np.heart, fav_index(&favs, url_of(&rd.list, rd.at)) < 0 ? "play_unfav"
                                                                                     : "play_fav");
        list_free(&favs);
    }
    set(np.song, song);
    if (!rd.pid)
        tk_snprintf(t, sizeof t,
                    tk_strcmp(rd.state, "error") ? "已停止"
                    : rd.error[0]                ? "无法播放：%s"
                                                 : "无法播放此电台",
                    rd.error);
    else if (!rd.since)
        tk_snprintf(t, sizeof t, "连接中…");
    else
        tk_snprintf(t, sizeof t, rd.bitrate[0] ? "%s  %s kbps" : "%s", rd.codec, rd.bitrate);
    set(np.info, t);
    t[0] = 0;
    if (rd.pid && rd.since)
        toolsTimeItoa(t, (int)((unsigned)(now_ms() - rd.since) / 1000)); /* 32-bit: 49 days */
    set(np.elapsed, t);
}

static int np_tick(const void *unused) {
    if (g_backlight_status) np_refresh();
    return 8; /* RET_REPEAT */
}

static int np_leave(const void *unused) {
    np.leave = 0;
    navigator_back();
    return 0;
}

static int np_keyup(void *ctx, void *event) {
    return return_key(event, &np.leave, np_leave);
}

static int np_closed(void *ctx, void *event) {
    stop_timer(&np.timer);
    stop_timer(&np.leave);
    memset(&np, 0, sizeof np);
    return 0;
}

/* The heart: the station playing in or out of favourites.m3u. */
static int np_heart(void *ctx, void *event) {
    if (rd.list.n) toast(fav_toggle(&rd.list, rd.at));
    return 0;
}

/* Spotify's Now Playing layout, the radio's icon for the art, and local Now Playing's heart. The wheel and Centre stay stock's:
 * the volume, and the screen. */
static void np_open(void) {
    if (np.page || !(np.page = page_open("radionp_page", np_closed, np_keyup))) return;
    void *page = np.page;
    widget_set_text_utf8(label(page, 16, 12, 200, 16, "s_scrlabel_white20l", 16, SPOT_GREY),
                         "网络电台");
    void *art = image_create(page, SPOT_ART_X, SPOT_ART_Y, SPOT_ART_PX, SPOT_ART_PX);
    image_set_draw_type(art, IMAGE_DRAW_SCALE_DOWN);
    image_base_set_image(art, "stream_radio");
    np.station =
        label(page, SPOT_TEXT_X, SPOT_ART_Y + 41, SPOT_TEXT_W, 28, "s_scrlabel_white20l", NP_TITLE_PX, 0xffffffffu);
    np.artist = label(page, SPOT_TEXT_X, SPOT_ART_Y + 75, SPOT_TEXT_W, 22, "s_scrlabel_white20l", NP_META_PX,
                      IPOD ? (0xff000000u | NP_ARTIST_RGB) : SPOT_GREY);
    np.song = label(page, SPOT_TEXT_X, SPOT_ART_Y + 103, SPOT_TEXT_W, 22, "s_scrlabel_white20l", NP_META_PX,
              IPOD ? (0xff000000u | NP_ALBUM_RGB) : SPOT_GREY);
    np.info = label(page, SPOT_TIME_X, SPOT_TIMES_Y, 375 - 2 * SPOT_TIME_X - 80, 16, "s_scrlabel_white20l", 14, SPOT_GREY);
    np.elapsed = label(page, 375 - SPOT_TIME_X - 80, SPOT_TIMES_Y, 80, 16, "s_scrlabel_white20r",
                       14, SPOT_GREY);
    /* local Now Playing's heart (tools/ipod.py img_fav) */
    np.heart =
        image_create(page, IPOD ? 303 : 320, IPOD ? SPOT_ART_Y + SPOT_ART_PX - 50 + NP_ICON_PAD : 176, 50, 50);
    image_set_draw_type(np.heart, IMAGE_DRAW_CENTER);
    widget_set_prop_str(np.heart, "clickable", "true");
    widget_on(np.heart, EVT_CLICK, np_heart, 0);
    np.seen = ~rd.serial;
    np.timer = timer_add(np_tick, 0, 500);
    np_refresh();
}

static void *fetch_worker(void *unused);
static int menu_click(void *ctx, void *event), pick_click(void *ctx, void *event),
    station_click(void *ctx, void *event);

/* The search box's OK, or its keyboard closed with text: the directory searched by name, Return then
 * going back to the menu or the country or genre list. An empty OK closes the keyboard with no other
 * event, so it is taken here too. */
static int searched(void *ctx, void *event) {
    char q[sizeof ui.caption];
    if (widget_get_text_utf8(ui.edit, q, sizeof q) || !q[0]) {
        const unsigned *text = widget_get_text(ui.edit);
        if (text && text[0]) toast("搜索内容过长");
        return later(A_SHOW, 0), 0;
    }
    snprintf(ui.caption, sizeof ui.caption, "%s", q);
    ui.favs = 0, ui.sel[STATIONS] = 0;
    if (ui.level != STATIONS) ui.from = ui.level;
    later(A_SEARCH, 0);
    return 0;
}

/* The box left without a search: the page as it was. */
static int search_left(void *ctx, void *event) {
    if (!ui.go) later(A_SHOW, 0);
    return 0;
}

/* navigation.c's pull-to-search, released on the radio page: peq_ui.c's T9 edit over the title,
 * its keyboard open. */
void radio_search(void) {
    if (!ui.page || ui.note || ui.edit) return;
    if (ui.view) ui.sel[ui.level] = widget_get_prop_int(ui.view, "_ringnav_index", 0);
    widget_destroy_children(ui.page), ui.view = 0; /* the box alone: the keyboard leaves no room for the rows */
    ui.edit = edit_create(ui.page, "text", 2, 26);
    widget_set_prop_str(ui.edit, "tips", "搜索电台");
    widget_on(ui.edit, EVT_VALUE_CHANGED, searched, 0);
    widget_on(ui.edit, EVT_IM_ACTION, searched, 0);
    widget_on(ui.edit, EVT_BLUR, search_left, 0);
    widget_set_focused(ui.edit, 1);
}

/* The page shows level's list, its row last chosen selected. */
static void show(int level) {
    static const char *const menu[] = { "正在播放", "收藏", "精选",
                                        "热门电台", "按国家", "按流派" };
    if (ui.view && !ui.note) ui.sel[ui.level] = widget_get_prop_int(ui.view, "_ringnav_index", 0);
    ui.level = level;
    ui.note = 0;
    ui.edit = 0;
    int n = 0;
    if (level == MENU) {
        for (int r = rd.list.n || !access(RADIO_LAST, 0) ? R_PLAYING : R_FAVS; r <= R_GENRE; ++r)
            ui.rows[n++] = r;
        ui.view = page_list(ui.page, ui.page, 0, "网络电台", n, 48);
        for (int i = 0; i < n; ++i) /* Now Playing plays; the rest open lists */
            if (ui.rows[i] == R_PLAYING)
                page_row_detail(ui.view, i, menu[ui.rows[i]], 0, menu_click);
            else
                page_row_into(ui.view, i, menu[ui.rows[i]], menu_click);
    } else {
        const list_t *l = level == PICK ? &ui.pick : &ui.list;
        n = l->n;
        ui.view = page_list(ui.page, ui.page, 0,
                            level == PICK ? ui.genre ? "流派" : "国家" : ui.caption, n, 48);
        for (int i = 0; i < n; ++i)
            if (level == PICK)
                page_row_into(ui.view, i, name_of(l, i), pick_click);
            else
                page_row_detail(ui.view, i, name_of(l, i), 0, station_click);
    }
    if (n) ringnav_select(ui.view, ui.sel[level] < n ? ui.sel[level] : n - 1, n);
}

/* A caption over no rows, in place of the list; Return leaves it for the list. */
static void note(const char *caption) {
    if (ui.view && !ui.note) ui.sel[ui.level] = widget_get_prop_int(ui.view, "_ringnav_index", 0);
    ui.note = 1;
    ui.view = ui.edit = 0;
    page_list(ui.page, ui.page, 0, caption, 0, 48);
}

static void fetch_cancel(void) {
    unsigned none = 0;
    worker_stop(fx.thread, &fx.running, &fx.cancel, &none);
    free(fx.body);
    fx.body = 0;
}

/* RADIO_API path fetched on fx's pthread for level want, "Loading…" meanwhile. */
static void fetch(const char *path, int want) {
    fetch_cancel();
    ui.want = want;
    ui.sel[want] = 0;
    tk_snprintf(fx.url, sizeof fx.url, RADIO_API "%s", path);
    fx.n = 0;
    fx.cancel = fx.done = 0;
    fx.code = 0;
    fx.running = get_wifisignal() > 0 && (fx.body = calloc(FETCH_MAX, 1)) &&
                 !pthread_create(&fx.thread, 0, fetch_worker, 0);
    note(fx.running ? "加载中…" : get_wifisignal() > 0 ? "电台目录不可用" : "未连接 Wi-Fi");
}

static unsigned fetch_got(const char *data, unsigned size, unsigned n, void *unused) {
    if (fx.cancel || fx.n + size * n >= FETCH_MAX) return 0; /* stops the transfer */
    memcpy(fx.body + fx.n, data, size * n);
    fx.n += size * n;
    return size * n;
}

static int fetch_progress(void *u, long long a, long long b, long long c, long long d) {
    return fx.cancel; /* Return while loading: the transfer ends within a second */
}

static void *fetch_worker(void *unused) {
    void *c = curl_easy_init();
    if (c) {
        curl_easy_setopt(c, 10002, fx.url);         /* CURLOPT_URL */
        curl_easy_setopt(c, 20011, fetch_got);      /* WRITEFUNCTION */
        curl_easy_setopt(c, 20219, fetch_progress); /* XFERINFOFUNCTION */
        curl_easy_setopt(c, 43, 0L);                /* NOPROGRESS off, for it */
        curl_easy_setopt(c, 99, 1L);                /* NOSIGNAL: off the UI thread */
        curl_easy_setopt(c, 52, 1L);                /* FOLLOWLOCATION */
        curl_easy_setopt(c, 78, 10L);               /* CONNECTTIMEOUT */
        curl_easy_setopt(c, 13, 30L);               /* TIMEOUT */
        curl_easy_setopt(c, 10018, "Q2Pod");        /* USERAGENT, as the directory asks */
        curl_easy_setopt(c, 10065, RADIO_CA);       /* CAINFO */
        curl_easy_setopt(c, 64, 1L);                /* SSL_VERIFYPEER */
        curl_easy_setopt(c, 81, 2L);                /* SSL_VERIFYHOST */
        curl_easy_setopt(c, 20108, tls_untimed);    /* SSL_CTX_FUNCTION: no date checks */
        if (!curl_easy_perform(c)) curl_easy_getinfo(c, 0x200002, &fx.code); /* RESPONSE_CODE */
        curl_easy_cleanup(c);
    }
    fx.done = 1;
    return 0;
}

/* The page's timer: a finished fetch becomes ui.want's list. */
static int tick(const void *unused) {
    if (!fx.running || !fx.done) return 8; /* RET_REPEAT */
    pthread_join(fx.thread, 0);
    fx.running = 0;
    if (fx.code == 200 && fx.n) {
        list_t *l = ui.want == PICK ? &ui.pick : &ui.list;
        list_set(l, fx.body, fx.n, ui.want == PICK, !ui.genre);
        if (l->n)
            show(ui.want);
        else
            note("暂无电台");
    } else
        note(get_wifisignal() > 0 ? "电台目录不可用" : "未连接 Wi-Fi");
    free(fx.body);
    fx.body = 0;
    return 8;
}

/* A row's choice, on a timer: a row's click may not rebuild the list it is in. */
static int go(const void *unused) {
    char key[128], path[256];
    int i = ui.arg;
    ui.go = 0;
    if (ui.next == R_FAVS) {
        favs_read(&ui.list, 1);
        tk_snprintf(ui.caption, sizeof ui.caption,
                    ui.list.n ? "收藏" : "暂无收藏");
        show(STATIONS);
    } else if (ui.next == R_FEATURED) {
        list_set(&ui.list, STARTERS, sizeof STARTERS - 1, 0, 0);
        tk_snprintf(ui.caption, sizeof ui.caption, "精选");
        show(STATIONS);
    } else if (ui.next == A_SHOW) {
        show(ui.level);
    } else if (ui.next == A_SEARCH) {
        url_escape(ui.caption, key, sizeof key);
        tk_snprintf(path, sizeof path, "/m3u/stations/byname/%s" RADIO_QUERY, key);
        fetch(path, STATIONS);
    } else if (ui.next == R_TOP) {
        tk_snprintf(ui.caption, sizeof ui.caption, "热门电台");
        fetch("/m3u/stations/topvote/100?hidebroken=true", STATIONS);
    } else if (ui.next != A_PICK) {
        ui.genre = ui.next == R_GENRE;
        fetch(ui.genre ? "/csv/tags?order=stationcount&reverse=true&limit=60"
                       : "/csv/countries?order=stationcount&reverse=true&limit=60",
              PICK);
    } else {
        url_escape(url_of(&ui.pick, i), key, sizeof key);
        tk_snprintf(path, sizeof path,
                    ui.genre ? "/m3u/stations/bytagexact/%s" RADIO_QUERY
                             : "/m3u/stations/bycountrycodeexact/%s" RADIO_QUERY,
                    key);
        tk_snprintf(ui.caption, sizeof ui.caption, "%s", name_of(&ui.pick, i));
        fetch(path, STATIONS);
    }
    return 0;
}

static void later(int next, int arg) {
    ui.next = next, ui.arg = arg;
    if (ui.page) rearm(&ui.go, go, 0); /* not once closed: the box's blur can come after */
}

static int menu_click(void *ctx, void *event) {
    int r = ui.rows[(int)(long)ctx];
    if (r != R_PLAYING) {
        ui.favs = r == R_FAVS, ui.from = MENU;
        ui.sel[STATIONS] = 0;
        later(r, 0);
        return 0;
    }
    if (!rd.list.n) { /* RADIO_LAST, after a restart */
        char buf[1024];
        unsigned n = 0;
        slurp(RADIO_LAST, buf, &n, sizeof buf);
        list_set(&rd.list, buf, n, 0, 0), rd.at = 0;
    }
    if (!rd.pid) radio_play(rd.at);
    np_open();
    return 0;
}

static int pick_click(void *ctx, void *event) {
    ui.favs = 0, ui.from = PICK;
    later(A_PICK, (int)(long)ctx);
    return 0;
}

static int station_click(void *ctx, void *event) {
    list_copy(&rd.list, &ui.list);
    radio_play((int)(long)ctx);
    np_open();
    return 0;
}

/* ringnav_keylong: Play/Pause held on a station, or on Now Playing for the one playing, adds it to
 * favourites.m3u, or takes it out when it is there. The toast's text, or 0 when top is neither. */
const char *radio_hold(void *top) {
    const list_t *l = &ui.list;
    int i = -1;
    if (np.page && top == np.page)
        l = &rd.list, i = rd.at;
    else if (ui.page && top == ui.page && !ui.note && ui.level == STATIONS && ui.view)
        i = widget_get_prop_int(ui.view, "_ringnav_index", -1);
    if (i < 0 || i >= l->n) return 0;
    return fav_toggle(l, i);
}

static int leave(const void *unused) {
    ui.leave = 0;
    if (ui.edit) /* the search box emptied, so no search; its blur shuts the keyboard, then the box */
        widget_set_text_utf8(ui.edit, ""), widget_set_focused(ui.edit, 0);
    else if (ui.note)
        fetch_cancel(), show(ui.level);
    else if (ui.level != MENU)
        show(ui.level == STATIONS ? ui.from : MENU);
    else
        navigator_back();
    return 0;
}

/* Return: the search box shut, else a list goes up a level, the menu back to Streaming. */
static int keyup(void *ctx, void *event) {
    return return_key(event, &ui.leave, leave);
}

static int closed(void *ctx, void *event) {
    fetch_cancel();
    stop_timer(&ui.timer);
    stop_timer(&ui.go);
    stop_timer(&ui.leave);
    list_free(&ui.pick);
    list_free(&ui.list);
    memset(&ui, 0, sizeof ui);
    return 0;
}

/* Streaming's Internet Radio row (navigation.c ringnav_stream). */
int radio_open(void *ctx, void *event) {
    if (ui.page || !(ui.page = page_open("radio_page", closed, keyup))) return 0;
    if (access(RADIO_DIR, 0)) mkdir(RADIO_DIR, 0777);
    ui.timer = timer_add(tick, 0, 250);
    show(MENU);
    return 0;
}
int radio_status(void) { return rd.active ? (rd.pid ? 2 : 3) : 0; }
unsigned radio_session(void) { return rd.serial; }
void radio_set_playing(int playing) {
    if (!rd.active) return;
    if (!playing)
        radio_halt();
    else if (!rd.pid)
        radio_play(rd.at);
}

#endif
