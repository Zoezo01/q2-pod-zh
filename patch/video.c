/* q2video, Videos' player (docs/internals.md#videos): /usr/bin/q2video DEVICE FILE [VOLUME],
 * started by demo (books.c play_video). Stock ffmpeg decodes FILE, fits it into the screen, turns
 * it onto the portrait panel as the boot logo is stored and converts it to the framebuffer's BGRA,
 * frames on one pipe and 48 kHz stereo on another; this plays the sound on ALSA DEVICE ("-"
 * for none; VOLUME, Bluetooth's or a soft USB volume's 0-100, scales it as hciplayer does there,
 * and "h" before it marks a USB DAC that applies the volume itself) and puts each frame on
 * /dev/fb0 when the sound reaches it, dropping late ones. demo sends keys as datagrams to
 * Q2VIDEO_SOCK: p pause, f and b seek SEEK_S, s seek mode, v and a byte the volume set, q quit;
 * the last four show a bar along the bottom for OVERLAY_MS, the position or the volume. It starts
 * at Q2VIDEO_PLACE's second, demo's, and leaves there resume_at's for a quit (0 for an end).
 * q2video -r DEVICE URL [VOLUME] plays Internet Radio (radio.c): the stream's sound alone, ffmpeg
 * restarted when it drops, and its report's ICY tags in RADIO_STATE; keys come on RADIO_SOCK, v
 * and q as above. q2video -s DEVICE VOLUME is librespot's sink on Bluetooth or a USB DAC
 * (spotify.c): stdin's 44.1 kHz sound, v and q on SINK_SOCK. No MIPS sysroot: the declarations
 * below are glibc 2.28's and alsa-lib's, with MIPS o32 constants. */
#define W 320 /* /dev/fb0: 320 x 375, 32 bpp, red at bit 16, two pages (docs/internals.md#videos) */
#define H 375
#define FPS 25
#define RATE 48000
#define CHUNK 512      /* frames per ALSA write: the audio clock's step, about 11 ms */
#define LATENCY 200000 /* ALSA buffer, us */
#define SEEK_S 10
#define SEEK_WAIT_MS 400 /* a run of wheel ticks restarts ffmpeg once */
#define OVERLAY_MS 1500
#define EDGE_S 30 /* a stop this near either end restarts at 0, as navigation.c's RESUME_EDGE_S */
#define BAR_X 40 /* the bar's ends from the picture's sides, clear of the 80 px glass corners */
#define BAR_Y 22 /* its bottom from the picture's */
#define BAR_H 8
#define Q2VIDEO_SOCK "/tmp/q2video.sock"
#define Q2VIDEO_PLACE "/tmp/q2video.place" /* an int, the second: books.c VIDEO_PLACE */
#define RADIO_SOCK "/tmp/q2radio.sock"
#define SINK_SOCK "/tmp/q2sink.sock" /* spotify.c SPOT_SINK */
#define SWAP_FILE "/tmp/q2swap"
#define MONO_FILE "/tmp/q2mono" /* peq.h's: an int, 1 for Mono Audio */
#define RADIO_STATE "/tmp/q2radio.state" /* key=value lines, as spotify.c reads librespot's */
#define RADIO_TRIES 5                       /* starts in a row without sound, then state=error */
#define RADIO_SILENT_MS 20000 /* a start without a sound written: the output is stuck, give up */
#define FFMPEG "/usr/bin/ffmpeg"
#define DAC "/dev/shanling_dac"
#define DAC_PCM 0xc0044d1bu  /* hciplayer sets it to 0 for each PCM track: undoes a DSD one */
#define DAC_MUTE 0xc0044d1fu /* hciplayer sets it on pause and close, clears it on start */

#ifdef PEQ_HOST /* test/peq.py */
#include <stdio.h>
#include <string.h>
#else
int snprintf(char *, unsigned, const char *, ...), sscanf(const char *, const char *, ...);
void *memcpy(void *, const void *, unsigned), *memset(void *, int, unsigned);
char *strstr(const char *, const char *);
unsigned strlen(const char *);
#endif

