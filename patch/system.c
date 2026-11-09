/* The background polls that keep the Q2 itself well: charge limit, Wi-Fi signal, watchdog and
 * the stall and state records, the standby limit and the boot logo. */
#include "navigation.h"

/* What the polls keep between passes. */
static struct {
    int standby_off; /* Standby limit's alarm ended a Standby, so the next pass powers off */
    int wdt;         /* the hardware watchdog's fd: 0 untried, -1 none or refused (wdt_poll) */
    int wdt_long;    /* counting WDT_LONG_MS for a long pass (ringnav_wdt_long) */
    int ended; /* a clean end began: no feed and no state record from here (ringnav_clean_end) */
    int suspended;  /* inside Standby's suspend (ringnav_standby), for the state record */
    char state[96]; /* the state record last written (state_poll) */
    /* the boot logo's poll: last check, card seen mounted, its sync thread, its failure to toast */
    unsigned logo_at;
    int logo_mounted;
    volatile int logo_busy;
    const char *volatile logo_err;
} pw __attribute__((section(".scratch")));

/* Charge limit (docs/internals.md#charge-limit): get_battery_capacity keeps g_power_capacity and
 * g_power_chargestate (1 Charging, 2 Full, the AXP2101's battery status); switch_charge_enable
 * drives the charge enable, as stock's USB mode does with g_usbdac_chargeflag. At CHARGE_STOP
 * charging stops, and again whenever something else (USB mode's exit, AirPlay) turned it back on,
 * until the level falls to CHARGE_RESUME; then, or with the limit off, charging is handed back as
 * stock would have it: USB mode's and AirPlay's own choice while their page is open, else on. */
static void charge_poll(void) {
    unsigned now = time_now_ms();
    if (st.charge_at && now - st.charge_at < CHARGE_POLL_MS) return;
    st.charge_at =
        now | !now; /* never 0, no poll yet; |1 would eat a millisecond when now is even */
    int level = I(g_power_capacity, 0), charging = (unsigned)I(g_power_chargestate, 0) - 1 < 2;
    int hold = st.charge_limit && level > CHARGE_RESUME && (st.charge_held || level >= CHARGE_STOP);
    if (hold && (!st.charge_held || charging))
        switch_charge_enable(0);
    else if (!hold && st.charge_held)
        switch_charge_enable(navigator_window_is_exist("usbmode_page") ||
                                     navigator_window_is_exist("airplay_page")
                                 ? g_usbdac_chargeflag
                                 : 1);
    st.charge_held = hold;
}

/* The Wi-Fi signal (docs/internals.md#battery), for networkGetWifiSignal's two pollers: the status
 * bar's bars (get_wifisignal) and the network monitor thread's connect status, each about once a
 * second. Stock asks with SIOCGIWSTATS, which the driver answers through get_station, whose TX
 * report takes the radio out of power save (LPS) every time. The driver keeps the same signal in
 * rx_signal, a percentage that get_station reports less 100 as dBm, and reading it touches no
 * hardware. Unreadable, it is 0, as stock's failed ioctl. Stock's unsigned level wraps the one
 * value 0 dBm (100%) to -256, one bar; here it is 0, four. */
#define WIFI_SIGNAL "/proc/net/rtl8733bs/wlan0/rx_signal"
static struct {
    unsigned at;
    int dbm;
} wifi __attribute__((section(".scratch")));

int ringnav_wifi_signal(void) {
    unsigned now = time_now_ms();
    if (wifi.at && now - wifi.at < WIFI_SIGNAL_MS) return wifi.dbm;
    char text[512];
    int fd = open(WIFI_SIGNAL, 0x80000); /* O_RDONLY | O_CLOEXEC, MIPS */
    int n = fd < 0 ? 0 : read(fd, text, sizeof text - 1);
    if (fd >= 0) close(fd);
    text[n > 0 ? n : 0] = 0;
    const char *s = strstr(text, "signal_strength:");
    int pct = 0;
    if (s)
        for (s += 16; *s >= '0' && *s <= '9'; ++s) pct = pct * 10 + *s - '0';
    wifi.dbm = s ? pct - 100 : 0;
    wifi.at = now | !now; /* never 0, not read yet */
    return wifi.dbm;
}

