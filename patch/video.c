/* q2video, the sound helper (docs/internals.md#q2video): /usr/bin/q2video -r DEVICE URL [VOLUME]
 * plays Internet Radio (radio.c), started by demo (radio.c radio_start). Stock ffmpeg decodes URL
 * to 48 kHz stereo on a pipe, restarted when it drops, and its report's ICY tags go to RADIO_STATE;
 * this plays the sound on ALSA DEVICE ("-" for none; VOLUME, Bluetooth's or a soft USB volume's
 * 0-100, scales it as hciplayer does there, and "h" before it marks a USB DAC that applies the
 * volume itself). demo sends keys as datagrams to RADIO_SOCK: v and a byte the volume set, q quit.
 * q2video -s DEVICE VOLUME is librespot's sink on Bluetooth or a USB DAC (spotify.c): stdin's
 * 44.1 kHz sound, v and q on SINK_SOCK. No MIPS sysroot: the declarations below are glibc 2.28's
 * and alsa-lib's, with MIPS o32 constants. */
#define RATE 48000
#define CHUNK 512      /* frames per ALSA write, about 11 ms */
#define LATENCY 200000 /* ALSA buffer, us */
#define RADIO_SOCK "/tmp/q2radio.sock"
#define SINK_SOCK "/tmp/q2sink.sock" /* spotify.c SPOT_SINK */
#define SWAP_FILE "/tmp/q2swap"
#define MONO_FILE "/tmp/q2mono"          /* peq.h's: an int, 1 for Mono Audio */
#define RADIO_STATE "/tmp/q2radio.state" /* key=value lines, as spotify.c reads librespot's */
#define RADIO_TRIES 5                    /* starts in a row without sound, then state=error */
#define RADIO_SILENT_MS 20000 /* a start without a sound heard: the output is stuck, give up */
#define FFMPEG "/usr/bin/ffmpeg"
#define DAC "/dev/shanling_dac"
#define DAC_PCM 0xc0044d1bu  /* hciplayer sets it to 0 for each PCM track: undoes a DSD one */
#define DAC_MUTE 0xc0044d1fu /* hciplayer sets it on pause and close, clears it on start */

#ifdef PEQ_HOST /* test/peq.py */
#include <stdio.h>
#include <string.h>
#else
int snprintf(char *, unsigned, const char *, ...);
void *memcpy(void *, const void *, unsigned);
char *strstr(const char *, const char *);
unsigned strlen(const char *);
int strncmp(const char *, const char *, unsigned);
#endif

/* hciplayer's Bluetooth soft volume (its mixer_setvolume, 0x4287a4) for demo's volume v, 0-100,
 * in 1/65536: 0.002 v under 50, then 0.1 + 0.018 (v - 50), 1 at 100. */
int bt_gain(int v) {
    v = v < 0 ? 0 : v > 100 ? 100 : v;
    return 65536 * (v < 50 ? 2 * v : 18 * v - 800) / 1000;
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
        short left = s[i];
        s[i] = s[i + 1];
        s[i + 1] = left;
    }
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

/* out's first n bytes in place, short of a UTF-8 sequence cut at the end (meta has already left out
 * any line break). */
static void tag_text(char *out, unsigned n) {
    unsigned k = n;
    while (k && ((unsigned char)out[k - 1] & 0xc0) == 0x80) --k; /* its last sequence's lead */
    if (k && (unsigned char)out[k - 1] >= 0xc0) {
        unsigned lead = (unsigned char)out[k - 1], whole = lead >= 0xf0 ? 4 : lead >= 0xe0 ? 3 : 2;
        if (n - (k - 1) < whole) n = k - 1;
    }
    out[n] = 0;
}

/* A report line that is key's own ("      TITLE           : Song": ffmpeg prints an Ogg stream's
 * Vorbis comments with their keys in capitals) into out, n bytes, short of a UTF-8 sequence cut at
 * the end; 0 for any other line, such as SUBTITLE's or an MP3's "title". */