/* ffmpeg's argv into a (27 slots) for frames from second at; ss holds the start. The picture is
 * fitted into the landscape H x W view, then turned clockwise onto the portrait framebuffer. Without
 * audio there is no sound output. */
void ffmpeg_argv(const char **a, char *ss, int at, const char *file, int audio) {
    snprintf(ss, 16, "%d", at);
    const char *v[27] = { FFMPEG, "-nostdin", "-loglevel", "quiet", "-ss", ss, "-i", file,
                          "-map", "0:v:0", "-vf",
                          "scale=375:320:force_original_aspect_ratio=decrease:flags=fast_bilinear,"
                          "format=bgra,pad=375:320:(ow-iw)/2:(oh-ih)/2,transpose=clock",
                          "-r", "25", "-f", "rawvideo", "pipe:3",
                          "-map", "0:a:0", "-ac", "2", "-ar", "48000", "-f", "s16le", "pipe:4", 0 };
    memcpy(a, v, sizeof v);
    if (!audio) a[17] = 0;
}

/* hciplayer's Bluetooth soft volume (its mixer_setvolume, 0x428744) for demo's volume v, 0-100,
 * in 1/65536: 0.002 v under 50, then 0.1 + 0.018 (v - 50), 1 at 100. */
int bt_gain(int v) {
    v = v < 0 ? 0 : v > 100 ? 100 : v;
    return 65536 * (v < 50 ? 2 * v : 18 * v - 800) / 1000;
}

int video_pause(int paused, int command) {
    return command == 'p' ? !paused : command == 'a' ? 1 : command == 'r' ? 0 : paused;
}

/* n samples times gain g (at most 65536, so no sample overflows). */
void scale(short *s, unsigned n, int g) {
    for (unsigned i = 0; i < n; ++i) s[i] = (short)(s[i] * g >> 16);
}

/* Mono Audio: each of n stereo frames' channels their mean. */
void mono(short *s, unsigned n) {
    for (unsigned i = 0; i < 2 * n; i += 2) s[i] = s[i + 1] = (short)((s[i] + s[i + 1]) >> 1);
}

void swap(short *s, unsigned n) {
    for (unsigned i = 0; i < 2 * n; i += 2) {
        short left = s[i]; s[i] = s[i + 1]; s[i + 1] = left;
    }
}

/* The seconds in ffmpeg's "Duration: HH:MM:SS.ss" line; 0 without one. */
int duration(const char *s) {
    int h, m, sec;
    s = strstr(s, "Duration: ");
    return s && sscanf(s + 10, "%d:%d:%d", &h, &m, &sec) == 3 ? h * 3600 + m * 60 + sec : 0;
}

/* The second to start at after a quit at pos of len (0 unknown): 0 within EDGE_S of either end. */
int resume_at(int pos, int len) { return pos < EDGE_S || (len && pos > len - EDGE_S) ? 0 : pos; }

/* The overlay on framebuffer page f, line bytes a row: a bar along the picture's bottom, white for
 * n of total, black for the rest. The picture is turned clockwise (picture x is panel y), so the
 * bar runs down the panel's columns BAR_Y to BAR_Y + BAR_H. */
void overlay(unsigned char *f, unsigned line, int n, int total) {
    int end = H - BAR_X;
    if (total <= 0) return;
    n = n < 0 ? 0 : n > total ? total : n;
    int fill = BAR_X + (end - BAR_X) * n / total; /* total: seconds, far below overflow */
    for (int y = BAR_X; y < end; ++y) memset(f + y * line + BAR_Y * 4, y < fill ? 0xff : 0, BAR_H * 4);
}

/* Frame n's fate at clock ms: 0 wait, 1 show, 2 drop (a whole frame late). */
int frame_due(int n, long long clock) {
    long long due = (long long)n * 1000 / FPS;
    return clock < due ? 0 : clock < due + 1000 / FPS ? 1 : 2;
}

/* ffmpeg's report line s: key's value ("    icy-br          : 128", "Metadata update for
 * StreamTitle: A - B") into out, n bytes; 0 when the line has no key. */
