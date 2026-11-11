#!/usr/bin/env python3
"""Reproducibly patch only the audited Q2 V1.34 ZIP. Requires LLVM, squashfs-tools and ImageMagick.

--logo swaps the boot splash JPEG (320x375); it defaults to assets/boot-logo.jpg.
"""
import argparse, hashlib, io, json, pathlib, re, shlex, struct, subprocess, tarfile, zipfile
from typing import TypeVar
ROOT = pathlib.Path(__file__).resolve().parents[1]
ZIP_SHA = '6d3e5952d0a71e55f247bdbfcab0353ed21c5e64df9d46c081f27b600bb0109a'
DEMO_SHA = '1aee4394c3b3daafc562f0553cbbae78d55c6e76feb1d60f3c228e6e31e0e70a'
VERSION = '1.8.0'  # major.minor.patch, single digits, shown as is (About and release tag)
BETA = 2  # a pre-release's beta number (1-9), 0 for the release itself
REPO = 'DiamondBond/q2-pod'  # whose releases Network Update fetches (--update-repo) and release.py publishes
# The updater's identity (firmware_v20.info and demo's version literal) must be 5 characters and
# differ from the installed one, so it packs the digits and the beta number: 1.8.0 beta 2 is V1802,
# 1.8.0 itself V1800, so each installs over the betas before it. Releases up to 1.8.0 BETA ended in I
# (1.0.1 was V101I). About shows the stock firmware version and a CFW. Version row with VERSION and
# the edition instead (ringnav_about).
TAG = 'V' + VERSION.replace('.', '') + str(BETA)

def dev_tag(number):
    """--dev build number's tag: V, three base-36 digits and i (V00Ai), so no two test builds share a
    tag, and none equals a release's, and the updater takes each."""
    digits, n = '', (number - 1) % (36**3 - 1) + 1  # 1..ZZZ, then round again
    for _ in range(3): n, d = divmod(n, 36); digits = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'[d] + digits
    return 'V' + digits + 'i'
BASE = 0xb00000
SCRATCH = 0xbc0000  # 768 KB of payload text below it
RING_STEP = 48
HOOKS = {
    'createPlayListItem': (0x517dec, 'ringnav_playlist_create', None),
    'playlist_delete_click': (0x4b6198, 'ringnav_playlist_delete', None),
    'localmusic_playlistsmore_page_init': (0x4b64e4, 'ringnav_playlistsmore', 'playlistsmore'),
    'on_wm_keydown_before_fun': (0x4f22ec, 'ringnav_keydown', 'keydown'),
    'on_wm_keyup_before_fun': (0x4f2490, 'ringnav', 'keyup'),
    'on_wm_tsdown_before_fun': (0x4f280c, 'ringnav_touch', 'touch'),
    'widget_on_paint_border': (0x66c6b0, 'ringnav_paint', 'paint'),
    'widget_dispatch': (0x6710fc, 'ringnav_dispatch', 'dispatch'),
    'on_wm_keylong_fun': (0x4f2614, 'ringnav_keylong', 'keylong'),
    'playset_equalizer_page_init': (0x4b9b64, 'peq_page_init', None),
    'set_equalizer_value': (0x5037f0, 'peq_stock_eq', 'eq'),
    'select_timezone': (0x503970, 'ringnav_timezone', 'timezone'),
    'timezone_row': (0x4c18ec, 'ringnav_timezone_row', 'timezone_row'),  # autotime_page's row i (0: the switch)
    'home_page_init': (0x53485c, 'coverflow_home', 'home'),
    'localmusic_page_init': (0x535024, 'ringnav_localmusic', 'localmusic'),
    # Library lists: an Unknown row with no songs is dropped
    'load_localclass_list': (0x51338c, 'ringnav_localclass', 'localclass'),
    'toolsQueryDbTableAndSort': (0x5dab9c, 'ringnav_sorted_query', 'sorted_query'),
    'toolsQueryDbTable': (0x5da96c, 'ringnav_library_query', 'dbquery'),
    # An artist's albums tab (getAlbumByAlbumArtist): the same album-artist rewrite
    'toolsQueryContentDbTable': (0x5daf64, 'ringnav_content_query', 'content_query'),
    'keylock_rows': (0x4c9c18, 'ringnav_keylock_rows', None),
    'quick_lock': (0x4a17a0, 'ringnav_quick_lock', None),
    'dialog_statusbar_dialog_init': (0x4a23b8, 'ringnav_quick_settings', 'quicksettings'),
    # An Albums row: the album it opens keeps only that album's songs
    'load_album_detaillist': (0x5139bc, 'ringnav_album_detail', 'detail'),
    # An artist's tabs: its albums' art keys (navigation.c ringnav_artist_list)
    'load_localartist_list': (0x513e6c, 'ringnav_artist_list', 'artist'),
    # The three songtable writers; Coverflow keeps its album list until one runs.
    'scanAllMusicFile': (0x507128, 'coverflow_scan_all', 'scan_all'),
    'scanSpecFolder': (0x507304, 'coverflow_scan_folder', 'scan_folder'),
    'deleteMusicFromMusicDb': (0x50b5c4, 'coverflow_delete_song', 'delete_song'),
    # A song's added date: the card's creation stamp, not the access time (coverflow.c)
    'toolsGetFileCreateTime': (0x5d0624, 'ringnav_file_created', 'file_created'),
    'player_play_pause': (0x520220, 'ringnav_play_pause', 'pause'),
    # A user's seek stamps a press for hciplayer's wait for the DAC (docs/internals.md#wired-start)
    'player_seek_time': (0x51f044, 'ringnav_seek_time', 'seek_time'),
    'player_seek_start': (0x51eea0, 'ringnav_seek_start', 'seek_start'),
    'mclSetBtVol': (0x5bf14c, 'ringnav_btvol', 'btvol'),
    'mclSetOutputWay': (0x5bd2b4, 'ringnav_output', 'output'),
    'write_int_config': (0x4fe4ac, 'ringnav_write_config', 'write_config'),
    'main_loop_sleep_default': (0x65bf10, 'ringnav_sleep', 'sleep'),
    'systemset_about_page_init': (0x4c1260, 'ringnav_about', 'about'),
    # Network Update (patch/netupdate.c): the latest GitHub release to the card's update.tar
    'systemset_fwupdate_page_init': (0x4c8710, 'ringnav_fwupdate', 'fwupdate'),
    # Podcasts and Audiobooks: folder_page opened at their folder, and Back there leaving it
    'folder_page_init': (0x533bc0, 'ringnav_folder', 'folder'),
    'folder_back': (0x512588, 'ringnav_folder_back', 'folder_back'),
    # Folder's roots: OTG only with a drive plugged in (g_otg_status)
    'folder_load_top': (0x511e08, 'ringnav_folder_top', 'folder_top'),
    # Videos from Folder view: a video row plays in q2video, not as a song (navigation.c)
    'folder_click': (0x532104, 'ringnav_folder_click', 'folder_click'),
    # Videos: no input reaches the UI while q2video plays
    'window_manager_dispatch_input_event': (0x6804ec, 'ringnav_input', 'input'),
    # SyncLink's (Eddict Player's) play/pause, previous and next pass the Buttons lock (#113)
    'main_loop_post_key_event': (0x69e64c, 'ringnav_post_key', 'post_key'),
    # Key Tone: no click while music plays, and none on the buzzer while headphones or Bluetooth listen
    'buzzeer_switch': (0x4fe228, 'ringnav_buzzer', 'buzzer'),
    # Artists (PLAYSET ARTISTTYPE) without a saved choice is Album Artist
    'read_artistype_value': (0x4fecb0, 'ringnav_read_artistype', None),
    # Tag reads: a FLAC's "ALBUM ARTIST" when stock's libtag finds no ALBUMARTIST
    'toolsGetMusicInfo': (0x5d8868, 'ringnav_music_info', 'music_info'),
    # Embedded art: none from stock's TagLib for an .ogg whose comment header is over OGG_TAGS_MAX (coverflow.c)
    'toolsGetAlbumCover': (0x5d6d6c, 'ringnav_album_cover', 'album_cover'),
    # Power gains Charge limit; Playback gains Artists (Album Artist)
    'systemset_sysset_page_init': (0x4d3a88, 'ringnav_systemset', 'systemset'),
    'systemset_display_page_init': (0x4c6fa8, 'ringnav_display', 'display'),
    # Lock screen (patch/lockscreen.c): the built-in wallpapers in the picture list, the Lock screen
    # page's Clock row and the built-ins' thumbnails, and the lock window's wallpaper and Large clock
    'get_screensaver_image': (0x5054c8, 'lockscreen_images', 'saverlist'),
    'screensaver_rows': (0x4cfd40, 'lockscreen_rows', 'saverrows'),
    'saverscreen_page_init': (0x53f110, 'lockscreen_init', 'saver'),
    # The lock window's cover (unnamed, from its 20 ms timer): repainted once it changes
    'saver_cover': (0x53ede4, 'lockscreen_cover', 'savercover'),
    'save_memoryplay_info': (0x520e68, 'ringnav_savequeue', 'savequeue'),
    # A reboot saves the queue first, as power-off does (playback.c ringnav_reboot): Restart and
    # the firmware update's dialog, which stops the player before it flashes and reboots
    'device_reboot': (0x501e34, 'ringnav_reboot', 'reboot'),
    'dialog_checkfw_dialog_init': (0x49983c, 'ringnav_checkfw', 'checkfw'),
    'mclLoadPlayList': (0x5bcd38, 'ringnav_load', 'load'),
    'mclNextSong': (0x5c03ac, 'ringnav_next', 'next'),
    # Lyrics: a local track without a .lrc takes its LYRICS tag (lyrics.c ringnav_lyric)
    'mclLoadExLyric': (0x5bd0f4, 'ringnav_lyric', 'lyric'),
    # its .lrc's UTF-8 test, which read 6 bytes and no 4-byte character (lyrics.c ringnav_lrc_utf8)
    'mclIsUtf8WithoutBOM': (0x5bb9bc, 'ringnav_lrc_utf8', None),
    # Now Playing's lyrics match (patch/onlinematch.c): LRCLIB alone, in place of stock's Kugou search
    'kugou_obtainlyric': (0x51b204, 'ringnav_lyric_search', None),
    # its ⋯ page: Download Album Lyrics under stock's Download Cover & Lyrics (onlinematch.c)
    'playermore_page_init': (0x539c70, 'ringnav_playermore', 'playermore'),
    # Classic's Search: its tabs' words, after stock's init writes them (navigation.c)
    'dialog_search_dialog_init': (0x4a0004, 'ringnav_search_init', 'search_init'),
    # The sleep timer's switch as a row, under a set that hides its toolbar (navigation.c)
    'systemset_sleepshutdown_page_init': (0x4d1104, 'ringnav_sleeptimer', 'sleeptimer'),
    # Classic's Set Date and Set Time: the title and the selectors' changes, after stock's init (navigation.c)
    'systemset_manualtime_page_init': (0x4cc67c, 'ringnav_manualtime', 'manualtime'),
    # any Tidal track asked for, from a queue or Now Playing, wakes its stream thread (tidal.c)
    'get_tidal_trackurl': (0x483e98, 'ringnav_tidal_track', 'tidaltrack'),
    # its cover match: MusicBrainz/Cover Art Archive and iTunes candidates, then stock's Kugou search
    'kugou_obtaincover': (0x51bd84, 'ringnav_cover_search', 'kugoucover'),
    'mclPrevSong': (0x5c0288, 'ringnav_prev', 'prev'),
    'mclSetPlayMode': (0x5bd6bc, 'ringnav_mode', 'mode'),
    'mclSetPlaySpeed': (0x5bd74c, 'ringnav_speed', 'speed'),
    'mcl_open_preload': (0x5bad68, 'ringnav_open_preload', 'open_preload'),
    # Manual Next/Prev: at the queue ends stock wraps or loads the next folder without mclNextSong
    'player_change_music': (0x51fb34, 'ringnav_change', 'change'),
    'mcl_preload': (0x5bb03c, 'ringnav_preload', 'preload'),
    'memeory_startplayer': (0x521004, 'ringnav_memory', 'memory'),
    'systemset_powermanager_page_init': (0x4cdd14, 'ringnav_powermanager', 'power'),
    # Standby limit: the suspend to RAM between the RTC wake alarm's set and clear
    'toolsIntoStandby': (0x5d0de0, 'ringnav_standby', 'standby'),
    # Standby's card driver reload: no removed and inserted toasts on the wake
    'check_cardmount_status': (0x4f37d4, 'ringnav_cardmount', 'cardmount'),
    'playset_playset_page_init': (0x4bd3a0, 'ringnav_audioset', 'audioset'),
    # Bluetooth's list, rebuilt on every search and link change, gains Resume on connect
    'btmenu_rows': (0x4c3d38, 'ringnav_btrows', 'btrows'),
    # Spotify (patch/spotify.c): Streaming's row, and local music letting librespot go first
    'stream_page_init': (0x540ddc, 'ringnav_stream', 'stream'),
    'mclStartPlayer': (0x5bfe88, 'ringnav_start_player', 'start_player'),
    # View Queue's row and Up Next's (navigation.c): a picked queue index, kept in advanced play's order
    'player_start_queue': (0x520c00, 'ringnav_start_queue', 'start_queue'),
    # A list pick of the playing file only asks (playback.c)
    'player_start': (0x52095c, 'ringnav_player_start', 'player_start'),
    # Card apps (patch/addon.c): USB mode's page, before it unmounts the card, ends them first
    'usbmode_page_init': (0x542b78, 'ringnav_usbmode', 'usbmode'),
    # Tidal cache (patch/tidal.c): the stream URL thread's player start, with its song id in $s2
    'mcl_tidalStartPlayer': (0x5bf6b8, 'tidal_start', 'tidal'),
    # Tidal's playlist, album and favourite-tracks threads: every page, not stock's first 50 (tidal.c)
    'tidalPlaylistDetailThd': (0x48c0e8, 'ringnav_tidal_playlist', 'tidalplaylist'),
    'tidalAlbumDetailThd': (0x48c2ac, 'ringnav_tidal_album', 'tidalalbum'),
    'tidalColTracksThd': (0x488fe4, 'ringnav_tidal_favs', 'tidalfavs'),
    # Ratings (ratings.c rate_bind): Now Playing's heart, held, opens the picker
    'playing_page_init': (0x53dfec, 'ringnav_playing', 'playing'),
    # The Wi-Fi signal (system.c ringnav_wifi_signal): the driver's rx_signal instead of an ioctl
    # that takes the radio out of power save; the status bar and the network monitor (a bal) poll it
    'networkGetWifiSignal': (0x5c2348, 'ringnav_wifi_signal', None),
    'playlist_rebuild': (0x4b5454, 'ringnav_playlist', 'playlist'),
    'widget_on_paint_background': (0x66f78c, 'ringnav_paint_bg', 'paint_bg'),
    'bigcover_page_init': (0x52d8dc, 'ringnav_bigcover', 'bigcover'),
    # Every window's asset (window_open's): Centred Now Playing's page (navigation.c ringnav_ui_load)
    'ui_loader_load_widget_with_parent': (0x666da4, 'ringnav_ui_load', 'ui_load'),
    # Status bar's 1 s tick: the payload's play state, before any paint (navigation.c bar_sync)
    'systembar_showface': (0x5414e0, 'ringnav_showface', 'showface'),
    'style_get_color': (0x65cf7c, 'ringnav_style_color', 'color'),
    'image_manager_add': (0x6575e4, 'ringnav_image_add', 'image'),
    # Song rows: no format badge (navigation.c ringnav_badge)
    'list_get_img_pic': (0x50c40c, 'ringnav_badge', None),
    # Every scrolling label's start: its delay and pace (navigation.c ringnav_hscroll_start)
    'hscroll_label_start': (0x5fafac, 'ringnav_hscroll_start', 'hscroll_start'),
    # Smart Wi-Fi (smart_wifi.c smart_poll): the Wi-Fi page wakes the radio before it reads g_wififlag
    'systemset_wifi_page_init': (0x4d668c, 'ringnav_wifi_page', 'wifi_page'),
    # Smart Wi-Fi (smart_wifi.c smart_hold): stock actions that check the link once, held while the
    # radio comes back. Streaming's Tidal row (stream_page_init's row click), Network service's rows
    # (Wi-Fi transfer only) and Now Playing More's Download Cover & Lyrics (its row's case); none has
    # a symbol.
    'stream_row': (0x540c8c, 'ringnav_stream_row', 'stream_row'),
    'netservice_row': (0x4cd09c, 'ringnav_netservice_row', 'netservice_row'),
    'playermore_online': (0x539324, 'ringnav_online_match', 'online_match'),
    # Scan Folders' row click: a tap on a folder with no folders inside ticks it (scan_folders.c)
    'specfolder_click': (0x53f674, 'ringnav_specfolder_click', 'specfolder_click'),
}
# The payload's stock_<name>_trampoline, the hooks' third element, resumes each hook past its 3-word PIC
# prologue.
# Every audited stock PIC prologue resolves this GOT base.
GP = 0xa3a4e0
# style_get_gradient has no PIC prologue. It is a leaf that null-checks the style and its
# vtable, then tail-calls get_gradient (+0x18); its first two words (beqz a0; nop) become the jump
# and the payload does the whole of it. The third word is pinned too, so the layout is the audited one.
GRADIENT_LEAF = ('style_get_gradient', 0x65cf4c, 'ringnav_style_gradient', (0x10800009, 0, 0x8c820000))
# Videos: window_manager_paint is a leaf too (null-checks the manager and its vtable at +0x94, then
# tail-calls paint, +0xc); the payload does the whole of it and skips it while q2video plays.
WM_PAINT_LEAF = ('window_manager_paint', 0x6804bc, 'ringnav_wm_paint', (0x10800009, 0, 0x8c820094))
# The UI loop's input queue has one way in, main_loop_queue_event, a leaf too (null-checks the loop, its
# queue_event slot at +0x18 and the request, then tail-calls the slot); the payload does the whole of it
# and keeps the touch thread's empty events out of the queue (playback.c ringnav_queue_event).
QUEUE_LEAF = ('main_loop_queue_event', 0x65beb8, 'ringnav_queue_event', (0x10800008, 0, 0x8c990018))
# Videos' player (patch/video.c): a separate executable against the rootfs's own libraries,
# with display_logo's inode metadata.
HELPER, HELPER_LIKE = 'usr/bin/q2video', 'usr/bin/display_logo'
HELPER_LIBS = ['lib/libc-2.28.so', 'lib/libpthread-2.28.so', 'usr/lib/libasound.so.2.0.0']
# Rockbox dual boot (docs/boot.md#rockbox): S90play starts Rockbox whenever the card has it, as an
# iPod with Rockbox does. Play/Pause held at power-on (patch/boot.c reads the key) or Rockbox's
# exit 0x51 (Boot stock OS) starts Q2 Pod for that session only. boot-target selects the next
# power-on default (missing or invalid retains Rockbox). ponytail: the card's mount is awaited (up to 3 s without a card)
# unless Q2 Pod was asked for, as the card probe can't tell "no card" from "not yet". Rockbox runs
# with the launcher contract in its tools/shanlingq2/README; demo starts when it exits, or when the
# card has none. demo keeps its argv[0], which checkappprocess.sh pgreps for. iPod's Home Rockbox row
# (navigation.c rockbox_open) leaves ROCKBOX_FLAG and kills demo: Rockbox runs again, its
# watchdog stopped in case demo's kill was not reached, then demo once more. Any other end of demo
# ends the loop, so the watchdog reboots as stock's would. Wi-Fi is off while Rockbox runs (WIFI_OFF).
BOOT = 'usr/bin/q2boot'
REMOTE = 'usr/bin/q2remote'
S90PLAY = 'etc/init.d/S90play'
ROCKBOX_FLAG = '/tmp/q2pod-rockbox'
# Rockbox can't use Wi-Fi and the radio costs battery, so rockbox() below stops it. Nothing is saved or restored
# here: demo, which follows every Rockbox exit, restarts Wi-Fi from WIFI in config.ini, which wifi_switch writes on
# every toggle. That covers a forced reset during Rockbox too, and writing the config here would lose the user's choice.
# No rmmod 8733bs: that module also holds Bluetooth's power line, and Bluetooth must survive the hand-over.
# The rest ends as stock's networkStop does. wifi_connect.sh because demo may have just started it. No ifconfig wlan0
# 0.0.0.0: BusyBox brings a down interface up to set an address, which would wake a cold boot's radio; demo clears it.
# Home's hand-over pauses first: a shell demo forked for one of these just before it died isn't named yet, and would
# start Wi-Fi under Rockbox. demo is gone, so nothing new can start and a short wait is enough. Not a wait for Wi-Fi to
# finish starting: wifi_connect.sh exits before its /tmp/wpasupplicant_initsuccess without a saved network, and demo
# starts udhcpc later too. A second pass 2 s in, called off if Rockbox has already left and demo is starting Wi-Fi
# again, catches a shell that took longer. ponytail: 300 ms and 2 s are guesses; a shell slower than 2 s still escapes.
WIFI_OFF = 'killall -9 wifi_connect.sh udhcpc wpa_supplicant 2>/dev/null; ifconfig wlan0 down 2>/dev/null'
BOOT_HOOK = (b'    /release/bin/demo &\n', f'''    (
        c=/mnt/data/q2-crash.log
        m=/mnt/data/q2-state
        [ -f $m ] && {{
            [ -f $c ] || {{
                echo "== date"; date
                echo "== demo did not end: a forced restart, or the battery ran out"
            }} > $c
            {{ echo "== last state"; ls -l $m; cat $m; }} >> $c
            # Coverflow's open never finished (coverflow.c cf_trace): the phase it reached
            [ -f /mnt/data/q2-cf-trace ] && {{ echo "== cover flow"; cat /mnt/data/q2-cf-trace; }} >> $c
            # The UI loop stalled (system.c stall_write): what its thread was doing
            [ -f /mnt/data/q2-stall ] && {{ echo "== stall"; cat /mnt/data/q2-stall; }} >> $c
            rm -f $m
        }}
        rm -f /mnt/data/q2-cf-trace /mnt/data/q2-stall
        [ -f $c ] && (
            i=0
            while [ $i -lt 60 ] && ! grep -q ' /mnt/mmc ' /proc/mounts; do usleep 500000; i=$((i+1)); done
            grep -q ' /mnt/mmc ' /proc/mounts || exit
            # the card keeps the two before it: q2-crash.log.1, then .2; they move only once the copy is
            # whole (a full card or a power cut leaves them, and the log stays on /mnt/data)
            l=/mnt/mmc/q2-crash.log
            cp $c $l.new && sync || exit
            [ -f $l.1 ] && mv -f $l.1 $l.2
            [ -f $l ] && mv -f $l $l.1
            mv -f $l.new $l && sync && rm -f $c
        ) &
        rb=/mnt/mmc/.rockbox
        rockbox() {{
            [ -f $rb/rockbox ] || return
            # Rockbox only starts here, so power-on and Home's hand-over both lose Wi-Fi (build.py WIFI_OFF)
            {WIFI_OFF}
            (cd $rb && exec ./rockbox) > $rb/rockbox.log 2>&1
            echo "exit $?" >> $rb/rockbox.log
        }}
        if [ "$(cat /mnt/data/boot-target 2>/dev/null)" != q2pod ] && ! /usr/bin/q2boot; then
            for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
                [ -e /tmp/mmc_add ] && break
                usleep 200000
            done
            rockbox
        fi
        # demo's stderr (docs/boot.md#rockbox-from-home): stock starts its Bluetooth and Wi-Fi helpers from demo,
        # so they inherit it and their reasons for failing land here, in RAM, instead of on the serial console;
        # emptied once a minute past 512 KB (O_APPEND keeps demo writing at its end), its tail goes in the crash
        # log. stdout stays on the console as stock's; --dev takes both (LOG_HOOK)
        (while sleep 60; do n=$(wc -c 2>/dev/null </tmp/q2-demo.err); [ "${{n:-0}}" -gt 524288 ] && : >/tmp/q2-demo.err; done) &
        while :; do
            /release/bin/demo 2>>/tmp/q2-demo.err
            s=$?
            [ -e {ROCKBOX_FLAG} ] || break
            rm -f {ROCKBOX_FLAG}
            killall checkappprocess.sh 2>/dev/null
            # demo is gone: what it just launched gets this long to reach a name WIFI_OFF kills (build.py)
            usleep 300000
            (usleep 2000000; {WIFI_OFF}) &
            rockbox
            kill $! 2>/dev/null
        done
        # 0 and 143 are a power-off's, but a clean end (ringnav_clean_end) removes $m first: still there, it wasn't one
        case $s in 0|143) [ -f $m ] || s= ;; esac
        if [ "$s" ]; then
            {{
                echo "== date"; date
                echo "== demo exit $s"
                # the OOM killer's own lines from all of dmesg: its task dump can push them out of the tail
                echo "== oom"; dmesg | grep -E 'invoked oom-killer|oom-kill:|Killed process|oom_reaper'
                echo "== dmesg"; dmesg | tail -n 200
                echo "== meminfo"; cat /proc/meminfo
                echo "== ps"; ps
                [ -f /tmp/q2-scan-file ] && {{ echo "== last scanned"; cat /tmp/q2-scan-file; }}
                [ -f /tmp/q2-scan.log ] && {{ echo "== scan"; tail -n 60 /tmp/q2-scan.log; }}
                [ -f /tmp/q2-art-file ] && {{ echo "== last art"; cat /tmp/q2-art-file; }}
                [ -f /tmp/q2-art.log ] && {{ echo "== art"; tail -n 60 /tmp/q2-art.log; }}
                [ -f /tmp/q2-demo.err ] && {{ echo "== stderr"; tail -n 60 /tmp/q2-demo.err; }}
                [ -f /tmp/q2-demo.log ] && {{ echo "== q2-demo.log"; tail -n 200 /tmp/q2-demo.log; }}
            }} > $c.tmp 2>&1; mv $c.tmp $c
            sync
        fi
    ) &
'''.encode())
# User boot logo (docs/boot.md#custom-boot-logo): demo copies the card's to /mnt/data, which
# S10mount_ubifs mounts before the splash; without it, the built-in one, or under Light Mode
# (LIGHT=1 in config.ini, also on /mnt/data) its light twin, which iPod builds of the default logo ship.
S11LOGO = 'etc/init.d/S11logo_display_shell'
LOGO, LOGO_LIGHT = 'release/assets/default/raw/images/xx/logo.jpg', 'release/assets/default/raw/images/xx/logo-light.jpg'
REMOTE_HOOK = (b'/usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf\n',
               b'/usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf\n/usr/bin/q2remote &\n')
