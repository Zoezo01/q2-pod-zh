/* Headset I/O stays on one bounded worker; only the UI loop controls players. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

extern int stock_pause_trampoline(void);
extern int ringnav_bt_resume(void);
/* Stock drops an unlisted device's Connected (3) while a link thread waits; adopt it when the wait
 * is for BTLINKMAC (0xa38168), its Connect running (5); checkBluetoothStatus then judges it last.
 * ponytail: the target's Connect still runs; powered on within its page timeout, both may link. */
static void headset_bt_status(int event, char *address, char *name, void *data) {
    char target[32] = { 0 };
    int status = 0;
    if (event == 3 && *(const volatile int *)bt_connectstatus == 1 && !getBtLinkStatus(address) &&
        checkPairDev(address)) {
        getBtLinkItem(getBtLinkSize() - 1, &status, target);
        if (status == 5 && !strcmp(target, (const char *)0xa38168)) {
            addBtLinkItem(address, 3);
            updateBtLinkStatus(4, target);
            return;
        }
    }
    ((void (*)(int, char *, char *, void *))0x4f1a50)(event, address, name, data);
}
void headset_status_register(void) { btctl_device_status_callback(headset_bt_status, 0); }
/* Auto-link would append its target after a connected incoming link; its failure clears both. */
int headset_bt_autolink(char *address) {
    char item[32];
    int status = 0;
    for (int i = getBtLinkSize(); status != 3 && i-- > 0;) getBtLinkItem(i, &status, item);
    return status != 3 && checkPairDev(address);
}
/* The control thread can discover a boot reconnect before openBluetooth powers the radio. */
int headset_bt_init(void *thread) {
    initBluetoothParam(); /* Lists must exist before callbacks; the later reset would erase links. */
    btctl_set_prompt_callback((void *)0x4ef600, 0);
    headset_status_register();
    btctl_set_a2dp_callback((void *)btrecvA2dpFunc, (void *)0x01020304);
    return btctl_create_daemon_thread(thread);
}
static struct {
    volatile unsigned generation, running, head, tail, report;
    volatile int keys[16];
    unsigned worker_generation, checked, epoch, paused_epoch, paused_at, recent_at,
        recent_owner, recent_session;
    unsigned last_at, session, linked_at;
    int linked, known, ears, want, paused, owner, automatic, last_key;
    unsigned char address[14];
    int airpods;
} hs __attribute__((section(".scratch")));

void headset_manual(void) {
    if (!hs.automatic) {
        ++hs.epoch;
        hs.recent_owner = 0;
    }
}
int ringnav_play_pause(void) {
    headset_manual();
    return stock_pause_trampoline();
}
int media_owner(void) { return video_status() ? 4 : radio_status() ? 3 : spot_status() ? 2 : 1; }
int media_playing(void) {
    return (video_status() ?: radio_status() ?: spot_status() ?: mclGetPlayStatus()) == 2;
}
static unsigned media_session(void) {
    int owner = media_owner();
    if (owner == 4) return video_session();
    if (owner == 3) return radio_session();
    if (owner == 2) return spot_session();
    /* Both local and stock Tidal queues: changing the queue or its position ends this session. */
    return *(volatile unsigned *)MCL_POS ^ *(volatile unsigned *)MCL_TYPE ^
           *(volatile unsigned *)mcl_pdeqplaylist;
}
void media_set_playing(int playing) {
    hs.automatic = 1;
    int owner = media_owner();
    if (owner == 4)
        video_set_playing(playing);
    else if (owner == 3)
        radio_set_playing(playing);
    else if (owner == 2)
        spot_set_playing(playing);
    else if (media_playing() != playing)
        player_play_pause();
    hs.automatic = 0;
}
/* The streaming page on top, which owns the playback keys: 1 Spotify, 2 radio, 3 Tidal; 0 none. */
static int focus(void) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "spotify_page") || !tk_strcmp(name, "spotbrowse_page") ? 1
           : !tk_strcmp(name, "radio_page") || !tk_strcmp(name, "radionp_page") ? 2
           : tk_str_start_with(name, "tidal_")                                  ? 3
                                                                                : 0;
}
int media_focused(void) { return focus() != 0; }
void media_key(unsigned key) {
    headset_manual();
    int f = focus(); /* a focused Spotify or radio that isn't running leaves them to the owner */
    if ((f == 1 && spot_media(key)) || (f == 2 && radio_media(key)))
        return;
    if (f != 3 && video_status())
        video_key(key);
    else if (f != 3 && radio_status())
        radio_media(key);
    else if (f != 3 && spot_status())
        spot_media(key);
    else if (key == KEY_PLAY)
        player_play_pause();
    else if (key == KEY_FWD_BTN)
        player_next_music();
    else if (key == KEY_BACK_BTN)
        player_prev_music();
}

