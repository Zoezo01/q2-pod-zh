#!/usr/bin/env python3
"""JPEG checks; optionally pass the stock ZIP to test packaging with a custom logo and quoted paths too."""
import io, sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first: test/build.py must import tools/build.py
from build import jal, j, ICONS, LABELS, relabel, ROOT, STOCK_EQ, TAG, dev_tag, jpeg_size, FONT, TAG_C, patch_m4b_cover, WEB, patch_web, DEFAULT_CONFIG, COVERMODE

logo = (ROOT/'assets/boot-logo.jpg').read_bytes()
assert jpeg_size(logo) == (320, 375)
# JPEG permits extra FF fill bytes before a marker.
assert jpeg_size(logo[:2] + b'\xff' + logo[2:]) == (320, 375)
frame = b'\xff\xd8\xff\xc0\x00\x11\x08\x01\x77\x01\x40'
# The stock display_logo reads three bytes per pixel without converting grayscale/CMYK.
# It also requires the documented 8-bit baseline format.
def sof(marker=0xc0, precision=8, components=3):
    return (b'\xff\xd8\xff' + bytes([marker]) + (8+3*components).to_bytes(2,'big') +
            bytes([precision]) + b'\x01\x77\x01\x40' + bytes([components]) +
            b''.join(bytes([i+1,0x11,0]) for i in range(components)))
assert jpeg_size(sof()) == (320,375)
for data in (b'', b'not a JPEG', frame, frame + bytes(8),
             b'\xff\xd8\xff', b'\xff\xd8\xff\xe0\x00',
             b'\xff\xd8\xff\xe0\x00\x01', b'\xff\xd8\xff\xd9' + logo[2:],
             sof(components=1), sof(components=4), sof(precision=12),
             sof(marker=0xc2), sof(marker=0xc3)):
    try:
        jpeg_size(data)
    except ValueError:
        continue
    raise AssertionError(f'Accepted malformed JPEG header: {data!r}')
print('JPEG header regression checks passed.')

# --dev tags: five characters, one per build number, never a release's.
tags = [dev_tag(n) for n in range(1, 36**3)]
assert tags[:2] == ['V001i', 'V002i'] and tags[35] == 'V010i' and tags[-1] == 'VZZZi'
assert len(set(tags)) == len(tags) and all(len(t) == 5 for t in tags) and TAG not in tags
assert dev_tag(36**3) == 'V001i'
print('Dev build tag checks passed.')

def boot_check():
    """S90play's dual boot (build.py BOOT_HOOK) under the host sh, with stand-in programs."""
    import subprocess, tempfile, pathlib
    from build import BOOT_HOOK, WHEEL_LOG_HOOK, ROCKBOX_FLAG, WIFI_OFF
    with tempfile.TemporaryDirectory(prefix='q2-boot-') as tmp:
        r = pathlib.Path(tmp)
        def host(hook):
            script = hook.decode().replace(' &\n', '\n')
            for old in ('/mnt/', '/tmp/mmc_add', ROCKBOX_FLAG, '/usr/bin/q2boot', '/release/bin/demo', '/proc/mounts', '/tmp/q2-demo.log'):
                script = script.replace(old, f'{r}{old}')
            script = script.replace('usleep 200000\n', ':\n').replace('usleep 500000', ':')
        # This script runs under the PC's own sh: the real killall -9 and ifconfig would take down the PC's network,
        # not a Q2's. So they never run here: a log line stands in, and the text must be gone before anything starts.
            assert script.count(WIFI_OFF) == 2  # before Rockbox, and the hand-over's late pass
            script = script.replace(WIFI_OFF, f'echo wifi >> {r}/order')
            assert 'usleep 300000' in script  # Home's hand-over pauses before Rockbox: logged, its place is the point
            script = script.replace('usleep 300000', f'echo settle >> {r}/order')
            assert 'usleep 2000000' in script and '\n            rockbox\n            kill $! 2>/dev/null\n' in script  # called off once Rockbox is gone
            script = script.replace('usleep 2000000', f'echo late >> {r}/order')  # runs in line here: & is stripped above
            for real in ('killall -9', 'ifconfig', 'wlan0', 'wpa_supplicant', 'udhcpc', 'wifi_connect'): assert real not in script, real
            return script
        script = host(BOOT_HOOK[1])
        for d in ('proc', 'mnt/data', 'mnt/mmc/.rockbox', 'tmp', 'usr/bin', 'release/bin'): (r/d).mkdir(parents=True)
        def exe(path, body): (r/path).write_text('#!/bin/sh\n' + body + '\n'); (r/path).chmod(0o755)
        # demo takes Home's Rockbox row (leaves the flag) as many times as the shortcuts file says
        flag, shortcuts = f'{r}{ROCKBOX_FLAG}', r/'shortcuts'
        exe('release/bin/demo', f'echo demo >> {r}/ran; echo demo >> {r}/order; n=$(cat {shortcuts}); '
            f'[ "$n" -gt 0 ] && echo $((n - 1)) > {shortcuts} && : > {flag}; '
            f's=$(head -n1 {r}/code); [ $(wc -l < {r}/code) -gt 1 ] && sed -i 1d {r}/code; exit $s')  # one status per run, the last repeats
        rb, target = r/'mnt/mmc/.rockbox/rockbox', r/'mnt/data/boot-target'
        def boot(held, card, code=81, shortcut=0, status=137):
            shortcuts.write_text(f'{shortcut}\n')
            (r/'code').write_text(''.join(f'{c}\n' for c in (status if isinstance(status, (list, tuple)) else [status])))
            exe('usr/bin/q2boot', 'exit ' + ('0' if held else '1'))
            if card: exe('mnt/mmc/.rockbox/rockbox', f'pwd >> {r}/ran; echo rockbox >> {r}/order; exit {code}')
            else: rb.unlink(missing_ok=True)
            (r/'ran').write_text(''); (r/'order').write_text('')
            subprocess.run(['sh', '-c', script], check=True)
            return (r/'ran').read_text().split(), target.exists() and target.read_text().strip()
        (r/'proc/mounts').write_text('')
        rockbox = [str(rb.parent), 'demo']
        assert boot(False, False) == (['demo'], False)          # no Rockbox on the card: Q2 Pod
        assert boot(False, True, 1) == (rockbox, False)         # Rockbox by default; a crash starts Q2 Pod
        assert (r/'mnt/mmc/.rockbox/rockbox.log').read_text() == 'exit 1\n'
        assert boot(False, True) == (rockbox, False)            # Boot stock OS (0x51): Q2 Pod starts, nothing saved
        assert boot(True, True, 1) == (['demo'], False)         # Play/Pause held: Q2 Pod, this session
        assert boot(False, True, 1) == (rockbox, False)         # the next power-on is Rockbox again
        target.write_text('stock\n')                            # a stale V8.4 choice is ignored
        assert boot(False, True, 1) == (rockbox, 'stock')
        assert boot(True, True, 1) == (['demo'], 'stock')
        for value in ('q2pod', 'rockbox', 'bad', '', 'q2pod\n', 'q2pod-extra'):
            target.write_text(value)
            expected = ['demo'] if value.rstrip('\n') == 'q2pod' else rockbox
            assert boot(False, True, 0) == (expected, value.strip())
            assert boot(False, True, 1) == (expected, value.strip())  # restart preserves choice
            assert boot(True, True) == (['demo'], value.strip())
            assert boot(False, False) == (['demo'], value.strip())
            assert boot(True, True, 81, 1) == (['demo', *rockbox], value.strip())
        target.unlink()
        # Home's Rockbox row: Rockbox, then Q2 Pod again, as often as it is taken; the flag is gone after
        assert boot(True, True, 81, 1) == (['demo', *rockbox], False)
        assert boot(False, True, 81, 2) == (rockbox * 3, False) and not pathlib.Path(flag).exists()
        assert boot(True, False, 81, 1) == (['demo', 'demo'], False)  # no Rockbox on the card: Q2 Pod again
        # Wi-Fi goes off before every start of Rockbox and at no other time; demo follows Rockbox whatever its status
        def order(*args): boot(*args); return (r/'order').read_text().split()
        session = ['wifi', 'rockbox', 'demo']
        for code in (0, 1, 81, 137, 255): assert order(False, True, code) == session
        handover = ['settle', 'late', 'wifi']
        assert order(True, True, 81, 1) == ['demo', *handover, *session]  # Home's Rockbox row waits for what demo launched
        assert order(False, True, 81, 2) == session + [*handover, *session] * 2  # power-on doesn't wait, the row twice does
        assert order(False, False) == ['demo']                  # no Rockbox on the card
        assert order(True, False, 81, 1) == ['demo', *handover, 'demo']  # nor when the row is taken
        assert order(True, True) == ['demo']                    # Play/Pause held
        target.write_text('q2pod'); assert order(False, True) == ['demo']; target.unlink()
        # an abnormal end of demo leaves its status on /mnt/data, which the next boot copies to a mounted card;
        # 0, 143 and the Rockbox hand-over leave nothing
        crash, copy, mounts = r/'mnt/data/q2-crash.log', r/'mnt/mmc/q2-crash.log', r/'proc/mounts'
        def logged(status, card, shortcut=0):
            for f in (crash, copy): f.unlink(missing_ok=True)
            mounts.write_text(f'tmpfs /tmp tmpfs rw 0 0\n/dev/mmcblk0p1 {r}/mnt/mmc vfat rw 0 0\n' if card else '')
            boot(True, False, shortcut=shortcut, status=status)
            return crash.exists() and crash.read_text(), copy.exists() and copy.read_text()
        out, cp = logged(137, True)
        assert 'demo exit 137' in out and '== dmesg' in out and '== meminfo' in out and '== ps' in out and cp is False  # no card write while dying
        assert not crash.with_name('q2-crash.log.tmp').exists()  # written whole, then renamed
        mounts.write_text(''); boot(True, False, status=0); assert not copy.exists() and crash.exists()  # no card: kept for a later boot
        mounts.write_text(f'/dev/mmcblk0p1 {r}/mnt/mmc vfat rw 0 0\n'); boot(True, False, status=0)
        assert copy.read_text() == out and not crash.exists()  # the next boot with a card moves it there
        copy.write_text('kept'); boot(True, False, status=0); assert copy.read_text() == 'kept'  # and never brings it back
        out, cp = logged(139, False)
        assert 'demo exit 139' in out and cp is False
        assert logged(0, True) == (False, False) and logged(143, True) == (False, False)
        assert logged([137, 0], True, 1) == (False, False)  # the Rockbox hand-over's kill, then a clean demo
        # /mnt/data/q2-state (navigation.c state_poll) still there at boot: the last session ended in a forced
        # restart, or the battery. It goes into q2-crash.log, a new one that says so or the crash's, and goes.
        state, line = r/'mnt/data/q2-state', 'screen:off power:suspending cable:out battery:57 play:paused out:jack\n'
        state.write_text(line); out, cp = logged(0, False)
        assert 'did not end' in out and '== last state' in out and line in out and not state.exists() and cp is False
        for f in (crash, copy): f.unlink(missing_ok=True)
        crash.write_text('== demo exit 137\n'); state.write_text(line); boot(True, False, status=0)
        assert crash.read_text().startswith('== demo exit 137\n== last state\n') and line in crash.read_text() and not state.exists()
        for f in (crash, copy): f.unlink(missing_ok=True)
        # the wheel's stale-press notes (navigation.c stale_note): a release build leaves them alone, and a --dev one
        # takes them to the card, appended, on their own
        assert 'wheel' not in script
        wheel, wcopy = r/'mnt/data/q2-wheel.log', r/'mnt/mmc/q2-wheel.log'
        script = host(BOOT_HOOK[1].replace(*WHEEL_LOG_HOOK))
        wheel.write_text('== wheel: one\n'); mounts.write_text(''); boot(True, False, status=0)
        assert wheel.exists() and not wcopy.exists() and not copy.exists()  # no card: kept
        mounts.write_text(f'/dev/mmcblk0p1 {r}/mnt/mmc vfat rw 0 0\n'); wcopy.write_text('== wheel: earlier\n'); boot(True, False, status=0)
        assert wcopy.read_text() == '== wheel: earlier\n== wheel: one\n' and not wheel.exists() and not copy.exists()
        wheel.write_text('== wheel: two\n'); crash.write_text('== demo exit 137\n'); boot(True, False, status=0)
        assert wcopy.read_text().endswith('== wheel: two\n') and copy.read_text() == '== demo exit 137\n' and not wheel.exists() and not crash.exists()
        wcopy.write_bytes(b'x' * 262144); wheel.write_text('== wheel: three\n'); boot(True, False, status=0)
        assert wcopy.stat().st_size == 262144 and not wheel.exists()  # the card's file stops growing
        wcopy.write_bytes(b'x' * 262143); wheel.write_text('== wheel: four\n'); boot(True, False, status=0)
        assert wcopy.read_bytes().endswith(b'== wheel: four\n') and not wheel.exists() and not (r/'mnt/data/q2-wheel.log.m').exists()
        for f in (crash, copy, wheel, wcopy): f.unlink(missing_ok=True)
    print('Dual boot: Rockbox by default, one-session Q2 Pod, stale choice ignored, card fallback, Home Rockbox row and Wi-Fi off before Rockbox passed.')