int meta(const char *s, const char *key, char *out, unsigned n) {
    const char *v = strstr(s, key);
    unsigned len = 0;
    if (!v) return 0;
    for (v += strlen(key); *v == ' ';) ++v;
    if (*v++ != ':') return 0;
    while (*v == ' ') ++v;
    while (v[len] && v[len] != '\n' && v[len] != '\r' && len + 1 < n) ++len;
    memcpy(out, v, len);
    out[len] = 0;
    return 1;
}

#ifndef PEQ_HOST
struct timespec {
    long sec, nsec;
};
struct pollfd {
    int fd;
    short events, revents;
};
struct sockaddr_un {
    unsigned short family;
    char path[108];
};
int open(const char *, int, ...), close(int), read(int, void *, unsigned), ioctl(int, unsigned, ...);
int fork(void), execv(const char *, const char *const *), waitpid(int, int *, int), kill(int, int);
int dup2(int, int), pipe2(int *, int), fcntl(int, int, ...), poll(struct pollfd *, unsigned, int);
int socket(int, int, int), bind(int, const void *, unsigned), recv(int, void *, unsigned, int);
void *fopen(const char *, const char *);
unsigned fwrite(const void *, unsigned, unsigned, void *);
int fclose(void *);
int atoi(const char *), unlink(const char *), rename(const char *, const char *), write(int, const void *, unsigned), usleep(unsigned), clock_gettime(int, struct timespec *), strcmp(const char *, const char *);
int setenv(const char *, const char *, int);
void *mmap(void *, unsigned, int, int, int, long), *malloc(unsigned), (*signal(int, void (*)(int)))(int);
void _exit(int) __attribute__((noreturn));
int pthread_create(unsigned long *, const void *, void *(*)(void *), void *), pthread_join(unsigned long, void **);
int snd_pcm_open(void **, const char *, int, int), snd_pcm_close(void *), snd_pcm_drop(void *);
int snd_pcm_set_params(void *, int, int, unsigned, unsigned, int, unsigned), snd_pcm_prepare(void *);
int snd_pcm_recover(void *, int, int), snd_pcm_delay(void *, long *), snd_pcm_drain(void *);
const char *snd_strerror(int);
long snd_pcm_writei(void *, const void *, unsigned long);

#define O_RDWR 2
#define O_WRONLY 1
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_CLOEXEC 0x80000
#define SIGKILL 9
#define SIGCHLD 18
#define AF_UNIX 1
#define SOCK_DGRAM 1 /* MIPS swaps it with SOCK_STREAM */
#define F_SETPIPE_SZ 1031
#define F_SETFD 2
#define FBIOGET_VSCREENINFO 0x4600
#define FBIOGET_FSCREENINFO 0x4602
#define FBIOPAN_DISPLAY 0x4606

static struct {
    void *pcm;
    int fd;                /* ffmpeg's sound */
    int gain;              /* bt_gain's, 65536 on the DAC */
    volatile int paused;   /* the main loop's; the writer holds back */
    volatile int done;     /* the sound ended: the rest goes on the monotonic clock */
    volatile long played;  /* frames heard since this ffmpeg started */
    char error[64];        /* why sound_open found no device, or the writer stopped, for RADIO_STATE */
    int video;             /* a failed write is dropped: the picture needs ffmpeg's sound read on */
} au;