int tag_line(const char *s, const char *key, char *out, unsigned n) {
    unsigned k = strlen(key);
    while (*s == ' ') ++s;
    if (strncmp(s, key, k) || (s[k] != ' ' && s[k] != ':') || !meta(s, key, out, n)) return 0;
    tag_text(out, strlen(out));
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
int open(const char *, int, ...), close(int), read(int, void *, unsigned),
    ioctl(int, unsigned, ...);
int fork(void), execv(const char *, const char *const *), waitpid(int, int *, int), kill(int, int);
int dup2(int, int), pipe2(int *, int), fcntl(int, int, ...), poll(struct pollfd *, unsigned, int);
int socket(int, int, int), bind(int, const void *, unsigned), recv(int, void *, unsigned, int);
int atoi(const char *), unlink(const char *), rename(const char *, const char *),
    write(int, const void *, unsigned), usleep(unsigned), clock_gettime(int, struct timespec *),
    strcmp(const char *, const char *);
int setenv(const char *, const char *, int);
void *mmap(void *, unsigned, int, int, int, long), (*signal(int, void (*)(int)))(int);
void _exit(int) __attribute__((noreturn));
int pthread_create(unsigned long *, const void *, void *(*)(void *), void *),
    pthread_join(unsigned long, void **);
int snd_pcm_open(void **, const char *, int, int), snd_pcm_close(void *), snd_pcm_drop(void *);
int snd_pcm_set_params(void *, int, int, unsigned, unsigned, int, unsigned),
    snd_pcm_prepare(void *);
int snd_pcm_recover(void *, int, int), snd_pcm_delay(void *, long *), snd_pcm_drain(void *);
const char *snd_strerror(int);
long snd_pcm_writei(void *, const void *, unsigned long), snd_pcm_avail(void *);
int snd_pcm_get_params(void *, unsigned long *, unsigned long *);

#define O_RDWR 2
#define O_WRONLY 1
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_CLOEXEC 0x80000
#define SIGKILL 9
#define SIGCHLD 18
#define AF_UNIX 1
#define SOCK_DGRAM 1 /* MIPS swaps it with SOCK_STREAM */
#define F_SETFD 2

static struct {
    void *pcm;
    int fd;               /* ffmpeg's sound */
    int gain;             /* bt_gain's, 65536 on the DAC */
    volatile int done;    /* the sound ended */
    volatile long played; /* frames heard since this ffmpeg started */
    char error[64]; /* why sound_open found no device, or the writer stopped, for RADIO_STATE */
    unsigned long buffer; /* ALSA's ring, frames (sound_open) */
} au;

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(1, &t); /* CLOCK_MONOTONIC */
    return (long long)t.sec * 1000 + t.nsec / 1000000;
}

/* The flag file at path mapped, or 0 while it doesn't exist. */
static volatile int *flag_at(const char *path) {
    int fd = open(path, 0); /* O_RDONLY */
    if (fd < 0) return 0;
    void *p = mmap(0, sizeof(int), 1, 1, fd, 0); /* PROT_READ, MAP_SHARED */
    close(fd);
    return p != (void *)-1 ? p : 0;
}

static void *writer(void *unused) {
    short buf[2 * CHUNK];
    long written = 0, delay;
    volatile int *mono_on =
        0; /* demo's MONO_FILE, looked for every hundredth chunk till it appears */
    volatile int *swap_on = 0;
    for (int end = 0, tries = 0; !end;) {
        if (!swap_on && !(tries % 100)) swap_on = flag_at(SWAP_FILE);
        if (!mono_on && !(tries % 100)) mono_on = flag_at(MONO_FILE);
        ++tries;
        unsigned got = 0;
        while (got < sizeof buf) {
            int r = read(au.fd, (char *)buf + got, sizeof buf - got);
            if (r <= 0) {
                end = 1;
                break;
            } /* the short last block still plays */
            got += (unsigned)r;
        }
        unsigned frames = got / 4;
        if (!frames) break;
        if (au.gain < 65536) scale(buf, 2 * frames, au.gain);
        if (mono_on && *mono_on > 0) mono(buf, frames);
        if (swap_on && *swap_on > 0) swap(buf, frames);
        int recovered = 0;
        for (unsigned sent = 0; sent < frames;) {
            long r = snd_pcm_writei(au.pcm, buf + 2 * sent, frames - sent);
            if (r < 0) {
                int err = recovered ? (int)r : snd_pcm_recover(au.pcm, (int)r, 1);
                if (!err) {
                    recovered = 1;
                    continue;
                }
                /* a lost device ends the sink, or radio's run, which then says why */
                return snprintf(au.error, sizeof au.error, "write: %s", snd_strerror(err)),
                       au.done = 1, (void *)0;
            }
            if (!r) {
                usleep(1000);
                continue;
            }
            sent += (unsigned)r;
            written += r;
            recovered = 0;
        }
        long avail = snd_pcm_avail(au.pcm), ring = avail >= 0 ? (long)au.buffer - avail : 0;
        if (snd_pcm_delay(au.pcm, &delay) || delay < 0) delay = 0; /* no answer: it ran dry */
        /* nothing heard while ALSA still holds all that was written (its first fill; Bluetooth adds
         * the link's own delay), nor what its ring still holds: after a restart's drop bluealsa
         * answers the link's delay alone */
        if (delay < ring) delay = ring;
        au.played = delay < written ? written - delay : 0;
    }
    return au.done = 1, (void *)0;
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
        for (int i = 0;
             au.pcm && (err = snd_pcm_set_params(au.pcm, 2, 3, 2, rate, 1, LATENCY)) == -16 &&
             !jack && i < 50;
             ++i) /* S16_LE, RW_INTERLEAVED */
            usleep(100000);
        if (au.pcm && err)
            snd_pcm_close(au.pcm),
                au.pcm = 0, snprintf(au.error, sizeof au.error, "params: %s", snd_strerror(err));
        unsigned long period;
        if (au.pcm) snd_pcm_get_params(au.pcm, &au.buffer, &period);
        if (au.pcm && jack && (dac = open(DAC, O_RDWR | O_CLOEXEC)) >= 0)
            ioctl(dac, DAC_PCM, &off), ioctl(dac, DAC_MUTE, &off);
    }
    return dac;
}