/* Only complete, valid ear packets are published, in one word. No battery state. */
int ringnav_aap_parse(const unsigned char *p, int n) {
    if (!p || n != 8 || p[0] != 4 || p[1] || p[2] != 4 || p[3] || p[4] != 6 || p[5] || p[6] > 2 ||
        p[7] > 2)
        return 0;
    hs.report = 4u | (p[6] == 0) | ((p[7] == 0) << 1);
    return 2;
}
static int wait_fd(int fd, int writing) {
    if (fd < 0 || fd >= 1024) return -1;
    unsigned set[32] = { 0 };
    long tv[2] = { 0, 200000 };
    set[fd / 32] = 1u << (fd % 32);
    return select(fd + 1, writing ? 0 : set, writing ? set : 0, 0, tv);
}
/* Up to 1 s for either fd (-1 for none) to read: bit 1 a, bit 2 b, -1 an error. */
static int wait_either(int a, int b) {
    if (a >= 1024 || b >= 1024) return -1;
    unsigned set[32] = { 0 };
    long tv[2] = { 1, 0 };
    if (a >= 0) set[a / 32] |= 1u << (a % 32);
    if (b >= 0) set[b / 32] |= 1u << (b % 32);
    int n = select((a > b ? a : b) + 1, set, 0, 0, tv);
    if (n <= 0) return n;
    return (a >= 0 && set[a / 32] >> (a % 32) & 1) | (b >= 0 && set[b / 32] >> (b % 32) & 1) << 1;
}
static int avrcp_open(void) {
    char path[32], name[64];
    for (int i = 0; i < 32; ++i) {
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        int fd = open(path, 0x80080); /* O_RDONLY | O_NONBLOCK | O_CLOEXEC, MIPS */
        if (fd < 0) continue;
        memset(name, 0, sizeof name);
        int got = ioctl(fd, 0x40404506u, name);
        name[63] = 0;
        if (got > 0 && strstr(name, "(AVRCP)")) return fd;
        close(fd);
    }
    return -1;
}
static const unsigned char hello[16] = { 0, 0, 4, 0, 1, 0, 2, 0 };
static const unsigned char flags[14] = { 4, 0, 4, 0, 0x4d, 0, 0xd7 };
static const unsigned char notify[10] = { 4, 0, 4, 0, 0xf, 0, 255, 255, 255, 255 };
static int aap_open(const unsigned char *address, unsigned generation) {
    int aap = -1;
    if (hs.airpods && generation == hs.generation) {
        aap = socket(31, 5, 0);
        if (aap >= 0) {
            if (fcntl(aap, 4, 0x80) < 0) {
                close(aap);
                return -1;
            } /* F_SETFL, O_NONBLOCK: connect and send never block */
            int ready = connect(aap, address, 14) == 0;
            for (int i = 0; !ready && i < 5 && generation == hs.generation; ++i)
                if (wait_fd(aap, 1) > 0) {
                    int error = -1;
                    unsigned len = sizeof error;
                    ready = !getsockopt(aap, 0xffff, 0x1007, &error, &len) &&
                            !error; /* SOL_SOCKET, SO_ERROR */
                    break;
                }
            if (!ready || send(aap, hello, sizeof hello, 0x40) != sizeof hello ||
                send(aap, flags, sizeof flags, 0x40) != sizeof flags ||
                send(aap, notify, sizeof notify, 0x40) != sizeof notify)
                close(aap), aap = -1;
        }
    }
    return aap;
}
static void *headset_worker(void *arg) {
    unsigned generation = (unsigned)(long)arg;
    int aap = -1, input = -1, tries = 0;
    unsigned char address[14];
    memcpy(address, hs.address, sizeof address);
    while (generation == hs.generation) {
        /* A pass is up to 1 s. Retry throughout the connection, including late-created and
         * replaced uinput devices: each second for the first 10, then every 2 s; AACP every 5. */
        if (aap < 0 && !(tries % 5)) aap = aap_open(address, generation);
        if (input < 0 && (tries < 10 || !(tries % 2))) input = avrcp_open();
        if (aap < 0 && input < 0) {
            usleep(1000000); /* a stale worker exits at the loop head; headset_poll ignores it */
            ++tries;
            continue;
        }
        int ready = wait_either(aap, input);
        if (ready < 0) { /* which one: a closed fd shows as readable, so take each alone */
            if (aap >= 0 && wait_fd(aap, 0) < 0) close(aap), aap = -1;
            if (input >= 0 && wait_fd(input, 0) < 0) close(input), input = -1;
            ++tries;
            continue;
        }
        if (ready & 1) {
            unsigned char packet[256];
            int n = recv(aap, packet, sizeof packet, 0x40);
            if (n <= 0) close(aap), aap = -1;
            /* Drain the queue: AirPods stream other reports too, and one packet a pass fell
             * behind, delaying ear reports. */
            for (int i = 0; n > 0 && i < 64 && generation == hs.generation; ++i) {
                ringnav_aap_parse(packet, n);
                n = recv(aap, packet, sizeof packet, 0x40);
            }
        }
        if (ready & 2) {
            struct {
                unsigned sec, usec;
                unsigned short type, code;
                int value;
            } ev[8];
            int n = read(input, ev, sizeof ev);
            if (n <= 0)
                close(input), input = -1;
            else
                for (int i = 0; i < n / (int)sizeof ev[0] && generation == hs.generation; ++i)
                    if (ev[i].type == 1 && ev[i].value == 1 && hs.head - hs.tail < 16) {
                        hs.keys[hs.head % 16] = ev[i].code;
                        __sync_synchronize();
                        ++hs.head;
                    }
        }
        ++tries;
    }
    if (input >= 0) close(input);
    if (aap >= 0) close(aap);
    __sync_synchronize();
    hs.running = 0;
    return 0;
}
static void headset_start(void) {
    char mac[64] = { 0 };
    int n = getBtLinkSize(), status = 0;
    if (n > 0) getBtLinkItem(n - 1, &status, mac);
    hs.airpods = 0;
    void *pairs = *(void *volatile *)0xa38110; /* stock pdeq_btpairlist */
    for (unsigned i = 0; pairs && i < deque_size(pairs) && i < 64; ++i) {
        const char *p = deque_at(pairs, i);
        if (p && !strcmp(p + 4, mac) && strstr(p + 0x24, "AirPods")) hs.airpods = 1;
    }
    memset(hs.address, 0, sizeof hs.address);
    hs.address[0] = 31;
    hs.address[2] = 1;
    hs.address[3] = 16;
    if (strlen(mac) != 17)
        hs.airpods = 0;
    else
        for (int i = 0; i < 6; ++i) {
            int hi = hex(mac[3 * i]), lo = hex(mac[3 * i + 1]);
            if (hi < 0 || lo < 0 || (i < 5 && mac[3 * i + 2] != ':')) hs.airpods = 0;
            hs.address[9 - i] = (unsigned char)(hi * 16 + lo);
        }
    hs.report = hs.head = hs.tail = 0;
    hs.known = hs.paused = 0;
    unsigned long tid;
    hs.worker_generation = hs.generation;
    hs.running = 1;
    if (pthread_create(&tid, 0, headset_worker, (void *)(long)hs.generation))
        hs.running = 0;
    else
        pthread_detach(tid);
}