# --dev only (docs/remote.md#log): demo's console output, and hciplayer's, which inherits it, in RAM, so
# emptied once a minute past 4 MB (O_APPEND keeps demo writing at its end), in place of the release's stderr
# file and its cap. In BOOT_HOOK's start path.
LOG_HOOK = (b'        (while sleep 60; do n=$(wc -c 2>/dev/null </tmp/q2-demo.err); [ "${n:-0}" -gt 524288 ] && : >/tmp/q2-demo.err; done) &\n'
            b'        while :; do\n            /release/bin/demo 2>>/tmp/q2-demo.err\n',
            b'        (while sleep 60; do n=$(wc -c 2>/dev/null </tmp/q2-demo.log); [ "${n:-0}" -gt 4000000 ] && : >/tmp/q2-demo.log; done) &\n'
            b'        while :; do\n            /release/bin/demo >>/tmp/q2-demo.log 2>&1\n')
# --dev only: the wheel's frame log (playback.c wl_flush) and stale-press notes (navigation.c stale_note) go to the
# card's q2-wheel.log, appended, when the card is up, up to 256 KB. In BOOT_HOOK's start path.
WHEEL_LOG_HOOK = (b'        rb=/mnt/mmc/.rockbox\n',
                  b'        w=/mnt/data/q2-wheel.log\n'
                  b'        [ -f $w ] && (\n'
                  b'            i=0\n'
                  b"            while [ $i -lt 60 ] && ! grep -q ' /mnt/mmc ' /proc/mounts; do usleep 500000; i=$((i+1)); done\n"
                  b"            grep -q ' /mnt/mmc ' /proc/mounts || exit\n"
                  b'            c=/mnt/mmc/q2-wheel.log\n'
                  b'            n=$(wc -c 2>/dev/null <$c)\n'
                  b'            [ "${n:-0}" -lt 262144 ] || { rm -f $w; exit; }\n'
                  # renamed first: a flush after this makes a new file for the next boot
                  b'            mv -f $w $w.m && cat $w.m >> $c && sync && rm -f $w.m\n'
                  b'        ) &\n'
                  b'        rb=/mnt/mmc/.rockbox\n')
# The byte in bluealsa's AAC capability holding the 44.1 kHz bit; see docs/internals.md.
BLUEALSA = 'usr/bin/bluealsa'
# Its launcher: keep the A2DP transport open 5 s after hciplayer closes the PCM (a stop, a skip, a cold start), so
# a track change doesn't restart the stream (true-wireless buds started one ear first).
BLUEALSA2 = 'usr/bin/bluealsa2'
BT_KEEPALIVE = (b'bluealsa -c LDAC', b'bluealsa --keep-alive=5 -c LDAC')
SCROBBLE_CA = 'etc/scrobble-ca.pem'
AAC_44K1 = 0x317b8
# DROP is acknowledged before the encoder handles its signal. Flush in the control
# thread instead, so a delayed signal cannot discard the next track's first PCM.
BT_DROP = 0x40cb50
BT_DROP_HANDLER = 0x4147ac
BT_DROP_PAYLOAD = 0x450000
# Return address -> PCM ffb_t offset from the caller's io state. The last caller
# is Q2's LDAC loop, which accumulates 256 samples in saved s6/s7 instead of ffb_t.
BT_PCM_CALLERS = ((0x40649c, 0x74), (0x4150dc, 0x34), (0x41746c, 0x98),
                  (0x418114, 0x34), (0x418c84, 0x60), (0x41a6ec, None))
# A skip left the encoder without PCM for ~400 ms and stock sent no packets, so the headset's buffer
# ran dry: tools/bt_silence.S, linked at BT_SILENCE, returns silence from Drain's timeout instead,
# for up to BT_SILENCE_SAMPLES (interleaved: 2 s at 48 kHz stereo, 1 s at 96 kHz) after real PCM.
BT_SILENCE, BT_SILENCE_SAMPLES = 0x450400, 192000
# (address, stock word, patched with: j or jal to a bt_silence.S symbol, or a word):
# poll's timeout, poll's timeout path (its delay slot takes the moved move a1,zero), the PCM read,
# and the signal jump table's PAUSE entry: demo pauses hciplayer before some skips, so PAUSE takes
# Drain's case (a 100 ms poll, then the fill) instead of polling again unbounded.
BT_SILENCE_SITES = ((0x41465c, 0x8e460030, 'j', 'timeout'), (0x414684, 0x00002825, 'j', 'fill'),
                    (0x414688, 0x0c10c2b0, None, 0x00002825), (0x414754, 0x0c105053, 'jal', 'read'),
                    (0x42d09c, 0x0041463c, None, 0x004147a0))
# platform_init's crash watchdog forks pgrep every 2 s; 10 s is still quick to reboot a dead UI.
WATCHDOG = 'usr/bin/checkappprocess.sh'
WATCHDOG_SLEEP = (b'\tsleep 2\n', b'\tsleep 10\n')
# The config a reset copies to /mnt/data: Enlarged Album Art (10 s wait) and Standby start off. So does stock's Wheel Function: Q2 Pod runs stock in Volume Mode whatever the key holds (WHEEL_MODE), and a Q2 reset here and returned to stock gets Menu Mode, the default stock's changelog names and a missing key gives (the shipped file says 1).
DEFAULT_CONFIG = 'release/assets/default/raw/config.ini'
DEFAULT_CONFIG_OFF = (b'COVERMODE', b'STANDBYMODE', b'WHEELMODE')
def default_config(config):
    for key in DEFAULT_CONFIG_OFF:
        check(config.count(b'\n%s=1\n' % key) == 1, 'Unexpected default config.ini')
        config = config.replace(b'\n%s=1\n' % key, b'\n%s=0\n' % key)
    return config
# toolsSetRtcTime's command, with its padding; -u keeps the RTC in UTC. See docs/internals.md#clock.
RTC_WRITE = (b'hwclock -w\0\0', b'hwclock -wu\0')
# network_checkstatus's command once Wi-Fi connects (its digit at file offset 0x384d2d): stock turns
# the radio's power save (LPS) off for the whole connection; 2 restores the driver's own default,
# PS_MODE_MAX. See docs/internals.md#battery.
WIFI_LPS = (b'rtwpriv wlan0 pm_set lps=0\0', b'rtwpriv wlan0 pm_set lps=2\0')
# The lock window's 12-hour clock format (after its 24-hour hh:mm, by clock_time): stock's HH:TT shows
# "05:PM"; H:mm is the hour 1-12 unpadded and the minutes (tk_date_time_format_impl), "5:07".
LOCK_CLOCK_12H = (b'hh:mm\0\0\0HH:TT\0', b'hh:mm\0\0\0H:mm\0\0')
# Now Playing's info page: its one fill (0x53b9b8), a bal, becomes jal ringnav_np_info, which tidies the lines.
NP_INFO_CALL = 0x53d00c
# Now Playing's A-B markers (0x53b7e4): its three calls of their x (0x53b718), each a bal, become jal ringnav_ab_x,
# which puts them on Classic's bar as ipod_times fitted it to the track.
AB_X_CALLS = (0x53b8b8, 0x53b934, 0x53b944)
# Songs' row bind (0x4ab178): a record with no artist gets stock's "Unknown Artist" through this getFormatString jalr,
# which becomes jal ringnav_song_unknown.
SONG_UNKNOWN_CALL = 0x4ab3b4
# table_client's load loop: its on_load_data jalr becomes jal ringnav_table_load, which calls it and
# then re-picks the row's faces for the text it bound (the load is lw t9, TABLE_LOAD_DATA(s4) at -0x14).
TABLE_LOAD_CALL = 0x5dca2c
# Folder row names are text, not printf formats ("99% Invisible" crashes stock's formatter).
# tk_strncpy writes its terminator at n, so reserve the last byte of the 512-byte buffer.
FOLDER_NAME = ((0x5329a0, 0x24050200, 0x240601ff),  # a2 = 511
               (0x5329ac, 0x8f99a7c4, 0x8f99b028),  # tk_snprintf -> tk_strncpy
               (0x5329b4, 0x8e260008, 0x8e250008))  # a1 = record->name
# mclNextSong's shuffle pick; the payload calls the stock pick, then applies a pending Play next.
SHUFFLE_CALL = (0x5c05e0, 0x0411e7d7)  # bal mcl_shuffle_pick; its delay slot (a0=1) stays
# Hold Return: only on_wm_keylong_fun's final Home call (bal) changes; all stock gates and its release
# guard precede it. It becomes jal compact_now_playing (docs/ipod.md#hold-return).
HOLD_RETURN = (0x4f27fc, 0x0411117c)
# Hold Play/Pause and Return: input_device_status' long press is 1000 ms for every key but 232 (unused on the
# Q2). The key-down's lw $t9 timer_add becomes move $a3, $s3 (the key) and its jalr jal ringnav_hold_timer; the
# timer's bal input_device_status_find_key_long_press_info becomes jal ringnav_hold_info. Both give KEY_HOLD_MS
# to those two keys (patch/navigation.c, docs/internals.md#queue-menu).
HOLD_KEYS = ((0x6b30f8, 0x8f99b220, 0x02603825, None), (0x6b3108, 0x0320f809, None, 'ringnav_hold_timer'),
             (0x6b3458, 0x0411ffa4, None, 'ringnav_hold_info'))
# device_standby's bal awake_screen after the resume from Standby becomes li $v0, 1: the screen stays dark,
# so the resuming key acts as it does dark (Play plays, centre wakes).
STANDBY_WAKE = (0x5022e4, 0x0411ff26, 0x24020001)
# config_init reads SYSSET NAVHIDE and WHEELMODE, V1.34's Show Navigation Bar and Wheel Function, into g_navhide_flag and
# g_wheelmode. Q2 Pod's themes own the bar and every list's box, and its wheel is built on stock's Volume Mode (1): NAVHIDE's
# sb stores $zero (the bar shown) and WHEELMODE's movz $v0, $zero, $v1 becomes li $v0, 1, whatever the keys hold. Neither
# key is written, and stock's two rows are not offered (navigation.c ringnav_display, ringnav_systemset).
NAV_SHOWN = (0x50498c, 0xa0620000, 0xa0600000)
WHEEL_MODE = (0x5049d4, 0x0003100a, 0x24020001)
# V1.34's home_page_init binds a key-up of its own on Home (0x534764: Return opens Now Playing and stops there; its wheel and
# Centre act only outside Volume Mode). Home's keys are ringnav's and on_wm_keyup_fun's, as they were: the widget_on call goes.
HOME_KEYUP = (0x534918, 0x0320f809, 0)
# V1.34's on_wm_keyup_before_fun applies Buttons lock on the lit lock window too (beqz g_lockscreen_pageflag past the lock).
# Buttons lock stays the dark screen's (navigation.c lock_names), as V1.32's: the branch is taken always.
KEYLOCK_LIT = (0x4f2558, 0x10400010, 0x10000010)
# V1.34's volume_dialog_on_keyup leaves at once with the screen off (beqz g_backlight_status), so a dark wheel tick over the
# volume dialog falls through to on_wm_keyup_fun, which opens "dialog/volume_dialog" again: navigator_to looks an open window
# up by that path, never its name "volume_dialog", so every tick stacked one more dialog and none changed the volume. Each
# closes on its own 3 s only once on top, so the lit screen sat under them (#303). The dialog steps dark as V1.32's: no branch.
VOL_DARK = (0x4a36b4, 0x10400045, 0)
# Now Playing More's Artist info (0x53960c) asks navigator_window_is_exist for "artistinfo_page" and "albuminfo_page", then
# closes "localmusic/artistinfo_page" and "localmusic/albuminfo_page", the names it opens them by: navigator_close finds a
# window by its own name (widget_child, a strcmp), which has no folder, so neither ever closed and a second artist page
# opened over the first. The artist page's record is one global its close frees (0x4aec20), so the older page's close freed
# the newer's strings, and the newer's freed them again (#303). Each close takes the name its check asked for, the addiu's
# immediate (docs/internals.md#queue-menu). ponytail: only this handler opens an artist page over another (qm_goto's rows are
# left out while one is open); a new opener must close the old page first, or the page's init wants the guard.
MORE_ARTIST_CLOSE = ((0x5396e4, 0x2624c798, 0x26246404), (0x539710, 0x2484c77c, 0x248497dc))
# on_wm_idle_status, stock's idle callback (every UI pass), calls player_mainscheduling (jalr $t9), whose
# mclAutoChange asks hciplayer for its status and waits for the answer: 40 to 125 round trips a second.
# The call goes through ringnav_mainscheduling (navigation.c), which paces it (docs/internals.md#battery).
MAINSCHEDULING_CALL = (0x4f40d4, 0x0320f809)
# mclStartPlayer's wired mute and sleep from the idle player (crossfade.c start_mute, docs/internals.md#wired-start).
START_MUTE_CALL = (0x5bf634, 0x0411fefa)  # bal mclSetMute; its delay slot (a0=1) stays
START_SLEEP_CALL = (0x5bf64c, 0x0320f809)  # jalr usleep, a0=100000
# checkBluetoothStatus' jalr player_save_start once a headset's A2DP comes up (a0 0: the queue restarts
# unpaused, whatever it was doing). It becomes jal ringnav_bt_connect_start (docs/internals.md#resume-on-connect).
BT_CONNECT_START = (0x4fdaec, 0x0320f809)
# platform_init's control thread start: install callbacks before it discovers boot connections.
BT_INIT = (0x51d058, 0x0320f809)
BT_PARAMS = (0x4fe014, 0x0411f5ef)  # config_bt_init's reset moves before the control thread too
# openBluetooth's jalr btctl_device_status_callback(0x4fbfb0) registers headset_bt_status instead, and
# autoLinkBluetooth's bal checkPairDev also skips while the link list holds a connected link.
BT_STATUS = (0x4fa530, 0x0320f809)
BT_AUTOLINK = (0x4fbd5c, 0x0411fd04)
# player_parsecover_thd's jalr toolsGetExternCover (lw $t9 at 0x51dacc; Now Playing's folder art, after its
# embedded art failed) becomes jal coverflow_player_cover, which gives ffmpeg what stock's decoder refuses.
PLAYER_COVER = (0x51dadc, 0x0320f809)
# player_get_id3info's jalr strcpy of the parsed path (lw $t9 at 0x51f3d8), which every fresh parse reaches
# before Now Playing's lines, becomes jal ringnav_id3_path: a CUE track without sheet tags takes its file's.
ID3_PATH = (0x51f3e0, 0x0320f809)
# The Now Playing timer's jalr scroll_view_scroll_to(scroll_lrc, 0, y, 10) (lw $t9 at 0x53d3d8), its lyric
# follow, becomes jal ringnav_lyric_follow, which pads the content so the view's end is a row top.
LYRIC_FOLLOW = (0x53d3e8, 0x0320f809)
# hscroll_label's timer, at a looping label's end of pass, adds its lull with jalr widget_add_timer (lw $t9 at
# 0x5fb414): jal ringnav_hscroll_lull, which holds the end MARQUEE_HOLD_MS and snaps the label back to its
# start when the lull ends, so the start's delay (ringnav_hscroll_start) holds there instead of at the end.
HSCROLL_LULL = (0x5fb418, 0x0320f809)
# A pass of a label that doesn't loop ends at once, its timer removed (bnez loop at 0x5fb358 falls through), so
# stock paints its start with the ellipsis the moment it reaches its end. The branch goes to the lull for
# every label (b 0x5fb3f0): ringnav_hscroll_lull holds the end, and its end stops a single pass there.
HSCROLL_ONCE = (0x5fb358, 0x14400025, 0x10000025)
# The page refresh's bal to the lyric row builder (0x53b3bc, which ends at offset 0) becomes jal
# ringnav_lyric_rows, which keeps the offset: Play/Pause no longer flicks the lyrics to the top.
LYRIC_ROWS = (0x53ce10, 0x0411f96a)
# Stock's .lrc loader (0x5bbb38) opens the file it parses with jalr fopen (lw $t9 at 0x5bc074): jal
# ringnav_lrc_open, which keeps a copy that ringnav_lyric reads again, without its blank lines and, for iPod's
# lyrics page, with its milliseconds and word stamps.
LYRIC_OPEN = (0x5bc07c, 0x0320f809)
# parse_albumcovertask_thd's jalr toolsGetAlbumCover (Albums grids; Folder view's folder and track rows),
# each with its lw $t9, become jal coverflow_grid_cover, which gives ffmpeg the Ogg pictures stock skips;
# the Albums grid's (first, before stock's toolsGetExternCover) jal coverflow_album_cover, and the folder
# row's jal coverflow_folder_cover, which tries the folder's first song by name first.
GRID_COVER = ((0x4f846c, 0x4f8468, 'coverflow_album_cover'), (0x4f9568, 0x4f9558, 'coverflow_folder_cover'),
              (0x4f9624, 0x4f9620, 'coverflow_grid_cover'))
