"""Smart Wi-Fi's radio waits, Internet Radio's directory, Boot to and System settings' rows."""
from q2emu import *

# A radio that can't come back (stock's own gate shut: the first scan not taken) still runs the press
# SMART_CONNECT_MS on, unheld, so it fails as without Smart Wi-Fi instead of waiting again.
m=SmartRadio(); m.config['TOKEN']='t'; m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
m.byte(syms['wifi_initstatus'],2); m.windows('localmusic_page')
assert m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0)==0 and m.toasts[-1][3]==SW_WAIT and m.flag()==0
m.advance(SW_CONNECT-500); assert m.toasts[-1][3]==SW_WAIT
m.advance(500); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.timers and not m.threads
m.advance(SW_CONNECT); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.timers; passed()
# Spotify: Streaming's page wakes the radio; its row then waits for the link before starting librespot
# (which never retries a Connect login or mDNS that failed) and opening the browser. A running
# librespot keeps the radio on.

m=SmartBrowse(); m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
button=m.add_on_row(); assert m.flag()==1  # stream_page_init woke it
m.windows('stream_page'); m.click(button)
assert m.toasts[-1][3]==SW_WAIT and not m.systems and m.top!=None and m.nodes[m.top]['name']!='spotbrowse_page'
m.advance(500); assert not m.systems
m.link=1; m.advance(500); assert m.systems==['/bin/sh /mnt/mmc/.spotify/run'] and m.nodes[m.top]['name']=='spotbrowse_page'
m.link=0; m.windows('home_page'); m.poll(SW_IDLE); m.poll(SW_IDLE); assert not m.switch() and m.flag()==1; passed()
# Without a link SMART_CONNECT_MS on, it starts anyway, as Spotify's row would.
m=SmartBrowse(); m.poll(1); m.poll(SW_IDLE); button=m.add_on_row(); m.windows('stream_page'); m.click(button)
m.advance(SW_CONNECT-500); assert not m.systems
m.advance(500); assert m.systems==['/bin/sh /mnt/mmc/.spotify/run']; passed()
# Internet Radio: a directory list waits for the link ("Connecting to Wi-Fi…"), then fetches; a station
# started meanwhile waits for it too before q2video runs, the local track stopped at the press.
m=SmartRadio(); m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
page=radio_open(m); assert m.flag()==1
m.row(2); assert m.labels()[-1]=='Connecting to Wi-Fi…' and not m.threads
m.advance(250); assert m.labels()[-1]=='Connecting to Wi-Fi…' and not m.threads
m.link=1; m.advance(250); assert m.labels()[-1]=='Loading…' and len(m.threads)==1; passed()
m=SmartRadio(); m.poll(1); m.poll(SW_IDLE); page=radio_open(m); m.row(1); m.forked=4343
m.row(1); np=m.top; assert m.nodes[np]['name']=='radionp_page' and 'fork' not in [c[0] for c in m.calls]
assert 'player_stop' in [c[0] for c in m.calls]  # the local track stops now, not once the station starts
m.advance(500); assert m.labels(np)[4]=='Connecting…'
m.poll(); assert 'fork' not in [c[0] for c in m.calls]
m.link=1; m.poll(); assert [c[0] for c in m.calls].count('fork')==1; passed()

# Boot logo: a card mount (boot, USB storage's end) starts a thread that keeps the card's logo as the
# splash's /mnt/data copy; the poll toasts its failure. The thread is run here once the poll starts it.

LOGO_BAD,LOGO_FAIL='Boot logo must be a 320x375 JPEG','Could not save boot logo'
LOGO_SEEN='/mnt/data/boot-logo.bad'  # the refused card file's size and mtime: told once