static void *writer(void *unused) {
    short buf[2 * CHUNK];
    long written = 0, delay;
    volatile int *mono_on = 0; /* demo's MONO_FILE, looked for every hundredth chunk till it appears */
    volatile int *swap_on = 0;
    for (int end = 0, tries = 0; !end;) {
        if (!swap_on && !(tries % 100)) {
            int fd = open(SWAP_FILE, 0);
            if (fd >= 0) {
                void *p = mmap(0, sizeof *swap_on, 1, 1, fd, 0);
                close(fd);
                if (p != (void *)-1) swap_on = p;
            }
        }
        if (!mono_on && !(tries % 100)) {
            int fd = open(MONO_FILE, 0); /* O_RDONLY */
            if (fd >= 0) {
                void *p = mmap(0, sizeof *mono_on, 1, 1, fd, 0); /* PROT_READ, MAP_SHARED */
                close(fd);
                if (p != (void *)-1) mono_on = p;
            }
        }
        ++tries;
        unsigned got = 0;
        while (got < sizeof buf) {
            int r = read(au.fd, (char *)buf + got, sizeof buf - got);
            if (r <= 0) { end = 1; break; } /* the short last block still plays */
            got += (unsigned)r;
        }
        unsigned frames = got / 4;
        if (!frames) break;
        while (au.paused) usleep(100000);
        if (au.gain < 65536) scale(buf, 2 * frames, au.gain);
        if (mono_on && *mono_on > 0) mono(buf, frames);
        if (swap_on && *swap_on > 0) swap(buf, frames);
        int recovered = 0;
        for (unsigned sent = 0; sent < frames;) {
            long r = snd_pcm_writei(au.pcm, buf + 2 * sent, frames - sent);
            if (r < 0) {
                int err = recovered ? (int)r : snd_pcm_recover(au.pcm, (int)r, 1);
                if (!err) { recovered = 1; continue; }
                if (!au.video) /* a lost device ends the sink, or radio's run, which then says why */
                    return snprintf(au.error, sizeof au.error, "write: %s", snd_strerror(err)),
                           au.done = 1, (void *)0;
                break;
            }
            if (!r) { usleep(1000); continue; }
            sent += (unsigned)r;
            written += r;
            recovered = 0;
        }
        au.played = !snd_pcm_delay(au.pcm, &delay) && delay > 0 && delay < written ? written - delay
                                                                                 : written;
    }
    return au.done = 1, (void *)0; /* played stays behind the buffer, as the picture's clock wants */
}

/* fd onto at for ffmpeg: dup2 of an fd onto itself keeps O_CLOEXEC, so exec would close it (demo
 * may start us without stdio, which puts a pipe on 4 already). */
static void move_fd(int fd, int at) {
    dup2(fd, at);
    fcntl(at, F_SETFD, 0);
}

/* ffmpeg with args, a onto fd at_a and b (unless -1) onto at_b; its pid, -1 when fork failed. */
static int ffmpeg(const char **args, int a, int at_a, int b, int at_b) {
    int pid = fork();
    if (!pid) {
        move_fd(a, at_a);
        if (b >= 0) move_fd(b, at_b);
        execv(FFMPEG, args);
        _exit(127);
    }
    return pid;
}

/* A datagram socket bound at path, a stale one unlinked first. */
static int dgram_listen(const char *path) {
    int sock = socket(AF_UNIX, SOCK_DGRAM | O_CLOEXEC, 0);
    struct sockaddr_un addr = { AF_UNIX, { 0 } };
    memcpy(addr.path, path, strlen(path) + 1);
    unlink(path);
    bind(sock, &addr, sizeof addr);
    return sock;
}

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(1, &t); /* CLOCK_MONOTONIC */
    return (long long)t.sec * 1000 + t.nsec / 1000000;
}

/* "usb", the USB DAC, as plughw:N,0: its card is the one /proc/asound/cards lists as "N [id]:
 * USB-Audio - name", 1 behind the headphone DAC's 0 (stock sets its volume on "hw:1"). */
static const char *usb_device(char *out) {
    char b[1024], *u;
    int fd = open("/proc/asound/cards", O_CLOEXEC), n = fd >= 0 ? read(fd, b, sizeof b - 1) : 0;
    if (fd >= 0) close(fd);
    b[n > 0 ? n : 0] = 0;
    if (!(u = strstr(b, ": USB-Audio - "))) return "plughw:1,0";
    while (u > b && u[-1] != '\n') --u;
    snprintf(out, 16, "plughw:%d,0", atoi(u));
    return out;
}

/* ALSA dev ("-" for none, "usb" for usb_device) into au.pcm at rate; Bluetooth or a USB DAC (a
 * volume, vol) get hciplayer's soft volume unless "h". The headphone DAC, plughw:0,0 (Spotify's
 * sink there with Mono Audio has an h volume), is readied: returns it, else -1; without a device,
 * au.error says which step failed and ALSA's reason. */