# The bal toolsTrimLeft on each name copy in the two library name comparators (0x5cc540, 0x5cce58, the
# Chinese and other-language sorts); they become jal ringnav_sort_key, which also drops a leading article.
SCAN_REFRESH = (0x5dc144, 0x0411fd88)  # toolsLoadAllFile's existing-song check, folder scans only
AUDIO_EXTENSION_CALLS = (0x5d497c, 0x5d66cc)  # toolsLoadDirectory and recursive scan
M3U_DIR_CALL = 0x518578  # importPlayList's toolsGetDirectoryM3u8 listing (card apps' own imports)
SORT_TRIMS = (0x5cc638, 0x5cc6a4, 0x5ccf50, 0x5ccfbc)
# Tag text as tagged (docs/internals.md#tag-text): toolsFixId3ErrorCode (0x5d8620), which toolsGetMusicInfo
# runs on each of its six fields, took any field whose every character TagLib decoded below U+0100 ("Lo que
# la música me enseñó") for GBK bytes when they went to UTF-8 and back unchanged, and showed it in Chinese
# ("m" + 鷖...). That round trip's strcmp (jalr, with the round trip's copy in its delay slot) becomes jal
# ringnav_gbk_tag, which also asks that the bytes read as GBK; otherwise TagLib's UTF-8 copy is kept.
TAG_TEXT = (0x5d87a4, 0x0320f809)
# toolsGetMusicInfo's genre strcpy (dst info+0x200 at 0x5d8bf8, the 0x80-byte style field before
# title at +0x280) becomes jal ringnav_genre_copy. Its delay slot still loads the joined genre into
# a1; bounding this one copy covers both the library scan and the player's tag reads.
GENRE_COPY = (0x5d8c04, 0x0320f809)
# The CUE sheet parser (the unnamed function after toolsFreeStSongInfo) ran each field through the same
# toolsIconvConvert(field, "utf-8", "gbk"); its seven bal become jal ringnav_cue_text.
CUE_TEXT = (0x5d2ff8, 0x5d30bc, 0x5d3174, 0x5d3230, 0x5d34f0, 0x5d38ac, 0x5d3c34)
# dialog_modal (0x6521bc) ends by queueing its dialog's close (0x651c7c) with idle_add(close, dialog): a
# bare pointer, so a dialog freed before that idle ran was read from freed memory. Its bal (the close in
# its delay slot) becomes jal ringnav_dialog_close (system.c), which closes only a dialog still open.
DIALOG_CLOSE = 0x652314
# The library scan's folder walk (the unnamed recursive function toolsLoadAllFile runs, 0x5d648c) skips
# ".", "..", .LOST.DIR, lost+found and the like by name; its first strcmp (jalr, with "." in its delay
# slot) becomes jal ringnav_scan_dir, which skips every hidden folder, as Folder view's list does: a card
# app's own (q2-deezer's stream cache) never joins the Library.
SCAN_DIRS = (0x5d6534, 0x0320f809)
# toolsLoadAllFile stops a scan at 20,000 songs: lw $2,count; slti $2,$2,0x4e20 before each entry. 65,000
# overflows slti's signed immediate, so the lw becomes jal ringnav_scan_room and the slti loads the count
# into a0 in its delay slot; the bnez after it, and its delay slot's stop-flag load, stay.
SCAN_LIMIT = (0x5dc0a4, 0x8fc2ff14, 0x28424e20, 0x8fc4ff14)  # lw $2/$4, -0xec($fp)
# Library Albums' cover cache names (navigation.c art_key): the row renderer's snprintf of the name,
# with a2 the row (addiu a2,"%s" becomes move a2,s3), the Albums page's albumcoverinfo_init calls
# and the album page's big cover snprintf become jal to the payload; so do an artist page's albums
# renderer (its row in s2) and albumcoverinfo_init calls on tab entry, return and init.
ALBUM_ART = ((0x4a52f0, 'ringnav_art_name'), (0x4a5c34, 'ringnav_albumcovers'), (0x4a5c9c, 'ringnav_albumcovers'),
             (0x4a75c4, 'ringnav_albumcovers'), (0x4a9078, 'ringnav_art_header'),
             (0x4adaec, 'ringnav_artist_art_name'), (0x4aeb3c, 'ringnav_artist_covers'),
             (0x4b07f0, 'ringnav_artist_covers'), (0x4b12ac, 'ringnav_artist_covers'))
ALBUM_ART_ROW = ((0x4a52f4, 0x24c6a8c4, 0x02603025), (0x4adaf0, 0x24c6a8c4, 0x02403025))
# albumcoverinfo_init's and foldercoverinfo_init's jalr _deque_push_back of each row's cover task becomes jal
# ringnav_cover_push, which frees the task's name and path once the list has copied them; see docs/internals.md.
COVER_PUSH = ((0x4f77f4, 0x4f77e8), (0x4f799c, 0x4f7994))  # the jalr, the lw t9 that loads it
# Library Artists rows: the row bind's bal to class_icon becomes jal ringnav_class_icon, which hides the head.
CLASS_ICON = 0x4b37fc
# Album grids' and Songs' scroll start no longer pauses the cover worker (sw $3,0($2) -> nop); see docs/internals.md.
COVER_PAUSE = ((0x4acdf0, 0xac430000, 0), (0x4a3930, 0xac430000, 0), (0x4a9b90, 0xac430000, 0))
# The playlist, album and favourite-tracks threads' done store (sb $3,0($2) -> nop): their hooks
# (tidal.c tidal_pages) run them once per page and set the page's flag after the last.
TIDAL_PAGE_DONE = ((0x48c28c, 0xa0430000, 0), (0x48c41c, 0xa0430000, 0), (0x489144, 0xa0430000, 0))
# An artist's albums scroll end divides yoffset by its 160 px rows, not 256; see docs/internals.md.
ARTIST_COVER_ROW = ((0x4b0adc, 0x244400ff, 0x240400a0),   # addiu $4,$2,0xff -> li $4,160
                    (0x4b0ae4, 0x28430000, 0x0044001a),   # slti $3,$2,0 -> div $zero,$2,$4
                    (0x4b0ae8, 0x0043200a, 0),            # movz $4,$2,$3 -> nop
                    (0x4b0af0, 0x00042203, 0x00002012))   # sra $4,$4,8 -> mflo $4
# switchBtPriority sends LDAC quality 0x16 (SQ, 660 kbps) for both HQ (row 0) and Standard (row 1).
# The dispatch keeps 0x15 + row in its free 0x14($sp) slot, so HQ sends 0x15 (990 kbps); see docs/internals.md.
LDAC_TABLE = 0x244228a0  # stock's addiu $2,$2,<its jump table's low half>, which the lw that replaces it keeps
LDAC_HQ = ((0x4fa88c, 0x00042080, 0x00041880),   # sll $3,$4,2
           (0x4fa890, LDAC_TABLE, 0x00431021),   # addu $2,$2,$3
           (0x4fa894, 0x00442021, 0x8c420000 | LDAC_TABLE & 0xffff),   # lw $2,<the table's low half>($2)
           (0x4fa898, 0x8c820000, 0x24840015),   # addiu $4,$4,0x15
           (0x4fa8a4, 0x00000000, 0xafa40014),   # sw $4,0x14($sp), jr's delay slot
           (0x4fa8b0, 0x24040016, 0x8fa40014))   # lw $4,0x14($sp), was li $4,0x16
# check_mem_thd (platform_init's detached thread) returns NULL on entry: it never opens drop_caches,
# writes 3 to it or wakes each second. lui/addiu $gp -> jr $ra; move $v0,$zero.
DROP_CACHES = ((0x51cb10, 0x3c1c0052, 0x03e00008), (0x51cb14, 0x279cd9d0, 0x00001025))
# Stock V1.34's main loop starts a thread of its own on /dev/input/event4, BlueZ's AVRCP keys, and posts Play and Pause
# alike as the Play button, Next and Previous as the skip buttons. patch/headset.c's worker reads that device already,
# and tells Play from Pause, holds a connecting headset's Play for Resume on Connect and sends the keys to the player
# that has the sound, so each press would act twice. btinput_thread_run returns NULL on entry, as DROP_CACHES' thread
# does, which its caller (0x635250) takes as no such device: the thread never starts.
BT_INPUT = ((0x638dc0, 0x3c1c0040, 0x03e00008), (0x638dc4, 0x279c1720, 0x00001025))
# Two threads started at boot polled a start flag with usleep(10000) for the life of the player: Baidu
# Pan's (baidu_pthread_playurl) and Tidal's URL worker (after tidalGetFavoritesIdsThd). Each poll call
# (jalr $25) becomes jal to its payload function (tidal.c), which parks the thread: Tidal's wakes once
# per track URL asked for, Baidu's never.
STREAM_POLL_CALLS = ((0x480528, 'ringnav_baidu_poll'), (0x487114, 'ringnav_tidal_poll'))
# get_onlineid3info's player_set_coverinfo(REC_COVER, REC_NAME, type) for a 0xf1 record: its jalr, the
# REC_COVER load in its delay slot, and the stub that adds the record ($s1) for tidal.c app_coverinfo
COVERINFO_CALL = (0x493678, 0x8e240028, 'app_coverinfo_call')
# Stock routines tidal.c's URL queues call at their addresses (not FUNCTIONS, whose names test/patch.py
# stands in for everywhere: these run as stock's in its scenarios). Checked against the ELF's symbols.
APP_URL_CALLS = {'mclSetPause': 0x5bd534, 'tidal_playurl': 0x5bc684, 'curl_stop_httpget': 0x505f98,
                 'toolsFreeStSongInfo': 0x5d2ae0, 'player_set_coverinfo': 0x51d60c}
# The jalr usleep (lw $t9,-0x4e4($gp)) of mclSyncPlayerStatus' 200 ms loop becomes jal ringnav_dark_usleep
# (its argument set in the delay slot): at least DARK_POLL_US while ringnav_sleep finds the Q2 dark, quiet and
# the DAC off, else stock's. parse_albumcovertask_thd's 50 ms and player_parsecover_thd's 100 ms waits become
# jal ringnav_dark_cover_usleep: at least DARK_POLL_US whenever the screen is off, playing or not.
DARK_POLLS = ((0x5bb9ac, 'ringnav_dark_usleep'), (0x4f8130, 'ringnav_dark_cover_usleep'),
              (0x51da6c, 'ringnav_dark_cover_usleep'))
# Download Cover & Lyrics' accept (0x535c14) snprintfs cp -rf /tmp/oslyric_N "/mnt/mmc/.downpic/<name>" for
# system(); that jalr snprintf becomes jal ringnav_lyric_save (onlinematch.c), which writes the lyrics beside
# the song instead. The word before it, addiu $a2,$a2,0x2630 (the format at 0x786260), is pinned.
LYRIC_SAVE_CALL, LYRIC_SAVE_FMT = 0x536178, (0x536174, 0x24c66260)
# The encoder thread's ts_read call (jalr $t9) becomes jal ringnav_ts_read, which makes the wheel's
# ticks from the samples (playback.c wheel_sample).
WHEEL_TS_READ = (0x6384ec, 0x0320f809)
# Smart Wi-Fi (smart_wifi.c smart_ready): networkStart and networkStop act only when initNetworkModule's
# type, the unnamed word NET_TYPE (patch/offsets.inc), is 2: lw $v1,-0x53c($v0) off GOT page 0xa50000;
# li $v0,2.
NET_TYPE_READS = ((0x5c1adc, 0x8c43fac4), (0x5c1ae0, 0x24020002), (0x5c1de8, 0x8c43fac4), (0x5c1dec, 0x24020002))
# English labels renamed, as an iPod words them: Now Playing More's queue, which stock calls "Playlists"
# (player_playlist, used nowhere else); the Library's plural categories; settings rows in title case
# and plain words; the Lock screen help; and Baidu without its (BETA).
LABELS = tuple((k.encode(), o.encode(), n.encode()) for k, o, n in (
    ('msg_playlistfull', 'Playlist Is Full', 'Could not create playlist'),
    ('player_playlist', 'Playlists', 'Queue'), ('system_powermanager', 'Idle', 'Power'),
    ('powerm_deep_off', 'standby/shutdown', 'Standby'),
    ('local_songer', 'Artist', 'Artists'), ('local_album', 'Album', 'Albums'),
    ('local_allsongs', 'All Songs', 'Songs'), ('local_playlist', 'Playlist', 'Playlists'),
    ('playset_bootvol', 'Default Vol', 'Default Volume'),
    ('playset_covermode', 'Enlarged album art(10s wait)', 'Enlarged Album Art'),
    ('playset_filter', 'Digital filters', 'Digital Filters'),
    ('playset_memplay', 'Memory playback', 'Memory Playback'),
    ('playset_noplay', 'Headphones required for playback', 'Headphones Required'),
    ('replaygain_track', 'Audio', 'Song'),
    ('small_playset', 'Playback Setting', 'Playback'), ('small_sysset', 'System Setting', 'System'),
    ('system_wifiset', 'Wireless Setting', 'Wireless'), ('system_display', 'Display setting', 'Display'),
    ('system_netservice', 'Network Service', 'Network'), ('system_time', 'Date and time', 'Date & Time'),
    ('netservice_wifitrans', 'Wi-Fi file transfer', 'Wi-Fi File Transfer'),
    ('system_keylock', 'Buttons lock', 'Buttons Lock'), ('system_keytone', 'Key tone', 'Key Tone'),
    ('system_carmode', 'In-Vehicle mode', 'In-Vehicle Mode'), ('system_reset', 'Reset settings', 'Reset Settings'),
    ('reset_initset', 'Reset only settings', 'Reset Settings Only'),
    ('reset_factory', 'Full factory reset', 'Full Factory Reset'),
    ('display_lighttime', 'Screen off time', 'Screen Off Time'), ('display_screenlock', 'Lock screen', 'Lock Screen'),
    ('screenlock_timeshow', 'Lockscreen Time Display', 'Show Time'),
    ('screenlock_about', 'Custom lock screen', 'Custom Lock Screen'),
    ('screenlock_info1', '1.Custom lock screen:Images must be stored in "lockscreenimage" folder,located in root folder of micro SD card;',
     '1. Put the pictures in a folder named "lockscreenimage" at the top of the card.'),
    ('screenlock_info2', '2.Supports PNG and JPG，resolution exactly 375 x 320，under 1MB;',
     '2. PNG or JPG, exactly 375 x 320 pixels, under 1 MB each.'),
    ('screenlock_info3', '3.Maximum of 5 images in folder.', '3. Up to 5 pictures.'),
    ('about_sn', 'Serial number', 'Serial Number'), ('about_version', 'FW. Version', 'Version'),
    ('list_baidu', 'Baidu Netdisk(BETA)', 'Baidu Netdisk'), ('msg_poweroff', 'Confirm Shutdown?', 'Shut Down?'),
    ('system_prove', '技术证明', 'Certification'),  # About's regulatory row, Chinese in stock's English
    ('genre', 'Genres', 'Genre'), ('player_era', 'Era', 'Year'), ('batch_addplaylist', 'Add to playlist', 'Add to Playlist'),
    ('msg_importplaylist', 'Import Playlist?', 'Import Playlists?'),  # it imports the whole folder
    ('scan_startscan', 'Scanning', 'Scan')))  # Scan specified folders' button; wifi_scan keeps its "Scanning"

# Wi-Fi transfer's web page (stock thttpd on :8888): in English, an Upload Folder button, and a folder,
# picked or dropped, keeps its tree: each file's folders are made with stock's create (a plain mkdir) before
# it goes, as stock's upload only moves a file into an existing folder. The formats Q2 Pod's pages read are allowed too.
MKDIR_JS = r'''// Each folder of a folder upload is made once per batch, parents first; stock's create is a plain mkdir,
// so a folder that exists fails harmlessly and a real failure shows as its files' upload error.
// ponytail: the stock CGIs run mkdir and mv through system() in double quotes, so a name with " ` or $
// can't be uploaded (plain files too); replacing the CGIs would lift it.
var _made = {};
function _mkdir(path, base) {
  if (path == base)
    return $.Deferred().resolve();
  if (!_made[path]) {
    var made = $.Deferred();
    _mkdir(path.replace(/[^\/]*\/$/, ""), base).always(function() {
      $.ajax({url: 'create', type: 'POST', data: {path: path.slice(0, -1)}, dataType: 'json'})
        .always(function() { made.resolve(); });
    });
    _made[path] = made;
  }
  return _made[path];
}

function _showError('''
WEB = {
  'usr/share/web/index.html': [
    ('山灵音乐', 'Shanling Q2'),
    ('支持音频格式:ISO,DFF,DSF,APE,FLAC,AIF,AIFF,WAV,M4A,AAC,MP2,AC3,DTS,MP3,OGG,WMA,CUE,M3U,M3U8<br/>图片格式:PNG,JPG,JPEG<br/>歌词格式:LRC<br/>固件升级包:BIN',
     'Audio: ISO, DFF, DSF, APE, FLAC, AIF, AIFF, WAV, M4A, M4B, AAC, MP2, AC3, DTS, MP3, OGG, WMA, CUE, M3U, M3U8<br/>'
     'Pictures: PNG, JPG, JPEG<br/>Lyrics: LRC<br/>Videos: MP4, M4V, MKV, AVI, MOV, MPG<br/>Books: TXT, EPUB<br/>Firmware: BIN'),
    ('''上传文件
          <input id="fileupload" type="file" name="files[]" multiple>
        </button>''', '''Upload Files
          <input id="fileupload" type="file" name="files[]" multiple>
        </button>
        <button type="button" class="btn btn-primary fileinput-button" id="upload-folder">
          <span class="glyphicon glyphicon-folder-open"></span> Upload Folder
          <input id="folderupload" type="file" name="files[]" multiple webkitdirectory>
        </button>'''),
    ('创建文件夹', 'New Folder'), ('返回上一级', 'Up'), ('正在进行文件上传', 'Uploading'), ('新建文件夹', 'New Folder'),
    ('请输入要创建的文件夹的名称:', 'Folder name:'), ('取消', 'Cancel'), ('移动项目', 'Move'), ('请输入此项的新位置:', 'New location:')],
  'usr/share/web/js/index.js': [
    ('"LRC","BIN"];', '"LRC","BIN","M4B","MP4","M4V","MKV","AVI","MOV","MPG","TXT","EPUB"];'),
    ('function _showError(', MKDIR_JS),
    ('''  $("#upload-file").click(function(event) {
    $("#fileupload").click();
  });
''', '''  $("#upload-file").click(function(event) {
    $("#fileupload").click();
  });
  $("#upload-folder").click(function(event) {
    $("#folderupload").click();
  });
'''),
    ('$("#fileupload").click(function(event) {', '$("#fileupload, #folderupload").click(function(event) {'),
    ('    dropZone: $(document),', '    fileInput: $("#fileupload, #folderupload"),\n    dropZone: $(document),'),
    ('''      $(".uploading").hide();''', '''      if (!$("#uploads").children().length) {  // rows still waiting on their folders keep it up
        $(".uploading").hide();
        _made = {};
      }'''),
    ('''      var file = data.files[0];
      if(_isValidFileType(file.name)) {
        data.formData = {
          path: _path
        };
        data.context = $(tmpl("template-uploads", {
          path: _path + file.name
        })).appendTo("#uploads");
        var jqXHR = data.submit();
        data.context.find("button").click(function(event) {
          jqXHR.abort();
        });''', '''      var file = data.files[0];
      // a dropped folder's files carry relativePath ("Album/CD1/"), a picked one's webkitRelativePath ("Album/CD1/01.flac")
      var dir = _path + (file.relativePath || (file.webkitRelativePath || "").replace(/[^\\/]*$/, ""));
      if (dir != _path && file.name.charAt(0) == ".")
        return;  // .DS_Store, macOS's ._ copies
      if(_isValidFileType(file.name)) {
        data.formData = {
          path: dir
        };
        data.context = $(tmpl("template-uploads", {
          path: dir + file.name
        })).appendTo("#uploads");
        $(".uploading").show();  // before its upload starts: it may wait on its folders
        var jqXHR = null;
        _mkdir(dir, _path).always(function() {
          if (data.context.parent().length)  // not cancelled while its folder was made
            jqXHR = data.submit();
        });
        data.context.find("button").click(function(event) {
          if (jqXHR) jqXHR.abort(); else data.context.remove();
        });'''),
    ('"无法获取列表内容 - \\""', '"Can\'t list \\""'),
    ('"无法创建\\"" + name + "\\"到\\""', '"Can\'t create \\"" + name + "\\" in \\""'), ('"无法移动\\""', '"Can\'t move \\""'), ('"\\"到\\""', '"\\" to \\""'),
    ('"无效的命名 - \'\'"', '"Invalid name - \'\'"'), ('"你确定要删除吗?"', '"Delete this item?"'),
    ('"没有权限删除\\""', '"Not allowed to delete \\""'), ('"无法删除\\""', '"Can\'t delete \\""'),
    ('"没有权限上传内容到\\""', '"Not allowed to upload to \\""'), ('"不支持上传此类型文件 - \\""', '"Unsupported file type - \\""'),
    ('"上传文件失败 \\"" + file.name + "\\" to \\"" + _path', '"Can\'t upload \\"" + file.name + "\\" to \\"" + data.formData.path'),
    ('"没有权限添加内容到\\""', '"Not allowed to add to \\""'), ('"新建文件夹"', '"New Folder"'),
    ('"没有权限移动内容到此目录 - \\""', '"Not allowed to move to \\""'), ('"当前已是顶级目录"', '"Already at the top folder"'),
    ('//去掉后面的/', '// drop the trailing /'), ('//得到上一级目录，/结束', '// the parent folder, ending in /')],
  # A dropped folder's entries: Chromium's readEntries gives at most 100 a call, so read until it gives none.
  'usr/share/web/js/jquery.fileupload.js': [
    ('''                dirReader.readEntries(function (entries) {
                    that._handleFileTreeEntries(
                        entries,
                        path + entry.name + '/'
                    ).done(function (files) {
                        dfd.resolve(files);
                    }).fail(errorHandler);
                }, errorHandler);''', '''                var all = [], readEntries = function () {
                    dirReader.readEntries(function (entries) {
                        if (entries.length) {
                            all = all.concat(Array.prototype.slice.call(entries));
                            return readEntries();
                        }
                        that._handleFileTreeEntries(
                            all,
                            path + entry.name + '/'
                        ).done(function (files) {
                            dfd.resolve(files);
                        }).fail(errorHandler);
                    }, errorHandler);
                };
                readEntries();''')]}

def patch_web(path, data):
    text = data.decode()
    for old, new in WEB[path]:
        check(old in text, f'Unexpected {path}: {old[:40]!r}')
        text = text.replace(old, new)
    check(not re.search('[　-鿿＀-￯]', text), f'Chinese left in {path}')
    return text.encode()

