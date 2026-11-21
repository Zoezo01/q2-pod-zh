/* SPDX-License-Identifier: GPL-3.0-only */
/* Narrow audio bridge for the q2-boy card game. Included by addon.c after game state. */
#define BOY_AUDIO_REQUEST "/tmp/q2-boy.audio.req"
#define BOY_AUDIO_REPLY "/tmp/q2-boy.audio.reply"
#define BOY_AUDIO_MODE "/mnt/mmc/q2-boy/audio-source.cfg"
#define BOY_AUDIO_MAGIC 0x51474131u

enum { GBA_GAME = 1, GBA_MUSIC, GBA_VOLUME, GBA_PREVIOUS, GBA_PLAY, GBA_NEXT, GBA_NOW_PLAYING };
struct boy_command {
    unsigned magic, seq;
    int action, value;
};
struct boy_reply {
    unsigned magic, seq;
    int music, volume, limit, route, gain, playing, native_ui;
};

static int boy_prefers_music(void) {
    char mode[24];
    if (!state_read(BOY_AUDIO_MODE, mode, sizeof mode)) return 0;
    const unsigned char *p = (const unsigned char *)mode + 1; /* state_read adds a newline */
    if (p[0] == 0xef && p[1] == 0xbb && p[2] == 0xbf) p += 3;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
    return !strncmp((const char *)p, "music", 5);
}

/* 0 wired DAC, 1 Bluetooth, 2 soft USB, 3 hardware-volume USB. */
static int boy_route(char vol[5]) {
    const char *dev = output_device(vol);
    if (!dev) return 0;
    if (!strcmp(dev, "plug:bluealsa")) return 1;
    return vol[0] == 'h' ? 3 : 2;
}

static void boy_take_audio(void) {
    gm.resume_owner = media_owner();
    gm.resume_status = gm.resume_owner == 1 ? mclGetPlayStatus() : (media_playing() ? 2 : 0);
    headset_manual();
    spot_yield();
    radio_stop();
    if (gm.resume_status == 2 || gm.resume_status == 3)
        player_save_stop();
    else
        player_stop();
    char vol[5];
    if (boy_route(vol) == 0) dac_take();
}

static void boy_restore_music(void) {
    int owner = gm.resume_owner, status = gm.resume_status;
    gm.resume_owner = gm.resume_status = 0;
    if (status != 2 && status != 3) return;
    if (owner == 2 && status == 2)
        spot_set_playing(1);
    else if (owner == 1 && !check_forbid_play_no_headset(0))
        player_save_start(status == 3);
}

static void boy_close_now_playing(int pop_page) {
    if (!gm.native_ui) return;
    gm.native_ui = 0;
    gm.native_origin = (void *)0;
    gm.held = 0;
    if (pop_page) navigator_back();
    __sync_lock_test_and_set(&wheel_travel, 0);
    wheel_raw = 1;
}
static void boy_open_now_playing(unsigned now) {
    if (gm.native_ui) return;
    if (gm.audio) {
        gm.audio = 0;
        boy_restore_music();
    }
    void *wm = window_manager();
    gm.native_origin = window_manager_get_top_window(wm);
    gm.native_at = now;
    gm.native_ui = 1;
    gm.held = 0;
    wheel_raw = 0;
    __sync_lock_test_and_set(&wheel_travel, 0);
    static const int context[4] = { 0, 0, 0xff, 2 };
    navigator_switch_to_with_context("playing_page", context, 0);
    widget_invalidate_force(wm, 0);
}
static void boy_native_poll(unsigned now) {
    if (!gm.native_ui || now - gm.native_at < 500) return;
    void *wm = window_manager();
    /* The normal touch Back also works. The origin pointer is only compared. */
    if (!window_manager_is_animating(wm) && window_manager_get_top_window(wm) == gm.native_origin)
        boy_close_now_playing(0);
}

static void boy_audio_poll(unsigned now) {
    if (!gm.boy_bridge || (gm.audio_poll && now - gm.audio_poll < 80)) return;
    gm.audio_poll = now;
    boy_native_poll(now);
    struct boy_command cmd;
    if (BLOB_IO(BOY_AUDIO_REQUEST, cmd, 0) && cmd.magic == BOY_AUDIO_MAGIC && cmd.seq &&
        cmd.seq != gm.audio_seq) {
        gm.audio_seq = cmd.seq;
        if (cmd.action == GBA_GAME && !gm.audio) {
            boy_take_audio();
            gm.audio = 1;
        } else if (cmd.action == GBA_MUSIC && gm.audio) {
            gm.audio = 0;
            boy_restore_music();
        } else if (cmd.action == GBA_NOW_PLAYING) {
            boy_open_now_playing(now);
        } else if (cmd.action == GBA_VOLUME) {
            int delta = cmd.value;
            if (delta > 10) delta = 10;
            if (delta < -10) delta = -10;
            int level = g_volume + delta;
            if (level < 0) level = 0;
            if (level > g_maxvolume) level = g_maxvolume;
            if (level > 100) level = 100;
            if (level != g_volume) {
                g_volume = (unsigned char)level;
                device_set_volume(level, 1);
                write_int_config(level, "PLAYSET", "VOLUME");
            }
        } else if (!gm.audio) {
            if (cmd.action == GBA_PREVIOUS) media_key(KEY_BACK_BTN);
            if (cmd.action == GBA_PLAY) media_key(KEY_PLAY);
            if (cmd.action == GBA_NEXT) media_key(KEY_FWD_BTN);
        }
    }
    char vol[5];
    int route = boy_route(vol);
    struct boy_reply reply = { BOY_AUDIO_MAGIC,
                               gm.audio_seq,
                               !gm.audio,
                               g_volume,
                               g_maxvolume,
                               route,
                               route == 1   ? headset_gain()
                               : route == 2 ? g_volume
                                            : 100,
                               media_playing(),
                               gm.native_ui };
    if (memcmp(&reply, gm.audio_last, sizeof reply) && BLOB_IO(BOY_AUDIO_REPLY, reply, 1))
        memcpy(gm.audio_last, &reply, sizeof reply);
}