static int sound_open(const char *dev, const char *vol, unsigned rate) {
    int dac = -1, off = 0, err = 0, jack;
    char usb[16];
    if (!strcmp(dev, "usb")) dev = usb_device(usb);
    jack = !strcmp(dev, "plughw:0,0");
    au.gain = vol && vol[0] != 'h' ? bt_gain(atoi(vol)) : 65536;
    if (strcmp(dev, "-")) {
        /* hciplayer lets go of the device, muting the DAC, a moment after demo's stop; bluealsa
         * (one client a PCM) can take longer, so Bluetooth and USB get 5 s */
        for (int i = 0; i < (jack ? 20 : 50) && (err = snd_pcm_open(&au.pcm, dev, 0, 0)); ++i)
            au.pcm = 0, usleep(100000);
        if (err) snprintf(au.error, sizeof au.error, "open: %s", snd_strerror(err));
        /* bluealsa claims its PCM here, not at open: busy (-EBUSY) while hciplayer holds it */
        for (int i = 0; au.pcm && (err = snd_pcm_set_params(au.pcm, 2, 3, 2, rate, 1, LATENCY)) == -16 &&
                        !jack && i < 50; ++i) /* S16_LE, RW_INTERLEAVED */
            usleep(100000);
        if (au.pcm && err)
            snd_pcm_close(au.pcm), au.pcm = 0,
                snprintf(au.error, sizeof au.error, "params: %s", snd_strerror(err));
        if (au.pcm && jack && (dac = open(DAC, O_RDWR | O_CLOEXEC)) >= 0)
            ioctl(dac, DAC_PCM, &off), ioctl(dac, DAC_MUTE, &off);
    }
    return dac;
}

static struct {
    char title[256], codec[16], bitrate[16];
    long long at; /* CLOCK_MONOTONIC ms the sound started, 0 before */
} rs;