/* Counts, not a bud mask: the report's bytes are the primary and secondary bud, which swap when
 * the primary leaves an ear, so a mask can miss the returning bud (one worn AirPod) or see a swap
 * as a removal. A worn bud coming out pauses, as on iOS; as many buds back as were worn resume. */
void headset_ears(unsigned report) {
    unsigned now = time_now_ms();
    int owner = media_owner();
    unsigned session = media_session();
    if (hs.paused && (hs.epoch != hs.paused_epoch || owner != hs.owner || session != hs.session ||
                      (media_playing() && now - hs.paused_at > 1500)))
        hs.paused = 0;
    if (report & 4) {
        int ears = report & 3, in = (ears & 1) + (ears >> 1), was = (hs.ears & 1) + (hs.ears >> 1);
        if (hs.known && in < was &&
            (media_playing() || (hs.recent_owner == (unsigned)owner &&
                                 hs.recent_session == session && now - hs.recent_at < 1500))) {
            if (!hs.paused) hs.want = was;
            hs.owner = owner;
            hs.session = session;
            hs.paused = 1;
            hs.paused_epoch = hs.epoch;
            hs.paused_at = now;
            if (media_playing()) media_set_playing(0);
        } else if (hs.known && hs.paused && in >= hs.want) {
            media_set_playing(1);
            hs.paused = 0;
        }
        hs.known = 1;
        hs.ears = ears;
    }
    if (hs.known && !hs.ears && media_playing()) {
        media_set_playing(0);
        if (!hs.paused) hs.want = 0; /* first report never grants auto-resume */
    }
}
/* Resume on connect off (navigation.c, the default): AirPods send AVRCP PLAY as their link comes
 * up. The worker finds the AVRCP input within about a second of the link (retried every fifth
 * 200 ms pass), and the headset's PLAY follows A2DP setup by a second or two, so a start within
 * CONNECT_HOLD_MS of the link is dropped; a deliberate press after that plays. */