boot_check()

def logo_check():
    """S11logo_display_shell's splash choice (build.py LOGO_HOOK) under the host sh."""
    import subprocess, tempfile, pathlib
    from build import LOGO_HOOK, LOGO, LOGO_LIGHT, TIMEZONES
    with tempfile.TemporaryDirectory(prefix='q2-logo-') as tmp:
        tmp = pathlib.Path(tmp)
        user, config, dark, light = tmp/'boot-logo.jpg', tmp/'config.ini', tmp/'logo.jpg', tmp/'logo-light.jpg'
        script = (LOGO_HOOK[1].decode().replace('/mnt/data/boot-logo.jpg', str(user)).replace('/mnt/data/config.ini', str(config))
                  .replace('/'+LOGO_LIGHT, str(light)).replace('/'+LOGO, str(dark)).replace('display_logo', 'echo'))
        def shown(): return subprocess.run(['sh', '-c', script], check=True, capture_output=True, text=True).stdout.strip()
        assert shown() == str(dark)  # no card logo, no config: the built-in splash
        config.write_text('[IPOD]\nLIGHT=1\n')
        assert shown() == str(dark)  # Light Mode, but no light twin (custom --logo)
        light.write_bytes(b'')
        assert shown() == str(light)
        config.write_text('[IPOD]\nLIGHT=0\nBACKLIGHT=1\n')
        assert shown() == str(dark)
        user.write_bytes(b'')
        config.write_text('[IPOD]\nLIGHT=1\n')
        assert shown() == str(user)  # the card's logo wins in either mode
        user.unlink()
        config.write_text('[IPOD]\nLIGHT=2\n')  # Auto: light from 07:00 to 18:59 by the clock
        def at(hour): return subprocess.run(['sh', '-c', script.replace('$(TZ=$z date +%H)', hour)], check=True, capture_output=True, text=True).stdout.strip()
        assert at('06') == str(dark) and at('07') == str(light) and at('18') == str(light) and at('19') == str(dark)
        # Auto reads the hour in the saved zone: only the zone's own TZ gets a light hour
        for i, (_, zone) in enumerate(TIMEZONES):
            config.write_text(f'[SYSSET]\nTIMEZONE={i}\n[IPOD]\nLIGHT=2\n')
            fake = f'''date() {{ [ "$TZ" = '{zone}' ] && echo 12 || echo 22; }}; '''
            assert subprocess.run(['sh', '-c', fake + script], check=True, capture_output=True, text=True).stdout.strip() == str(light), zone
        # No saved zone, or one past the list: stock's default, as the player reads it (config_init)
        for text in ('[IPOD]\nLIGHT=2\n', f'[SYSSET]\nTIMEZONE={len(TIMEZONES)}\n[IPOD]\nLIGHT=2\n'):
            config.write_text(text)
            fake = f'''date() {{ [ "$TZ" = '{TIMEZONES[22][1]}' ] && echo 12 || echo 22; }}; '''
            assert subprocess.run(['sh', '-c', fake + script], check=True, capture_output=True, text=True).stdout.strip() == str(light)
    print('Boot logo: the saved card logo, else the light splash under Light Mode, else the built-in one, passed.')
logo_check()