/* Hardware watchdog (docs/internals.md#watchdog). Stock loads soc_watchdog.ko and never starts it,
 * so a Q2 whose UI loop or kernel stalls stays dark, every button dead, until a 15 s Centre hold
 * cuts the power. The first UI loop pass starts it counting WDT_MS and every pass feeds it, so a
 * stall that long reboots instead. It counts on the RTC clock, through a suspend, so Standby stops
 * it before and starts it again after (ringnav_standby). Its close is a no-op, so a clean end
 * (ringnav_clean_end: power-off, Restart, a firmware update, Rockbox) stops it. libhardware2's
 * watchdog_start/stop/feed ioctls; no device, or a refused start, leaves stock's behaviour.
 * ponytail: the UI loop is the only feeder, so a stalled worker thread is not caught. */
#define WDT_DEV "/dev/jz_watchdog"
#define WDT_START 0x8004570d /* an int of milliseconds, by pointer */
#define WDT_STOP 0x2000570e
#define WDT_FEED 0x2000570f
/* Stall record (docs/internals.md#watchdog): after the watchdog's reset q2-crash.log says only that
 * demo did not end. A thread started with the watchdog wakes every STALL_POLL_MS; once the UI loop
 * has not passed for STALL_MS, and again at STALL_AGAIN_MS, it writes /mnt/data/q2-stall and syncs
 * it: the time since the pass, toast()'s dialogs open, the UI thread's state, wchan and syscall
 * line (sp and pc), MemAvailable, pgmajfault and workingset_refault_file, then the stack words from
 * that sp as a q2remote --prof line (ra 0). The time counts from the thread's first wake after the
 * pass, so up to STALL_POLL_MS short. The UI loop is demo's main thread, whose stat, wchan and
 * syscall /proc/self has. A pass removes the record: a stall that recovered is not what it is for.
 * Standby, a clean end and a long pass (ringnav_wdt_long) write nothing. Only open, read, write,
 * fsync, close and unlink on its own buffers, and the stack read in place: no stock call, no
 * allocation, no lock the UI thread takes. S90play puts it in q2-crash.log as "== stall". */