m=LogoMachine({LOGO_SRC:jpg(320,375)})
assert m.sync()==1 and m.files[LOGO_DST]==jpg(320,375) and m.renames==1 and LOGO_DST+'.tmp' not in m.files and not m.toasts; passed()
assert m.sync()==0 and m.renames==1; passed()  # still mounted: no edge, no sync
assert m.sync(False)==0 and m.sync()==1 and m.renames==1 and not m.toasts; passed()  # remounted, the same bytes: no write
m.files[LOGO_SRC]=jpg(320,375,pad=1); m.sync(False); m.sync(); assert m.files[LOGO_DST]==jpg(320,375,pad=1) and m.renames==2; passed()
del m.files[LOGO_SRC]; m.sync(False); m.sync(); assert LOGO_DST not in m.files and not m.toasts; passed()  # gone: the stock logo
m=LogoMachine(); assert m.sync()==1 and LOGO_DST not in m.files and not m.toasts and not m.execs; passed()
for bad in (jpg(375,375),jpg(320,375,n=1),jpg(320,375,sof=0xc2),jpg(320,375,bits=12),jpg(320,375,pad=512*1024),jpg(0,375),
            b'\xff\xd8\xff\xda\x00\x02',jpg(320,375)[:12],b'not a jpeg',b''):
    m=LogoMachine({LOGO_SRC:bad,LOGO_DST:b'old'}); m.sync()
    assert m.files[LOGO_DST]==b'old' and m.toasts==[LOGO_BAD] and not m.execs,bad[:16]
    assert not m.renames and LOGO_SEEN not in m.files,bad[:16]; passed()  # the emulator's stat fails: nothing recorded
    m.sync(False); m.sync(); assert m.toasts==[LOGO_BAD]*2,bad[:16]; passed()  # so a remount reports it again
m=LogoMachine({LOGO_SRC:jpg(375,320)}); m.child=True; m.sync()
assert m.exits==[127] and LOGO_DST not in m.files and m.execs==[['/usr/bin/ffmpeg','/usr/bin/ffmpeg','-nostdin','-v','quiet','-i',LOGO_SRC,'-vf','transpose=1','-pix_fmt',
                  'yuvj444p','-q:v','2','-f','mjpeg','-y',LOGO_ROT]],m.execs; passed()
m=LogoMachine({LOGO_SRC:jpg(375,320)}); m.rotated=jpg(320,375,pad=2); m.sync()
assert not m.execs and not m.exits
assert m.files[LOGO_DST]==m.rotated and LOGO_ROT not in m.files and not m.toasts; passed()
for rotated in (None,jpg(375,320)):  # ffmpeg failed, or wrote something else
    m=LogoMachine({LOGO_SRC:jpg(375,320),LOGO_DST:b'old'}); m.rotated=rotated; m.sync()
    assert m.files[LOGO_DST]==b'old' and m.toasts==[LOGO_BAD]; passed()
for failure in ('fopen','fwrite','fclose','rename'):
    m=LogoMachine({LOGO_SRC:jpg(320,375),LOGO_DST:b'old'}); m.failure=failure; m.sync()
    assert m.files[LOGO_DST]==b'old' and LOGO_DST+'.tmp' not in m.files and m.toasts==[LOGO_FAIL],failure; passed()
print('Boot logo: mount edges, validation, rotation, unchanged bytes and failed writes passed.')

# System boot row: regular-file gating, persisted choice and transactional failures.

for value,expected in ((None,'Rockbox'),(b'bad','Rockbox'),(b'q2pod','Q2 Pod'),(b'rockbox','Rockbox'),
                       (b'q2pod\n','Q2 Pod'),(b'q2pod\0bad','Rockbox')):
    m,button,label=boot_row(value)
    assert m.nodes[label]['text']=='Boot to: '+expected
    toggle_boot(m,button)
    next_value=b'rockbox' if expected=='Q2 Pod' else b'q2pod'
    assert m.files['/mnt/data/boot-target']==next_value
    assert m.nodes[label]['text']=='Boot to: '+('Rockbox' if next_value==b'rockbox' else 'Q2 Pod')
    restarted,_,l=boot_row(next_value); assert restarted.nodes[l]['text']==m.nodes[label]['text']
    passed()
for mode in (0,0o040755,0o020644): boot_row(mode=mode); passed()
for failure in ('fopen','fwrite','fflush','fsync','fclose','rename'):
    m,button,label=boot_row(b'rockbox'); m.failure=failure
    toggle_boot(m,button)
    assert m.files['/mnt/data/boot-target']==b'rockbox' and m.nodes[label]['text']=='Boot to: Rockbox'
    assert '/mnt/data/boot-target.tmp' not in m.files
    assert any(c[0]=='navigator_to_with_context' for c in m.calls)
    passed()