static struct {
    char title[256], codec[16], bitrate[16];
    char artist[96], song[150]; /* an Ogg station's, title's parts */
    long long at;               /* CLOCK_MONOTONIC ms the sound started, 0 before */
} rs;

/* RADIO_STATE, whole: written aside and renamed, so demo never reads half of it. */
static void radio_state(const char *state) {
    char s[480];
    int n = snprintf(s, sizeof s, "state=%s\ntitle=%s\ncodec=%s\nbitrate=%s\nat=%lld\nerror=%s\n",
                     state, rs.title, rs.codec, rs.bitrate, rs.at, au.error),
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

/* An Ogg station's song as title, "ARTIST - TITLE" as an ICY StreamTitle reads (radio.c splits it),
 * or the one of them it has. */
static void ogg_title(void) {
    snprintf(rs.title, sizeof rs.title, "%s%s%s", rs.artist,
             rs.artist[0] && rs.song[0] ? " - " : "", rs.song);
    radio_state(rs.at ? "playing" : "connecting");
}

/* -r: ffmpeg reads URL (argv[2]) with its ICY tags and reconnects as it can; when it ends anyway
 * it starts again, after a second more each time, until RADIO_TRIES starts in a row bring no
 * sound. Its report (stderr) gives the codec, bitrate and each StreamTitle, or the ARTIST and
 * TITLE an Ogg station's stream starts with. q quits, unlinking the state; giving up leaves
 * state=error. */
static int radio(int argc, char **argv) {
    radio_state("connecting");
    int soft = argc > 3 && argv[3][0] != 'h',
        dac = sound_open(argv[1], argc > 3 ? argv[3] : 0, RATE);
    int on = 1, quit = 0, tries = 0;
    int sock = dgram_listen(RADIO_SOCK);
    while (au.pcm && !quit && tries++ < RADIO_TRIES) {
        int ap[2], ep[2] = { -1, -1 }, len = 0;
        if (pipe2(ap, O_CLOEXEC) || pipe2(ep, O_CLOEXEC)) break;
        /* verbose: libavformat's http logs each StreamTitle there, not at info */
        const char *args[] = { FFMPEG,
                               "-nostdin",
                               "-hide_banner",
                               "-nostats",
                               "-loglevel",
                               "verbose",
                               "-icy",
                               "1",
                               "-reconnect",
                               "1",
                               "-reconnect_streamed",
                               "1",
                               "-rw_timeout",
                               "15000000",
                               "-i",
                               argv[2],
                               "-vn",
                               "-ac",
                               "2",
                               "-ar",
                               "48000",
                               "-f",
                               "s16le",
                               "pipe:4",
                               0 };
        rs.at = 0;
        rs.artist[0] = rs.song[0] = 0;
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
            else if (tag_line(line, "ARTIST", rs.artist, sizeof rs.artist) ||
                     tag_line(line, "TITLE", rs.song, sizeof rs.song))
                ogg_title();
        }
        if (pid > 0) kill(pid, SIGKILL), waitpid(pid, 0, 0);
        if (thread) pthread_join(thread, 0);
        close(ap[0]), close(ep[0]);
        snd_pcm_drop(au.pcm), snd_pcm_prepare(au.pcm);
        /* the wait before the next start, a key at a time */
        for (long long until = now_ms() + 1000 * tries;
             !quit && tries < RADIO_TRIES && now_ms() < until;) {
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

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    /* Stock ALSA's plug/prepare leaves a slave lock held; our PCM calls never overlap. */
    if (setenv("LIBASOUND_THREAD_SAFE", "0", 1)) return 1;
    for (int fd = 3; fd < 1024; ++fd) close(fd); /* demo's, inherited */
    signal(SIGCHLD, 0);                          /* SIG_DFL, so waitpid sees ffmpeg */
    if (argc > 3 && !strcmp(argv[1], "-r")) return radio(argc - 1, argv + 1);
    if (argc > 3 && !strcmp(argv[1], "-s")) return sink(argv + 1);
    return 2;
}
#endif