# Stock's UI assets carry Chinese text (docs/internals.md#english), most of it designers' placeholders that
# stock replaces, but some shows through in every language (Search's tabs, Now Playing's info labels, Folder's
# title, Quick Settings' row names, the test-mode pages). Every Chinese text in them is given in English
# (english_ui); a per-asset entry wins over the shared one. The T9 keyboard's EN key opened its Chinese
# pinyin page: it toggles case instead (ABC on the lowercase page, abc on the capitals), so that page is
# never reached, and its 《》 key types «».
CJK = re.compile('[\u3000-\u9fff\uff00-\uffef]')
ENGLISH = {
    '文件夹': 'Folder', '正在播放': 'Now Playing', '本地音乐': 'Library', '更多': 'More', 'AB复读': 'A-B Repeat',
    '倍速': 'Speed', '*仅对含48KHz以下有效': '*Only for 48 kHz and below', '无歌词': 'No lyrics', '专辑': 'Album',
    '风格': 'Genre', '大小': 'Size', '年代': 'Year', '编码': 'Format', '位深': 'Bit Depth', '比特率': 'Bit Rate',
    '采样率': 'Sample Rate', '位置': 'Location', '流媒体': 'Streaming', '自动扫描音乐': 'Auto Scan',
    '立即更新曲库': 'Update Library Now', '指定文件夹扫描': 'Scan Folders', '确认': 'OK', '确定': 'OK',
    '取消': 'Cancel', '单曲': 'Songs', '歌手': 'Artists', '蓝牙': 'Bluetooth', '按键锁': 'Key Lock',
    '低增益': 'Low Gain', 'USB存储': 'USB Storage', '播放设置': 'Playback', '设置': 'Settings',
    '自定义': 'Custom', '重置': 'Reset', '在浏览器输入以下网址:': 'Open this address in a browser:',
    '下载中': 'Downloading', '已下载': 'Downloaded', '百度网盘': 'Baidu Netdisk', '退出登录': 'Sign Out',
    '登录': 'Sign In', '请使用百度网盘或百度App扫码登录': 'Scan with the Baidu app to sign in',
    '百度加密技术保护您的隐私安全': 'Baidu encryption protects your privacy',
    '蓝牙定频测试,请操作电脑连接': 'Bluetooth RF test: connect from a computer', '按下一曲退出': 'Press Next to exit',
    '亮度测试': 'Brightness Test', '下一步': 'Next', '蓝牙测试': 'Bluetooth Test', '右': 'Right', '左': 'Left',
    '下': 'Down', '上': 'Up', '左滑': 'Swipe Left', '右滑': 'Swipe Right', '按键测试': 'Key Test',
    '耳机输出测试': 'Headphone Test', '请插单端耳机': 'Plug in single-ended headphones',
    '播放(男左女右)': 'Play (left male, right female)', '蓝牙接收测试': 'Bluetooth Receiver Test', '完成': 'Done',
    '电池检测': 'Battery Test', '当前电量：': 'Battery:', '充电状态：': 'Charging:', 'TF测试': 'microSD Test',
    '触摸测试': 'Touch Test', 'wifi测试': 'Wi-Fi Test', '太阳之子': 'Album', '周杰伦': 'Artist',
    '播放全部': 'Play All', '导入歌单': 'Import Playlists', '导出歌单': 'Export Playlists', '测试歌单': 'Playlist',
    '共0首': '0 songs',
}
ENGLISH_IN = {'testmode/test_key.bin': {'中': 'Centre'}, 'kb_default_t9.bin': {'中': 'abc', '《》': '«»'},
              'dialog/search_dialog.bin': {'专辑': 'Albums', '风格': 'Genres'},
              'systemset/screensaver_about.bin': {}}

def english_ui(rel, data):
    """rel's widgets' Chinese text in English, and the T9 keyboard's Chinese page out of reach."""
    from ipod import decode, encode, walk
    root = decode(data)
    check(encode(root) == data, f'{rel}: UI round trip differs')
    upper = {id(n) for page in walk(root) if page[2].get('name') == 'upper' for n in walk(page)}
    for n in walk(root):
        props = n[2]
        if rel == 'kb_default_t9.bin' and props.get('name') == 'page:chinese':  # EN: the case toggle
            props['name'], props['text'] = ('page:lower', 'abc') if id(n) in upper else ('page:upper', 'ABC')
        if rel == 'kb_default_t9.bin' and props.get('name') == 'opt:《》':
            props['name'] = 'opt:«»'
        text = props.get('text', '')
        if not CJK.search(text):
            continue
        if rel == 'systemset/screensaver_about.bin':  # Polish, with a full-width comma
            props['text'] = text.replace('，', ', ')
        else:
            props['text'] = ENGLISH_IN.get(rel, {}).get(text) or ENGLISH[text]
    check(not any(CJK.search(n[2].get('text', '')) for n in walk(root)), f'Chinese left in {rel}')
    return encode(root)

# The stock font (Arial merged with a CJK font) gives curly quotes the CJK em advance, a wide gap
# inside words wherever a tag has one; its cmaps map them to the straight glyphs instead. Its hearts
# mix too: the ratings' ♡ (CJK: smaller, higher, the em advance) is fitted to Arial's ♥ box, both a wider advance.
FONT = 'release/assets/default/raw/fonts/default.ttf'
QUOTES = {0x2018: 0x27, 0x2019: 0x27, 0x201c: 0x22, 0x201d: 0x22}

def font_quotes(data):
    """Only cmap is decompiled; fontTools copies every other table's bytes as they are, and head keeps
    its stock timestamp so the build is reproducible."""
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(data), lazy=True, recalcTimestamp=False)
    for table in font['cmap'].tables:
        if table.isUnicode():
            check(all(c in table.cmap for c in QUOTES.values()), 'Font without straight quotes')
            table.cmap.update({c: table.cmap[q] for c, q in QUOTES.items()})
    glyf, hmtx, cmap = font['glyf'], font['hmtx'], font.getBestCmap()
    full, empty = glyf[cmap[0x2665]], glyf[cmap[0x2661]]
    check(not empty.isComposite() and empty.xMin == 2 and empty.yMax == 159, 'Unexpected ♡ glyph')
    sx, sy = (full.xMax - full.xMin) / (empty.xMax - empty.xMin), (full.yMax - full.yMin) / (empty.yMax - empty.yMin)
    empty.coordinates.transform(((sx, 0), (0, sy)))
    empty.coordinates.translate((full.xMin - round(empty.xMin * sx), full.yMin - round(empty.yMin * sy)))
    empty.coordinates.toInt()
    empty.recalcBounds(glyf)
    hmtx[cmap[0x2661]] = hmtx[cmap[0x2665]] = (hmtx[cmap[0x2665]][0] + 32, full.xMin)  # a gap between hearts
    out = io.BytesIO()
    font.save(out, reorderTables=False)
    return out.getvalue()

PLAY_MARK = 0x25b6