/* RADIO_STATE, whole: written aside and renamed, so demo never reads half of it. */
static void radio_state(const char *state) {
    char s[480];
    int n = snprintf(s, sizeof s, "state=%s\ntitle=%s\ncodec=%s\nbitrate=%s\nat=%lld\nerror=%s\n", state,
                     rs.title, rs.codec, rs.bitrate, rs.at, au.error),
        fd = open(RADIO_STATE ".tmp", O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return;
    write(fd, s, (unsigned)n < sizeof s ? (unsigned)n : sizeof s - 1);
    close(fd);
    rename(RADIO_STATE ".tmp", RADIO_STATE);
}

/* A key from demo: v sets the soft gain, q quits. */
static void radio_key(int sock, int soft, int *quit) {
    char k[2] = { 0 };
    if (recv(sock, k, 2, 0) <= 0) return;
    if (k[0] == 'v') au.gain = soft ? bt_gain((unsigned char)k[1]) : 65536;
    *quit |= k[0] == 'q';
}

/* -r: ffmpeg reads URL (argv[2]) with its ICY tags and reconnects as it can; when it ends anyway
 * it starts again, after a second more each time, until RADIO_TRIES starts in a row bring no
 * sound. Its report (stderr) gives the codec, bitrate and each StreamTitle. q quits, unlinking
 * the state; giving up leaves state=error. */
static int radio(int argc, char **argv) {
    radio_state("connecting");
    int soft = argc > 3 && argv[3][0] != 'h', dac = sound_open(argv[1], argc > 3 ? argv[3] : 0, RATE);
    int on = 1, quit = 0, tries = 0;
    int sock = dgram_listen(RADIO_SOCK);
    while (au.pcm && !quit && tries++ < RADIO_TRIES) {
        int ap[2], ep[2] = { -1, -1 }, len = 0;
        if (pipe2(ap, O_CLOEXEC) || pipe2(ep, O_CLOEXEC)) break;
        /* verbose: libavformat's http logs each StreamTitle there, not at info */
        const char *args[] = { FFMPEG, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "verbose",
                               "-icy", "1", "-reconnect", "1", "-reconnect_streamed", "1", "-rw_timeout",
                               "15000000", "-i", argv[2], "-vn", "-ac", "2", "-ar", "48000", "-f", "s16le",
                               "pipe:4", 0 };
        rs.at = 0;
        radio_state("connecting");
        int pid = ffmpeg(args, ap[1], 4, ep[1], 2);
        close(ap[1]), close(ep[1]);
        unsigned long thread = 0;
        au.fd = ap[0];
        au.played = au.done = 0;
        if (pid < 0 || pthread_create(&thread, 0, writer, 0)) thread = 0;
        char line[512], c;
        long long started = now_ms();
        while (thread && !quit && !au.done) {
            struct pollfd p[2] = { { sock, 1, 0 }, { ep[0], 1, 0 } };
            poll(p, 2, 500);
            if (p[0].revents) radio_key(sock, soft, &quit);
            if (!rs.at && au.played > 0) rs.at = now_ms(), tries = 0, radio_state("playing");
            if (!rs.at && now_ms() - started > RADIO_SILENT_MS && !au.played) {
                /* the writer may be blocked in ALSA, past joining: say so and end here */
                if (!au.error[0]) snprintf(au.error, sizeof au.error, "no sound from the output");
                radio_state("error");
                unlink(RADIO_SOCK);
                if (pid > 0) kill(pid, SIGKILL);
                _exit(1);
            }
            if (!p[1].revents) continue;
            if (read(ep[0], &c, 1) <= 0) break; /* ffmpeg ended */
            if (c != '\n' && c != '\r') {
                if (len < (int)sizeof line - 1) line[len++] = c;
                continue;
            }
            line[len] = 0, len = 0;
            if (!rs.codec[0] && meta(line, "Audio", rs.codec, sizeof rs.codec)) {
                char *x = rs.codec; /* "mp3 (mp3float), 44100 Hz, ...": MP3 */
                for (; *x && *x != ' ' && *x != ','; ++x)
                    if (*x >= 'a' && *x <= 'z') *x -= 32;
                *x = 0;
            }
            if (meta(line, "StreamTitle", rs.title, sizeof rs.title) ||
                meta(line, "icy-br", rs.bitrate, sizeof rs.bitrate))
                radio_state(rs.at ? "playing" : "connecting");
        }
        if (pid > 0) kill(pid, SIGKILL), waitpid(pid, 0, 0);
        if (thread) pthread_join(thread, 0);
        close(ap[0]), close(ep[0]);
        snd_pcm_drop(au.pcm), snd_pcm_prepare(au.pcm);
        /* the wait before the next start, a key at a time */
        for (long long until = now_ms() + 1000 * tries; !quit && tries < RADIO_TRIES && now_ms() < until;) {
            struct pollfd p = { sock, 1, 0 };
            if (poll(&p, 1, 100) > 0) radio_key(sock, soft, &quit);
        }
    }
    if (quit)
        unlink(RADIO_STATE);
    else
        radio_state("error");
    if (au.pcm) snd_pcm_close(au.pcm);
    if (dac >= 0) ioctl(dac, DAC_MUTE, &on);
    unlink(RADIO_SOCK);
    return 0;
}

/* -s DEVICE VOLUME: librespot's sink off the headphone DAC (spotify.c, the card's aplay.sh): its
 * 44.1 kHz S16 stereo from stdin until it ends or the device fails, at the soft volume, with v and
 * q on SINK_SOCK as radio's. */
static int sink(char **argv) {
    int soft = argv[2][0] != 'h', quit = 0, sock = dgram_listen(SINK_SOCK);
    unsigned long thread = 0;
    sound_open(argv[1], argv[2], 44100);
    if (au.pcm && !pthread_create(&thread, 0, writer, 0)) /* au.fd 0: stdin */
        while (!au.done && !quit) {
            struct pollfd p = { sock, 1, 0 };
            if (poll(&p, 1, 500) > 0) radio_key(sock, soft, &quit);
        }
    /* an end, not a quit or a lost device: the buffer's last 200 ms still play (never paused in
     * ALSA, so the drain ends) */
    if (au.done && !quit && !au.error[0]) snd_pcm_drain(au.pcm);
    unlink(SINK_SOCK);
    return !au.pcm;
}

static void video_state(int paused) {
    void *f = fopen("/tmp/q2video.state.tmp", "wb");
    if (f) {
        int ok = fwrite(&paused, sizeof paused, 1, f) == 1;
        if (fclose(f)) ok = 0;
        if (ok) rename("/tmp/q2video.state.tmp", "/tmp/q2video.state");
    }
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    /* Stock ALSA's plug/prepare leaves a slave lock held; our PCM calls never overlap. */
    if (setenv("LIBASOUND_THREAD_SAFE", "0", 1)) return 1;
    for (int fd = 3; fd < 1024; ++fd) close(fd); /* demo's, inherited */
    signal(SIGCHLD, 0); /* SIG_DFL, so waitpid sees ffmpeg */
    if (argc > 3 && !strcmp(argv[1], "-r")) return radio(argc - 1, argv + 1);
    if (argc > 3 && !strcmp(argv[1], "-s")) return sink(argv + 1);
    unsigned var[40], fix[17];
    int fb = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fb < 0 || ioctl(fb, FBIOGET_VSCREENINFO, var) || ioctl(fb, FBIOGET_FSCREENINFO, fix)) return 1;
    unsigned line = fix[11], row = W * 4, y0 = var[5], back = y0 ? 0 : H; /* demo's other page */
    /* anything but the Q2's xres, yres, yres_virtual, bpp and red offset: no drawing */
    if (var[0] != W || var[1] != H || var[3] < 2 * H || var[6] != 32 || var[8] != 16 || row > line ||
        2 * H * line > fix[5])
        return 1;
    unsigned char *mem = mmap(0, fix[5], 3, 1, fb, 0), *frame = malloc(row * H);
    if (mem == (void *)-1 || !frame) return 1;
    int sock = dgram_listen(Q2VIDEO_SOCK);
    int soft = argc > 3 && argv[3][0] != 'h', dac = sound_open(argv[1], argc > 3 ? argv[3] : 0, RATE), on = 1;
    /* ffmpeg -i alone, meanwhile, for the length the position bar needs */
    char info[4096];
    int at = 0, audio = au.pcm != 0, first = 1, quit = 0, held = 0, pp[2] = { -1, -1 }, got = 0;
    const char *pa[] = { FFMPEG, "-nostdin", "-hide_banner", "-i", argv[2], 0 };
    int len = 0, vol = 0, pos_bar = 0, redraw = 0, probe = pipe2(pp, O_CLOEXEC) ? -1 : ffmpeg(pa, pp[1], 2, -1, 0);
    long long until = 0;
    close(pp[1]);
    int fd = open(Q2VIDEO_PLACE, O_CLOEXEC), place = 0;
    if (fd >= 0) read(fd, &at, sizeof at), close(fd);
    video_state(0);
    while (!quit) {
        int vp[2], ap[2] = { -1, -1 };
        if (pipe2(vp, O_CLOEXEC) || (audio && pipe2(ap, O_CLOEXEC))) break;
        fcntl(vp[0], F_SETPIPE_SZ, 4 << 20); /* frames ahead, so the sound paces ffmpeg */
        const char *args[27];
        char ss[16];
        ffmpeg_argv(args, ss, at, argv[2], audio);
        int pid = ffmpeg(args, vp[1], 3, audio ? ap[1] : -1, 4);
        close(vp[1]);
        unsigned long thread = 0;
        au.paused = held; /* before the writer starts, so a paused seek plays nothing */
        if (audio) {
            close(ap[1]);
            au.fd = ap[0];
            au.played = au.done = 0, au.video = 1;
            if (pthread_create(&thread, 0, writer, 0)) /* no reader: ffmpeg's sound write fails and it ends */
                thread = 0, close(ap[0]), ap[0] = -1;
        }
        int n = 0, seek = 0, ended = pid < 0, paced = audio && thread;
        unsigned have = 0, size = row * H;
        long long wall = 0, last = now_ms(), seek_at = 0;
        while (!ended && !quit) {
            long long t = now_ms(), clock;
            if (!au.paused) wall += t - last;
            last = t;
            if (paced && au.done) paced = 0, wall = (long long)au.played * 1000 / RATE;
            clock = paced ? (long long)au.played * 1000 / RATE : wall;
            if (until && t >= until) until = 0, redraw = au.paused;
            int wait = 100, fate = have == size ? frame_due(n, clock) : 0;
            if (fate == 1 || (redraw && have == size)) { /* paused, the next frame shows the change */
                for (unsigned y = 0; y < H; ++y)
                    memcpy(mem + (back + y) * line, frame + y * row, row);
                if (until)
                    overlay(mem + back * line, line, pos_bar ? at + seek + n / FPS : vol,
                            pos_bar ? len : 100);
                var[5] = back; /* draw the hidden page and pan, as demo does */
                ioctl(fb, FBIOPAN_DISPLAY, var);
                back = back ? 0 : H;
                redraw = 0;
            }
            if (fate) {
                ++n, have = 0;
                continue;
            }
            if (have == size && !au.paused) wait = (int)((long long)n * 1000 / FPS - clock) + 1;
            if (seek && seek_at - t < wait) wait = (int)(seek_at - t);
            if (seek && wait <= 0) break;
            /* POLLIN; poll skips the probe's -1 once it is read */
            struct pollfd p[3] = { { sock, 1, 0 }, { pp[0], 1, 0 }, { vp[0], 1, 0 } };
            int cap = au.paused && !until && !seek ? 1000 : 100; /* paused and still: wait on a key */
            poll(p, have < size ? 3 : 2, wait < cap ? wait : cap);
            char c[2] = { 0 };
            if (p[0].revents && recv(sock, c, 2, 0) > 0) {
                if (c[0] == 'p' || c[0] == 'a' || c[0] == 'r') { au.paused = video_pause(au.paused, c[0]);
                    video_state(au.paused);
                }
                if (c[0] == 'f' || c[0] == 'b') seek += c[0] == 'f' ? SEEK_S : -SEEK_S, seek_at = t + SEEK_WAIT_MS;
                if (c[0] == 'g') au.gain = soft ? bt_gain((unsigned char)c[1]) : 65536;
                if (c[0] == 'v') vol = (unsigned char)c[1], au.gain = soft ? bt_gain(vol) : 65536;
                if (c[0] && c[0] != 'p' && c[0] != 'a' && c[0] != 'r' && c[0] != 'g' && c[0] != 'q') /* s, f, b, v: the bar */
                    pos_bar = c[0] != 'v', until = t + OVERLAY_MS, redraw = au.paused;
                quit = c[0] == 'q';
            }
            if (p[1].revents) {
                int r = read(pp[0], info + got, sizeof info - 1 - (unsigned)got);
                if (r > 0) got += r;
                if (r <= 0 || got == sizeof info - 1) {
                    info[got] = 0, len = duration(info);
                    close(pp[0]), pp[0] = -1;
                    if (probe > 0) kill(probe, SIGKILL), waitpid(probe, 0, 0);
                }
            }
            if (have < size && p[2].revents) {
                int r = read(vp[0], frame + have, size - have);
                ended = r <= 0;
                if (r > 0) have += (unsigned)r;
            }
        }
        if (pid > 0) kill(pid, SIGKILL), waitpid(pid, 0, 0);
        held = au.paused && !quit; /* a seek keeps the pause, showing its first frame */
        au.paused = 0;             /* so the writer reads to the end and stops */
        if (thread) pthread_join(thread, 0);
        close(vp[0]);
        if (thread && !quit && !seek && !held) snd_pcm_drain(au.pcm); /* an end: the buffer plays out */
        if (audio) close(ap[0]), snd_pcm_drop(au.pcm), snd_pcm_prepare(au.pcm);
        place = quit ? resume_at(at + seek + n / FPS, len) : 0; /* a pending seek too */
        if (seek) {
            at += n / FPS + seek;
            at = at < 0 ? 0 : at;
        } else if (!n && first && audio)
            audio = 0; /* no sound stream: once more, silent, on the clock */
        else
            break;
        first = 0;
    }
    if (au.pcm) snd_pcm_close(au.pcm);
    if (dac >= 0) ioctl(dac, DAC_MUTE, &on); /* as hciplayer leaves it stopped */
    if (var[5] != y0) var[5] = y0, ioctl(fb, FBIOPAN_DISPLAY, var);
    fd = open(Q2VIDEO_PLACE, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) write(fd, &place, sizeof place), close(fd);
    unlink(Q2VIDEO_SOCK);
    unlink("/tmp/q2video.state");
    return 0;
}
#endif