#define STALL_FILE "/mnt/data/q2-stall"
#define STALL_WORDS 160 /* q2remote --prof's STACK_WORDS */
#define STALL_IN 4096   /* a /proc file's start, read; the record's buffers are the thread's own */
static struct {
    volatile unsigned passes; /* the UI loop's, counted by wdt_poll */
    unsigned seen, at;        /* the thread's: passes at its last wake, and since when (now_ms) */
    int level;                /* records written since that pass */
} stall __attribute__((section(".scratch")));
static void stall_read(char *in, const char *path) { /* the file's start, NUL-terminated */
    int fd = open(path, 0x80000), n = 0, got;        /* O_RDONLY | O_CLOEXEC */
    while (fd >= 0 && n < STALL_IN - 1 && (got = read(fd, in + n, STALL_IN - 1 - n)) > 0) n += got;
    if (fd >= 0) close(fd);
    in[n] = 0;
}
/* in's line that starts with key */
static const char *stall_find(const char *in, const char *key) {
    unsigned n = strlen(key);
    for (const char *s = in; *s; ++s) {
        if (!strncmp(s, key, n)) return s;
        while (*s && *s != '\n') ++s;
        if (!*s) break;
    }
    return 0;
}
static char *stall_add(char *p, char *end, const char *label, const char *s) { /* s's first line */
    if (!s || p >= end) return p;
    while (*label && p < end) *p++ = *label++;
    while (*s && *s != '\n' && p < end) *p++ = *s++;
    *p++ = '\n';
    return p;
}
static void stall_write(unsigned age) {
    char in[STALL_IN], out[2560], *p = out, *last = out + sizeof out - 1, *end = last - 11;
    char state = '?';
    unsigned long tid = 0, top = 0, sp = 0, pc = 0, d;
    p += snprintf(p, 64, "age %u ms\ntoasts %d\n", age, st.toasts);
    stall_read(in, "/proc/self/stat"); /* "pid (comm) S ...": the comm may hold ") " */
    const char *s = strrchr(in, ')');
    if (s && s[1] == ' ' && s[2]) state = s[2];
    for (int field = 2; s && *s; ++s) /* 28, startstack: the main stack's top */
        if (*s == ' ' && ++field == 28) {
            for (++s; *s >= '0' && *s <= '9'; ++s) top = top * 10 + *s - '0';
            break;
        }
    for (s = in; *s >= '0' && *s <= '9'; ++s) tid = tid * 10 + *s - '0';
    p += snprintf(p, 16, "state %c\n", state);
    stall_read(in, "/proc/self/wchan");
    p = stall_add(p, last, "wchan ", in);
    stall_read(in, "/proc/self/syscall"); /* "nr args sp pc", "-1 sp pc" or "running" */
    p = stall_add(p, last, "syscall ", in);
    for (s = in; (s = strstr(s, "0x"));) {
        unsigned long v = 0;
        for (s += 2; (d = *s >= '0' && *s <= '9' ? *s - '0' : (*s | 32) - 'a' + 10) < 16; ++s)
            v = v << 4 | d;
        sp = pc, pc = v; /* the last two */
    }
    stall_read(in, "/proc/meminfo");
    p = stall_add(p, last, "", stall_find(in, "MemAvailable:"));
    stall_read(in, "/proc/vmstat");
    p = stall_add(p, last, "", stall_find(in, "pgmajfault "));
    p = stall_add(p, last, "", stall_find(in, "workingset_refault_file "));
    /* The stack's words that could be code, as --prof's, read in place: from a blocked thread's sp
     * up to the main stack's top is its own stack, mapped; no other is within 8 MB of that top. */
    if (sp && !(sp & 3) && sp < top && top - sp < 0x800000 && p + 40 < end) {
        p += snprintf(p, 40, "%lu %c %lx 0", tid, state, pc);
        for (unsigned long a = sp; a < sp + 4 * STALL_WORDS && a + 4 <= top && p < end; a += 4) {
            unsigned w = *(const volatile unsigned *)a;
            if (w >= 0x400000 && w < 0x80000000 && !(w & 3)) p += snprintf(p, 10, " %x", w);
        }
        *p++ = '\n';
    }
    /* O_WRONLY | O_CREAT | O_CLOEXEC, and O_APPEND, or O_TRUNC for the first */
    int fd = open(STALL_FILE, stall.level ? 0x80109 : 0x80301, 0644);
    if (fd < 0) return;
    write(fd, out, p - out);
    fsync(fd);
    close(fd);
    ++stall.level;
}
static void stall_step(void) {
    unsigned now = (unsigned)now_ms(), passes = stall.passes;
    if (passes != stall.seen || pw.ended || pw.suspended || pw.wdt_long) {
        if (passes != stall.seen && stall.level) unlink(STALL_FILE), stall.level = 0;
        stall.seen = passes, stall.at = now;
        return;
    }
    unsigned age = now - stall.at;
    if (stall.level < 2 && age >= (stall.level ? STALL_AGAIN_MS : STALL_MS)) stall_write(age);
}
static void *stall_watch(void *arg) {
    for (;;) usleep(STALL_POLL_MS * 1000), stall_step();
    return arg;
}
static void wdt_set(int on) { /* Standby's suspend and the resume after it; a clean end */
    int ms = WDT_MS;
    if (pw.wdt > 0) ioctl(pw.wdt, on ? WDT_START : WDT_STOP, &ms);
}
static void wdt_span(int ms) {
    ioctl(pw.wdt, WDT_STOP, 0);
    ioctl(pw.wdt, WDT_START, &ms);
}
/* A known long pass is starting (an import's tag reads, a folder add's loads): WDT_LONG_MS for it,
 * and the next pass, once it has returned, WDT_MS again */