def play_mark(font):
    """U+25B6 drawn into font, a face without one: the mark before View Queue's playing title
    (navigation.c stock_row_style), which a row could otherwise only show in stock's face. A triangle on
    the middle of the capitals, 78% of their height, its left edge a pixel in at a Classic row's size."""
    cap = font['OS/2'].sCapHeight
    x0, x1, y0, y1 = round(cap * .073), round(cap * .773), round(cap * .11), round(cap * .89)
    name, points = 'uni25B6', [(x0, y0), (x0, y1), (x1, (y0 + y1) // 2)]  # clockwise, as glyf's contours are
    def drawn(pen, points):
        pen.moveTo(points[0])
        for point in points[1:]: pen.lineTo(point)
        pen.closePath()
        return pen
    if 'CFF ' in font:
        from fontTools.pens.t2CharStringPen import T2CharStringPen
        cff = font['CFF '].cff
        top = cff.topDictIndex[0]
        pen = drawn(T2CharStringPen(x0 + x1 - top.Private.nominalWidthX, None), points[::-1])
        top.CharStrings.charStringsIndex.append(pen.getCharString(top.Private, cff.GlobalSubrs))
        top.CharStrings.charStrings[name] = len(top.CharStrings.charStringsIndex) - 1
        top.charset.append(name)
    else:
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        font['glyf'][name] = drawn(TTGlyphPen(None), points).glyph()
    font['hmtx'][name] = (x0 + x1, x0)
    for table in font['cmap'].tables:
        if table.isUnicode(): table.cmap[PLAY_MARK] = name

def subset_font(src, family, ranges_macro, notice=None):
    """src given a play mark (play_mark), then cut to its Latin, Greek, Cyrillic, punctuation and that
    mark, renamed family (its own name is the font's reserved name under OFL and GUST alike): the font's
    bytes and ranges_macro listing exactly what it keeps. notice, if any, goes in its description (name
    ID 10)."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    font = TTFont(src, recalcTimestamp=False)
    old = font['name'].getDebugName(1)
    label = ranges_macro.split('_')[0].title()
    have = sorted(u for u in font.getBestCmap() if 0x20 <= u < 0x530 or 0x2000 <= u < 0x2200)
    play_mark(font)
    have.append(PLAY_MARK)
    options = subset.Options()
    if notice: options.name_IDs.append(7)  # the trademark notice, as its licence asks
    sub = subset.Subsetter(options)
    sub.populate(unicodes=have)
    sub.subset(font)
    check(sorted(font.getBestCmap()) == have, f'{label} font subset lost characters')
    # A subset is a Modified Version: it may not carry the Reserved Font Name (its names; the notices
    # are the licence files FONT_NOTICES ships beside it)
    for rec in font['name'].names:
        if rec.nameID in (1, 3, 4, 6, 16, 17, 18, 21, 22):
            rec.string = str(rec).replace(old, family).replace(old.replace(' ', ''), family.replace(' ', ''))
            check(old.split()[0] not in str(rec), f'{label} font name {rec.nameID} keeps {old.split()[0]}')
    if notice:
        font['name'].setName(notice, 10, 3, 1, 0x409)
    if 'CFF ' in font:  # the outlines' own names (PostScript FontName, FullName, FamilyName)
        cff = font['CFF '].cff
        top = cff.topDictIndex[0]
        cff.fontNames = [n.replace(old, family).replace(old.replace(' ', ''), family.replace(' ', '')) for n in cff.fontNames]
        for key in ('FullName', 'FamilyName'):
            setattr(top, key, getattr(top, key).replace(old, family).replace(old.replace(' ', ''), family.replace(' ', '')))
        check(old.split()[0] not in ''.join(cff.fontNames + [top.FullName, top.FamilyName]), f'{label} font CFF name keeps {old.split()[0]}')
    out = io.BytesIO()
    font.save(out, reorderTables=False)
    runs = []
    for u in have:
        if runs and runs[-1][1] + 1 == u: runs[-1][1] = u
        else: runs.append([u, u])
    return out.getvalue(), f'#define {ranges_macro} {{' + ','.join(f'0x{u:x}' for r in runs for u in r) + '}'

# The bold face (navigation.c theme_font; Modern's Now Playing title, Classic's fallback where its own lacks a character): Liberation Sans Bold (OFL, assets/fonts), cut to its Latin,
# Greek, Cyrillic and punctuation, with a play mark (play_mark). BOLD_RANGES, from the same cmap, lists exactly what it keeps, so a
# label holding anything else (CJK, say) keeps the stock font.
BOLD_TTF = 'release/assets/default/raw/fonts/bold.ttf'

def bold_font():
    return subset_font(ROOT/'assets/fonts/LiberationSans-Bold.ttf', 'Q2 Pod Sans', 'BOLD_RANGES')

# Classic's font (navigation.c theme_font): TeX Gyre Heros Bold (GUST Font License, assets/fonts), the same cut.
# The LPPL asks a modified font to say so itself, and where the original is: assets/fonts/README-classic-light.txt
CLASSIC_LIGHT_TTF = 'release/assets/default/raw/fonts/classic-light.ttf'
# OFL 2 asks each copy to hold its licence, and the subsets' name tables don't: these ship beside the fonts
FONT_NOTICES = ('LiberationSans-OFL.txt', 'GUST-FONT-LICENSE.txt', 'README-classic-light.txt')
CLASSIC_LIGHT_NOTICE = ('Modified version of TeX Gyre Heros Bold 2.004: cut to part of its Latin and Greek and its punctuation, its layout features to frac, liga and locl, a play mark added at U+25B6, and renamed. '
               'The original: https://www.gust.org.pl/projects/e-foundry/tex-gyre/heros')

def classic_light_font():
    return subset_font(ROOT/'assets/fonts/texgyreheros-bold.otf', 'Q2 Pod Classic Light', 'CLASSIC_LIGHT_RANGES', CLASSIC_LIGHT_NOTICE)

def relabel(data):
    """A strings/*.bin value replaced: after a u16 0 and a u16 pair count, a u32 offset to each key and
    value, in file order; the offsets past each change move with it."""
    for key, old, new in LABELS:
        at = data.find(b'\0' + key + b'\0' + old + b'\0')
        check(at > 0 and data.count(b'\0' + key + b'\0') == 1, f'Unexpected {key.decode()} label')
        at += len(key) + 2
        n = 2 * struct.unpack_from('<H', data, 2)[0]
        offsets = [o + len(new) - len(old) if o > at else o for o in struct.unpack_from(f'<{n}I', data, 4)]
        data = bytearray(data[:at] + new + data[at + len(old):])
        struct.pack_into(f'<{n}I', data, 4, *offsets)
    return bytes(data)

# demo's .pdr section (offset, size): MIPS procedure descriptors past every LOAD segment, which nothing
# reads at run time. Zeroed, they pack to almost nothing, which keeps the rootfs within stock's size.
PDR = (0x62a224, 0x6b080)

def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True)
def sha(b): return hashlib.sha256(b).hexdigest()

def source_sha256():
    """Hash every build input, so a test run cannot silently use a stale output directory."""
    h = hashlib.sha256()
    manual = [ROOT/'docs/manual'/f for f in ('build.py', 'guide.html')]
    for path in sorted([*(p for p in ROOT.glob('assets/**/*') if p.is_file()), *ROOT.glob('patch/*'),
                        *ROOT.glob('tools/*.py'), *ROOT.glob('tools/*.c'),
                        *ROOT.glob('tools/*.h'), *ROOT.glob('tools/*.S'), *manual]):
        h.update(str(path.relative_to(ROOT)).encode() + b'\0')
        h.update(path.read_bytes())
    return h.hexdigest()

def j(addr): return 0x08000000 | addr >> 2  # MIPS j
def jal(addr): return 0x0c000000 | addr >> 2  # MIPS jal
def check(condition, message):
    if not condition: raise ValueError(message)
_T = TypeVar('_T')
def need(value: _T | None, message: str) -> _T:
    """value, or check's ValueError(message) where a search or lookup came back None."""
    if value is None: raise ValueError(message)
    return value
def jpeg_size(b):
    """Validate the splash's supported JPEG frame header and return its dimensions."""
    check(b[:2] == b'\xff\xd8', 'Logo must be a JPEG')
    i = 2
    while i < len(b):
        check(b[i] == 0xFF, 'Malformed JPEG')
        while i < len(b) and b[i] == 0xFF: i += 1
        check(i < len(b), 'Truncated JPEG marker')
        marker = b[i]
        i += 1
        if marker in (0xD9, 0xDA): break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7: continue
        check(marker not in (0, 0xD8), 'Malformed JPEG marker')
        check(i + 2 <= len(b), 'Truncated JPEG segment')
        size = struct.unpack_from('>H', b, i)[0]
        check(size >= 2 and i + size <= len(b), 'Invalid JPEG segment length')
        if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
            check(size >= 8 and size == 8 + 3 * b[i+7], 'Invalid JPEG frame length')
            # Stock display_logo indexes decoded pixels as RGB triples (0x400ec0 onward),
            # but leaves libjpeg's output color space unchanged. Grayscale/CMYK are unsafe.
            check(marker == 0xC0 and b[i+2] == 8 and b[i+7] == 3,
                  'Logo must be an 8-bit, three-component baseline JPEG (not grayscale/CMYK)')
            h, w = struct.unpack_from('>HH', b, i+3)
            check(w > 0 and h > 0, 'Invalid JPEG dimensions')
            return w, h
        i += size
    raise ValueError('No JPEG size marker')
def symbols(p, table=None):
    out = {}
    for line in (table or run('readelf', '-Ws', p)).splitlines():
        s = line.split()
        if len(s) >= 8 and s[0].endswith(':'):
            try: out[s[7]] = int(s[1], 16)
            except ValueError: pass
    return out

def segments(b):
    phoff = struct.unpack_from('<I', b, 28)[0]
    size, num = struct.unpack_from('<HH', b, 42)
    check(size == 32, 'Unexpected ELF program header size')
    return [(phoff+i*size, struct.unpack_from('<8I', b, phoff+i*size)) for i in range(num)]
def load_elf(u, b, load=0):
    """Map an ELF's LOAD segments into a Unicorn instance, shifted by load."""
    for _, (t, o, v, _, f, m, _, _) in segments(b):
        if t != 1: continue
        v += load
        start = v & ~4095
        u.mem_map(start, ((v+m+4095) & ~4095) - start)
        u.mem_write(v, b[o:o+f])
def machine(raw, scratch, load=0):
    """A little-endian MIPS Unicorn with raw's LOAD segments (at +load) and 64 KB of scratch at scratch."""
    from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    load_elf(u, raw, load)
    u.mem_map(scratch, 0x10000)
    return u
def word(u, address, value): u.mem_write(address, struct.pack('<I', value & 0xffffffff))
def read(u, address): return struct.unpack('<I', u.mem_read(address, 4))[0]
def fileoff(b, a):
    for _, (t, o, v, _, f, _, _, _) in segments(b):
        if t == 1 and v <= a < v+f: return o+a-v
    raise ValueError(f'Unmapped address {a:x}')

# Every saved g_time_zone index, in stock's order (0-26) then the additions: its label and TZ. Labels
# are literal, short enough for the 246 px row at 24 px; the offset is the standard one. The
# rows show the one in effect now instead, daylight saving included (ringnav_timezone_row). POSIX rules stand in where the rootfs tzdata is out of date: Mexico City
# and Tehran dropped DST in 2022, Fiji suspended it. Stock's GMT-12 row was Kwajalein, which is +12;
# its +6 Almaty is +5 since 2024, so that row is Dhaka.
TIMEZONES = (
    ('GMT-12 Baker Island', '<-12>12'), ('GMT-11 Midway', 'Pacific/Midway'),
    ('GMT-10 Honolulu', 'Pacific/Honolulu'), ('GMT-9 Anchorage', 'America/Anchorage'),
    ('GMT-8 Los Angeles', 'America/Los_Angeles'), ('GMT-7 Denver', 'America/Denver'),
    ('GMT-6 Mexico City', 'CST6'), ('GMT-5 New York', 'America/New_York'),
    ('GMT-4 Halifax', 'America/Halifax'), ("GMT-3:30 St. John's", 'America/St_Johns'),
    ('GMT-3 Buenos Aires', 'America/Argentina/Buenos_Aires'), ('GMT-1 Azores', 'Atlantic/Azores'),
    ('GMT+0 London', 'Europe/London'), ('GMT+1 Amsterdam', 'Europe/Amsterdam'),
    ('GMT+2 Bucharest', 'Europe/Bucharest'), ('GMT+3 Kuwait', 'Asia/Kuwait'),
    ('GMT+3:30 Tehran', '<+0330>-3:30'), ('GMT+4 Muscat', 'Asia/Muscat'),
    ('GMT+4:30 Kabul', 'Asia/Kabul'), ('GMT+5 Karachi', 'Asia/Karachi'),
    ('GMT+6 Dhaka', 'Asia/Dhaka'), ('GMT+7 Bangkok', 'Asia/Bangkok'),
    ('GMT+8 Shanghai', 'Asia/Shanghai'), ('GMT+9 Tokyo', 'Asia/Tokyo'),
    ('GMT+9:30 Adelaide', 'Australia/Adelaide'), ('GMT+10 Sydney', 'Australia/Sydney'),
    ('GMT+12 Fiji', '<+12>-12'),
    ('GMT-9:30 Marquesas', 'Pacific/Marquesas'), ('GMT-2 South Georgia', 'Atlantic/South_Georgia'),
    ('GMT+5:30 Colombo', 'Asia/Colombo'), ('GMT+5:45 Kathmandu', 'Asia/Kathmandu'),
    ('GMT+6:30 Yangon', 'Asia/Yangon'), ('GMT+8:45 Eucla', 'Australia/Eucla'),
    ('GMT+10:30 L. Howe', 'Australia/Lord_Howe'), ('GMT+11 Noumea', 'Pacific/Noumea'),
    ('GMT+12:45 Chatham', 'Pacific/Chatham'), ('GMT+13 Tongatapu', 'Pacific/Tongatapu'),
    ('GMT+14 Kiritimati', 'Pacific/Kiritimati'),
    ('GMT-6 Chicago', 'America/Chicago'), ('GMT-7 Phoenix', 'America/Phoenix'),
    ('GMT+5:30 Kolkata', 'Asia/Kolkata'), ('GMT+10 Brisbane', 'Australia/Brisbane'),
    ('GMT+12 Auckland', 'Pacific/Auckland'),
)
# LIGHT=2 goes by the wall clock, as demo's Auto: the boot shell has no TZ, so the saved index picks it.
# One missing or out of range is config_init's: index 22.
LOGO_ZONES = ' '.join(f"{i}) z='{zone}';;" for i, (_, zone) in enumerate(TIMEZONES)) + f" *) z='{TIMEZONES[22][1]}';;"
LOGO_HOOK = (b'   \tdisplay_logo /release/assets/default/raw/images/xx/logo.jpg\n',
             b'   \tl=/mnt/data/boot-logo.jpg; [ -f $l ] || { l=/%s; light=0; grep -qx LIGHT=1 /mnt/data/config.ini 2>/dev/null && light=1; grep -qx LIGHT=2 /mnt/data/config.ini 2>/dev/null && { case $(sed -n s/^TIMEZONE=//p /mnt/data/config.ini) in %s esac; case $(TZ=$z date +%%H) in 0[7-9]|1[0-8]) light=1;; esac; }; [ $light = 1 ] && [ -f /%s ] && l=/%s; }; display_logo $l\n'
             % (LOGO.encode(), LOGO_ZONES.encode(), LOGO_LIGHT.encode(), LOGO_LIGHT.encode()))
def timezone_offset(label):
    sign, h, m = need(re.match(r'GMT([+-])(\d+)(?::(\d+))?', label), f'Unexpected time zone {label}').groups()
    return (1 if sign == '+' else -1) * (int(h) * 60 + int(m or 0))
# The rows, west to east: the saved index each row shows.
TIMEZONE_ORDER = sorted(range(len(TIMEZONES)), key=lambda i: (timezone_offset(TIMEZONES[i][0]), TIMEZONES[i][0]))

def patch_timezones(demo):
    # Keep the stock indices so saved zones survive; fit the extra rows in the existing table.
    start = fileoff(demo, 0x77ea30)
    original = demo[start:start+28*64]
    labels = [original[i:i+64].split(b'\0')[0] for i in range(0, len(original), 64)]
    check(labels[0] == b'timedate_auto' and labels[-1] == b'timezone_gmt+12', 'Unexpected timezone labels')
    labels = labels[:1] + [label.encode() for label, _ in TIMEZONES]
    check(len(labels)*32 <= len(original) and all(len(label) < 32 for label in labels), 'Timezone labels exceed stock table')
    demo[start:start+len(original)] = b''.join(label.ljust(32, b'\0') for label in labels).ljust(len(original), b'\0')
    for address, old, new in (
        (0x4c1a18, 0x00102980, 0x00102940),  # label stride: 64 -> 32
        (0x4c1d18, 0x2402001c, 0x24020000 | len(labels)),
        (0x4c2234, 0x2402001c, 0x24020000 | (len(labels)-1)),
        (0x504d1c, 0x2c43001b, 0x2c430000 | (len(labels)-1)),  # boot accepts saved additions
    ):
        off = fileoff(demo, address)
        check(struct.unpack_from('<I', demo, off)[0] == old, f'Unexpected timezone code at {address:x}')
        struct.pack_into('<I', demo, off, new)

FUNCTIONS = {
 '_create_vector': ('void *', 'const char *'),
 'vector_init': ('void', 'void *'),
 '_vector_push_back': ('void', 'void *, ...'),
 'sqlite3_open': ('int', 'const char *, void **'),
 'sqlite3_close': ('int', 'void *'),
 'sqlite3_exec': ('int', 'void *, const char *, int (*)(void *, int, char **, char **), void *, char **'),
 'getMusicByPlayList': ('int', 'int'),
 'sort_playlist_addtime': ('int', 'int'),
 'checkPlayListExist': ('int', 'void *, int'),
 'toolsSetTimeZone': ('int', 'const char *'),
 'widget_on': ('unsigned', 'void *, unsigned, int (*)(void *, void *), void *'),
 'widget_set_visible': ('int', 'void *, int, int'),
 'widget_set_opacity': ('int', 'void *, unsigned'),
 'widget_set_enable': ('int', 'void *, int'),
 # Crossfade's page (crossfade.c): stock's Boot volume slider, its value a double
 'slider_set_min': ('int', 'void *, double'), 'slider_set_max': ('int', 'void *, double'),
 'slider_set_step': ('int', 'void *, double'), 'slider_set_value': ('int', 'void *, double'),
 'widget_set_text_utf8': ('int', 'void *, const char *'),
 'text_selector_count_options': ('unsigned', 'void *'),
 'text_selector_set_selected_index': ('int', 'void *, unsigned'),
 'widget_use_style': ('int', 'void *, const char *'),
 'widget_set_name': ('int', 'void *, const char *'),
 'widget_set_sensitive': ('int', 'void *, int'),
 'widget_to_local': ('int', 'void *, void *'),
 'widget_dispatch_event_to_target_recursive': ('int', 'void *, void *'),
 'label_create': ('void *', 'void *, int, int, int, int'),
 'stock_search': ('int', 'void *, void *'),
 'widget_set_children_layout': ('int', 'void *, const char *'),
 'widget_get_canvas': ('void *', 'void *'),  # the lyrics page measures its lines (lyrics.c)
 'widget_resize': ('int', 'void *, int, int'),
 'widget_layout_children': ('int', 'void *'),
 'widget_lookup': ('void *', 'void *, const char *, int'),
 'scroll_bar_scroll_to': ('int', 'void *, int, unsigned'),
 'navigator_back_to_home': ('int', 'void'),
 'navigator_switch_to_with_context': ('int', 'const char *, const void *, int'),
 'window_manager': ('void *', 'void'),
 'window_manager_get_top_window': ('void *', 'void *'),
 'window_manager_is_animating': ('int', 'void *'),
 'window_manager_get_pointer_pressed': ('int', 'void *'),
 'main_loop': ('void *', 'void'),  # +LOOP_PRESSED: the touch thread's own down flag (navigation.c stale_note)
 'widget_get_visible': ('int', 'void *'),
 'widget_get_prop_bool': ('int', 'void *, const char *, int'),
 'widget_get_prop_int': ('int', 'void *, const char *, int'),
 'style_get_int': ('int', 'void *, const char *, int'),
 'widget_get_prop_str': ('const char *', 'void *, const char *, const char *'),
 'widget_get_text': ('const unsigned *', 'void *'),
 'widget_get_text_utf8': ('int', 'void *, char *, unsigned'),
 'widget_get_type': ('const char *', 'void *'),
 'widget_count_children': ('unsigned', 'void *'),
 'widget_get_child': ('void *', 'void *, unsigned'),
 'widget_set_prop_int': ('int', 'void *, const char *, int'),
 'widget_set_prop_str': ('int', 'void *, const char *, const char *'),
 'pages_set_active_by_name': ('int', 'void *, const char *'),
 'edit_set_select': ('int', 'void *, unsigned, unsigned'),
 'widget_invalidate_force': ('int', 'void *, void *'),
 'widget_animator_start': ('int', 'void *'),
 'widget_animator_prop_create': ('void *', 'void *, unsigned, unsigned, int, const char *'),  # Home's Settings slide
 'widget_animator_prop_set_params': ('int', 'void *, double, double'),
 'widget_animator_scroll_set_params': ('int', 'void *, int, int, int, int'),
 'slide_menu_set_value': ('int', 'void *, int'),
 'slide_menu_item_width': ('int', 'void *'),
 'slide_menu_on_scroll_done': ('int', 'void *, void *'),
 'widget_animator_scroll_create': ('void *', 'void *, unsigned, unsigned, int'),
 'widget_animator_on': ('unsigned', 'void *, unsigned, int (*)(void *, void *), void *'),
 'widget_set_focused': ('int', 'void *, int'),
 'input_method': ('void *', 'void'),
 'input_method_request': ('int', 'void *, void *'),
 'widget_animator_pause': ('int', 'void *'),
 'widget_animator_destroy': ('int', 'void *'),
 'easing_get': ('void *', 'int'),  # Coverflow's glides set a reused animator's easing
 'canvas_get_clip_rect': ('int', 'void *, void *'),
 'canvas_set_clip_rect': ('int', 'void *, const void *'),
 'canvas_set_fill_color': ('int', 'void *, unsigned'),
 'canvas_set_stroke_color': ('int', 'void *, unsigned'),
 'canvas_stroke_rect': ('int', 'void *, int, int, int, int'),
 'canvas_fill_rect': ('int', 'void *, int, int, int, int'),
 'canvas_draw_icon': ('int', 'void *, void *, int, int'),
 'canvas_set_font': ('int', 'void *, const char *, unsigned'),
 'canvas_set_text_color': ('int', 'void *, unsigned'),
 'canvas_draw_text_in_rect': ('int', 'void *, const unsigned *, unsigned, const void *'),
 'canvas_draw_text': ('int', 'void *, const unsigned *, unsigned, int, int'),  # Books (books.c)
 'canvas_measure_text': ('float', 'void *, const unsigned *, unsigned'),
 'canvas_fill_rounded_rect': ('int', 'void *, const void *, const void *, const void *, unsigned'),
 'canvas_stroke_rounded_rect': ('int', 'void *, const void *, const void *, const void *, unsigned, unsigned'),
 # The PEQ curve (peq_ui.c) and the visualizer (visualizer.c): AWTK's software nanovg, float_t float, color_t by value
 'canvas_get_vgcanvas': ('void *', 'void *'),
 'vgcanvas_save': ('int', 'void *'), 'vgcanvas_restore': ('int', 'void *'),
 'vgcanvas_translate': ('int', 'void *, float, float'),
 'vgcanvas_begin_path': ('int', 'void *'), 'vgcanvas_close_path': ('int', 'void *'),
 'vgcanvas_move_to': ('int', 'void *, float, float'), 'vgcanvas_line_to': ('int', 'void *, float, float'),
 'vgcanvas_arc': ('int', 'void *, float, float, float, float, float, int'),
 'vgcanvas_fill': ('int', 'void *'), 'vgcanvas_stroke': ('int', 'void *'),
 'vgcanvas_set_fill_color': ('int', 'void *, unsigned'), 'vgcanvas_set_stroke_color': ('int', 'void *, unsigned'),
 'vgcanvas_set_fill_linear_gradient': ('int', 'void *, float, float, float, float, unsigned, unsigned'),
 'vgcanvas_set_line_width': ('int', 'void *, float'), 'vgcanvas_set_line_cap': ('int', 'void *, const char *'),
 'pointer_event_init': ('void *', 'void *, int, void *, int, int'),
 'time_now_ms': ('unsigned', 'void'),
 'sleep_ms': ('int', 'unsigned'),
 'timer_add': ('unsigned', 'int (*)(const void *), void *, unsigned'),
 'idle_add': ('unsigned', 'int (*)(const void *), void *'),
 'timer_remove': ('int', 'unsigned'),
 'tk_strcmp': ('int', 'const char *, const char *'),
 'slide_menu_scroll_to_next': ('int', 'void *'),
 'slide_menu_scroll_to_prev': ('int', 'void *'),
 'table_client_stop_animator_scroll': ('int', 'void *'),
 'table_client_set_yoffset': ('int', 'void *, int'),
 'table_row_of': ('void *', 'void *'),  # the table row holding a widget, or 0
 'widget_dispatch_simple_event': ('int', 'void *, unsigned'),
 'scroll_view_set_offset': ('int', 'void *, int, int'),
 'scroll_view_scroll_to': ('int', 'void *, int, int, int'),
 'table_client_scroll_to': ('int', 'void *, int'),
 'table_client_get_virtual_h': ('int', 'void *'),
 'scroll_view_scroll_delta_to': ('int', 'void *, int, int, int'),
 'widget_destroy_children': ('int', 'void *'),
 'widget_destroy': ('int', 'void *'),  # Streaming's Baidu Netdisk row (home.c ringnav_stream)
 'list_item_create': ('void *', 'void *, int, int, int, int'),
 'hscroll_label_create': ('void *', 'void *, int, int, int, int'),
 'set_hscroll_label_attribute': ('void', 'void *'),
 'hscroll_label_set_only_focus': ('int', 'void *, int'),
 'hscroll_label_set_ellipses': ('int', 'void *, int'),
 'hscroll_label_set_xoffset': ('int', 'void *, int'),
 'hscroll_label_start': ('int', 'void *'),
 'hscroll_label_stop': ('int', 'void *'),
 'widget_add_timer': ('unsigned', 'void *, int (*)(const void *), unsigned'),
 'widget_set_text': ('int', 'void *, const unsigned *'),
 'widget_off_by_func': ('int', 'void *, unsigned, void *, void *'),
 'widget_off_by_ctx': ('int', 'void *, void *'),  # Home's Library list (home.c library_rows)
 'on_home_music_onclick': ('int', 'void *, void *'),  # stock's Library card, which Home's Full keeps (coverflow.c home_library)
 'on_home_stream_onclick': ('int', 'void *, void *'),  # stock's Streaming card, the same (coverflow.c home_streaming)
 'window_close': ('int', 'void *'),
 'dialog_quit': ('int', 'void *, unsigned'),  # quick settings' close, as its swipe up ends (0x4a2058)
 'window_manager_close_window_force': ('int', 'void *, void *'),
 'navigator_to': ('int', 'const char *'),
 'navigator_to_with_context': ('int', 'const char *, const void *'),
 'window_manager_get_input_device_status': ('char *', 'void *'),
 'airplayGetFlag': ('int', 'void'),
 'tk_snprintf': ('int', 'char *, unsigned, const char *, ...'),
 'toolsTimeItoa': ('int', 'char *, int'),
 'getMusicByAlbum': ('int', 'const char *'),
 'getMusicByAlbumAndSonger': ('int', 'const char *, const char *, int'),
 'getMusicByAlbumAndAlbumSonger': ('int', 'const char *, const char *, int'),
 'toolsLoadDirectory': ('int', 'const char *'),
 'toolsCheckTypeDeque': ('int', 'void *, int'),
 'get_specfolderurl': ('void', 'int, char *, int'),
 # Queue menu rows (navigation.c): artist/composer/genre queries, My Fav, the batch selection record
 'getMusicBySonger': ('int', 'const char *'),
 'getMusicByAlbumArtist': ('int', 'const char *'),
 'getMusicByComposer': ('int', 'const char *'),
 'getMusicByGenre': ('int', 'const char *'),
 'getMusicByAlbumAndComposer': ('int', 'const char *, const char *, int'),
 'getMusicByAlbumAndGenre': ('int', 'const char *, const char *, int'),
 'checkFavExist': ('int', 'void *'),
 'getAllFav': ('int', 'void'),
 # Long Songs (library_lists.c): a file's length, probed once, as stock's scanner reads tags
 'taglib_file_new': ('void *', 'const char *, int'),
 'taglib_file_tag': ('void *', 'void *'),
 'toolsCheckItemExist': ('int', 'void *, const char *, int'),
 'toolsGetMusicInfo': ('int', 'int, char *, const char *'),
 'toolsFixId3ErrorCode': ('void', 'int, const char *, char *, char *, int'),  # tags.c ogg_music_info
 'toolsUpdateDbTable': ('int', 'const char *, const char *'),
 'toolsTrimRight': ('void', 'char *'),
 'toolsHanziToPinyin': ('int', 'const char *, char *'),
 'toolsGetMusicNum': ('int', 'void'),
 'toolsGetUserStop': ('int', 'void'),  # toolsStopUpdateMusic's flag: the scan dialog's Back
 'taglib_file_audioproperties': ('void *', 'void *'),
 'taglib_file_free': ('void', 'void *'),
 'deleteMusicFromFav': ('int', 'void *'),
 'batch_init_selectrecord': ('int', 'int'),
 'batch_set_selectitem': ('int', 'int'),
 'batch_get_selectitem': ('int', 'int'),
 'batch_add_file': ('int', 'int, int, void *, void *, int'),
 'navigator_window_is_exist': ('int', 'const char *'),
 'mclStartPlayer': ('int', 'void'),
 'player_start_queue': ('int', 'int'),
 'mclStop': ('int', 'void'),
 'mclGetPlayTime': ('int', 'int *, int *'),
 'mclSetStartSeekTime': ('int', 'int'),
 'mcl_open_preload': ('int', 'void'),
 'mclLoadPlayList': ('int', 'void *, int, int'),
 'mcl_shuffle_pick': ('int', 'int'),
 'getAllAlbum': ('int', 'void'),
 # Shuffle Songs (library_lists.c): every song, shuffle saved as the play-mode setting does, a random start
 'getAllMusic': ('int', 'int'),
 'music_nagvation_title': ('int', 'int'),  # Library -> Songs' load (navigation.c songs_load)
 'mclGetPlayM3uFlag': ('int', 'void'),  # stock ignores Folder Skip on an M3U (playback.c change_restarts)
 'config_playmode': ('int', 'int, int'),
 'toolsRandnum': ('int', 'int'),
 'widget_restack': ('int', 'void *, unsigned'),
 'widget_index_of': ('int', 'void *'),  # Centred Now Playing's column back in its place (navigation.c np_full)
 'toolsThumbSpecCover': ('int', 'const char *, const char *, int, int'),
 'toolsGetAlbumCover': ('int', 'const char *, const char *, int, int'),
 'toolsGetExternCover': ('int', 'const char *, const char *, int, int'),
 'window_create': ('void *', 'void *, int, int, int, int'),
 'widget_factory': ('void *', 'void'),
 'widget_factory_create_widget': ('void *', 'void *, const char *, void *, int, int, int, int'),
 'image_create': ('void *', 'void *, int, int, int, int'),
 'image_set_draw_type': ('int', 'void *, int'),
 'image_base_set_image': ('int', 'void *, const char *'),
 'widget_load_image': ('int', 'void *, const char *, void *'),
 'widget_unload_image': ('int', 'void *, void *'),
 'list_view_create': ('void *', 'void *, int, int, int, int'),
 'scroll_view_create': ('void *', 'void *, int, int, int, int'),
 'navigator_back': ('int', 'void'),
 'write_int_config': ('int', 'int, const char *, const char *'),
 'toolsWriteConfig': ('int', 'const char *, const char *, const char *, const char *'),
 'toolsReadConfig': ('int', 'const char *, const char *, const char *, char *, const char *'),
 'button_create': ('void *', 'void *, int, int, int, int'),
 'widget_move_resize': ('int', 'void *, int, int, int, int'),
 # The artist page's toolbar (navigation.c artist_bar): its tabs move down under it
 'widget_set_self_layout_params': ('int', 'void *, const char *, const char *, const char *, const char *'),
 'widget_layout': ('int', 'void *'),
 'tk_str_end_with': ('int', 'const char *, const char *'),
 'tk_str_start_with': ('int', 'const char *, const char *'),
 'image_manager': ('void *', 'void'),
 'image_manager_unload_all': ('int', 'void *'),
 'image_manager_unload_bitmap_by_name': ('int', 'void *, const char *'),
 'bitmap_get_line_length': ('unsigned', 'void *'),
 'bitmap_lock_buffer_for_write': ('unsigned char *', 'void *'),
 'bitmap_unlock_buffer': ('int', 'void *'),
 # Coverflow depth (coverflow.c): its frame bitmap, reading decoded covers, and the slide_menu stride
 'bitmap_create_ex': ('void *', 'unsigned, unsigned, unsigned, unsigned'),
 'bitmap_destroy': ('int', 'void *'),
 'bitmap_lock_buffer_for_read': ('const unsigned char *', 'void *'),
 'canvas_draw_image': ('int', 'void *, void *, const void *, const void *'),
 'widget_paint': ('int', 'void *, void *'),  # the system bar, under a dialog that covers it (classic_pages.c search_paint)
 # Cover Flow's flip paints the tracks panel to a bitmap as stock's window snapshots do (coverflow.c flip_capture)
 'canvas_offline_create': ('void *', 'unsigned, unsigned, unsigned'),
 'canvas_offline_begin_draw': ('int', 'void *'),
 'canvas_offline_end_draw': ('int', 'void *'),
 'canvas_offline_destroy': ('int', 'void *'),
 'canvas_translate': ('int', 'void *, int, int'),
 'widget_on_paint_children': ('int', 'void *, void *'),  # a wallpaper thumbnail's check badge (lockscreen.c)
 'slide_menu_set_spacer': ('int', 'void *, int'),
 'playing_timer_start': ('int', 'void *'),
 'playing_timer_clear': ('int', 'void *'),
 'player_seek_time': ('int', 'int'),
 'player_playtime': ('int', 'void'),
 'prev_quick_click': ('int', 'void'), 'next_quick_click': ('int', 'void'),
 'player_mainscheduling': ('int', 'void'),
 'player_playtime_and_length': ('int', 'int *, int *'),  # elapsed, total: track seconds; -1 none
 'player_start': ('int', 'void *, int, int, int'),
 'buzzeer_switch': ('int', 'int'),  # the stock key click; it reads g_keytone_flag
 # Headphones required (g_block_withoutphone_flag) with no output: toasts (0) and returns 1, as before each stock start
 'check_forbid_play_no_headset': ('int', 'int'),
 'on_wm_keyup_fun': ('int', 'void *, void *'),  # stock key-up: np_single replays a centre release
 'get_wifisignal': ('int', 'void'),  # the status bar's Wi-Fi bars, 1-4; -1 when not connected
 'fwupdate_tf': ('int', 'void'),  # System Update's TF card update: battery check, confirm, flash
 'widget_set_tr_text': ('int', 'void *, const char *'),
 # Podcasts and Audiobooks (navigation.c): folder_page's reload of g_folder_path, title and list rebuild
 'folder_reload_data': ('int', 'void'),
 'folder_reset_variable': ('int', 'void'),  # a fresh visit: layer 0, no path, enter history or CUE
 'folder_reinit_navbarname': ('int', 'void'),
 'folder_refresh': ('int', 'void *'),
 # Videos (books.c): stop the music, the DAC's power, and the screen and standby timeouts held off
 'mclGetOutputWay': ('int', 'void'),
 'mclGetLyricSize': ('int', 'void'),  # the playing track's lyric lines, 0 without
 'player_play_pause': ('int', 'void'),
 'player_next_music': ('int', 'void'), 'player_prev_music': ('int', 'void'),
 'getBtLinkSize': ('int', 'void'), 'getBtLinkItem': ('int', 'int, int *, char *'),
 'getBtLinkStatus': ('int', 'char *'), 'addBtLinkItem': ('void', 'char *, int'),
 'updateBtLinkStatus': ('int', 'int, char *'), 'checkPairDev': ('int', 'char *'),
 'btctl_create_daemon_thread': ('int', 'void *'),
 'initBluetoothParam': ('void', 'void'),
 'btctl_set_prompt_callback': ('void', 'void *, void *'),
 'btctl_device_status_callback': ('void', 'void *, void *'),
 'btctl_set_a2dp_callback': ('void', 'void *, void *'),
 'btrecvA2dpFunc': ('int', 'int, void *, void *'),
 'mclGetCueFlag': ('int', 'void'),  # the playing record has a CUE start or end
 'mclGetPlayStatus': ('int', 'void'),  # 1 stopped, 2 playing, 3 paused (mclStop, mclSetResume, mclSetPause)
 'player_stop': ('int', 'void'),
 'player_save_start': ('int', 'int'),  # pause: mclStartPlayer at the saved seek, then paused if set
 'mclGetSavePlayStatus': ('int', 'void'),  # player_save_stop's mclGetPlayStatus, unless that was 1
 'save_memoryplay_info': ('int', 'void'),  # Memory playback's queue and position, as into_poweroff saves them
 'mclSetDacPwr': ('int', 'int'),
 'reset_poweroptions_timer': ('int', 'int, int, int'),
 'btctl_transport_get_volume': ('int', 'void'),
 'btctl_transport_set_volume': ('int', 'int'),
 'device_set_volume': ('int', 'int, int'),  # volume, notify: the DAC's or hciplayer's, as the volume dialog
 # Spotify (spotify.c): the headphone output as a headset insert sets it, then PCM mode and unmuted, as AirPlay's page
 'config_outputchannel': ('int', 'int, int'),  # g_headset_output, 2
 'mclSetPcmMode': ('int', 'void'),
 'mclSetMute': ('int', 'int'),
 'toolsTrimLeft': ('void', 'char *'),
 'switch_charge_enable': ('int', 'int'),  # 1 charges: the active-low charge enable on GPIO PE22 (usbmode_page_init)
 # Coverflow's Sort: another ORDER BY over getAllAlbum's grouping, filled by its own row callback
 'toolsQueryDbTable': ('int', 'const char *, const char *, void *, int'),  # db, sql, row, name sort
 'album_row': ('int', 'void *, int, char **, char **'),
 'albumcoverinfo_init': ('int', 'void *'),  # a list's cover tasks, each a copy of its row's name and path
 'class_icon': ('int', 'void *, int'),  # Artists, Composers and Genres rows' img_icon (CLASS_ICON)
 'np_info': ('int', 'void *'),  # Now Playing's info page labels (ringnav_np_info)
 'ab_x': ('int', 'int'),  # an A-B marker's x for a point (ringnav_ab_x)
 'getFormatString': ('int', 'const char *, char *, int'),  # a string's label in the language: key, out, size
 # Lyrics and cover search (onlinematch.c): demo's cJSON, a node's valuestring at +16
 'cJSON_Parse': ('void *', 'const char *'), 'cJSON_Delete': ('void', 'void *'),
 'cJSON_GetArraySize': ('int', 'void *'), 'cJSON_GetArrayItem': ('void *', 'void *, int'),
 'cJSON_GetObjectItem': ('void *', 'void *, const char *'),
 'online_getkeyword_info': ('void', 'char *'),  # stock's lyric keywords (0x600 bytes), copied under its lock
 # the cover match's candidate URLs (onlinematching_page shows each), under the same lock
 'online_addcoverlist': ('void', 'const char *'), 'online_getsize_coverlist': ('int', 'void'),
 # Tidal's list replies (tidal.c tidal_pages): /tmp/tidal_<thread> parsed into stTidalTracks
 'parseTidalPlaylistDetailJson': ('int', 'const char *'), 'parseTidalAlbumDetailJson': ('int', 'const char *'),
 'parseTidalColTracksJson': ('int', 'const char *'),
 # A card app's library update (addon_library.c): stock's Import Playlists, and its folder listing
 'importPlayList': ('int', 'void'), 'toolsGetDirectoryM3u8': ('int', 'const char *, void *'),
 'mclSetSeek': ('int', 'int'),  # the player's seek in file seconds; it plays on from there, or on Play once paused
 # Smart Wi-Fi (smart_wifi.c smart_poll): stock's Wi-Fi switch, a toggle of g_wififlag (its callers pass 1,
 # the background connect), the link and the queue's type
 'wifi_switch': ('int', 'int'),
 'networkGetConnectStatus': ('int', 'void'),  # 0 no link (networkStop), above 0 connected
 'mclGetPlayType': ('int', 'void'),  # the queue's type: 2, 3, 0xf1 and 0xf2 stream (on_wm_timer_networkstatus)
}
# Local stock routines in the SHA-256-pinned V1.34 executable.
PRIVATE_FUNCTIONS = {
    "playlist_delete_click": 0x4b6198,
    'keylock_rows': HOOKS['keylock_rows'][0],
    'timezone_row': HOOKS['timezone_row'][0],
    'quick_lock': HOOKS['quick_lock'][0],
    "playlist_rebuild": HOOKS['playlist_rebuild'][0],  # picker init and re-entry rebuild all rows
    "stock_search": 0x534dc4,
    "slide_menu_item_width": 0x605970,
    "slide_menu_on_scroll_done": 0x605f84,
    "mcl_preload": HOOKS['mcl_preload'][0],
    "mcl_open_preload": HOOKS['mcl_open_preload'][0],
    "btmenu_rows": HOOKS['btmenu_rows'][0],  # systemset_bluetooth_page's list: rows, paired, found
    "mcl_shuffle_pick": 0x5ba540,
    "album_row": 0x506cc4,  # getAllAlbum's sqlite3_exec callback: id, album, songer, fileurl to a record
    "folder_refresh": 0x531bec,  # folder_page's navbar and table from p_deque_showlist (its init, back)
    "class_icon": 0x4b2268,  # a category row's picture: the head for Artists, a genre icon, else .downpic
    "screensaver_rows": HOOKS['screensaver_rows'][0],  # the Lock screen page's rows and picture grid
    "folder_click": HOOKS['folder_click'][0],  # folder_page's row click (folder_enter, then a file's queue)
    "saver_cover": HOOKS['saver_cover'][0],  # the lock window's img_playcover from the playing track
    "np_info": 0x53b9b8,  # Now Playing's info page labels, from the track (NP_INFO_CALL)
    "ab_x": 0x53b718,  # an A-B marker's x, on the boot set's np_bar_x and np_bar_w (AB_X_CALLS)
    "fwupdate_tf": 0x4c7cf4,  # systemset_fwupdate_page's row 0 click (0x4c830c): stock's TF card update
    # Smart Wi-Fi's held stock actions (smart_wifi.c smart_hold)
    "stream_row": HOOKS['stream_row'][0],  # Streaming's Tidal row: atoi of the button's name
    "netservice_row": HOOKS['netservice_row'][0],  # Network service's DLNA, AirPlay and Wi-Fi transfer
    "playermore_online": HOOKS['playermore_online'][0],  # More's Download Cover & Lyrics: (window)
    "specfolder_click": HOOKS['specfolder_click'][0],  # Scan Folders' row click
}
GLOBALS = ['tidal_albuminfo_thread_run', 'tidal_collection_thread_run',  # 2: a Tidal list loaded
           'savestartbt', 'transposition', 'tidalfurlstart',  # a stream to start paused; 1 arms Now Playing's online tick (tidal.c app_start)
           'g_time_zone','g_playspeed_mode', 'g_time24h_flag', 'g_backlight_status', 'g_lockscreen_pageflag', 'g_testmode_flag',
           'g_keylock_flag', 'g_keylock_mode',
           'g_bluetoothflag', 'bt_linkstatus', 'g_guideflag', 'g_poweroff_state', 'g_usblink_status', 'bt__recv_pageflag',
           'g_power_longkey', 'g_ingore_bootkey_flag', 'g_equalizer_flag', 'g_navbar_status', 'g_playcover_type',
           'g_keytone_flag', 'g_folder_layer', 'g_otg_status', 'g_delete_flag', 'g_volume', 'g_maxvolume',
           'g_po_status', 'g_bal_status',  # 3.5 mm and 4.4 mm jacks: 1 plugged (check_headset_status)
           'g_usbvol_mode',  # USB DAC volume: 0 fixed, else the volume (config_usbvolmode, device_set_volume)
           'g_usbdac_chargeflag',  # USB mode's charge choice, which switch_charge_enable gets there
           'g_memory_play', 'g_carmode', 'g_carmode_state',  # In-Vehicle Mode (pod_click)
           'g_bigcovermode', 'g_foldercover_flag',  # Enlarged Album Art (1 on) and Folder Album Cover (pod_click)
           'g_headset_output',  # the output config_outputchannel sets: 0, 1 the jacks, 2 Bluetooth
           'g_usbmode',  # USB mode: 1 Audio and DAC, else USB Storage (usbmode_page_init)
           'g_usbdet_value',  # 1 while a USB cable is in (USB mode's scan prompt outlives it)
           # Smart Wi-Fi (smart_wifi.c smart_poll): the Wi-Fi switch's state and stock's own gates on
           # toggling it (wifi_initstatus 0, wifi_connectstatus not 1, connecting), Network service's
           # AirPlay and DLNA, SyncLink (2: over Wi-Fi) and its session, Wi-Fi transfer and a pending time sync
           'g_wififlag', 'wifi_initstatus', 'wifi_connectstatus', 'g_airplayflag', 'g_dlnaflag',
           'g_synclinkflag', 'g_syncLink_status', 'g_wifitransport_flag', 'g_syncnettime_flag']
# Audited stock browsing state, deque pointers, art locks, the status bar widget
# (system_bar_init stores it), the playing cover's track path and the playing track's tags as
# player_get_id3info parsed them; sizes are checked against the ELF.
CONTEXT_DATA = {'bt_showcoding': 4, 'g_memory_info': 3476, 'g_folder_path': 1024, 'g_class_type': 4,
                'g_local_classinfo_save': 912, 'g_artist_type': 4, 'album_modetype': 4, 'g_sort_flag': 4,
                'g_playlistsort_flag': 4,  # a playlist's Sort (PLAYLISTSORT): 0 by name, else Time added
                'p_deque_playlist': 4, 'p_deque_showlist': 4, 'tools_pdeq_directory': 4, 'mcl_pdeqplaylist': 4,
                'parse_cover_mutex': 24, 'g_playcover_mutex': 24, 'system_bar': 4, 'g_lastcover_url': 1024,
                'g_dacoff_time': 4, 'p_vector_select_record': 4,
                'g_play_id3_info': 2716,
                # Artists' source (PLAYSET ARTISTTYPE, the artist page's switch: 1 album artist);
                # the battery level (0-100) and the charger's state (1, 2 charging), get_battery_capacity's
                'artist_type': 4, 'g_power_capacity': 4, 'g_power_chargestate': 4,
                'pdeq_albumcoverlist': 4, 'aclist_mutex': 24,
                'tidalStreamingLevelNow': 4,  # the quality the Tidal URL thread asked for
                'stTidalTracks': 16,  # a Tidal track list: total, limit, offset, its deque
                'g_pressdown_y': 4,  # the touch's start, the window manager's pointer-down writes it
                'g_dropdown_menu_state': 4,  # Quick Settings: 1, 2 while it slides or follows a swipe, 3 open
                # the lock screen's picture (SYSSET SCREENSAVERPATH) and the picker's list
                'g_screensaver_picture': 256, 'p_deque_screensaverlist': 4,
                'bt_connectstatus': 4,  # a link thread runs (1), ended (2), checked (3)
                'bt_initstatus': 4}  # Bluetooth's start-up status: -102 is stock's "Bluetooth abnormal" (system.c reboot_record)
# Windows the payload creates at runtime (window_create), so no rootfs asset names them.
PAYLOAD_WINDOWS = {'playlistedit_page', 'chapters_page', 'coverflow_page', 'photos_page', 'books_page', 'games_page', 'mostplayed_page', 'shuffle_page', 'upnext_page', 'ratings_page', 'era_page', 'era_albums_page', 'rated_page', 'longsongs_page', 'spotify_page',
                   'spotbrowse_page', 'radio_page', 'radionp_page', 'streamconf_page', 'tidal_downloads', 'apps_page',
                   'addon_page', 'app_songs', 'qrcode_page'}
ICONS = [f'menu_{c}{s}.png' for c in ('coverflow', 'rockbox') for s in ('', 'down')]
# The stock EQ preset page and the images only it and the stock equalizer page show: the PEQ
# editor clears that page's widgets on init and never binds the preset button, so none can load.
STOCK_EQ = ['release/assets/default/raw/ui/playset/preseteq_page.bin'] + [
    f'release/assets/default/raw/images/xx/{n}.png' for n in
    ['eq_bg', 'eq_off', 'eq_sidebg', 'eqbox'] + [f'eq_{p}{s}' for p in
    ('blues', 'classical', 'custon', 'dance', 'jazz', 'metal', 'pop', 'rock', 'scene') for s in ('', '_select')]]
# Stock V1.34's Album Wall, spectrum and VU pages, and the images only they and the buttons that open them show.
# Q2 Pod offers none: tools/ipod.py takes their Home card and their two Now Playing buttons out of the assets, and
# only those widgets' click handlers name the pages (on_home_coverflow_onclick, and Now Playing's two in
# tools/stock_map.tsv, playing_page_vu_on_click and playing_page_spectrum_on_click), so nothing can load them.
STOCK_PAGES = [f'release/assets/default/raw/ui/{n}_page.bin' for n in ('coverflow', 'spectrum', 'vu')] + [
    f'release/assets/default/raw/images/xx/{n}.png' for n in
    ['default_album_coverflow', 'play_spectrum', 'play_spectrumdown', 'play_vu', 'play_vudown', 'spectrum_back', 'spectrum_bar',
     *(f'spectrum_point{i}' for i in range(12)), 'vu_back', 'vu_dot', 'vu_left', 'vu_right']]
# Superseded library versions (the unversioned and soname links already resolve to the newer ones)
# and on-device debugging tools; nothing in the rootfs names them.
OBSOLETE = ['usr/lib/libdbus-1.so.3.19.8', *(f'usr/lib/lib{n}-2.0.so.0.5400.3' for n in
            ('gio', 'glib', 'gmodule', 'gobject', 'gthread')),
            *(f'usr/bin/{n}' for n in ('strace', 'strace-log-merge', 'sqlite3', 'hcidump'))]

FLAGS = ['--target=mipsel-linux-gnu','-march=mips32r2','-mabi=32','-mfp64',
         '-mno-abicalls','-fno-pic','-G0','-ffreestanding','-fno-builtin',
         '-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
         '-Oz','-Wall','-Wextra','-Werror','-Wno-unused-parameter']
# navigation.c and the files split out of it (patch/navigation.h), each its own object, linked in this order.
NAVIGATION = ['navigation.c', 'theme_engine.c', 'playlists.c', 'classic_pages.c', 'home.c', 'np_bar.c', 'volume.c', 'smart_wifi.c', 'system.c', 'tags.c', 'scrobble_ui.c', 'library_lists.c', 'ratings.c', 'scan_folders.c']

def compile_payload(out):  # pragma: no mutate block
    """Compile and link the payload."""
    from peq import compile_common
    extra = compile_common(out, out/'stock-demo')  # also writes the libc/libcstl imports navigation.c uses
    nav = [out/f'{name[:-2]}.o' for name in NAVIGATION]
    for name, o in zip(NAVIGATION, nav):
        run('clang',*FLAGS,'-I',out,'-c',ROOT/'patch'/name,'-o',o)
    asm = ['.set noreorder', '.text']
    resume = {x: address for address, _, x in HOOKS.values() if x}
    for x, address in resume.items():
        asm += [f'.globl stock_{x}_trampoline', f'stock_{x}_trampoline:', f'lui $gp, {GP >> 16}',
                f'ori $gp, $gp, {GP & 65535}', f'j 0x{address + 12:x}', 'nop']
    (out/'trampoline.S').write_text('\n'.join(asm)+'\n')
    run('clang',*FLAGS,'-c',out/'trampoline.S','-o',out/'trampoline.o')
    run('ld.lld','-m','elf32ltsmip','--gc-sections','-T',ROOT/'patch/link.ld','-e','ringnav',
        *[f'--undefined={name}' for _, name, _ in HOOKS.values()], f'--undefined={GRADIENT_LEAF[2]}', '--undefined=ringnav_lrc_open',
        f'--undefined={WM_PAINT_LEAF[2]}', f'--undefined={QUEUE_LEAF[2]}', '--undefined=ringnav_audio_extension', '--undefined=start_mute', '--undefined=start_sleep', '--undefined=ringnav_genre_copy', '--undefined=ringnav_lyric_save', '--undefined=ringnav_bootmode', '--undefined=lockscreen_access','--undefined=ringnav_ts_read',
        '--undefined=ringnav_baidu_poll', '--undefined=ringnav_tidal_poll', '--undefined=addon_m3u_dir', f'--undefined={COVERINFO_CALL[2]}',
        '--undefined=coverflow_player_cover', '--undefined=coverflow_grid_cover', '--undefined=coverflow_album_cover', '--undefined=coverflow_folder_cover', '--undefined=headset_bt_init',
        '--undefined=headset_status_register', '--undefined=headset_bt_autolink',
        *nav,out/'trampoline.o',*extra,'-o',out/'patch.elf')
    run('llvm-objcopy','-O','binary',out/'patch.elf',out/'patch.bin')
    return symbols(out/'patch.elf')

def check_layout(payload, ps):
    """The payload at BASE ends by SCRATCH, and its scratch state starts there and fills at most 128 KB."""
    check(len(payload) <= SCRATCH-BASE, 'Payload overlaps its scratch page')
    check(ps['__scratch_start'] == SCRATCH, 'Scratch state moved')
    check(ps['__scratch_end'] <= SCRATCH + 0x20000, 'Scratch state exceeds its 128 KB')

def compile_helper(out, cat, src, name, rels):
    """Link patch/{src}.c and start.c as name against the stock rootfs's libraries rels."""
    libs = []
    for rel in rels:
        libs.append(out/rel.rsplit('/', 1)[-1])
        libs[-1].write_bytes(cat(rel))
    objs = [out/f'{s}.o' for s in (src, 'start')]
    for o in objs:
        run('clang', *[f for f in FLAGS if f not in ('-mno-abicalls', '-G0')], '-mnan=2008', '-mabs=2008',
            '-mabicalls', '-c', ROOT/'patch'/f'{o.stem}.c', '-o', o)
    run('ld.lld', '-m', 'elf32ltsmip', '-e', '__start', '--dynamic-linker', '/lib/ld-linux-mipsn8.so.1',
        '--image-base=0x400000', '-z', 'noexecstack', '--gc-sections', '-s', '--hash-style=sysv', '--build-id=none',
        *objs, *libs, '-o', out/name)
    return (out/name).read_bytes()

def append_payload(image, payload, base, memsz, label):
    """Map payload at base through the image's final PT_NULL header, R/W/X: payloads keep static state."""
    segs = segments(image)
    nulls = [(o,p) for o,p in segs if p[0] == 0]
    check(len(nulls) == 1 and nulls[0][0] == segs[-1][0], f'{label}: no final PT_NULL slot')
    check(all(p[2]+p[5] < base for _,p in segs if p[0] == 1), f'{label}: payload mapping overlaps')
    off = (len(image)+65535)&~65535
    image.extend(bytes(off-len(image)))
    image.extend(payload)
    struct.pack_into('<8I',image,nulls[0][0],1,off,base,base,len(payload),memsz,7,65536)

TAG_C = 'usr/lib/libtag_c.so.2.0.1'
TAG_C_SHA = 'b00f06c20b33b1238d095fcbcd9e9e04594e715b44e8a9e1a0aa9e3962e8b2b1'

def patch_m4b_cover(raw):
    """Extend the pinned TagLib cover dispatcher with its existing MP4 extractor."""
    from ipod import branch
    check(sha(raw) == TAG_C_SHA, 'Unexpected TagLib cover library')
    image = bytearray(raw)
    base, site, mp4, next_format, gp = 0x30000, 0x125ac, 0x126d8, 0x125b4, 0x327e0
    # Keep M4A's successful comparison, then compare M4B before the original MP4 check.
    # GP-relative data and PC-relative branches work at any shared-library load address.
    words = [branch(0x10400000, base, mp4), 0,
             0x8f998170, 0x02002025, 0x27850000 | ((base + 44 - gp) & 0xffff),
             0x0320f809, 0,
             branch(0x10400000, base + 28, mp4), 0x8fbc0010,
             branch(0x10000000, base + 36, next_format), 0]
    payload = struct.pack('<11I', *words) + b'm4b\0'
    append_payload(image, payload, base, len(payload), 'TagLib cover')
    struct.pack_into('<I', image, segments(image)[-1][0] + 24, 5)  # immutable code/data
    check(struct.unpack_from('<I', raw, fileoff(raw, site))[0] == 0x1040004a,
          'Unexpected TagLib M4A cover branch')
    struct.pack_into('<I', image, fileoff(raw, site), branch(0x10000000, site, base))
    return bytes(image)

def bt_silence():
    """tools/bt_silence.S linked at BT_SILENCE: its bytes and symbols."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        run('clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mno-abicalls', '-fno-pic',
            f'-DBUDGET={BT_SILENCE_SAMPLES}', '-c', ROOT/'tools/bt_silence.S', '-o', d/'s.o')
        run('ld.lld', '-m', 'elf32ltsmip', f'-Ttext={BT_SILENCE:#x}', '-e', 'timeout', d/'s.o', '-o', d/'s.elf')
        run('llvm-objcopy', '-O', 'binary', '-j', '.text', d/'s.elf', d/'s.bin')
        return (d/'s.bin').read_bytes(), symbols(d/'s.elf')

def patch_bluealsa(raw):
    """Offer AAC at 48 kHz only. A headset that opens the stream itself (AirPods out of the case)
    picks 44.1 kHz, and bluealsa, still fed 48 kHz, drops about 8% of the AAC frames."""
    image = bytearray(raw)
    image[AAC_44K1] = 0
    # o32: preserve PCM and ra; hold its fd mutex while flushing the nonblocking
    # FIFO in 32 KiB batches, then send DROP. No encoder restart or codec change.
    words = [0x27bdffe0, 0xafbf001c, 0xafb00018, 0x00808025,
             0x0c10c1a4, 0x2604000c,
             0x0c10502e, 0x02002025, 0x1c40fffd, 0,
             0x0c10c0e8, 0x2604000c,
             0x8e040000, 0x24050006, 0x0c102dce, 0x24840050,
             0x8fb00018, 0x8fbf001c, 0x00001025, 0x03e00008, 0x27bd0020]
    reset = BT_DROP_PAYLOAD + len(words) * 4
    # The shared read helper receives a raw tail pointer, not the ffb_t. Rewind
    # each audited caller's partial PCM and update the helper's pointer/capacity
    # before it repolls; otherwise old and new tracks share one encoder frame.
    words += [0x8fa80054]  # saved return address
    for caller, offset in BT_PCM_CALLERS:
        check(struct.unpack_from('<I', raw, fileoff(raw, caller-8))[0] == 0x0c105174,
              'Bluetooth PCM caller mismatch')
        body = ([0x264b0000 | offset, 0x8d760000, 0xad760004, 0x8d750008]
                if offset is not None else
                [0x8fab004c, 0x8fac0040, 0x02cbb023, 0x02ccb021,
                 0xafac004c, 0x24150100, 0xafb50048])
        body += [0x081051e4, 0]  # existing OPEN/RESUME pacing reset and repoll
        words += [0x3c0a0000 | caller >> 16, 0x354a0000 | caller & 0xffff,
                  0x150a0000 | len(body)+1, 0, *body]
    words += [0x081051e4, 0]  # unknown caller: reset pacing without touching its buffer
    check(len(words)*4 <= BT_SILENCE - BT_DROP_PAYLOAD, 'Bluetooth DROP payload too long')
    words += [0] * ((BT_SILENCE - BT_DROP_PAYLOAD) // 4 - len(words))
    code, sym = bt_silence()
    payload = struct.pack(f'<{len(words)}I', *words) + code
    append_payload(image, payload, BT_DROP_PAYLOAD, len(payload), 'bluealsa')
    struct.pack_into('<2I', image, fileoff(raw, BT_DROP),
                     j(BT_DROP_PAYLOAD), 0)
    # Never flush the new track's FIFO from this asynchronously handled signal.
    struct.pack_into('<2I', image, fileoff(raw, BT_DROP_HANDLER), j(reset), 0)
    for address, old, op, target in BT_SILENCE_SITES:
        check(struct.unpack_from('<I', raw, fileoff(raw, address))[0] == old, 'Bluetooth silence site mismatch')
        new = {'j': j, 'jal': jal}[op](sym[target]) if op else target
        struct.pack_into('<I', image, fileoff(raw, address), new)
    return bytes(image)

STOCK_VERSION = 'V1.34'

def stock_header(context, ranges, themes, dev=0, repo=REPO):
    """stock.h, which the payload's C includes: context maps each CONTEXT_DATA name to the C address of
    its data (demo's symbol on the Q2; test/fw/host.py's own array), ranges are the fonts' range macros
    (subset_font) and themes theme.builtins()'s."""
    from ipod import SETTINGS_ICONS
    from theme import theme_keys
    header = [f'#define RING_STEP {RING_STEP}', *ranges]
    header.append('#define TIMEZONE_NAMES {' + ','.join(json.dumps(zone) for _, zone in TIMEZONES) + '}')
    header.append('#define TIMEZONE_ORDER {' + ','.join(map(str, TIMEZONE_ORDER)) + '}')
    header.append('#define TIMEZONE_CITIES {' + ','.join(json.dumps(label.split(' ', 1)[1]) for label, _ in TIMEZONES) + '}')
    header += [f'#define {name} ((const unsigned char *){context[name]})' for name in CONTEXT_DATA]
    # iPod's image hook leaves the settings icons' category colours alone (navigation.c settings_icon).
    names = ''.join(n.removesuffix('.png') + '\\0' for n in SETTINGS_ICONS)
    header.append(f'#define SETTINGS_ICON_NAMES "{names}"')
    header.append(f'#define ROCKBOX_FLAG "{ROCKBOX_FLAG}"')  # Home's Rockbox row, for S90play
    # About: the stock firmware's version on its own row, and this build's on the CFW. Version row.
    header += [f'#define STOCK_VERSION "{STOCK_VERSION}"', f'#define Q2POD_VERSION "{VERSION} Pod{f" dev {dev}" if dev else f" beta {BETA}" if BETA else ""}"',
               f'#define Q2POD_TAG "{"" if dev else VERSION}"',  # netupdate.c: the release tag, none when --dev
               f'#define Q2POD_DEV {1 if dev else 0}',  # navigation.c stale_note
               f'#define Q2POD_REPO "{repo}"',  # netupdate.c: the repo whose latest release it fetches
               f'#define Q2POD_UA "Q2Pod/{VERSION} (https://github.com/DiamondBond/q2-pod)"']  # onlinematch.c, as MusicBrainz asks
    header.append(f'#define THEME_N {len(themes)}')  # theme_engine.c setting_counts[]
    header.append(f'#define THEME_KEYS {len(theme_keys(themes))}')  # navigation.c st.own[], themes.inc theme_keys[]
    from art import corner_table, qs_lit
    header.append(f'#define ROUND_COVER {corner_table()}')  # navigation.c paint_round_rows
    header.append(f'#define QS_LIT "{qs_lit()}"')  # navigation.c quick_page: Classic's buttons that are on
    return '\n'.join(header)+'\n'

def build(zip_path, out, logo, dev=False, repo=REPO, aic_idle_wait=False, rotator_lazy_clock=False, pwm_idle_clock=False, mcu_idle_sleep=False, flac_experiments='sync,lpc,output'):  # pragma: no mutate block
    from flac import selection
    flac_experiments = selection(flac_experiments)
    # Kernel-module experiments: no measured saving (docs/clock-gating.md, pwm-clock-gating.md,
    # audio-notifier.md), none handles suspend/resume, and stock Standby suspends by default; dev only.
    check(not aic_idle_wait or dev, '--aic-idle-wait requires --dev')
    check(not rotator_lazy_clock or dev, '--rotator-lazy-clock requires --dev')
    check(not pwm_idle_clock or dev, '--pwm-idle-clock requires --dev')
    check(not mcu_idle_sleep or (dev and pwm_idle_clock), '--mcu-idle-sleep requires --dev --pwm-idle-clock')
    version = TAG
    check(re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9._-]+', repo), f'{repo}: not a GitHub owner/name')  # a C string
    out.mkdir(parents=True, exist_ok=True)
    check(not (out/'update.tar').exists(), 'Output already exists; use a fresh --out directory')
    if dev:  # the next number in the untracked .build-number
        counter = ROOT/'.build-number'
        dev = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(f'{dev}\n')
        version = dev_tag(dev)
    source = source_sha256()
    raw = zip_path.read_bytes()
    check(sha(raw) == ZIP_SHA, 'Unsupported ZIP: SHA-256 differs from audited original')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        tarbytes = z.read('Q2 Firmware V1.34/update.tar')
    with tarfile.open(fileobj=io.BytesIO(tarbytes)) as t:
        meta = t.getmembers()
        check([m.name for m in meta] == ['firmware_v20.info','recovery-update',
              'recovery-update/xImage','recovery-update/rootfs.squashfs'], 'Unexpected package members')
        blobs = {m.name:need(t.extractfile(m), f'Unreadable {m.name}').read() for m in meta if m.isfile()}
    info = blobs['firmware_v20.info'].decode().splitlines()
    check(info[:2] == ['Shanling Q2', STOCK_VERSION], 'Wrong model/version')
    sq = out/'stock.squashfs'; sq.write_bytes(blobs['recovery-update/rootfs.squashfs'])
    def cat(path): return subprocess.check_output(['unsquashfs', '-cat', str(sq), path])
    raw_demo = cat('release/bin/demo')
    (out/'libc-2.28.so').write_bytes(cat('lib/libc-2.28.so'))
    # peq.h tls_untimed writes OpenSSL 1.1.1f's SSL_CTX->param (0x100) and its ->flags (0xc) itself,
    # as demo imports neither accessor: their first instructions must still read those fields.
    for lib, fn, at, word in (('libssl.so.1.1', 'SSL_CTX_get0_param', 4, 0x8c820100),         # lw v0, 0x100(a0)
                              ('libcrypto.so.1.1', 'X509_VERIFY_PARAM_set_flags', 0, 0x8c86000c)):  # lw a2, 0xc(a0)
        data = cat('usr/lib/'+lib); (out/lib).write_bytes(data)
        address = symbols(out/lib)[fn+'@@OPENSSL_1_1_0']  # its text LOAD maps file offset 0 at 0
        check(struct.unpack_from('<I', data, address + at)[0] == word, f'{lib}: {fn} layout changed')
    # Every allowlisted context must be a window name. The runtime name is the root "name"
    # property of the UI asset, not the asset path, so check the stock rootfs assets directly:
    # a prefix-trimmed typo cannot silently disable a screen this way.
    from ipod import (AUDIT, ARTIST_ALBUMS, SETTINGS_ICONS, inc, UI_ASSETS, patch_asset,
                      QUIET_ICONS, quiet_icon, imagemagick, patch_code, patch_style, patch_word, branch, settings_icon,
                      STATUS_ICONS, status_icon, row_icon, pane_icon, ART_PLACEHOLDERS, sized_art, PLAYING_STOCK, PLAYING_PAGE, playing_stock,
                      set_assets)
    contexts = re.findall(r'"([^"]+)"', (ROOT/'patch/contexts.inc').read_text())
    check(contexts, 'No navigation contexts audited')
    windows, chinese = set(), set()  # chinese: the UI assets english_ui rewrites
    for line in run('unsquashfs', '-l', sq).splitlines():
        found = re.search(r'/raw/ui/(.+)\.bin$', line)
        if not found: continue
        rel = found.group(1)
        data = cat('release/assets/default/raw/ui/'+rel+'.bin')
        if CJK.search(data.decode('utf-8', 'replace')) and 'release/assets/default/raw/ui/'+rel+'.bin' not in STOCK_EQ + STOCK_PAGES:
            chinese.add(rel + '.bin')  # the stock EQ pages go, and the pages not offered
        i = data.find(b'name\x00')
        name = data[i+5:data.find(b'\x00', i+5)].decode('utf-8', 'replace') if i >= 0 else ''
        windows.add(name or rel.split('/')[-1])
        # iPod pre-sizes the settings icons, so no UI asset may name one (only native settings code does).
        for icon in SETTINGS_ICONS:
            check(icon.removesuffix('.png').encode() + b'\0' not in data, f'{rel}: names settings icon {icon}')
    check(windows, 'No UI assets in the stock rootfs')
    for name in contexts:
        check(name in windows | PAYLOAD_WINDOWS, f'Context {name} is not a window name in the stock rootfs')
    demo = out/'stock-demo'; demo.write_bytes(raw_demo)
    symbol_table = run('readelf', '-Ws', demo)
    syms = symbols(demo, symbol_table)
    for name, address in APP_URL_CALLS.items():
        check(syms.get(name) == address, f'{name}: not at {address:#x} in the stock demo')
    syms.update(PRIVATE_FUNCTIONS)
    bold, bold_ranges = bold_font()
    classic_light, classic_light_ranges = classic_light_font()
    for name in GLOBALS:
        check(re.search(rf'\b1\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+{name}$',
                        symbol_table, re.M), f'{name}: byte global size mismatch')
    for name, size in CONTEXT_DATA.items():
        check(re.search(rf'\b{size}\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+{name}$',
                        symbol_table, re.M), f'{name}: context data size mismatch')
    from theme import builtins, emit, geometry_sets
    themes = builtins()
    (out/'stock.h').write_text(stock_header({name: f'0x{syms[name]:x}u' for name in CONTEXT_DATA},
                                            [bold_ranges, classic_light_ranges], themes, dev, repo))
    set_pages, redirects = {}, {}
    ui = 'release/assets/default/raw/ui/'
    for i, g in enumerate(geometry_sets(themes)[1:], 1):
        for path, data in set_assets(cat, g).items():
            folder, _, base = path.rpartition('/')
            like = f'{folder}/{base.removeprefix(g.name + "_")}'
            rel = like.removeprefix('release/assets/default/raw/ui/')
            set_pages[path] = (like, english_ui(rel, data) if rel in chinese else data)
            redirects[rel.removesuffix('.bin')] = redirects.get(rel.removesuffix('.bin'), 0) | 1 << i
    emit(themes, out, redirects)
    ps = compile_payload(out)
    payload = (out/'patch.bin').read_bytes()
    check_layout(payload, ps)
    patched = bytearray(raw_demo)
    patch_timezones(patched)
    def jump(off, name): patched[off:off+8] = struct.pack('<II', j(ps[name]), 0)
    for name, (address, replacement, _) in HOOKS.items():
        check(syms[name] == address, f'{name}: callback address mismatch')
        off = fileoff(patched, address)
        prolog = struct.unpack_from('<III', patched, off)
        check(prolog[0] >> 16 == 0x3c1c and prolog[1] >> 16 == 0x279c and
              prolog[2] == 0x0399e021, f'{name}: unexpected PIC prologue')
        low = prolog[1] & 65535
        gp = ((prolog[0] & 65535) << 16) + (low if low < 32768 else low - 65536) + address
        check(gp == GP, f'{name}: unexpected GOT base')
        jump(off, replacement)
    for name, address, replacement, words in (WM_PAINT_LEAF, GRADIENT_LEAF, QUEUE_LEAF):
        off = fileoff(patched, address)
        check(syms[name] == address and struct.unpack_from('<III', patched, off) == words, f'{name}: unexpected code')
        jump(off, replacement)
    # style_get_color's own bal style_get_gradient, returning to STYLE_COLOR_GRADIENT_RET, stays unmapped.
    ret = inc('STYLE_COLOR_GRADIENT_RET')
    check(struct.unpack_from('<I', patched, fileoff(patched, ret - 8))[0] == branch(0x04110000, ret - 8, GRADIENT_LEAF[1]),
          'style_get_color: unexpected gradient call')
    # tidal_start reads the song id from $s2: set to the thread's own copy at 0x48718c, freed after
    # its only mcl_tidalStartPlayer call (lw t9; jalr; move a0, s7).
    check(struct.unpack_from('<I', patched, fileoff(patched, 0x48718c))[0] == 0x00409025 and
          struct.unpack_from('<III', patched, fileoff(patched, 0x487a98)) == (0x8f99ab18, 0x0320f809, 0x02e02025),
          'Tidal URL thread: unexpected song id register')
    # ringnav_mode leaves advanced play on for player_initconfig's call, which returns to INITCONFIG_MODE_RET.
    ret = inc('INITCONFIG_MODE_RET')
    check(struct.unpack_from('<II', patched, fileoff(patched, ret - 12)) == (0x8f99b800, 0x0320f809),  # 0x51ded4: lw $t9, mclSetPlayMode's slot
          'player_initconfig: unexpected mclSetPlayMode call')
    patch_word(patched, 0x503fb8, 0x0411e963, jal(ps['ringnav_bootmode']),
               'config_init preserves the saved queue when applying the boot play mode')
    patch_word(patched, 0x504b54, 0x0320f809, jal(ps['lockscreen_access']),
               'startup accepts built-in wallpapers')
    patch_word(patched, 0x505554, 0x0320f809, jal(ps['lockscreen_access']),
               'wallpaper list accepts built-in wallpapers')
    from peq import patch_player
    raw_player = cat('usr/bin/hciplayer')
    audio = patch_player(raw_player, out/'peq', flac_experiments)
    bluealsa = patch_bluealsa(cat(BLUEALSA))
    (out/'bluealsa').write_bytes(bluealsa)
    (out/'watchdog').write_bytes(cat(WATCHDOG).replace(*WATCHDOG_SLEEP))
    for address, old, new in ARTIST_ALBUMS:
        patch_word(patched, address, old, new, 'artist detail opens on Albums')
    patch_word(patched, *SHUFFLE_CALL, jal(ps['ringnav_shuffle']),
               'shuffle honours Play next')
    patch_word(patched, *HOLD_RETURN, jal(ps['compact_now_playing']),
               'long Return destination after stock input gates')
    patch_word(patched, *STANDBY_WAKE, 'Standby resume stays dark')
    patch_word(patched, *NAV_SHOWN, "stock's navigation bar stays shown")
    patch_word(patched, *WHEEL_MODE, "stock's wheel stays in Volume Mode")
    patch_word(patched, *HOME_KEYUP, 'Home binds no key-up of its own')
    patch_word(patched, *KEYLOCK_LIT, 'Buttons lock acts only with the screen off')
    patch_word(patched, *VOL_DARK, 'The volume dialog steps with the screen off')
    for site in MORE_ARTIST_CLOSE:
        patch_word(patched, *site, "Artist info closes the artist and album pages it found")
    patch_word(patched, *MAINSCHEDULING_CALL, jal(ps['ringnav_mainscheduling']), 'player scheduling paced')
    patch_word(patched, *START_MUTE_CALL, jal(ps['start_mute']), 'natural start: no mute before the end')
    patch_word(patched, *START_SLEEP_CALL, jal(ps['start_sleep']), 'natural start: no sleep before the end')
    for address, old, new, fn in HOLD_KEYS:
        patch_word(patched, address, old, new or jal(ps[fn]), 'Play/Pause and Return hold time')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x51dacc))[0] == 0x8f99b764,
          'player cover: expected toolsGetExternCover import')
    patch_word(patched, *PLAYER_COVER, jal(ps['coverflow_player_cover']), 'Now Playing art through ffmpeg')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x51f3d8))[0] == 0x8f99f674,
          'id3 path: expected strcpy import')
    patch_word(patched, *ID3_PATH, jal(ps['ringnav_id3_path']), "a CUE track's file tags")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x53d3d8))[0] == 0x8f99c140,
          'lyric follow: expected scroll_view_scroll_to import')
    patch_word(patched, *LYRIC_FOLLOW, jal(ps['ringnav_lyric_follow']), 'lyric rows whole at the top')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x5fb414))[0] == 0x8f99a858,
          'hscroll lull: expected widget_add_timer import')
    patch_word(patched, *HSCROLL_LULL, jal(ps['ringnav_hscroll_lull']), 'a scrolling label holds at its start')
    patch_word(patched, *HSCROLL_ONCE, 'a single pass holds its end')
    patch_word(patched, *LYRIC_ROWS, jal(ps['ringnav_lyric_rows']), 'lyrics keep their place on a refresh')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x5bc074))[0] == 0x8f99f5a0,
          'lyric file: expected fopen import')
    patch_word(patched, *LYRIC_OPEN, jal(ps['ringnav_lrc_open']), 'lyric files read as tags are')
    for call, load, fn in GRID_COVER:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, load))[0] == 0x8f99b760,
              'grid cover: expected toolsGetAlbumCover import')
        patch_word(patched, call, 0x0320f809, jal(ps[fn]), 'Ogg art in Albums and Folder view')
    patch_word(patched, *BT_CONNECT_START, jal(ps['ringnav_bt_connect_start']),
               'Bluetooth: Resume on connect')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, BT_INIT[0] - 4))[0] == 0x8f99f610,
          'Bluetooth init: expected btctl_create_daemon_thread import')
    patch_word(patched, *BT_INIT, jal(ps['headset_bt_init']), 'Bluetooth callbacks before control thread')
    patch_word(patched, *BT_PARAMS, 0, 'Bluetooth state initialized before control thread')
    check(struct.unpack_from('<III', raw_demo, fileoff(raw_demo, BT_STATUS[0] - 4)) ==
          (0x8f99fc10, 0x0320f809, 0x2484bfb0), 'Bluetooth status: expected btctl_device_status_callback(0x4fbfb0)')
    patch_word(patched, *BT_STATUS, jal(ps['headset_status_register']), 'Bluetooth: incoming link during a connect')
    patch_word(patched, *BT_AUTOLINK, jal(ps['headset_bt_autolink']), 'Bluetooth: auto-link keeps an incoming link')
    patch_word(patched, *SCAN_REFRESH, jal(ps['ringnav_scan_refresh']), 'folder scan refreshes existing tags')
    patch_word(patched, *TAG_TEXT, jal(ps['ringnav_gbk_tag']), 'tag text GBK only when it reads as GBK')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, GENRE_COPY[0] - 4))[0] == 0x8f99f674,
          'genre copy: expected strcpy import')
    patch_word(patched, *GENRE_COPY, jal(ps['ringnav_genre_copy']),
               'long genre tags stay inside the style field')
    for address in CUE_TEXT:
        patch_word(patched, address, branch(0x04110000, address, syms['toolsIconvConvert']),
                   jal(ps['ringnav_cue_text']), 'CUE text read as UTF-8, GBK or Windows-1252')
    patch_word(patched, DIALOG_CLOSE, branch(0x04110000, DIALOG_CLOSE, syms['idle_add']),
               jal(ps['ringnav_dialog_close']), "a modal dialog's close skips one already gone")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, SCAN_DIRS[0] + 4))[0] == 0x24a59270,
          'library scan: expected the "." exclusion')
    patch_word(patched, *SCAN_DIRS, jal(ps['ringnav_scan_dir']), 'the library scan skips hidden folders')
    scan, load, limit, count = SCAN_LIMIT
    patch_word(patched, scan, load, jal(ps['ringnav_scan_room']), 'scan up to 65,000 songs')
    patch_word(patched, scan + 4, limit, count, 'scan up to 65,000 songs')
    for address, old, new in DROP_CACHES:
        patch_word(patched, address, old, new, 'keep the page cache')
    for address, old, new in BT_INPUT:
        patch_word(patched, address, old, new, "a headset's keys are read once, by the headset worker")
    for address, name in STREAM_POLL_CALLS:
        patch_word(patched, address, 0x0320f809, jal(ps[name]), 'stream threads park while idle')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, COVERINFO_CALL[0] + 4))[0] == COVERINFO_CALL[1],
          "online id3: expected the REC_COVER load in the cover call's delay slot")
    patch_word(patched, COVERINFO_CALL[0], 0x0320f809, jal(ps[COVERINFO_CALL[2]]), "a card app's cover by URL")
    for address, name in DARK_POLLS:
        patch_word(patched, address, 0x0320f809, jal(ps[name]), 'stock polls idle while dark')
    check(struct.unpack_from('<I', patched, fileoff(patched, LYRIC_SAVE_FMT[0]))[0] == LYRIC_SAVE_FMT[1],
          'Unexpected Download Cover & Lyrics save')
    patch_word(patched, LYRIC_SAVE_CALL, 0x0320f809, jal(ps['ringnav_lyric_save']), 'lyrics saved beside the song')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, SONG_UNKNOWN_CALL-8))[0] == 0x8f99a924,
          "song bind: expected getFormatString import")  # lw t9, -0x56f8(gp)
    patch_word(patched, SONG_UNKNOWN_CALL, 0x0320f809, jal(ps['ringnav_song_unknown']), "a song row's empty artist")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, TABLE_LOAD_CALL-0x14))[0] == 0x8e9900e4,
          "table load: expected on_load_data's callback")  # lw t9, 0xe4(s4)
    patch_word(patched, TABLE_LOAD_CALL, 0x0320f809, jal(ps['ringnav_table_load']), "a bound table row's faces")
    for word in FOLDER_NAME:
        patch_word(patched, *word, 'folder names are literal text')
    patch_word(patched, NP_INFO_CALL, branch(0x04110000, NP_INFO_CALL, PRIVATE_FUNCTIONS['np_info']),
               jal(ps['ringnav_np_info']), "Now Playing's info lines")
    for site in AB_X_CALLS:
        patch_word(patched, site, branch(0x04110000, site, PRIVATE_FUNCTIONS['ab_x']),
                   jal(ps['ringnav_ab_x']), "A-B markers on Classic's fitted bar")
    for address, old, new in LDAC_HQ:
        patch_word(patched, address, old, new, 'LDAC HQ sends 990 kbps')
    patch_word(patched, WHEEL_TS_READ[0], WHEEL_TS_READ[1], jal(ps['ringnav_ts_read']), 'wheel ticks')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, GP - 0x7f44))[0] == 0xa50000 and
          all(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, a))[0] == w for a, w in NET_TYPE_READS),
          'networkStart/networkStop: unexpected network type gate')
    for address in AUDIO_EXTENSION_CALLS:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, address-8))[0] == 0x8f99fa08,
              'audio extension: expected strstr import')
        patch_word(patched, address, 0x0320f809, jal(ps['ringnav_audio_extension']),
                   'recognize M4B alongside existing audio extensions')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, M3U_DIR_CALL-4))[0] == 0x8f99bb5c,
          'playlist import: expected toolsGetDirectoryM3u8 call')  # lw t9, -0x452c(gp)
    patch_word(patched, M3U_DIR_CALL, 0x0320f809, jal(ps['addon_m3u_dir']), "card apps import only their own playlists")
    for address in SORT_TRIMS:
        patch_word(patched, address, branch(0x04110000, address, syms['toolsTrimLeft']),
                   jal(ps['ringnav_sort_key']), 'sort without a leading article')
    for address, name in ALBUM_ART:
        patch_word(patched, address, 0x0320f809, jal(ps[name]), 'same-named albums apart in art')
    for row in ALBUM_ART_ROW:
        patch_word(patched, *row, 'same-named albums apart in art')
    for address, load in COVER_PUSH:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, load))[0] == 0x8f99f7ec,
              'cover push: expected _deque_push_back import')  # lw t9, -0x8cc(gp)
        patch_word(patched, address, 0x0320f809, jal(ps['ringnav_cover_push']), "a cover task's name and path freed once copied")
    patch_word(patched, CLASS_ICON, branch(0x04110000, CLASS_ICON, syms['class_icon']),
               jal(ps['ringnav_class_icon']), 'Artists rows without the head picture')
    for word in COVER_PAUSE:
        patch_word(patched, *word, 'album grids never pause the cover worker')
    for word in TIDAL_PAGE_DONE:
        patch_word(patched, *word, 'Tidal lists load every page')
    for word in ARTIST_COVER_ROW:
        patch_word(patched, *word, "an artist's albums request the covers in view")
    # Local art completion: pass the worker's copied key, then validate the row under aclist_mutex.
    patch_word(patched, 0x4f82ec, 0x8f99b754, 0x27a61688, 'cover completion key')
    patch_word(patched, 0x4f82f0, 0x0411ff44, jal(ps['ringnav_cover_done']),
               'discard stale cover completion')
    # The folder-cover path branched past the key (b 0x4f82f0); it enters at the key as the others do.
    patch_word(patched, 0x4f932c, 0x1000fbf0, 0x1000fbef, 'folder cover completion key')
    # Pin added private entry points as well as every replaced instruction, and the stock bitmap,
    # canvas and slide_menu entries Coverflow's depth renderer calls (docs/internals.md#coverflow-depth).
    for name, original in AUDIT['private_prologues'].items():
        off = fileoff(raw_demo, syms[name])
        check(raw_demo[off:off+12].hex() == original, f'{name}: unexpected stock entry')
    for address, original in AUDIT['event_abi_words'].items():
        off = fileoff(raw_demo, int(address, 16))
        check(raw_demo[off:off+4].hex() == original, f'{address}: unexpected event ABI instruction')
    code_changes = patch_code(patched, ps)
    # Single shared version literal: the updater refuses an equal firmware_v20.info version (update_firmware
    # 0x502a00, check_otginfo 0x502f28), so it carries the build tag and stock V1.34 can be flashed back.
    # About's FW. Version row reads it too (0x4c0f80); ringnav_about shows STOCK_VERSION there instead.
    check(patched.count(b'V1.34\0') == 1, 'Version literal is not unique')
    check(len(version) + 1 == len(b'V1.34\0'),
          'VERSION must be three single digits (x.y.z): the 5-character tag would shift every later file offset')
    patched = patched.replace(b'V1.34\0', version.encode()+b'\0')
    patched = patched.replace(*RTC_WRITE)
    check(patched.count(WIFI_LPS[0]) == 1, 'Unexpected Wi-Fi power save command')
    patched = patched.replace(*WIFI_LPS)
    check(patched.count(LOCK_CLOCK_12H[0]) == 1, 'Unexpected lock clock formats')
    patched = patched.replace(*LOCK_CLOCK_12H)
    check(re.search(r'\.pdr +PROGBITS +0+ +0*%x +0*%x ' % PDR, run('readelf', '-SW', demo)) and
          all(p[1]+p[4] <= PDR[0] for _,p in segments(patched) if p[0] == 1), '.pdr: not the audited section')
    patched[PDR[0]:PDR[0]+PDR[1]] = bytes(PDR[1])
    append_payload(patched, payload, BASE, ps['__scratch_end']-BASE, 'demo')
    (out/'demo').write_bytes(patched)
    # Pseudo-file round trip preserves every original inode's metadata and hardlinks.
    pseudo = out/'root.pseudo'
    run('unsquashfs','-pf',pseudo,sq)
    p = pseudo.read_bytes()
    # mksquashfs takes "/" from the source dir, not the pseudo file; carry stock values over.
    root = need(re.search(rb'^/ D (\d+) (\d+) (\d+) (\d+)$',p,re.M), 'Missing root pseudo inode')
    t,mode,uid,gid = (x.decode() for x in root.groups())
    rootargs = ['-root-time',t,'-root-mode',mode,'-root-uid',uid,'-root-gid',gid]
    # Paths are passed through a shell by mksquashfs F entries; quote them explicitly.
    def inode(p, path): return re.search(rb'^'+re.escape(path)+rb' R (\d+) (\d+) (\d+) (\d+) .+$',p,re.M)
    def swap_inode(p, path, src):
        line = need(inode(p, path), f'Missing {path.decode()} pseudo inode')
        return p[:line.start()]+path+b' F '+b' '.join(line.groups())+b' cat '+shlex.quote(str(src)).encode()+p[line.end():]
    s90play = cat(S90PLAY)
    check(s90play.count(BOOT_HOOK[0]) == 1 and s90play.count(REMOTE_HOOK[0]) == 1, 'Unexpected S90play')
    s90play = s90play.replace(*BOOT_HOOK)
    check(s90play.count(LOG_HOOK[0]) == 1, 'Unexpected demo loop')
    check(s90play.count(WHEEL_LOG_HOOK[0]) == 1, 'Unexpected card path')
    (out/'S90play').write_bytes(s90play.replace(*REMOTE_HOOK).replace(*LOG_HOOK).replace(*WHEEL_LOG_HOOK) if dev else s90play)
    (out/'config.ini').write_bytes(default_config(cat(DEFAULT_CONFIG)))
    s11logo = cat(S11LOGO)
    check(s11logo.count(LOGO_HOOK[0]) == 1, 'Unexpected S11logo_display_shell')
    (out/'S11logo').write_bytes(s11logo.replace(*LOGO_HOOK))
    bluealsa2 = cat(BLUEALSA2)
    check(bluealsa2.count(BT_KEEPALIVE[0]) == 1, 'Unexpected bluealsa2')
    (out/'bluealsa2').write_bytes(bluealsa2.replace(*BT_KEEPALIVE))
    logo_data = logo.read_bytes()
    check(jpeg_size(logo_data) == (320, 375), 'Logo must be 320x375 like the stock splash')
    # Package exactly the validated bytes, even if the input is edited during compression.
    logo = out/'logo.jpg'
    logo.write_bytes(logo_data)
    # Light Mode's splash: the default logo inverted. A custom --logo has no twin, so it shows either way.
    light = logo_data == (ROOT/'assets/boot-logo.jpg').read_bytes() and (ROOT/'assets/boot-logo-light.jpg').read_bytes()
    check(not light or jpeg_size(light) == (320, 375), 'Light logo must be 320x375 like the stock splash')
    for path, src in ((b'release/bin/demo', out/'demo'), (b'usr/bin/hciplayer', out/'peq/hciplayer'),
                      (BLUEALSA.encode(), out/'bluealsa'), (WATCHDOG.encode(), out/'watchdog'),
                      (S90PLAY.encode(), out/'S90play'), (S11LOGO.encode(), out/'S11logo'),
                      (BLUEALSA2.encode(), out/'bluealsa2'), (DEFAULT_CONFIG.encode(), out/'config.ini'),
                      (LOGO.encode(), logo)):
        p = swap_inode(p, path, src)
    aic_notifier = None
    if aic_idle_wait:
        from aic import MODULE_PATH, PATCH_POLICY, STOCK_SHA256, patch_module
        module = patch_module(cat(MODULE_PATH))
        (out/'soc_aic.ko').write_bytes(module)
        p = swap_inode(p, MODULE_PATH.encode(), out/'soc_aic.ko')
        aic_notifier = dict(policy=PATCH_POLICY, path=MODULE_PATH,
                            original_sha256=STOCK_SHA256, sha256=sha(module))
    rotator = None
    if rotator_lazy_clock:
        from rotator import MODULE_PATH, STOCK_SHA256, patch_module
        module = patch_module(cat(MODULE_PATH))
        (out/'soc_rotator.ko').write_bytes(module)
        p = swap_inode(p, MODULE_PATH.encode(), out/'soc_rotator.ko')
        rotator = dict(policy='lazy-first-use-v1', path=MODULE_PATH,
                       original_sha256=STOCK_SHA256, sha256=sha(module))
    pwm = None
    if pwm_idle_clock:
        from pwm import patch_bundle
        files, pwm = patch_bundle(cat, sleep=mcu_idle_sleep)
        for path, data in files.items():
            src = out/pathlib.Path(path).name
            src.write_bytes(data)
            p = swap_inode(p, path.encode(), src)
    # New inodes, each with its stock image's metadata: Home's Coverflow and Shortcut card icons (menu_music's,
    # in their own colours like stock's cards), the Local Music and Spotify rows' icons, drawn in assets/icons/ (stock's
    # 52px style) or else a copy of the stock image named (not the copies iPod pre-sizes for Settings), and q2video.
    xx = 'release/assets/default/raw/images/xx/'
    icons = {n: ('menu_musicdown.png' if n.endswith('down.png') else 'menu_music.png',
                 (ROOT/'assets/icons'/n).read_bytes()) for n in ICONS}
    drawn = ('podcasts', 'audiobooks', 'books', 'videos')
    icons.update({f'local_{n}.png': (like, (ROOT/'assets/icons'/f'local_{n}.png').read_bytes() if n in drawn
                                     else cat(xx+like)) for n, like in dict(
        shuffle='playset_playmode.png', scrobble='wifiset_wifi.png', podcasts='netservice_dlna.png',
        audiobooks='playset_foldercover.png', photos='playset_covermode.png', books='system_language.png',
        videos='system_display.png', longsongs='system_time.png', era='local_genre.png').items()})
    icons['local_games.png'] = ('system_language.png', (ROOT/'assets/icons/local_games.png').read_bytes())
    # Minimal's selected Wi-Fi and Bluetooth rows draw this grey copy over stock's white delete mark
    icons['list_delete.png'] = ('navbar_delete.png', cat(xx+'navbar_delete.png'))
    for n in ('spotify', 'radio', 'app', 'config'):  # Streaming's Spotify, Internet Radio, card app and Configuration rows
        icons[f'stream_{n}.png'] = ('list_tidal.png', (ROOT/'assets/icons'/f'stream_{n}.png').read_bytes())
    icons['stream_airplay.png'] = ('list_tidal.png', cat(xx+'netservice_airplay.png'))  # iPod resizes stock's
    art = (ROOT/'assets/icons/default_album_home.png').read_bytes()
    # Streaming's rows draw them at SET_ICON; Home's placeholder at the art panel's 290px
    from theme import builtins as builtin_themes
    pane = max(t['pane_svc_px'] for t in builtin_themes())  # Home's pane on Streaming's list, 1:1 (coverflow.c pane_service)
    for n in ('spotify', 'radio', 'app', 'airplay', 'config'):
        like, data = icons[f'stream_{n}.png']
        icons[f'classic_light_stream_{n}.png'] = (like, pane_icon(data, pane))
        icons[f'stream_{n}.png'] = (like, row_icon(data, sharp=True))
    icons['classic_light_list_tidal.png'] = ('list_tidal.png', pane_icon(cat(xx+'list_tidal.png'), pane))
    icons['default_album_home.png'] = ('default_album_big.png', art)
    icons['default_album_flow.png'] = ('default_album_big.png', sized_art(art, 160))
    pages = {ui+PLAYING_STOCK: (ui+'playing_page.bin', english_ui(PLAYING_PAGE, playing_stock(cat(ui+'playing_page.bin')))), **set_pages}
    added = []
    changed_assets = {}
    from art import images as theme_images
    drawn = {xx+n+'.png': (xx+'list_tidal.png', d) for t in builtin_themes() for n, d in theme_images(t).items()}
    for path, (like, data) in {**{xx+n: (xx+l, d) for n, (l, d) in icons.items()}, **pages, **drawn,
                               HELPER: (HELPER_LIKE, compile_helper(out, cat, 'video', 'q2video', HELPER_LIBS)),
                               SCROBBLE_CA: ('etc/hosts', (ROOT/'assets/scrobble-ca.pem').read_bytes()),
                               BOOT: (HELPER_LIKE, compile_helper(out, cat, 'boot', 'q2boot', HELPER_LIBS[:1])),
                               **({LOGO_LIGHT: (xx+'list_tidal.png', light)} if light else {}),  # the swapped logo.jpg has no stock inode left
                               BOLD_TTF: (FONT, bold),
                               CLASSIC_LIGHT_TTF: (FONT, classic_light),
                               **{'release/assets/default/raw/fonts/' + n: ('etc/hosts', (ROOT/'assets/fonts'/n).read_bytes()) for n in FONT_NOTICES},
                               **({REMOTE: (HELPER_LIKE, compile_helper(out, cat, 'remote', 'q2remote', HELPER_LIBS[:1]))} if dev else {})}.items():
        stock = need(inode(p, like.encode()), f'Missing stock inode for {path}')
        name = path.rsplit('/', 1)[-1]
        if path not in (HELPER, BOOT, REMOTE): (out/name).write_bytes(data)  # package the hashed bytes, as the logo
        if inode(p, path.encode()):  # stock has a file of that name (V1.34's own menu_coverflow*.png): ours takes its inode
            changed_assets[path] = dict(original_sha256=sha(cat(path)), sha256=sha(data))
            p = swap_inode(p, path.encode(), out/name)
            continue
        path = path.encode()
        entry = path+b' F '+b' '.join(stock.groups())+b' cat '+shlex.quote(str(out/name)).encode()+b'\n'
        at = p.index(b'# START OF DATA')  # definitions precede the embedded data
        p = p[:at]+entry+p[at:]
        added.append([path, b'R', *stock.groups()])
    removed = []
    for path in STOCK_EQ + STOCK_PAGES + OBSOLETE:
        line = need(re.search(rb'^'+re.escape(path.encode())+rb' R .+\n', p, re.M), f'Missing stock inode {path}')
        removed.append(line.group().split()[:6])
        p = p[:line.start()]+p[line.end():]
    for path, fix, name in ((TAG_C, patch_m4b_cover, 'libtag_c.so.2.0.1'), (FONT, font_quotes, 'default.ttf'),
                            *((p, lambda d, p=p: patch_web(p, d), p.rsplit('/', 1)[-1]) for p in WEB)):
        original = cat(path)
        data = fix(original)
        (out/name).write_bytes(data)
        p = swap_inode(p, path.encode(), out/name)
        changed_assets[path] = dict(original_sha256=sha(original), sha256=sha(data))
    patched_ui = list(UI_ASSETS)
    check(chinese, 'No Chinese UI text found')
    assets = ['ui/'+rel for rel in patched_ui + sorted(chinese - set(patched_ui))] + ['strings/en_US.bin']
    assets += ['styles/'+rel for rel in AUDIT['styles']]
    # images changed in place, so each keeps its inode metadata: settings icons pre-sized to the rows'
    # SET_ICON and the art placeholders
    assets += ['images/'+name for name in SETTINGS_ICONS | QUIET_ICONS | STATUS_ICONS | ART_PLACEHOLDERS]
    for rel in assets:
        kind, name = rel.split('/', 1)
        path = 'release/assets/default/raw/' + ('images/xx/'+name if kind == 'images' else rel)
        original = cat(path)
        data = (relabel(original) if kind == 'strings' else
                patch_style(original, AUDIT['styles'][name]) if kind == 'styles' else
                (sized_art(art, ART_PLACEHOLDERS[name]) if name in ART_PLACEHOLDERS else
                 status_icon(name, original) if name in STATUS_ICONS else quiet_icon(name, original) if name in QUIET_ICONS else settings_icon(name, original)) if kind == 'images' else
                patch_asset(name, original) if name in patched_ui else original)
        if kind == 'ui' and name in chinese:
            data = english_ui(name, data)
        target = out/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        p = swap_inode(p, path.encode(), target)
        changed_assets[path] = dict(original_sha256=sha(original), sha256=sha(data))
    pseudo.write_bytes(p)
    (out/'empty').mkdir(exist_ok=True)
    newsq = out/'rootfs.squashfs'
    epoch = struct.unpack_from('<I',sq.read_bytes(),8)[0]
    run('mksquashfs',out/'empty',newsq,'-pf',pseudo,'-noappend','-comp','lzo',
        '-b','131072','-Xcompression-level','9','-tailends','-mkfs-time',epoch,*rootargs,'-processors','1','-no-progress')
    # All inodes, including demo and the logo, keep name/type/mtime/mode/uid/gid (sizes/offsets shift).
    def inodes(image):
        text = subprocess.check_output(['unsquashfs','-pf','-',str(image)]).split(b'\n# START OF DATA')[0]
        return sorted(l.split()[:6] for l in text.splitlines() if l and not l.startswith(b'#'))
    check(inodes(newsq) == sorted([i for i in inodes(sq) if i not in removed]+added),
          'Repacked rootfs metadata differs from stock')
    blobs['recovery-update/rootfs.squashfs'] = newsq.read_bytes()
    # Stock image proves this size fits; do not enlarge beyond its padded size.
    check(len(blobs['recovery-update/rootfs.squashfs']) <= sq.stat().st_size, 'Repacked rootfs exceeds stock size')
    blobs['firmware_v20.info'] = (f'Shanling Q2\n{version}\n'+''.join(
        hashlib.md5(blobs[n]).hexdigest()+'  '+n+'\n' for n in [
            'recovery-update/xImage','recovery-update/rootfs.squashfs'])).encode()
    with tarfile.open(out/'update.tar','w',format=tarfile.GNU_FORMAT) as t:
        for m in meta:
            data = blobs.get(m.name)
            if data is not None: m.size=len(data)
            t.addfile(m,io.BytesIO(data) if data is not None else None)
    manifest = dict(input_zip_sha256=ZIP_SHA, stock_demo_sha256=DEMO_SHA, source_sha256=source,
        demo_sha256=sha(patched), patch_sha256=sha(payload), update_sha256=sha((out/'update.tar').read_bytes()),
        rootfs_sha256=sha(newsq.read_bytes()), kernel_sha256=sha(blobs['recovery-update/xImage']),
        **({'aic_notifier': aic_notifier} if aic_notifier else {}),
        patch_bytes=len(payload), ring_step_pixels=RING_STEP,
        audio_extension_calls=[dict(address=hex(a), original='0x0320f809', replacement=hex(jal(ps['ringnav_audio_extension']))) for a in AUDIO_EXTENSION_CALLS],
        version=version, dev=dev, update_repo=repo, peq=audio, bluealsa_sha256=sha(bluealsa), q2video_sha256=sha((out/'q2video').read_bytes()), q2boot_sha256=sha((out/'q2boot').read_bytes()), **({'q2remote_sha256': sha((out/'q2remote').read_bytes())} if dev else {}), compact_code=code_changes, changed_assets=changed_assets, logo_sha256=sha(logo_data),
        **({'rotator_clock': rotator} if rotator else {}),
        **({'pwm_clock': pwm} if pwm else {}),
        patch_symbols={n:hex(v) for n,v in ps.items() if n.startswith('stock_')},
        tools={t:run(t,'--version').splitlines()[0] for t in ['clang','ld.lld','llvm-objcopy']} |
              {'imagemagick': imagemagick('-version', data=b'').decode().splitlines()[0]})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ['update_sha256','patch_bytes','version']},indent=2))

if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('zip',type=pathlib.Path)
    ap.add_argument('--out',type=pathlib.Path,default=ROOT/'build')
    ap.add_argument('--logo',type=pathlib.Path,default=ROOT/'assets/boot-logo.jpg',
                    help='320x375 JPEG boot splash (default: assets/boot-logo.jpg)')
    ap.add_argument('--dev', action='store_true',
                    help='development build: tag from the next .build-number (V001i, V002i...); never a release input')
    ap.add_argument('--mcu-idle-sleep', action='store_true',
                    help='experimental: sleep parked MCU with explicit IRQ wake (requires --dev --pwm-idle-clock)')
    ap.add_argument('--flac-experiments', default='sync,lpc,output', metavar='sync,lpc,output',
                    help='FLAC fast paths (comma-separated subset; empty for stock FLAC)')
    ap.add_argument('--update-repo', default=REPO, metavar='OWNER/NAME',
                    help="GitHub repo whose latest release Network Update fetches (default: %(default)s), for a fork's test builds")
    ap.add_argument('--aic-idle-wait', action='store_true',
                    help='experimental: sleep the stopped PCM notifier until start or teardown (requires --dev)')
    ap.add_argument('--rotator-lazy-clock', action='store_true',
                    help='experimental: defer the rotator clock until its first conversion (requires --dev)')
    ap.add_argument('--pwm-idle-clock', action='store_true',
                    help='experimental: cooperative Linux/MCU PWM clock gating (requires --dev)')
    a=ap.parse_args()
    try:
        build(a.zip,a.out.resolve(),a.logo,a.dev,a.update_repo,a.aic_idle_wait,a.rotator_lazy_clock,a.pwm_idle_clock,a.mcu_idle_sleep,a.flac_experiments)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))