def validate_assets(directory):
    import functools, json, re, struct, subprocess
    from build import sha, run, fileoff, symbols, BLUEALSA, AAC_44K1, GRADIENT_LEAF, WM_PAINT_LEAF, QUEUE_LEAF, HELPER, HELPER_LIKE, BOOT, BOOT_HOOK, REMOTE, REMOTE_HOOK, LOG_HOOK, WHEEL_LOG_HOOK, S90PLAY, S11LOGO, LOGO_HOOK, LOGO_LIGHT, BLUEALSA2, BT_KEEPALIVE, RTC_WRITE, WIFI_LPS, HOOKS, LOCK_CLOCK_12H, WATCHDOG, WATCHDOG_SLEEP, DROP_CACHES, STREAM_POLL_CALLS, DARK_POLLS, WHEEL_TS_READ, PDR
    from ipod import (ART_PLACEHOLDERS, sized_art, AUDIT, TOOLBAR_SWITCHES, BOTTOM, CONFIRM, VOLUME, BIGCOVER, BC_ART, BC_TITLE_PAD, QUICK_SETTINGS, QS_TOP, QS_LABEL_GAP, QS_LABEL_H,
                      QS_LABEL_W, QS_ROW_GAP, QS_PITCH, QS_BAR, QS_TOUCH, QS_EDGE, QS_SUN, HOME_LABEL_END, HOME_LIST_W, HOME_TEXT_X, HOME_TOP, PITCH, ARTIST_PAGE, HOME_PAGE, HOME_ROW, HOME_ROWS, NAVBAR_ONLY, PLAYING_PAGE, SET_ROW, SET_ROWS, SET_TOP, UI_ASSETS,
                      NP_BAR, NP_TOUCH, NP_MARGIN, NP_POS_X, NP_TOP, STATUS_BAR, STATUS_HIDDEN, STATUS_LEFT, STATUS_MARGIN, STATUS_RIGHT, CLOCK_MIN, CLOCK_TEXT, corner_inset, corner_x,
                      STATUS_ICONS, status_icon, QUIET_ICONS, quiet_icon, SET_ICON, SET_STOCK_ICON, SETTINGS_ICONS, SETTINGS_SLIDERS, decode, slider_plain, imagemagick, inc, png_header, settings_icon, walk,
                      patch_asset, patch_code, patch_style, style_props, SLIDE, SET0, status_metrics, CLASSIC_LIGHT_FONT_SRC)
    manifest = json.loads((directory/'manifest.json').read_text())
    stock = (directory/'stock-demo').read_bytes()
    demo = (directory/'demo').read_bytes()
    from build import patch_timezones, TIMEZONES, CJK, english_ui
    zones = bytearray(stock)
    patch_timezones(zones)
    start = fileoff(stock, 0x76b5d0)
    labels = [zones[start+i*32:start+(i+1)*32].split(b'\0')[0] for i in range(1+len(TIMEZONES))]
    assert labels == [b'timedate_auto'] + [label.encode() for label, _ in TIMEZONES]
    assert demo[start:start+28*64] == zones[start:start+28*64]
    for address in (0x4bce34, 0x4bd090, 0x4bd4d0, 0x4fa5dc):
        off = fileoff(stock, address)
        assert demo[off:off+4] == zones[off:off+4]
    assert manifest['version'].encode()+b'\0' in demo
    # .pdr (build.py PDR), past every LOAD segment, ships zeroed.
    assert demo[PDR[0]:PDR[0]+PDR[1]] == bytes(PDR[1]) and any(stock[PDR[0]:PDR[0]+PDR[1]])
    # The RTC is written in UTC: the one hwclock -w literal gains -u in its own padding.
    assert demo.index(RTC_WRITE[1]) == stock.index(RTC_WRITE[0])
    # Wi-Fi keeps its power save: network_checkstatus's lps=0 becomes lps=2, the one byte at file
    # offset 0x37133d, and networkGetWifiSignal jumps to the payload, which reads rx_signal instead.
    assert demo.index(WIFI_LPS[1]) == stock.index(WIFI_LPS[0]) == 0x37133d - 25 and WIFI_LPS[0] not in demo
    assert [i for i in range(0x371300, 0x371360) if demo[i] != stock[i]] == [0x37133d] and demo[0x37133d:0x37133e] == b'2'
    off = fileoff(stock, HOOKS['networkGetWifiSignal'][0])
    assert demo[off:off+8] == j(symbols(directory/'patch.elf')['ringnav_wifi_signal']).to_bytes(4, 'little') + bytes(4)
    # The lock window's 12-hour clock reads hour:minutes (H:mm), in stock HH:TT's slot after its 24-hour hh:mm.
    assert demo.index(LOCK_CLOCK_12H[1]) == stock.index(LOCK_CLOCK_12H[0]) and LOCK_CLOCK_12H[0] not in demo
    # The slide hint names a stock animator and gives it a duration (a missing one means stock's 500 ms).
    assert re.fullmatch(r'htranslate\(duration=\d+\)', SLIDE) and b'\0htranslate\0' in stock
    # The animated lookup pages need synchronous unwind.
    for address, slot in ((0x4a6250, 0x468), (0x4ad458, 0x468), (0x526e70, 0x420)):
        off = fileoff(stock, address)
        want = struct.pack('<III', 0x8f99a8f8, 0x0320f809, 0x8fa40000 | slot)
        assert demo[off:off+12] == want
    # Every hook and leaf jumps to its payload function (build.py pins the leaves' words): the
    # leaves are window_manager_paint (Videos), style_get_gradient and main_loop_queue_event.
    for address, name in [*(h[:2] for h in HOOKS.values()), WM_PAINT_LEAF[1:3], GRADIENT_LEAF[1:3], QUEUE_LEAF[1:3]]:
        off = fileoff(stock, address)
        assert demo[off:off+8] == j(symbols(directory/'patch.elf')[name]).to_bytes(4, 'little') + bytes(4)
    from build import AUDIO_EXTENSION_CALLS
    for entry, address in zip(manifest['audio_extension_calls'], AUDIO_EXTENSION_CALLS, strict=True):
        off = fileoff(stock, address)
        assert entry['address'] == hex(address)
        assert struct.unpack_from('<I', stock, off)[0] == int(entry['original'], 16)
        assert struct.unpack_from('<I', demo, off)[0] == int(entry['replacement'], 16)
        assert stock[off-8:off-4] == struct.pack('<I', 0x8f99f950)
    changed = manifest['changed_assets']
    xx = 'release/assets/default/raw/images/xx/'
    def read(image, rel):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory/image), rel])
    # Every stock UI asset with Chinese text is given in English (english_ui), the edited ones after their edit.
    ui = 'release/assets/default/raw/ui/'
    chinese = {l.removeprefix('squashfs-root/'+ui) for l in run('unsquashfs', '-l', directory/'stock.squashfs').splitlines()
               if l.startswith('squashfs-root/'+ui) and l.endswith('.bin') and l.removeprefix('squashfs-root/') not in STOCK_EQ}
    chinese = {rel for rel in chinese if CJK.search(read('stock.squashfs', ui+rel).decode('utf-8', 'replace'))}
    assert chinese and {'testmode/test_key.bin', 'kb_default_t9.bin', 'dialog/search_dialog.bin'} <= chinese
    patched_ui = set(UI_ASSETS)
    assert set(changed) == {ui+p for p in patched_ui | chinese} | {
        'release/assets/default/raw/styles/'+p for p in AUDIT['styles']} | {
        xx+n for n in SETTINGS_ICONS | QUIET_ICONS | STATUS_ICONS | ART_PLACEHOLDERS} | {'release/assets/default/raw/strings/en_US.bin', FONT, TAG_C, *WEB}
    master = (ROOT/'assets/icons/default_album_home.png').read_bytes()
    for name, size in ART_PLACEHOLDERS.items():
        old, new = read('stock.squashfs', xx+name), read('rootfs.squashfs', xx+name)
        assert png_header(new)[:2] == (size if isinstance(size, tuple) else (size, size)), name
        assert new == sized_art(master, size), name
        assert changed[xx+name] == dict(original_sha256=sha(old), sha256=sha(new))
        if not isinstance(size, tuple):  # transparent corners, not black, over a selected row
            assert imagemagick('png:-', '-format', '%[fx:p{0,0}.a]', 'info:', data=new) == b'0', name
    games = read('rootfs.squashfs', xx+'local_games.png')
    assert games == (ROOT/'assets/icons/local_games.png').read_bytes()
    assert png_header(games) == (52, 52, 8, 6)
    assert imagemagick('png:-', '-format', '%[fx:p{0,0}.a]', 'info:', data=games) == b'0'
    flow = read('rootfs.squashfs', xx+'default_album_flow.png')
    assert flow == sized_art(master, 160) and png_header(flow) == (160, 160, 8, 6)
    assert read('rootfs.squashfs', xx+'default_album_home.png') == master
    # Home: Carousel's cards in their own colours, stock's untouched, Coverflow's and the Shortcut's added.
    for name in ICONS:
        assert read('rootfs.squashfs', xx+name) == (ROOT/'assets/icons'/name).read_bytes(), name
    tag_old, tag_new = read('stock.squashfs', TAG_C), read('rootfs.squashfs', TAG_C)
    assert tag_new == patch_m4b_cover(tag_old)
    assert changed[TAG_C] == dict(original_sha256=sha(tag_old), sha256=sha(tag_new))
    for path in WEB:  # Wi-Fi transfer's page: English, folder uploads
        old, new = read('stock.squashfs', path), read('rootfs.squashfs', path)
        assert new == patch_web(path, old)
        assert changed[path] == dict(original_sha256=sha(old), sha256=sha(new))
    from build import segments
    tag_segment = segments(tag_new)[-1][1]
    assert tag_segment[0] == 1 and tag_segment[2] == 0x30000 and tag_segment[4:7] == (48, 48, 5)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.ttf') as font:
        font.write(read('stock.squashfs','release/assets/default/raw/fonts/default.ttf')); font.flush()
        for text,size,room in [('Q2',16,28),('12:59 PM',16,CLOCK_TEXT),('100%',inc('BATT_PCT_PX'),inc('BATT_PCT_W'))]:
            rendered=imagemagick('-font',font.name,'-pointsize',str(size),'label:'+text,'png:-',data=b'')
            assert png_header(rendered)[0]-1<=room,(text,size,room,png_header(rendered))
    # Now Playing's queue reads "Queue", Idle "Power" and standby/shutdown "Standby" (LABELS); the table's later offsets move with them and nothing else changes.
    strings = 'release/assets/default/raw/strings/en_US.bin'
    old, new = read('stock.squashfs', strings), read('rootfs.squashfs', strings)
    assert new == relabel(old)
    n = 2 * struct.unpack_from('<H', new, 2)[0]
    table = dict(zip(*[iter(new[o:new.index(b'\0', o)] for o in struct.unpack_from(f'<{n}I', new, 4))] * 2))
    assert [table[k] for k, _, _ in LABELS] == [v for _, _, v in LABELS] and table[b'powerm_sleep'] == b'Sleep Timer'
    # Settings icons: the audited 52px artwork, packaged as SET_ICON RGBA with the same transparency
    # and, on a plain background, the same average colour. Minimal greys them
    # as they load (navigation.c ringnav_image_add: every disc MINIMAL_CHEVRON, glyphs white).
    def mean(png, bg):
        cmd = ['png:-', '-background', bg, '-flatten', '-format', '%[fx:mean.r],%[fx:mean.g],%[fx:mean.b]', 'info:']
        return [float(v) for v in imagemagick(*cmd, data=png).split(b',')]
    for name, digest in SETTINGS_ICONS.items():
        old, new = read('stock.squashfs', xx+name), read('rootfs.squashfs', xx+name)
        assert sha(old) == digest and png_header(old) == (SET_STOCK_ICON, SET_STOCK_ICON, 8, 6), name
        assert changed[xx+name] == dict(original_sha256=digest, sha256=sha(new)) and new == settings_icon(name, old), name
        assert png_header(new) == (SET_ICON, SET_ICON, 8, 6), name
        try: settings_icon(name, old[:-1] + b'x')
        except ValueError: pass
        else: raise AssertionError(f'{name}: accepted a changed icon')
        for bg in ('#000000', '#6e6e6e'):  # the list, and Classic's Graphite selection bar
            assert max(abs(a - b) for a, b in zip(mean(old, bg), mean(new, bg))) < 0.02, (name, bg)
        px = bytearray(imagemagick('png:-', '-depth', '8', 'rgba:-', data=new))
        sat = lambda i: max(px[i:i+3]) - min(px[i:i+3])
        disc = max([1] + [sat(i) for i in range(0, len(px), 4)])
        assert disc > 100, name  # a coloured disc to grey
        for i in range(0, len(px), 4): px[i:i+3] = bytes([255 - (255 - 0x77)*min(sat(i), disc)//disc])*3
        runtime = imagemagick('-size', f'{SET_ICON}x{SET_ICON}', '-depth', '8', 'rgba:-', '-define', 'png:color-type=6', 'png:-', data=bytes(px))
        values = mean(runtime, '#000000')  # Minimal's black list: neutral grey
        assert max(values) - min(values) < 0.001, name
    # Videos' player: an ELF with the stock binaries' ABI flags (nan2008, o32, mips32r2).
    helper = read('rootfs.squashfs', HELPER)
    assert sha(helper) == manifest['q2video_sha256'] and helper[:4] == b'\x7fELF'
    assert helper[36:40] == read('stock.squashfs', HELPER_LIKE)[36:40]
    # Rockbox's dual boot: q2boot likewise, and S90play differs from stock only in starting demo.
    boot = read('rootfs.squashfs', BOOT)
    assert sha(boot) == manifest['q2boot_sha256'] and boot[:4] == b'\x7fELF' and boot[36:40] == helper[36:40]
    expected_start = read('stock.squashfs', S90PLAY).replace(*BOOT_HOOK)
    if manifest['dev']:
        remote = read('rootfs.squashfs', REMOTE)
        assert sha(remote) == manifest['q2remote_sha256'] and remote[:4] == b'\x7fELF'
        assert remote[36:40] == helper[36:40]
        expected_start = expected_start.replace(*REMOTE_HOOK).replace(*LOG_HOOK).replace(*WHEEL_LOG_HOOK)
        assert b'q2-wheel.log' in demo
    else:
        assert b'q2-wheel.log' not in demo and b'q2-wheel.log' not in expected_start  # a test build's diagnostic
        assert 'q2remote_sha256' not in manifest
        assert not (directory/'q2remote').exists()
        absent = subprocess.run(['unsquashfs', '-cat', str(directory/'rootfs.squashfs'), REMOTE],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert absent.returncode != 0, 'release rootfs contains q2remote'
    assert read('rootfs.squashfs', S90PLAY) == expected_start
    assert read('rootfs.squashfs', S11LOGO) == read('stock.squashfs', S11LOGO).replace(*LOGO_HOOK)  # the card's logo, if saved
    assert read('rootfs.squashfs', BLUEALSA2) == read('stock.squashfs', BLUEALSA2).replace(*BT_KEEPALIVE)  # keep-alive 5 s
    # bluealsa: AAC capability, the two DROP jumps, the silence sites, and one appended LOAD payload.
    from build import BT_DROP, BT_DROP_HANDLER, BT_DROP_PAYLOAD, BT_SILENCE_SITES, patch_bluealsa, segments
    old, new = read('stock.squashfs', BLUEALSA), read('rootfs.squashfs', BLUEALSA)
    header = segments(old)[-1][0]
    allowed = {AAC_44K1, *range(header, header+32)}
    for address, size in ((BT_DROP, 8), (BT_DROP_HANDLER, 8), *((a, 4) for a, *_ in BT_SILENCE_SITES)):
        off = fileoff(old, address)
        allowed.update(range(off, off+size))
    assert {i for i in range(len(old)) if old[i] != new[i]} <= allowed
    assert new == patch_bluealsa(old)
    assert segments(new)[-1][1][2] == BT_DROP_PAYLOAD
    assert new[AAC_44K1] == 0 and manifest['bluealsa_sha256'] == sha(new)
    from bluealsa import check as check_bluetooth_drop
    check_bluetooth_drop(directory)
    from build import SCROBBLE_CA
    assert read('rootfs.squashfs', SCROBBLE_CA) == (ROOT/'assets/scrobble-ca.pem').read_bytes()
    # Classic's bold ships, cut to what BOLD_RANGES lists
    from build import BOLD_TTF, CLASSIC_LIGHT_TTF, CLASSIC_LIGHT_NOTICE, FONT_NOTICES, bold_font, subset_font
    assert read('rootfs.squashfs', BOLD_TTF) == bold_font()[0]
    assert read('rootfs.squashfs', CLASSIC_LIGHT_TTF) == subset_font(ROOT/'assets/fonts/texgyreheros-bold.otf', 'Q2 Pod Classic Light', 'CLASSIC_LIGHT_RANGES', CLASSIC_LIGHT_NOTICE)[0]
    for name in FONT_NOTICES:
        assert read('rootfs.squashfs', 'release/assets/default/raw/fonts/' + name) == (ROOT/'assets/fonts'/name).read_bytes()
    # Centred Now Playing's page is English, as the iPod page is (english_ui)
    from build import CJK
    from ipod import PLAYING_STOCK
    stock_page = decode(read('rootfs.squashfs', 'release/assets/default/raw/ui/' + PLAYING_STOCK))
    assert not any(CJK.search(n[2].get('text', '')) for n in walk(stock_page))
    # its lyrics page follows the page's size, for full-screen lyrics (navigation.c np_full)
    named = {n[2].get('name'): n[2] for n in walk(stock_page)}
    assert named['view_lrc']['self_layout'] == 'default(x=0,y=0,w=100%,h=-20)'
    assert named['label_lyricmsg']['self_layout'] == 'default(x=0,y=73,w=100%,h=28)'
    # Curly quotes draw the straight glyphs and ♡ takes ♥'s box and advance; other tables keep their
    # stock bytes (head: its checksum).
    from build import QUOTES
    from fontTools.ttLib import TTFont
    old, new = (TTFont(io.BytesIO(read(i, FONT))) for i in ('stock.squashfs', 'rootfs.squashfs'))
    assert all(new.getBestCmap()[c] == old.getBestCmap()[q] for c, q in QUOTES.items())
    full, empty = (new.getBestCmap()[c] for c in (0x2665, 0x2661))
    g = new['glyf']
    assert new['hmtx'][empty] == new['hmtx'][full] == (old['hmtx'][full][0] + 32, g[full].xMin)
    assert abs(g[empty].xMin - g[full].xMin) <= 1 and abs(g[empty].xMax - g[full].xMax) <= 1
    assert abs(g[empty].yMin - g[full].yMin) <= 1 and abs(g[empty].yMax - g[full].yMax) <= 1
    assert all(new.reader[t] == old.reader[t] for t in old.reader.keys() if t not in ('cmap', 'head', 'glyf', 'loca', 'hmtx'))
    # The crash watchdog only sleeps longer; check_mem_thd returns on entry, so it never drops caches.
    old, new = read('stock.squashfs', WATCHDOG), read('rootfs.squashfs', WATCHDOG)
    assert new == old.replace(*WATCHDOG_SLEEP) and new != old
    for address, word, patched in DROP_CACHES:
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, off)[0] == word and struct.unpack_from('<I', demo, off)[0] == patched
    # The stream threads' idle polls (usleep, jalr $25) park in tidal.c instead, which is the only way out of them.
    for address, name in STREAM_POLL_CALLS:
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, off)[0] == 0x0320f809
        assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')[name])
    # Stock's status sync and cover pollers sleep through the dark usleeps (navigation.c).
    for address, name in DARK_POLLS:
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, off)[0] == 0x0320f809
        assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')[name])
    # The encoder thread's ts_read call goes through ringnav_ts_read (playback.c wheel_sample).
    off = fileoff(stock, WHEEL_TS_READ[0])
    assert struct.unpack_from('<I', stock, off)[0] == WHEEL_TS_READ[1]
    assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')['ringnav_ts_read'])
    # hciplayer's code (.text) differs from stock only in the VBR scan branch, now a nop, the 48 kHz EQ gates,
    # the ID3v2 walker calls, now id3_gain (MP3 ReplayGain), the MQA side chain's gate and probe length and
    # the pause and idle loops' futex waits and their wakes, gapless MP3 allocation/pre-filter/last-frame
    # hooks, the USB DAC buffer's two stores, now nops, the wired unmute calls, now the DAC release wait,
    # the DSF, DFF and ISO checks' stop stores, now jumps to probe_stop, and the preload's publish, its failure
    # stores, moved to its exit, and the cold path's clear of the same word; the forced EOF's store, now a hook;
    # the stores of the stop on the thread's other paths, the close handlers, the preload's open;
    # the FLAC seek's gate exit, no-sync scan and near-the-end steps.
    from peq import VBR_SCAN, EQ_RATE_GATES, ID3_CALLS, MQA_GATE, MQA_PROBE, PAUSE_WAIT, CMD_DONE, IDLE_WAIT, MP3_ALLOC, MP3_FILTER, MP3_CLOSE, MP3_READ, MP3_FILL, USB_BUFFER, DAC_RELEASE, PROBE_STOP, EXIT_HOLD, PRELOAD_PUBLISH, PRELOAD_FAILED, PRELOAD_FAIL_STORES, COLD_CLEAR, FORCED_EOF, STOP_SITES, STOP_NOPS, STOP_FILL, PRELOAD_CLOSE, FLAC_GATE, SEEK_NOSYNC, SEEK_WORDS, FLAC_META_EOF, MEDIA_INFO_NULL
    old, new = read('stock.squashfs', 'usr/bin/hciplayer'), read('rootfs.squashfs', 'usr/bin/hciplayer')
    off = fileoff(old, VBR_SCAN)
    words = (*EQ_RATE_GATES, *MQA_GATE, *MQA_PROBE, *USB_BUFFER, *PRELOAD_FAIL_STORES, *STOP_NOPS, *SEEK_WORDS, *FLAC_META_EOF, *MEDIA_INFO_NULL)
    gates = [fileoff(old, a) for a in (*(a for a, _, _ in (*words, *DAC_RELEASE, *PROBE_STOP, *STOP_SITES, STOP_FILL, *PRELOAD_CLOSE, FORCED_EOF, EXIT_HOLD, PRELOAD_PUBLISH, PRELOAD_FAILED, COLD_CLEAR, FLAC_GATE, SEEK_NOSYNC)), *ID3_CALLS, MP3_ALLOC, MP3_FILTER, MP3_READ,
                                       IDLE_WAIT[0], PAUSE_WAIT[0], CMD_DONE[0])]
    # The FLAC fast paths (tools/flac.py, on by default) patch hciplayer's sync calls and LPC/output pointers.
    from flac import patch_words as flac_words
    flac = flac_words('sync,lpc,output', symbols(directory/'peq'/'peq.elf'))
    gates += [fileoff(old, a) for a, _, _ in flac]
    # Lossy gapless: the window's room and the kept edit list.
    from peq import LAVF_PRIV, MOV_EDITS
    gates += [fileoff(old, a) for a, _, _ in (LAVF_PRIV, MOV_EDITS)]
    assert [struct.unpack_from('<I', new, fileoff(old, a))[0] for a, _, _ in (LAVF_PRIV, MOV_EDITS)] == [LAVF_PRIV[2], MOV_EDITS[2]]
    differ = [i for i in range(0x3e40, 0x485250) if old[i] != new[i]]
    assert set(differ) <= {*range(off, off+4), *(i for g in gates for i in range(g, g+4))} and not any(new[off:off+4])
    assert [struct.unpack_from('<I', new, fileoff(old, a))[0] for a, _, _ in words] == [p for _, _, p in words]
    assert [struct.unpack_from('<I', new, fileoff(old, a))[0] for a, _, _ in flac] == [p for _, _, p in flac]
    id3_gain = symbols(directory/'peq'/'peq.elf')['id3_gain']
    assert {struct.unpack_from('<I', new, fileoff(old, a))[0] for a in ID3_CALLS} == {jal(id3_gain)}
    ps = symbols(directory/'peq'/'peq.elf')
    for address, value in ((IDLE_WAIT[0], jal(ps['hci_idle_wait'])),
                           (PAUSE_WAIT[0], jal(ps['hci_pause_wait'])), (CMD_DONE[0], jal(ps['hci_command'])),
                           (MP3_ALLOC, jal(ps['mp3_private_alloc'])),
                           (MP3_FILTER, jal(ps['mp3_filter_entry'])), (MP3_CLOSE, ps['mp3_close']),
                           (MP3_FILL, ps['mp3_fill']),
                           *((a, jal(ps[name])) for a, _, name in DAC_RELEASE),
                           *((a, j(ps[name])) for a, _, name in (*PROBE_STOP, STOP_FILL, *PRELOAD_CLOSE, PRELOAD_PUBLISH, FLAC_GATE, SEEK_NOSYNC)),
                           *((a, jal(ps[name])) for a, _, name in (*STOP_SITES, FORCED_EOF, EXIT_HOLD, PRELOAD_FAILED, COLD_CLEAR))):
        assert struct.unpack_from('<I', new, fileoff(old, address))[0] == value
    # Include every excluded UI screen and saved-preference defaults in byte parity checks.
    paths = [l.removeprefix('squashfs-root/') for l in run('unsquashfs', '-l', directory/'stock.squashfs').splitlines()
             if ('/raw/ui/' in l or '/raw/styles/' in l) and l.endswith('.bin') or l.endswith('/config.ini')]
    names = set(run('unsquashfs', '-l', directory/'rootfs.squashfs').splitlines())
    # Light Mode's splash ships with the default logo only (build.py LOGO_HOOK)
    light = (ROOT/'assets/boot-logo-light.jpg').read_bytes()
    default = manifest['logo_sha256'] == sha((ROOT/'assets/boot-logo.jpg').read_bytes())
    assert ('squashfs-root/'+LOGO_LIGHT in names) == default and (not default or read('rootfs.squashfs', LOGO_LIGHT) == light)
    assert not {'squashfs-root/'+rel for rel in STOCK_EQ} & names, 'Stock EQ assets remain'
    # The Library's rows, stock's and list_row_core's alike, draw each local_* icon centred in one 52px
    # box (x 8): each disc's alpha-weighted centre must sit on its image's.
    for rel in sorted(n.removeprefix('squashfs-root/') for n in names if n.startswith('squashfs-root/'+xx+'local_')):
        png = read('rootfs.squashfs', rel)
        w, h = png_header(png)[:2]
        alpha = imagemagick('png:-', '-alpha', 'extract', '-depth', '8', 'gray:-', data=png)
        off = [sum(a*(i % w if axis else i // w) for i, a in enumerate(alpha))/sum(alpha) - ((w if axis else h)-1)/2 for axis in (1, 0)]
        assert max(map(abs, off)) < 0.5, (rel, off)
    # The carousel's images stay (and its arrows); the Coverflow and Shortcut cards' are added.
    assert {'squashfs-root/release/assets/default/raw/images/xx/'+n for n in ICONS + ['menu_music.png', 'menu_left.png', 'menu_right.png']} <= names
    # Colours the firmware will paint (inline style props, else the theme entry for the widget's style):
    # no screen may gain a light background or border, or dark text, that stock did not already paint.
    def palette(path):
        return {(w, s, p): v.hex() for w, s, state, p, _, v in style_props(read(path, 'release/assets/default/raw/styles/default.bin'))
                if state == 'normal' and p in ('bg_color', 'border_color', 'text_color')}
    themes = palette('stock.squashfs'), palette('rootfs.squashfs')
    def painted(root, theme):
        out = set()
        for kind, _, props, _ in walk(root):
            style = props.get('style', 'default')
            for p in ('bg_color', 'border_color', 'text_color'):
                v = props.get('style:normal:'+p, '').lstrip('#').lower() or theme.get((kind, style, p))
                if v: out.add((kind, props.get('name', ''), p, v if len(v) == 8 else v+'ff'))
        return out
    def luma(v): return sum(k*int(v[i:i+2], 16) for k, i in ((0.2126, 0), (0.7152, 2), (0.0722, 4))) / 255
    def jarring(c):
        kind, _, p, v = c
        if int(v[6:], 16) <= 0x40: return False
        return luma(v) < 0.25 if p == 'text_color' else luma(v) > 0.35 and kind != 'image'
    # iPod: text and icons must clear the glass's rounded corners (ipod.CORNER_R); backgrounds,
    # bars and full-bleed art may reach into them. Content is a label's font-high band, an image drawn centred at its
    # size, a slider's bar, else the widget; the window clips it, and row layouts place their children.
    # List rows scroll, so only fixed widgets are checked.
    fonts = {(w, s): int.from_bytes(v, 'little') for w, s, state, p, _, v in style_props(
        read('rootfs.squashfs', 'release/assets/default/raw/styles/default.bin')) if state == 'normal' and p == 'font_size'}
    @functools.cache
    def image_size(name):
        try: return struct.unpack('>II', read('rootfs.squashfs', f'release/assets/default/raw/images/xx/{name}.png')[16:24])
        except subprocess.CalledProcessError: return None
    def content(kind, props, x, y, w, h):
        if kind in ('label', 'hscroll_label'):
            size = int(props.get('style:normal:font_size') or fonts.get((kind, props.get('style', 'default')), 18))
            return x, y if props.get('style:normal:text_align_v') == 'top' else y + (h - size) // 2, w, size
        if kind == 'slider':
            bar = int(props.get('bar_size', h))
            return x, y + (h - bar) // 2, w, bar
        if kind == 'progress_bar': return x, y, w, h
        name = props.get('image') or props.get('style:normal:bg_image')
        if kind not in ('image', 'gif', 'image_animation') or not name: return None
        draw = props.get('draw_type') if 'image' in props else props.get('style:normal:bg_image_draw_type', 'center')
        if draw == 'fill': return None  # full-bleed art, like a background: the glass rounds its corners
        size = image_size(name) if draw == 'center' else None
        return (x + (w - size[0]) // 2, y + (h - size[1]) // 2, *size) if size else (x, y, w, h)
    def corners(short, n, x0, y0, window, slot=None):
        kind, (x, y, w, h), props, children = n
        if props.get('visible') == 'false' or kind in ('list_item', 'table_row'): return  # hidden, or rows that scroll
        x, y, w, h = slot or (x, y, w, h)
        if at := re.match(r'default\(x=(-?\d+),y=(-?\d+),', props.get('self_layout', '')):
            x, y = int(at[1]), int(at[2])
        x, y = x0 + x, y0 + y
        if box := content(kind, props, x, y, w, h):
            bx, by, bw, bh = box
            top, bottom = max(by, window[0]), min(by + bh, window[1])
            inset = max(corner_inset(top), corner_inset(bottom))
            assert bx + bw <= 0 or bx >= 375 or top >= bottom or inset <= bx and min(bx + bw, 375) <= 375 - inset, (
                short, props.get('name'), box, inset)
        slots, row = {}, re.fullmatch(r'default\(r=1,c=0,(a=right,)?xm=(\d+),s=(\d+)\)', props.get('children_layout', ''))
        if row:  # AWTK's row layout, every child shown
            gap, cx = int(row[3]), int(row[2])
            if row[1]: cx = w - cx - sum(c[1][2] for c in children) - gap * (len(children) - 1)
            for c in children:
                slots[id(c)] = (cx, 0, c[1][2], h)
                cx += c[1][2] + gap
        for c in children: corners(short, c, x, y, window, slots.get(id(c)))
    for rel in paths:
        if rel in STOCK_EQ: continue
        original, new = read('stock.squashfs', rel), read('rootfs.squashfs', rel)
        if '/raw/ui/' in rel and new[:4] == bytes.fromhex('12122211'):
            now = painted(decode(new), themes[1])
            bad = [c for c in now - painted(decode(original), themes[0]) if jarring(c)
                   and (c[2] != 'text_color' or c[0] in ('label', 'hscroll_label', 'button', 'edit', 'tab_button'))]
            # The rows are transparent, so a light list container stock hid behind them would show.
            bad += [c for c in now if c[2] == 'bg_color' and jarring(c)
                    and c[0] in ('list_view', 'list_item', 'scroll_view', 'table_view', 'table_client', 'view')]
            assert not bad, (rel, bad)
        if rel not in changed:
            assert new == (original.replace(*COVERMODE) if rel == DEFAULT_CONFIG else original), rel
            continue
        assert changed[rel] == dict(original_sha256=sha(original), sha256=sha(new))
        if '/raw/styles/' in rel:
            audit = AUDIT['styles'][rel.split('/raw/styles/')[1]]
            assert new == patch_style(original, audit) and len(new) == len(original)
            wrong = [*audit['edits'][0][:3], 'ffffffff', *audit['edits'][0][4:]]
            try:
                patch_style(original, dict(audit, edits=[wrong]))
                raise AssertionError('Accepted a wrong old style value')
            except ValueError:
                pass
            continue
        if '/raw/images/' in rel:
            name=rel.rsplit('/',1)[-1]
            if name in STATUS_ICONS:
                assert new == status_icon(name, original)
                assert png_header(new) == (14 if name.startswith('bar_wifi') else 42 if name.startswith('bar_bt') else 12, 16, 8, 6)
            elif name in QUIET_ICONS:
                assert sha(original)==QUIET_ICONS[name] and new==quiet_icon(name,original)
                assert png_header(new)==(28,28,8,6)
            else: assert name in SETTINGS_ICONS and new==settings_icon(name,original)
            continue
        short = rel.split('/raw/ui/')[1]
        edited = patch_asset(short, original) if short in patched_ui else original
        assert new == (english_ui(short, edited) if short in chinese else edited), short
        if short not in patched_ui:
            continue
        if short in chinese:  # the checks below are of the edits: compare with stock given in English
            original = english_ui(short, original)
        if short == 'dialog/addplaylist_dialog.bin':
            expected = decode(original)
            expected[3][0][3][0][2]['sensitive'] = 'true'
            assert decode(new) == expected
            continue
        root = decode(new)
        if short in SETTINGS_SLIDERS:
            before = {n[2].get('name'): n for n in walk(decode(original))}
            assert not [n for n in walk(root) if n[0] == 'image' and n[2].get('image')]  # no endpoint glyphs
            gone = [n for n in walk(root) if n[2].get('name') in before and before[n[2]['name']][2].get('image')]
            assert len(gone) == 2 and all(n[1][2:] == [0, 0] and n[1][:2] == before[n[2]['name']][1][:2] for n in gone)
            for n in walk(root):
                if n[0] == 'slider':
                    props = n[2]
                    old = before[props['name']]
                    assert n[1] == old[1] and props['bar_size'] == '8'
                    assert not [k for k in props if k.endswith((':bg_image', ':fg_image', ':icon', ':y_offset'))]
                    assert props['style'] == 's_ipod_progress'
                    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
                        assert props[f'style:{state}:bg_color'] == '#1c1c1c'
                        assert props[f'style:{state}:fg_color'] == '#6e6e6e'
                        assert props[f'style:{state}:round_radius'] == '4'
                        assert props[f'style:{state}:border_color'] == '#00000000'
                    assert {k: v for k, v in props.items() if not k.startswith('style') and k != 'bar_size'} == {
                        k: v for k, v in old[2].items() if not k.startswith('style') and k != 'bar_size'}
                    continue
                if n[2].get('name') in ('img_add', 'img_dec'):
                    assert n == before[n[2]['name']]  # ranges, touch and wheel controls stay stock
        if short == VOLUME:  # Hidden before the first paint, including transition snapshots.
            plain = decode(original); del plain[2]['highlight']
            for child in plain[3]: child[2]['visible'] = 'false'
            assert root == plain
            continue
        # Browsing, settings and the PEQ editor slide; Home, Now Playing, the bar and dialogs don't.
        slides = short not in (HOME_PAGE, STATUS_BAR, PLAYING_PAGE, QUICK_SETTINGS, CONFIRM, BIGCOVER)
        assert root[2].get('anim_hint') == (SLIDE if slides else None), short
        # screen coordinates: the bar, full-screen dialogs (quick settings slides down from -320), windows
        origin, window = {STATUS_BAR: (0, (0, SET0.bar)), QUICK_SETTINGS: (320, (0, 320)), CONFIRM: (0, (0, 320)),
                          BIGCOVER: (0, (0, 320))}.get(short, (SET0.bar, (SET0.bar, 320)))
        corners(short, root, 0, origin, window)
        if short == QUICK_SETTINGS:  # iPod only: the stock 4x2 grid, even label areas, a slim brightness track
            menu, light = root[3]
            icons = [n for n in menu[3] if n[0] == 'image']; labels = [n for n in menu[3] if n[0] == 'label']
            assert len(icons) == len(labels) == 8 and menu[1][1] >= QS_TOP
            assert sorted({n[1][0] for n in icons}) == sorted({n[1][0] for n in decode(original)[3][0][3] if n[0] == 'image'})
            for icon, label in zip(icons, labels):
                x, y, w, h = icon[1]
                # The same 16px two-line area, top-aligned, centred under its icon and clear of the next row.
                assert label[1] == [x + (w - QS_LABEL_W) // 2, y + h + QS_LABEL_GAP, QS_LABEL_W, QS_LABEL_H]
                assert label[2]['style'] == 's_label_white16c' and label[2]['style:normal:text_align_v'] == 'top'
                assert 2 * (16 + 2) <= QS_LABEL_H and label[2]['line_wrap'] == label[2]['word_wrap'] == 'true'
            assert {n[1][1] for n in icons} == {0, QS_PITCH}
            slider, dim, bright = light[3]
            assert light[1][1] == menu[1][1] + menu[1][3] + QS_ROW_GAP and light[1][1] + light[1][3] <= 320 - 16
            assert slider[2]['bar_size'] == str(QS_BAR) and slider[1][3] == QS_TOUCH >= 44 and slider[2]['slide_with_bar'] == 'true'
            assert not [k for k in slider[2] if k.endswith((':bg_image', ':fg_image', ':icon'))]
            assert slider[2]['style'].encode() not in read('rootfs.squashfs', 'release/assets/default/raw/styles/default.bin')
            assert dim[1][0] == QS_EDGE and bright[1][0] + bright[1][2] == 375 - QS_EDGE and dim[2]['image'] == 'drop_lighleft'
            assert dim[1][0] + QS_SUN < slider[1][0] and slider[1][0] + slider[1][2] < bright[1][0]
            continue
        if short == CONFIRM:  # iPod only: the stock pair as two full-width text rows under the prompt
            cancel, enter = root[3]
            assert [n[0] for n in root[3]] == ['button', 'button'] and [n[2]['name'] for n in root[3]] == ['img_cancel', 'img_enter']
            assert cancel[1] == [0, 208, 375, 48] and enter[1] == [0, 256, 375, 48] and enter[1][1] + enter[1][3] <= 320
            assert [n[2]['text'] for n in root[3]] == ['Cancel', 'OK']
            continue
        if short == BIGCOVER:  # iPod only: a centred square of art clear of the glass (corners above), the name on its foot
            art, shade, title = root[3]
            x, y = (375 - BC_ART) // 2, (320 - BC_ART) // 2
            assert root[2] == decode(original)[2] and art[2]['image'] == 'default_bigcover' and art[2]['draw_type'] == 'scale_auto'
            assert art[1] == [x, y, BC_ART, BC_ART] and art[2]['self_layout'] == f'default(x={x},y={y},w={BC_ART},h={BC_ART})'
            assert abs((375 - x - BC_ART) - x) <= 1 and 320 - y - BC_ART == y and BC_ART > 280  # centred, and still most of the height
            assert (375 - BC_ART - 2) // 2 < corner_x((320 - BC_ART - 2) // 2, BC_ART + 2)  # the largest that clears
            assert shade[1] == [x, y + BC_ART - 50, BC_ART, 50] and shade[2] == decode(original)[3][1][2]
            assert title[1] == [x + BC_TITLE_PAD, y + BC_ART - 50 + 11, BC_ART - 2 * BC_TITLE_PAD, 28] and title[2] == decode(original)[3][2][2]
            continue
        if short == HOME_PAGE:  # seven rows with the stock names, beside the art; bytes equal patch_asset above
            art, (lv, lg, _, [sv]), (sl, sg, sp, [ss]), *carousel = root[3]
            assert lv == 'list_view' and sv[0] == 'scroll_view' and art[2]['name'] == 'img_homeart'
            # The Settings list: stock's Playback and System setting rows (stock binds them; their
            # labels are literal), hidden in the list's place until Home's Settings row slides it in.
            assert sl == 'list_view' and sp['name'] == 'list_view_homeset' and sp['visible'] == 'false'
            assert sg == [0, HOME_TOP, HOME_LIST_W, 2*HOME_ROW] and [r[2]['name'] for r in ss[3]] == ['btn_playset', 'btn_sysset']
            assert [r[3][1][2]['name'] for r in ss[3]] == ['img_playset', 'img_sysset'] and [r[3][0][2]['text'] for r in ss[3]] == ['Playback', 'System']
            # The art fills the right panel below the status bar; the payload fits and crops it.
            assert art[0] == 'image' and art[1] == [0, 0, 375, BOTTOM] and art[2]['draw_type'] == 'fill' and HOME_LIST_W == 375
            assert all(v == '#00000000' for k,v in root[3][1][2].items() if k.startswith('style:'))
            assert [r[2]['name'] for r in sv[3]] == ['btn_'+n for n in HOME_ROWS] and HOME_ROWS[2] == 'coverflow'
            for name, (_, _, _, (label, image)) in zip(HOME_ROWS, sv[3]):
                assert label[2]['name'] == ('label_library' if name == 'localmusic' else 'label_'+name)
                assert image[2] == {'name': 'img_'+name, 'clickable': 'true'}
                # Labels end before the chevron's glyph; longer ones scroll (only_focus) or end in an ellipsis.
                assert label[1][0] + label[1][2] == image[1][2] - HOME_LABEL_END and label[1][2] >= 100
                assert label[2]['only_focus'] == 'true' and label[2]['ellipses'] == 'true'
            assert [r[3][0][2].get('text') for r in sv[3]] == [None, 'Library', 'Cover Flow', None, 'Rockbox', None, 'Settings']
            assert lg == [0, HOME_TOP, HOME_LIST_W, 7*HOME_ROW] and HOME_TOP + 7*HOME_ROW <= BOTTOM - HOME_TOP
            # Home: Carousel's: stock's carousel and arrows after the lists, hidden and taking no taps
            # until chosen (coverflow_home_layout); its cards stock's in their places, Library's label
            # literal, Coverflow's after it and the Shortcut's after Folder on their own images.
            stock_root = decode(original)
            assert [n[2]['name'] for n in carousel] == ['slide_menu', 'img_left', 'img_right']
            for n, old in zip(carousel, stock_root[3]):
                assert n[:2] == old[:2] and n[2] == {**old[2], 'visible': 'false', 'sensitive': 'false'}
            slide = carousel[0]
            assert [c[2]['name'] for c in slide[3]] == ['btn_'+n for n in ('playing', 'localmusic', 'coverflow', 'folder', 'rockbox', 'stream', 'playset', 'sysset')]
            assert [c[3][1][2].get('text') for c in slide[3]] == [stock_root[3][0][3][0][3][1][2]['text'], 'Library', 'Cover Flow', None, 'Rockbox', None, None, None]
            for card in slide[3]:
                image = card[3][0][2]
                assert {v for k, v in image.items() if k.endswith(':bg_image')} == {'menu_' + image['name'][4:].replace('localmusic', 'music'),
                                                                                  'menu_' + image['name'][4:].replace('localmusic', 'music') + 'down'}
            dot = inc('HOME_DOT_X'), inc('HOME_DOT')
            assert HOME_TEXT_X == sum(dot) + inc('HOME_DOT_GAP')
            # The dot and the labels clear the bottom row's corner.
            assert dot[0] >= corner_x(SET0.bar + HOME_TOP + 6*HOME_ROW + (HOME_ROW - dot[1])//2, dot[1])
            assert HOME_TEXT_X >= corner_x(SET0.bar + HOME_TOP + 6*HOME_ROW + 9, 20)
            # Split (coverflow_home_layout) gives the menu half the screen, the art the rest;
            # Split's glyph ends 13px before the art, as in the Home concept.
            assert inc('HOME_SPLIT_W') == 375 // 2
            assert inc('CHEVRON_W') - 30 - inc('HOME_CHEVRON_SHIFT') == 13
            assert inc('HOME_MODERN_TEXT_X') >= corner_x(SET0.bar + HOME_TOP + 6*HOME_ROW + 9, 20)
            # Modern's labels stop short of Split's glyph (x 20 to 30 of list_into) and Full's.
            for row, shift in ((inc('HOME_SPLIT_W'), inc('HOME_CHEVRON_SHIFT')), (inc('HOME_MODERN_ROW'), inc('HOME_FULL_CHEVRON_SHIFT'))):
                assert row - inc('HOME_MODERN_LABEL_END') + 5 <= row - inc('CHEVRON_W') + shift + 20
            continue
        if short == STATUS_BAR:  # iPod only: play state left, title between, four icons right
            left, right, *rest = root[3]
            assert all(root[2][f'style:{s}:border_color'] == '#00000000' for s in ('normal', 'disable', 'focused'))  # Light Mode: no paper line
            assert [n[2]['name'] for n in left[3]] == STATUS_LEFT and [n[2]['name'] for n in right[3]] == STATUS_RIGHT
            title = rest.pop()
            assert title[0] == 'hscroll_label' and title[2]['name'] == 'label_clock'
            x, _, w, _ = title[1]
            assert x + w/2 == 375/2 and w >= CLOCK_MIN  # centred on the screen, clear of both groups (corners below)
            assert inc('CLOCK_EDGE') >= corner_x((SET0.bar - 20) // 2, 20) and x >= inc('CLOCK_EDGE')
            assert title[2]['style:normal:font_size'] == '16'
            assert all(v[2]['children_layout'].endswith(f'xm={STATUS_MARGIN},s={inc("STATUS_GAP")})') for v in (left, right))
            idle = rest.pop()
            assert idle[1] == [STATUS_MARGIN, 0, 28, SET0.bar] and idle[2]['text'] == 'Q2' and idle[2]['visible'] == 'false'
            assert idle[2]['style:normal:font_size'] == '18' and STATUS_MARGIN >= corner_x(6, 18)
            assert STATUS_MARGIN + 28 < x
            assert [n[2]['name'] for n in rest] == STATUS_HIDDEN and all(n[1][0] + n[1][2] < 0 for n in rest)
            # The Battery setting's percentage and payload battery start hidden (navigation.c bar_sync).
            pct, slot = right[3][2:4]
            assert pct[1][2] == inc('BATT_PCT_W') and pct[2]['visible'] == 'false' and pct[2]['style:normal:text_align_h'] == 'right' and pct[2]['style:normal:font_size'] == str(inc('BATT_PCT_PX'))
            assert slot == ['view', [0, 0, inc('BATT_BODY_W') + inc('BATT_NUB_W') + inc('BATT_GAP') + inc('BATT_PCT_W'), 0], {'name': 'view_battery', 'visible': 'false'}, []]
            continue
        if short == PLAYING_PAGE:  # see the sketch in docs/ipod.md
            named = {n[2].get('name'): n for n in walk(root)}
            assert {n[2].get('name') for n in walk(decode(original))} < set(named)  # stock names kept
            assert [n[2].get('name') for n in root[3]] == ['view_buttons', 'label_playtime', 'label_playlen', 'label_ipod_remain',
                                                           'slide_view_view', 'slider_play', 'img_repeata', 'img_repeatb', 'image_wait']
            pos = named['label_ipod_pos'][1]
            assert pos[0] + pos[2] == named['img_more'][1][0] and named['img_return'][1][0] < 0
            assert not {'img_fav', 'img_playmode'} & {n[2].get('name') for n in named['view_buttons'][3]}
            assert named['img_more'][1][1:] == [0, 50, 40]
            album = named['view_album'][3]
            assert [n[2]['name'] for n in album] == ['img_cover', 'img_playstate', 'scrlabel_title', 'scrlabel_artist', 'label_ipod_album',
                                                     'img_playmode', 'img_fav']
            # 16px outer margins, 12px from the art to the text, the text column 165px wide; the title larger.
            # Order and favourite glyphs level with the art's foot (frames NP_ICON_PAD lower); favourite centred below More. The art's gap to
            # the dots below (y 182) matches its gap to "3 of 12"'s text above.
            assert [n[1] for n in album] == [[16, 2, 166, 166], [39, 25, 120, 120], [194, 18, 165, 28], [194, 52, 165, 22], [194, 80, 165, 22],
                                             [247, 133, 50, 50], [303, 133, 50, 50]]
            assert album[-1][1][0] == named['img_more'][1][0] and album[-1][1][2] == named['img_more'][1][2] == 50
            assert album[-1][1][0] - (album[-2][1][0] + album[-2][1][2]) == 6 and 80 + 22 < 118 + 13
            assert album[1][2]['opacity'] == '0'  # playback gestures retain their original target
            assert album[2][2]['style:normal:font_size'] == '22' and album[3][2]['style:normal:font_size'] == '18'
            for node, color in ((album[3], '#CCCCCC'), (album[4], '#BBBBBB')):
                assert {v for k, v in node[2].items() if k.endswith(':text_color')} == {color}
            assert pos[0] == NP_POS_X and album[0][1][0] == NP_MARGIN and 375 - 16 == album[2][1][0] + album[2][1][2]
            # Distinct bands: top row, art, page dots, bar (A-B markers y 250 to 260), then the times.
            dots = named['slide_indicator1'][1]
            assert NP_TOP + 10 + 166 < NP_TOP + dots[1] and NP_TOP + dots[1] + 10 < NP_BAR[1] - 1
            # The dots' own anchor, the slide_view's centre: stock's, with none, followed a swipe's offset.
            anchor = named['slide_indicator1'][2]
            assert anchor['anchor_x'] == f'{375 // 2 - dots[0]}px' and anchor['anchor_y'] == '0px' and 0 < dots[3] // 2
            assert named['slide_view'][1] == [0, 0, 375, 186]
            # The lyrics page (patch/lyrics.c): its rows span the page, a LYRIC_SLOT each after a blank one,
            # three to its 178px; the text, LYRIC_X in, clears the glass's corners, as the art does.
            lrc, slot = named['view_lrc'][3][0][2], inc('LYRIC_SLOT')
            assert named['view_lrc'][2]['self_layout'] == 'default(x=0,y=0,w=375,h=178)' and 3 * slot <= 178 < 4 * slot
            assert lrc['self_layout'] == 'default(x=0,y=0,w=100%,h=100%)' and lrc['children_layout'] == f'list_view(x=0,y={slot},s=0)'
            assert inc('LYRIC_X') == NP_MARGIN >= corner_x(SET0.bar + NP_TOP, 178)
            slider = named['slider_play']
            assert slider[1] == [NP_BAR[0], 233, NP_BAR[2], NP_TOUCH] and NP_TOUCH >= 44
            assert slider[2]['bar_size'] == '4' and slider[2]['slide_with_bar'] == 'true'
            assert slider[1][1] + (slider[1][3] - int(slider[2]['bar_size'])) // 2 == NP_BAR[1]
            assert NP_TOP + dots[1] + dots[3] < slider[1][1]  # page swipes and seeking have separate targets
            assert not [k for k in slider[2] if k.endswith((':bg_image', ':fg_image', ':icon'))]
            assert {v for k, v in slider[2].items() if k.endswith('_color')} == {'#1c1c1c', '#6e6e6e', '#00000000'}
            assert all(slider[2][f'style:{s}:border_color'] == '#00000000' for s in ('normal','pressed','over','disable','focused'))
            assert {v for k, v in slider[2].items() if k.endswith(':round_radius')} == {'2'}  # a slim line
            # No theme style of that name, so no thumb icon: stock fills exactly to the value.
            assert slider[2]['style'].encode() not in read('rootfs.squashfs', 'release/assets/default/raw/styles/default.bin')
            played, remain = (named[n][1] for n in ('label_playtime', 'label_ipod_remain'))
            assert played[1:] == remain[1:] == [265, 80, 16] and played[0] == 375 - remain[0] - 80 and 250 + 10 < 265
            assert named['label_playlen'][2]['visible'] == 'false'
            assert {v for n in ('label_playtime', 'label_ipod_remain') for k, v in named[n][2].items() if k.endswith(':text_color')} == {'#AAAAAA'}
            # Stock places the A-B markers at y 250 and x = 50 + t * 290 / length; iPod's follow its bar.
            assert [int.from_bytes(demo[fileoff(demo, a):fileoff(demo, a)+4], 'little') for a in (0x52a318, 0x52a330)] == [
                0x24020000 | NP_BAR[2], 0x24420000 | NP_BAR[0]]
            continue
        if short == ARTIST_PAGE:
            assert [n[2]['value'] for n in walk(root) if n[0] == 'pages'] == ['1'], 'Artist page must show Albums'
        if short in AUDIT['slide_only'] or short in TOOLBAR_SWITCHES:
            # Only the hint and settings slider styling change.
            root[2].pop('anim_hint')
            kept = decode(original)
            slider_plain(short, kept)
            assert root == kept, short
            continue
        nav = next(n for n in root[3] if n[2].get('name') == 'view_navbar')
        if short in NAVBAR_ONLY:  # content moves up 50; lists hold four whole SET_ROW rows from SET_TOP
            for old, node in zip(decode(original)[3], root[3]):
                if node is nav: continue
                if node[0] == 'list_view':
                    assert node[1] == [0, SET_TOP, 375, SET_ROWS * SET_ROW] and SET_TOP + SET_ROWS * SET_ROW <= BOTTOM, short
                    assert node[2] == {**old[2], 'default_item_height': str(SET_ROW)} and node[3] == old[3], short
                    continue
                assert node[1][1] == old[1][1] - 50 and node[1][3] == old[1][3] and node[2] == old[2], short
        if short == 'localmusic/playlist_page.bin':
            old_nav = next(n for n in decode(original)[3] if n[2].get('name') == 'view_navbar')
            gif, old = (next(n for n in p[3] if n[2].get('name') == 'gif_returnplay') for p in (nav, old_nav))
            assert gif[1] == [375 - corner_x(SET0.bar, old[1][3]) - old[1][2], *old[1][1:]]
            assert nav[2].get('visible') != 'false' and nav[2].get('enable') != 'false'
            gif[1][0] = old[1][0]
            assert nav == old_nav, 'only the right button keeps clear of the glass'
        else:
            assert nav[2]['visible'] == 'false' and nav[2]['enable'] == 'false'
        assert not [v for n in walk(root) if n[0] in ('button', 'list_item', 'table_row')
                    for k, v in n[2].items() if k.endswith(':bg_color') and v == '#000000'], 'Opaque inline row background'
        old_nodes, new_nodes = list(walk(decode(original))), list(walk(root))
        assert len(old_nodes) == len(new_nodes)
        for old, node in zip(old_nodes, new_nodes):
            for key, value in old[2].items():
                if short in SETTINGS_SLIDERS and node[0] == 'slider' and key == 'style':
                    value = 's_ipod_progress'
                if 'font' in key or key == 'style': assert node[2][key] == value
        if short in ('folder_page.bin', 'localmusic_page.bin', 'localmusic/localclass_page.bin', 'localmusic/playlist_page.bin'):
            surface = next(n for n in root[3] if n[0] in ('list_view', 'table_view'))
            top = 50 if short == 'localmusic/playlist_page.bin' else 0
            assert surface[1][1:] == [top, 375, BOTTOM-top], 'Lists must fill the client area'
            assert 4*PITCH <= BOTTOM, 'Four complete rows must fit'
        # Corrupt inputs must be rejected, never silently patched.
        try: patch_asset(short, original[:-1]+b'x')
        except ValueError: pass
        else: raise AssertionError('Accepted a changed asset')
    # Every geometry set's built list windows take the row height of the page's pitch
    # immediates (art pages take art_pitch), and its rows fit.
    from theme import builtins, geometry_sets, SCHEMA
    from ipod import PAGE_KIND as page_kind, page_rows
    built = set(run('unsquashfs', '-l', directory/'rootfs.squashfs').split())
    for g in geometry_sets(builtins()):
        assert g.body <= g.pitch and g.art_body <= g.art_pitch and g.song_body <= g.song_pitch, g.name
        assert g.art + 2*g.art_inset <= g.art_body, g.name
        assert g.text_art + 2*g.text_art_inset <= g.body or not g.text_art, g.name   # text rows' artwork fits, or none
        for y, h, body in ((g.title24_y, 24, g.body), (g.title_y, 20, g.body), (g.meta_y, 16, g.body), (g.count_y, 16, g.body),
                           (g.art_title24_y, 24, g.art_body), (g.art_meta_y, 16, g.art_body),
                           (g.song_title24_y, 24, g.song_body), (g.song_meta_y, 16, g.song_body)):
            assert 0 <= y and y + h <= body, (g.name, y, h, body)                    # every stock row label inside its body
        checked = set()
        for short, kind in page_kind.items():
            folder, _, base = short.rpartition('/')
            mine = ui + (folder and folder + '/') + f'{g.name}_{base}'
            new = decode(read('rootfs.squashfs', mine if 'squashfs-root/' + mine in built else ui + short))
            old = {n[2].get('name'): n[2] for n in walk(decode(read('stock.squashfs', ui + short)))}
            for n in walk(new):
                for prop in ('row_height', 'default_item_height'):
                    if old.get(n[2].get('name'), {}).get(prop) == '78':
                        assert n[2][prop] == str(page_rows(g, kind)[0]), (g.name, short, prop)
                        checked.add(short)
        assert checked == set(page_kind) - {'localmusic/album_page.bin'}, checked  # album_page's grid keeps 160
        assert g.set_top + g.set_rows*g.set_row <= 320 - g.bar, g.name
        # Home's rows are the set's and fit; a theme with Home's chevron ends its labels short of the
        # glyph (x 20 of list_into) in both widths, as Classic's below.
        mine = ui + f'{g.name}_{HOME_PAGE}'
        listed = {n[2].get('name'): n for n in walk(decode(read('rootfs.squashfs', mine if 'squashfs-root/' + mine in built else ui + HOME_PAGE)))}['list_view_home']
        assert listed[2]['item_height'] == str(g.home_row) and HOME_TOP + 7*g.home_row <= 320 - g.bar, g.name
        for t in builtins():
            if g in geometry_sets([t]) and t['chevron_home']:
                # a theme's own chevron in Split: the label ends at it
                assert t['home_label_end[0]'] >= (inc('HOME_SPLIT_W') - t['home_chevron_x'] if t['home_chevron_x']
                                                  else inc('CHEVRON_W') - inc('HOME_CHEVRON_SHIFT') - 15), t['id']
                assert t['home_label_end[1]'] >= inc('CHEVRON_W') - inc('HOME_FULL_CHEVRON_SHIFT') - 15, t['id']
        for rel in UI_ASSETS:
            folder, _, base = rel.rpartition('/')
            mine = ui + (folder and folder + '/') + f'{g.name}_{base}'
            if 'squashfs-root/' + mine not in built: continue
            for n in walk(decode(read('rootfs.squashfs', mine))):
                if n[0] != 'list_view' or n[2].get('default_item_height') != str(g.set_row): continue
                if rel in NAVBAR_ONLY:  # settings lists: the set's rows from its top
                    h = 320 - g.bar - g.set_top if g.list_end else g.set_rows*g.set_row
                    assert n[1] == [0, g.set_top, 375, h] and 'item_height' not in n[2], (g.name, rel)
                else:                   # any other list with that row height is not one (settings_list)
                    assert n[2].get('item_height') == str(g.set_row), (g.name, rel)
        if g.text_x:  # text from text_x clears the glass at the first row and at list_end's last; the lists take the window
            assert g.text_x >= corner_x(g.bar + 6, g.pitch - 12) and g.list_end % g.pitch == 0, g.name
            assert corner_x(g.bar + g.list_end - g.pitch + 6, g.pitch - 12) <= g.text_x < corner_x(g.bar + g.list_end + 6, g.pitch - 12), g.name
            ends = {}
            for rel in UI_ASSETS:  # every list box the transform resizes ends at the window's end
                folder, _, base = rel.rpartition('/')
                mine = ui + (folder and folder + '/') + f'{g.name}_{base}'
                if 'squashfs-root/' + mine not in built or rel in NAVBAR_ONLY: continue
                was = {n[2].get('name'): n[1] for n in decode(read('stock.squashfs', ui + rel))[3]}
                for n in decode(read('rootfs.squashfs', mine))[3]:
                    old = was.get(n[2].get('name'))
                    if n[0] in ('list_view', 'table_view') and old and old[1] in (50, 100) and n[1] != old:
                        ends[rel] = n[1][1] + n[1][3]
            assert ends and set(ends.values()) == {320 - g.bar}, (g.name, ends)
        if g.bar_px:  # Classic's bar: one cap centre, the title clear of the glass, the clock centred, no idle mark
            m = status_metrics(g, CLASSIC_LIGHT_FONT_SRC)
            bar = {n[2].get('name'): n for n in walk(decode(read('rootfs.squashfs', ui + f'{g.name}_system_bar.bin')))}
            assert bar['label_title'][1] == m['title'] and bar['label_clock'][1] == m['clock'] and 'label_idle' not in bar, g.name
            assert 2 * m['cap_top'] + m['cap'] in (g.bar - 3, g.bar - 2) and m['inset'] >= corner_inset(m['cap_top']), g.name
            assert 2 * m['clock'][0] + m['clock'][2] == 375, g.name
            # the battery's nub ends clear of the glass, at its top row, above the bar's two lines
            xm = g.bar_right
            assert xm >= corner_inset((g.bar - 2 - g.batt_h) // 2) and g.batt_h <= g.bar - 1 and bar['view_right'][2]['children_layout'].endswith(f'xm={xm},s={inc("STATUS_GAP")})'), g.name
            size = SCHEMA['bar.title.font_size'].field
            assert {t[size] for t in builtins() if g in geometry_sets([t])} == {g.bar_px}, g.name  # the theme's bar text is the set's
    payload_symbols = symbols(directory/'patch.elf')
    for address in [AUDIT['immediates'][0]['sites'][0][0], '0x522410', '0x523de0', '0x4b3130', AUDIT['row_layout_calls']['sites'][0][0],
                    hex(inc('LIST_VIEW_LAYOUT_SLOT'))]:
        damaged = bytearray(stock)
        damaged[fileoff(stock, int(address, 16))] ^= 1
        try: patch_code(damaged, payload_symbols)
        except ValueError: pass
        else: raise AssertionError(f'Accepted a changed instruction at {address}')
    print('Asset geometry, exclusion parity and mismatch rejection passed.')

if __name__ == '__main__':
    import argparse, json, pathlib, subprocess, tempfile
    from unittest.mock import patch
    from build import build, run, sha
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('zip',type=pathlib.Path,nargs='?')
    ap.add_argument('--build', type=pathlib.Path)
    args=ap.parse_args()
    if args.build: validate_assets(args.build)
    if args.zip:
        # Reproducibility and update.tar's MD5s are release.py package's; this covers the logo and quoting.
        with tempfile.TemporaryDirectory(prefix='q2-package-') as tmp:
            root=pathlib.Path(tmp)
            custom=root/"custom ' logo.jpg"
            custom.write_bytes(logo)
            def change_source(*command):
                if command[0]=='mksquashfs': custom.write_bytes(b'edited during compression')
                return run(*command)
            a=root/"build ' a"
            with patch('build.run',side_effect=change_source):
                build(args.zip,a,custom)
            validate_assets(a)
            manifest=json.loads((a/'manifest.json').read_text())
            assert manifest['logo_sha256']==sha(logo)
            for rel,expected in [('release/assets/default/raw/images/xx/logo.jpg',logo),
                                 ('release/bin/demo',(a/'demo').read_bytes())]:
                assert subprocess.check_output(['unsquashfs','-cat',str(a/'rootfs.squashfs'),rel])==expected
            print('Packaging, mutable logo and quoted paths checks passed.')