void ringnav_wdt_long(void) {
    if (pw.ended || pw.wdt <= 0 || pw.wdt_long) return;
    pw.wdt_long = 1;
    wdt_span(WDT_LONG_MS);
}
static void wdt_poll(void) {
    if (pw.ended || pw.wdt < 0) return;
    ++stall.passes;
    if (pw.wdt && pw.wdt_long) {
        pw.wdt_long = 0;
        wdt_span(WDT_MS);
    } else if (pw.wdt)
        ioctl(pw.wdt, WDT_FEED, 0);
    if (pw.wdt) return;
    int fd = open(WDT_DEV, 0x80002), ms = WDT_MS; /* O_RDWR, as libhardware2, | O_CLOEXEC (MIPS) */
    pw.wdt = fd > 0 && !ioctl(fd, WDT_START, &ms) ? fd : -1;
    if (fd > 0 && pw.wdt < 0) close(fd);
    unsigned long tid;
    if (pw.wdt > 0 && !pthread_create(&tid, 0, stall_watch, 0)) pthread_detach(tid);
}
/* The state record (docs/internals.md#watchdog): /mnt/data/q2-state says what the Q2 is doing, one
 * line rewritten and synced whenever it changes: the screen, Standby's suspend, the cable, the
 * battery, playback and its output. A clean end removes it; one still there at boot means a
 * forced restart (or the battery), and S90play puts it in q2-crash.log (docs/boot.md). */
#define STATE_FILE "/mnt/data/q2-state"
static char *put(char *p, const char *s) {
    while (*s) *p++ = *s++;
    return p;
}
static void state_poll(void) {
    static const char *const play[] = { "?", "stopped", "playing", "paused" };
    static const char *const out[] = { "jack", "bluetooth", "usb" };
    if (pw.ended) return;
    unsigned ps = (unsigned)mclGetPlayStatus(), ow = (unsigned)mclGetOutputWay();
    unsigned pct = (unsigned)I(g_power_capacity, 0), n = 0;
    char s[sizeof pw.state], d[4], *p = s;
    p = put(p, g_backlight_status ? "screen:on" : "screen:off");
    p = put(p, pw.suspended ? " power:suspending" : " power:running");
    p = put(p, g_usbdet_value ? " cable:in" : " cable:out");
    p = put(p, " battery:");
    if (pct > 100) pct = 100;
    do d[n++] = '0' + pct % 10;
    while (pct /= 10);
    while (n) *p++ = d[--n];
    p = put(p, " play:");
    p = put(p, play[ps < 4 ? ps : 0]);
    p = put(p, " out:");
    p = put(p, out[ow < 3 ? ow : 0]);
    p = put(p, "\n");
    *p = 0;
    if (!tk_strcmp(s, pw.state)) return;
    void *f = fopen(STATE_FILE, "w");
    if (!f) return;
    fwrite(s, 1, p - s, f);
    fflush(f);
    fsync(fileno(f));
    fclose(f);
    memcpy(pw.state, s, p - s + 1);
}
/* A clean end: power-off, Restart, a firmware update, Rockbox. Nothing to report at the next boot.
 */
void ringnav_clean_end(void) {
    if (pw.ended) return;
    pw.ended = 1;
    wdt_set(0);
    unlink(STATE_FILE);
    unlink(STALL_FILE); /* one the thread hadn't removed yet: S90play's next demo never would */
}
/* Standby limit (docs/internals.md#standby-limit): device_standby puts the radios, the card and the
 * DAC down, then toolsIntoStandby suspends to RAM ("mem" to /sys/power/state) and returns once
 * something resumes it. With a limit, the RTC's wake alarm is set that far ahead first and cleared
 * after. A resume at or past the alarm is the alarm's, and the next UI loop pass powers off as
 * stock's Auto power-off does (poweroff_page), unless a cable is in or the music plays. A key or
 * anything else that resumes it sooner leaves it on, and the next Standby counts from zero.
 * Without the RTC's time, or with an alarm it refuses, Standby is stock's. */