#define CONNECT_HOLD_MS 5000
static int connect_hold(unsigned now) {
    return hs.linked && now - hs.linked_at < CONNECT_HOLD_MS && !ringnav_bt_resume();
}
void headset_command(int code) {
    unsigned now = time_now_ms();
    if (hs.last_key == code && now - hs.last_at < 150) return;
    hs.last_key = code;
    hs.last_at = now;
    if (code == 163 || code == 165)
        media_key(code == 163 ? KEY_FWD_BTN : KEY_BACK_BTN);
    else if (code == 164) {
        if (hs.paused && now - hs.paused_at < 1500) return;
        if (connect_hold(now) && !media_playing()) return;
        media_key(KEY_PLAY);
    } else if (code == 119 || code == 201 || code == 166) {
        if (hs.paused && now - hs.paused_at < 1500) return;
        headset_manual();
        if (media_playing()) {
            hs.recent_at = now;
            hs.recent_owner = media_owner();
            hs.recent_session = media_session();
            media_set_playing(0);
        }
    } else if (code == 200 || code == 207) {
        if (hs.paused || connect_hold(now)) return; /* insertion is governed by the ear count */
        headset_manual();
        if (!hs.known || hs.ears) media_set_playing(1);
    }
}
void headset_poll(void) {
    unsigned now = time_now_ms();
    if (now - hs.checked < 100) return;
    hs.checked = now;
    int on = g_bluetoothflag && bt_linkstatus && !bt__recv_pageflag;
    if (on != hs.linked) {
        ++hs.generation;
        headset_volume_reset();
        hs.known = hs.paused = hs.recent_owner = 0;
        hs.linked = on;
        hs.linked_at = now;
        if (!on && media_playing()) media_set_playing(0);
    }
    if (!on) return;
    if (!hs.running) headset_start();
    if (hs.worker_generation != hs.generation) return;
    while (hs.tail != hs.head) {
        __sync_synchronize();
        int code = hs.keys[hs.tail % 16];
        ++hs.tail;
        headset_command(code);
    }
    headset_ears(hs.report);
}