m,button,label=boot_row(); m.rockbox_mode=0; toggle_boot(m,button)
assert m.nodes[label]['text']=='Boot to: Rockbox' and '/mnt/data/boot-target' not in m.files; passed()
# Wheel centre reaches Boot to through the existing selection/dispatch path.
m,button,label=boot_row(); view=m.nodes[m.top]['children'][0]; view=m.nodes[view]['children'][0]
m.paint(view)
for _ in range(8): m.call()  # stock's two, our six, then Boot to
assert m.confirm()==11 and m.dispatched()[0][1]==button; passed()
# System settings: stock V1.36's nine rows, its Language (English only for now), Wheel Function, In-Vehicle, General and System hidden, which the wheel
# skips; Controls added, and the four rows stock moved to its System page, opening stock's pages, so the
# list reads as it did. Boot to is Power's now.
for mode in (0o100755,0):
    m=BootMachine({}); m.rockbox_mode=mode; m.restack=True
    m.handlers[int(manifest['patch_symbols']['stock_systemset_trampoline'],16)]='stock_systemset'
    entries=[m.entry(0) for _ in range(9)]
    view=m.node('scroll_view','scroll_view_sysset',entries.copy())
    m.top=m.node('window','sysset_page',[m.node('list_view','list_view_sysset',[view])])
    assert m.call(address=HOOKS['systemset_sysset_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    kids=m.nodes[view]['children']; assert len(kids)==14
    assert [kids.index(e) for e in entries]==[6,0,1,2,4,9,10,11,12]  # Language, hidden, after Date and Time; the hidden four before About
    assert [m.nodes[e]['visible'] for e in entries]==[0,1,1,1,1,0,0,0,0]
    controls,date,update,reset,about=(m.nodes[kids[i]]['children'][0] for i in (3,5,7,8,13))
    assert m.nodes[m.nodes[controls]['children'][1]]['text']=='Controls'
    f,ctx=m.handler(controls,O['EVT_CLICK']); assert f==payload_syms['display_open'] and ctx==3
    for b,key,page in ((date,'system_time','systemset/datetime_page'),(update,'system_update','systemset/fwupdate_page'),(reset,'system_reset','systemset/reset_page'),(about,'system_about','systemset/about_page')):
        f,ctx=m.handler(b,O['EVT_CLICK']); assert m.nodes[m.nodes[b]['children'][1]]['text']==key and m.nodes[m.nodes[b]['children'][0]]['image']==key
        m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.opened[-1]==(page,None),key
    passed()
print('Boot settings: conditional row/order, restart, card removal and failed atomic writes passed.')

from build import AUDIO_EXTENSION_CALLS
for site in AUDIO_EXTENSION_CALLS:
    for ext, accepted in (('.m4b',1),('.M4B',1),('.m4B',1),('.M4b',1),('.m4a',1),('.M4A',1),('.flac',1),('.mp3',1),('.m4b.tmp',0),('.pdf',0)):
        m=Machine(); m.mock('strstr@GLIBC_2.0')
        formats=m.string('.mp3 .flac .m4a .M4A'); suffix=m.string(ext)
        m.word(0x7000f000+(0xdd4 if site==0x5d4cac else 0xd64),formats)
        def extension_return(u,address,size,unused):
            u.reg_write(UC_MIPS_REG_PC,0x1000000)
        m.u.hook_add(UC_HOOK_CODE,extension_return,begin=site+8,end=site+8)
        assert bool(m.call(address=site,args=(formats,suffix,0,0),gap=0))==bool(accepted)
        passed()
    # Folder view lists videos in the card's Videos folder (#128); the library scan and other folders never do.
    for folder in (b'/mnt/mmc/vIdEoS/Films',b'/mnt/mmc/Videos',b'/mnt/mmc/Music',b'/mnt/mmc/VideosOld',b'/mnt/sda1/Videos'):
        for ext in ('.mp4','.MKV','.pdf'):
            m=Machine(); m.mock('strstr@GLIBC_2.0'); m.u.mem_write(syms['g_folder_path'],folder+b'\0')
            formats=m.string('.mp3 .flac'); suffix=m.string(ext)
            m.word(0x7000f000+(0xdd4 if site==0x5d4cac else 0xd64),formats)
            m.u.hook_add(UC_HOOK_CODE,extension_return,begin=site+8,end=site+8)
            listed=site==0x5d4cac and folder in (b'/mnt/mmc/vIdEoS/Films',b'/mnt/mmc/Videos') and ext!='.pdf'
            assert bool(m.call(address=site,args=(formats,suffix,0,0),gap=0))==listed,(site,folder,ext)
            passed()