#define RTC_NOW "/sys/class/rtc/rtc0/since_epoch"
#define RTC_ALARM "/sys/class/rtc/rtc0/wakealarm"
static unsigned rtc_now(void) { /* the RTC's seconds since 1970 (UTC, RTC_WRITE); 0 unreadable */
    char s[16];
    void *f = fopen(RTC_NOW, "rb");
    if (!f) return 0;
    unsigned n = fread(s, 1, sizeof s, f), t = 0;
    fclose(f);
    for (unsigned i = 0; i < n && s[i] >= '0' && s[i] <= '9'; ++i) t = t * 10 + s[i] - '0';
    return t;
}
static int rtc_alarm(unsigned at) { /* 0 clears it; one already set refuses another (EBUSY) */
    char s[16];
    void *f = fopen(RTC_ALARM, "w");
    if (!f) return 0;
    unsigned n = (unsigned)tk_snprintf(s, sizeof s, "%u", at);
    int ok = fwrite(s, 1, n, f) == n;
    return !fclose(f) && ok;
}
int ringnav_standby(void) {
    static const unsigned hours[] = { 0, STANDBY_LIMIT_1, STANDBY_LIMIT_2, STANDBY_LIMIT_3 };
    pod_settings();
    unsigned start = st.standby_limit ? rtc_now() : 0;
    unsigned alarm = start + hours[st.standby_limit] * 3600;
    int armed = start && rtc_alarm(0) && rtc_alarm(alarm);
    pw.suspended = 1;
    state_poll(); /* the record a resume that never comes leaves behind */
    wdt_set(0);   /* it would count through the suspend */
    int result = stock_standby_trampoline();
    wdt_set(1);
    pw.suspended = 0; /* the next pass records the resume */
    if (armed) {
        unsigned now = rtc_now();
        rtc_alarm(0);
        pw.standby_off = now >= alarm;
    }
    return result;
}
static void standby_poll(void) {
    if (!pw.standby_off) return;
    pw.standby_off = 0;
    if (!g_usbdet_value && mclGetPlayStatus() != 2) navigator_to("poweroff_page");
}
/* User boot logo (docs/boot.md): the card's LOGO_SRC is kept as LOGO_DST, which the splash shows at
 * power-on, each time the card is mounted (boot, the end of USB storage). A 375x320 one is turned
 * upright by stock ffmpeg; anything but a 320x375 baseline JPEG is refused and LOGO_DST kept. No
 * card file, no LOGO_DST: the stock logo again. The thread keeps ffmpeg and flash off the UI; the
 * poll toasts its failure. */
#define LOGO_SRC "/mnt/mmc/Q2Pod/boot/boot-logo.jpg"
#define LOGO_DST "/mnt/data/boot-logo.jpg"
#define LOGO_ROT "/tmp/boot-logo.jpg"
#define LOGO_MAX (512 * 1024)
/* tools/build.py's jpeg_size: w << 16 | h of a baseline 8-bit three-component JPEG, the only kind
 * stock display_logo draws right; 0 for anything else. */
static unsigned jpeg_size(const unsigned char *b, unsigned n) {
    unsigned i = 2, m, size;
    if (n < 2 || b[0] != 0xff || b[1] != 0xd8) return 0;
    while (i < n) {
        if (b[i] != 0xff) return 0;
        while (i < n && b[i] == 0xff) i++;
        if (i >= n) return 0;
        m = b[i++];
        if (m == 0xd9 || m == 0xda || m == 0 || m == 0xd8) return 0;
        if (m == 1 || (m >= 0xd0 && m <= 0xd7)) continue;
        if (i + 2 > n || (size = b[i] << 8 | b[i + 1]) < 2 || i + size > n) return 0;
        if (m >= 0xc0 && m <= 0xcf && m != 0xc4 && m != 0xc8 && m != 0xcc) {
            if (size < 8 || size != 8 + 3u * b[i + 7] || m != 0xc0 || b[i + 2] != 8 ||
                b[i + 7] != 3)
                return 0;
            unsigned h = b[i + 3] << 8 | b[i + 4], w = b[i + 5] << 8 | b[i + 6];
            return w && h ? w << 16 | h : 0;
        }
        i += size;
    }
    return 0;
}
static void *logo_sync(void *unused) {
    unsigned n = 0, old_n;
    unsigned char *b = 0, *old = 0;
    const char *err = "Boot logo must be a 320x375 JPEG";
    if (access(LOGO_SRC, 0))
        unlink(LOGO_DST), err = 0;
    else if ((b = read_all(LOGO_SRC, LOGO_MAX, &n)) && jpeg_size(b, n) == (375u << 16 | 320)) {
        free(b),
            b = 0; /* landscape: ffmpeg turns it a quarter clockwise (coverflow.c's ff_thumb) */
        int status, pid = fork();
        if (!pid) {
            child_oom_first();
            execl("/usr/bin/ffmpeg", "/usr/bin/ffmpeg", "-nostdin", "-v", "quiet", "-i", LOGO_SRC,
                  "-vf", "transpose=1", "-pix_fmt", "yuvj444p", "-q:v", "2", "-f", "mjpeg", "-y",
                  LOGO_ROT, (char *)0);
            child_exit(127);
        }
        if (pid > 0 && waitpid(pid, &status, 0) == pid && !status)
            b = read_all(LOGO_ROT, LOGO_MAX, &n);
        unlink(LOGO_ROT);
    }
    if (b && jpeg_size(b, n) == (320u << 16 | 375)) {
        old = read_all(LOGO_DST, LOGO_MAX, &old_n);
        if ((old && old_n == n && !memcmp(old, b, n)) ||
            blob_io(LOGO_DST, LOGO_DST ".tmp", b, n, 1))
            err = 0; /* the same bytes are not written again each boot */
        else
            unlink(LOGO_DST ".tmp"), err = "Could not save boot logo";
    }
    free(b), free(old);
    pw.logo_err = err;
    pw.logo_busy = 0;
    return 0;
}
int card_mounted(void) {
    char m[4096]; /* ponytail: the Q2's few mounts fit (remote.c's card_mounted) */
    void *f = fopen("/proc/mounts", "rb");
    if (!f) return 0;
    unsigned n = fread(m, 1, sizeof m - 1, f);
    fclose(f);
    m[n] = 0;
    return strstr(m, " /mnt/mmc ") != 0;
}
static void logo_poll(void) {
    unsigned now = time_now_ms();
    const char *said = pw.logo_err;
    if (said) pw.logo_err = 0, toast(said); /* first: a toast's dialog polls here again */
    /* Mount edges (boot, USB storage's end) come lit; dark it keeps its edge for the first lit
     * poll. */
    if (!g_backlight_status || pw.logo_busy || (pw.logo_at && now - pw.logo_at < CHARGE_POLL_MS))
        return;
    pw.logo_at = now | !now;
    int mounted = card_mounted();
    unsigned long tid;
    if (mounted && !pw.logo_mounted) {
        ratings_copy_due(); /* another card, or the copy gone over USB storage */
        pw.logo_busy = 1;
        if (pthread_create(&tid, 0, logo_sync, 0)) {
            pw.logo_busy = 0; /* the edge is tried again next poll */
            return;
        }
        pthread_detach(tid);
    }
    pw.logo_mounted = mounted;
}

/* Every UI loop pass (navigation.c's main loop): Power's settings, then each poll. */
void power_poll(void) {
    pod_settings();
    charge_poll();
    standby_poll();
    logo_poll();
    ratings_copy_poll();
    wdt_poll();
    state_poll();
    smart_poll();
}
