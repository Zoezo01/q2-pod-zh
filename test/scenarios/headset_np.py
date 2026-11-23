"""Headsets and resume on connect, Streaming's Configuration, focused pages' keys and Now Playing's info page."""
from q2emu import *

# Ears are counted: the report's primary/secondary slots swap. One worn AirPod out pauses and back
# (now the other slot) resumes; a swap alone does nothing; a manual command still cancels resume.
for owner in (1,2,3):
    m=HeadsetMachine(owner); m.ears(1); m.ears(2); assert m.playing and not m.actions
    m.ears(0); assert not m.playing and m.actions==[(owner,'pause')]
    m.ears(2); assert m.playing and m.actions[-1]==(owner,'play')
    m.ears(1); m.ears(3); m.ears(2); assert not m.playing  # two worn: primary out, slots swapped
    m.ears(3); assert m.playing
    m.ears(1); m.fn('headset_manual'); m.ears(3); assert not m.playing
    passed()

for absolute,limit,want in ((127,40,40),(0,100,0),(64,100,50),(1,100,1),(126,100,99)):
    b=BtVolumeMachine(); b.poll(); b.byte(syms['g_maxvolume'],limit)
    b.absolute=absolute; b.poll()
    assert b.u.mem_read(syms['g_volume'],1)[0]==want
    assert b.absolute==(want*127+50)//100
    b.poll(); assert not b.writes()
    passed()
# A headset step that already reads as the Q2 volume stays: 2 is volume 2, never rewritten to 3.
b=BtVolumeMachine(); b.poll(); b.absolute=2; b.poll()
assert b.u.mem_read(syms['g_volume'],1)[0]==2 and not b.writes() and b.absolute==2
assert b.volume(2,2)==[(100,100)] and not b.writes(); b.poll(); assert not b.writes(); passed()
print('M4B recognition/resume and headset routing, ear masks, manual cancellation, malformed packets and volume limits passed.')

# Real helper APIs: explicit pause/resume is observable and repeatable, with shared gain changes.
def endpoint(m,name,value=0):
    return m.call(address=payload_syms[name],args=(value,0,0,0),gap=0,clear=False)

m=SpotMachine({BIN:bytearray()}); spot_open(m,stream_page(m)); m.state('playing'); m.poll()
for playing,command in ((0,'s'),(1,'P')):
    m.sent=[]; endpoint(m,'spot_set_playing',playing); endpoint(m,'spot_set_playing',playing)
    assert [x[1] for x in m.sent]==[command] and endpoint(m,'spot_status')==(2 if playing else 3)
ready_gain(m,1,32); m.now+=500; m.sent=[]; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v'+chr(100)
ready_gain(m,1,0); m.now+=500; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v\0'
ready_gain(m,0,32); m.now+=500; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v'+chr(32)
passed()
m=RadioMachine(); radio_open(m); m.row(1); m.row(0)
assert endpoint(m,'radio_status')==2
m.waited=m.forked
endpoint(m,'media_set_playing',0); endpoint(m,'media_set_playing',0); assert endpoint(m,'radio_status')==3
m.waited=0
endpoint(m,'media_set_playing',1); assert endpoint(m,'radio_status')==2
ready_gain(m,1,32); m.sent=[]; m.now+=500; endpoint(m,'radio_poll'); assert m.sent[-1][1]=='v'+chr(100)
ready_gain(m,0,32); m.sent=[]; m.now+=500; endpoint(m,'radio_poll'); assert m.sent[-1][1]=='v'+chr(32)
passed()
# Streaming's Configuration (docs/internals.md#streaming-configuration), after AirPlay: Tidal's and
# Spotify's cache caps cycle 512 MB to 8 GB from 2 GB, kept in their folders' .cache-size in
# librespot's form, which tidal.c's trim and the Spotify launcher read. Stop Streaming Services ends
# librespot and its loop, a station and a stock stream, and the keys are local music's again.
TCAP,SCAP='/mnt/mmc/.tidal/.cache-size','/mnt/mmc/.spotify/.cache-size'
m=RadioMachine({BIN:bytearray()}); spot_open(m,stream_page(m)); m.state('paused'); m.poll()
radio_open(m); m.row(1); m.row(0); assert endpoint(m,'radio_status')==2
m.top=m.node('window','home_page'); m.stream(); view=m.nodes[m.top]['children'][0]
conf=m.nodes[m.nodes[view]['children'][5]]['children'][0]; icon,label=m.nodes[conf]['children'][:2]
assert m.nodes[icon]['image']=='stream_config' and m.nodes[label]['text']=='Configuration'
m.click(conf); m.page=m.top; assert m.nodes[m.page]['name']=='streamconf_page'
assert m.labels()==['Configuration','Tidal Cache: 2 GB','Spotify Cache: 2 GB','Erase Spotify Data','Stop Streaming Services','TIDAL','Spotify','Tidal Downloads','Internet Radio','AirPlay']
m.row(0); assert m.files[TCAP]==b'4096M' and m.labels()[1]=='Tidal Cache: 4 GB' and endpoint(m,'stream_cache',0)==3
m.row(0); m.row(0); assert m.files[TCAP]==b'512M' and m.labels()[1]=='Tidal Cache: 512 MB'
m.row(1); assert m.files[SCAP]==b'4096M' and m.labels()[2]=='Spotify Cache: 4 GB' and endpoint(m,'stream_cache',1)==3
m.files[SCAP]=bytearray(b'9G'); assert endpoint(m,'stream_cache',1)==2  # not a choice: 2 GB
m.word(O['MCL_TYPE'],5); m.play=2; m.waited=m.forked
m.row(3); m.top=m.node('window','home_page')
assert 'killall -9 librespot aplay' in m.systems[-1] and 'player_stop' in [c[0] for c in m.calls]
assert [m.text(c[2]+8) for c in m.calls if c[0]=='navigator_to_with_context']==['Streaming stopped']
assert endpoint(m,'radio_status')==0 and endpoint(m,'spot_status')==0 and m.key(O['KEY_PLAY'])!=11; m.close(); passed()
# Erase Spotify Data, under Spotify Cache, as the App Store's Remove: a first press asks for a second;
# another row's press, or leaving the page, drops it. The second erases Spotify's login and cache (and
# .cache-size, so Spotify Cache reads 2 GB again) on the App Store's thread, Spotify stopped first.

m=ShelfMachine({SCAP:bytearray(b'4096M')}); conf_open(m)
assert m.labels()[2:5]==['Spotify Cache: 4 GB','Erase Spotify Data','Stop Streaming Services']
m.row(2); assert m.labels()[3]=='Press again to erase login and cache' and not m.threads
m.row(0); assert m.labels()[3]=='Erase Spotify Data'  # another row's press drops it
m.row(2); m.destroy(m.page); conf_open(m); assert m.labels()[3]=='Erase Spotify Data'  # so does leaving
m.row(2); m.row(2); assert m.labels()[3]=='Erasing…' and len(m.threads)==1 and KILL in m.systems[-1]
m.row(2); m.row(2); assert m.toasts[-1][3]=='Wait for the one in progress' and len(m.threads)==1
worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=50_000_000)==0
assert 'rm -rf /mnt/mmc/.spotify/cache /mnt/mmc/.spotify/.cache-size /mnt/mmc/.spotify/spot.log /mnt/mmc/.spotify/aplay.log /mnt/mmc/.spotify/load.log' in m.systems; del m.files[SCAP]; m.advance(500,clear=False)
assert m.toasts[-1][3]=="Spotify's login and cache erased" and KILL in m.systems[-1]
assert m.labels()[2:4]==['Spotify Cache: 2 GB','Erase Spotify Data']; passed()
# The wheel drives only allowlisted windows (patch/contexts.inc), so every runtime list page needs an
# entry; the two streaming Now Playing pages and a card app's QR code page (no list) handle their own keys.
pages=set(re.findall(r'page_open\("(\w+)"',''.join(p.read_text() for p in (ROOT/'patch').glob('*.c'))))
assert pages-set(re.findall(r'"(\w+)"',(ROOT/'patch/contexts.inc').read_text()))=={'radionp_page','spotify_page','qrcode_page'}; passed()
# Stock Tidal shares the native queue controls and session cancellation.
m=HeadsetMachine(); m.word(O['MCL_TYPE'],2); m.ears(3); m.ears(1); assert not m.playing
m.ears(3); assert m.playing; m.command(163); assert m.actions[-1]==(1,'local_next'); passed()
# A focused streaming page owns the keys: Spotify's while it runs, else the owner's; Tidal's are local's.
for owner,page,want in ((2,'spotbrowse_page',[(2,O['KEY_PLAY'])]),(1,'spotify_page',[(2,O['KEY_PLAY']),(1,'pause')]),
                        (3,'radionp_page',[(3,O['KEY_PLAY'])]),(2,'tidal_downloads',[(1,'pause')]),(2,'home_page',[(2,O['KEY_PLAY'])])):
    m=HeadsetMachine(owner); m.top=m.node('window',page,[])
    m.fn('media_key',O['KEY_PLAY']); assert m.actions==want,(page,m.actions); passed()
# Require Output (docs/internals.md): stock V1.36's switch lets nothing start with no output connected, and Q2 Pod's
# own starts ask stock's check_forbid_play_no_headset, as stock's do. Everywhere else this suite and test/fw answer for
# that function; from here to Resume on connect stock's own code answers. First the gate alone, on both builds: it
# refuses only with the switch on, outside test mode, and no output, which is a Bluetooth link with its codec up, a
# USB DAC (g_otg_status 2, not 1) or either jack plugged. Refusing, it opens stock's 2 s message when asked with 0, as
# Q2 Pod asks, and calls notifyPrompt when asked with 1.

NOTICE=['getFormatString',('dialog/msginfo_dialog',1,2000)]
for patched in (True,False):
    assert forbids(patched)==(1,NOTICE) and forbids(patched,1)==(1,['getFormatString','notifyPrompt'])
    for state in (dict(g_block_withoutphone_flag=0),dict(g_testmode_flag=1),dict(bt_linkstatus=1,bt_showcoding=1),
                  dict(g_otg_status=2),dict(g_po_status=1),dict(g_bal_status=1)):
        assert forbids(patched,**state)==(0,[]),state
    for state in (dict(bt_linkstatus=1),dict(bt_showcoding=1),dict(g_otg_status=1)):
        assert forbids(patched,**state)==(1,NOTICE),state
passed()
# Q2 Pod's two starts of its own, with stock's gate run: each asks with 0 and starts nothing it refuses. Play/Pause
# when not playing (ringnav_play_pause, the hook on stock's player_play_pause, whose body is the toggle here; a pause
# always goes through), the side buttons' and a headset's skip (media_key, playing or not). The toggle answers at +12,
# past the 3-word PIC prologue the hook's trampoline skips (build.py), so ringnav_play_pause and the real gate run.

for key,does in (('KEY_PLAY','play'),('KEY_FWD_BTN','local_next'),('KEY_BACK_BTN','local_prev')):
    assert hp_keys(key)==([],1) and hp_keys(key,g_po_status=1)==hp_keys(key,g_block_withoutphone_flag=0)==([(1,does)],0),key
assert hp_keys('KEY_PLAY',True)==([(1,'pause')],0) and hp_keys('KEY_FWD_BTN',True)==([],1); passed()
# Stock's own start runs through Q2 Pod's hook: Now Playing opened with a track to start, the way a list row's tap
# goes. Stock's playing_page_init, whole: refused, it shows the message, closes the window it was given and returns
# STOP before it starts or builds anything, and ringnav_playing binds nothing to a window that is gone. With an
# output the track starts and the labels bind, as they do opened with nothing to start (0xff), which asks nothing.

assert np_opens()==(11,['window_close'],False,1) and np_opens(0xff)==(0,[],True,0)
assert np_opens(g_po_status=1)==np_opens(g_block_withoutphone_flag=0)==(0,['player_start'],True,0); passed()
# Resume on connect (docs/internals.md#resume-on-connect), local music and Spotify. Off (default): a
# headset's PLAY within CONNECT_HOLD_MS (5 s) of the link starts nothing, a later one plays. On: it plays.
for owner in (1,2):
    for off in (0,1):
        m=HeadsetMachine(owner); m.playing=False
        if not off: m.config['RESUME']='1'
        for name in ('g_bluetoothflag','bt_linkstatus'): m.byte(syms[name],1)
        m.fn('headset_poll',gap=200); m.command(200)
        if not off: assert m.playing; passed(); continue
        m.command(164); m.command(207); assert not m.playing and not m.actions
        m.command(200,gap=5000); assert m.playing and m.actions==[(owner,'play')]; passed()
# Stock's restart once A2DP comes up (BT_CONNECT_START): on, player_save_start(0) as stock. Off (default): a
# playing queue goes on, a paused one comes back paused, a stopped one or a stream's turn starts nothing.
from build import BT_CONNECT_START
assert struct.unpack_from('<I',demo,fileoff(demo,BT_CONNECT_START[0]))[0]==jal(payload_syms['ringnav_bt_connect_start'])
for off,owner,saved,want in ((0,1,3,[0]),(0,2,0,[0]),(1,1,2,[0]),(1,1,3,[1]),(1,1,0,[]),(1,2,3,[])):
    m=HeadsetMachine(owner); m.saved=saved
    if not off: m.config['RESUME']='1'
    m.fn('ringnav_bt_connect_start')
    assert [c[1] for c in m.calls if c[0]=='player_save_start']==want; passed()
print('Real Spotify/radio play-state APIs, helper gain updates and stock Tidal routing passed.')

# Now Playing's info page: its one fill (NP_INFO_CALL) goes through ringnav_np_info after stock's, which
# writes "Label:value" and the bare folder: ": " after each label, units spaced, the folder labelled
# from player_location, en_US's first unknown ("Unknown Artist") plain "Unknown"; a second pass changes nothing.
from build import NP_INFO_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,NP_INFO_CALL))[0]==jal(payload_syms['ringnav_np_info'])

m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'
stock_lines={'label_album':'Album:Kid A','label_genre':'Genre:Unknown Artist','label_size':'File Size:35.92MB',
             'label_year':'Year:2000','label_encode':'Encoding:FLAC','label_bits':'Bit Depth:16bit','label_kbps':'Bitrate:950kbps',
             'label_sample':'Sample Rate:44.1kHz','label_location':'SD:/Music/Radiohead'}
lines={k:m.node('label',k,text=v) for k,v in stock_lines.items()}
win=m.node('window','playing_page',list(lines.values()))
for l in lines.values(): m.word(l+O['W_PARENT'],win)
want=['Album: Kid A','Genre:Unknown Artist','File Size: 35.92 MB','Year: 2000','Encoding: FLAC','Bit Depth: 16-bit',
      'Bitrate: 950 kbps','Sample Rate: 44.1 kHz','Location: SD:/Music/Radiohead']
for _ in range(2):
    m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
    assert [c[1] for c in m.calls if c[0]=='np_info']==[win] and [m.nodes[l]['text'] for l in lines.values()]==want, [m.nodes[l]['text'] for l in lines.values()]
assert next(n for n in m.nodes.values() if n.get('name')=='ringnav_genre_scroll')['text']=='Genre: Unknown'
passed()
# Classic's page has rows (#204's): beside each of stock's labels a <name>_value one (tools/ipod.py np_ipod); stock's
# keeps the name, the value goes beside it, both in the info role's face and size, the value in playing.info.value's
# grey for the half, the genre with them (no scroller); a second pass writes nothing.
CONFIG.clear(); CONFIG.update(THEME='1',LIGHT='0')
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'
lines={k:m.node('label',k,text=v) for k,v in stock_lines.items()}
values={k:m.node('hscroll_label',k+'_value') for k in stock_lines}
win=m.node('window','playing_page',[*lines.values(),*values.values()])
for l in (*lines.values(),*values.values()): m.word(l+O['W_PARENT'],win)
names=['Album','Genre','File Size','Year','Encoding','Bit Depth','Bitrate','Sample Rate','Location']
got=['Kid A','Unknown','35.92 MB','2000','FLAC','16-bit','950 kbps','44.1 kHz','SD:/Music/Radiohead']
for i in range(2):
    m.calls=[]; m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
    assert [m.nodes[l]['text'] for l in lines.values()]==names and [m.nodes[v]['text'] for v in values.values()]==got
    assert not i or not [c for c in m.calls if c[0]=='widget_set_text_utf8' and c[1] in values.values()]  # the same values: none written
    for k in stock_lines: m.nodes[lines[k]]['text']=stock_lines[k]  # stock's next fill
for w in (*lines.values(),*values.values()):
    assert (m.nodes[w]['style:normal:font_name'],m.nodes[w]['style:normal:font_size'])==('classic-light',17),m.nodes[w]
assert {m.nodes[v]['style:normal:text_color'] for v in values.values()}=={signed(color_t(0x8a8a8a))}
assert not any(n.get('name')=='ringnav_genre_scroll' for n in m.nodes.values()); passed()
CONFIG.clear()
# Genre shows in a private scroller, stock's label hidden as its source and left alone; its text is set once,
# so stock's next 250 ms fill resets nothing. Only a value wider than the label moves, on the album marquee's
# chain, and only in front, lit, on the info page.

m,win,sv,source,s,fill=genre_page('流行',100); fill()
assert m.nodes[source]['visible']==0 and m.nodes[source]['text']=='Genre:流行' and m.nodes[s]['text']=='Genre: 流行'
assert m.nodes[s]['type']=='hscroll_label' and m.nodes[s]['style']=='s_scrlabel_white16c' and m.get(s+O['W_X'])==75 and m.get(s+O['W_W'])==225
assert m.nodes[s]['hscroll_label_set_only_focus']==1
assert not m.timers and not [c for c in m.calls if c[0]=='widget_set_text_utf8' and c[1]==s]; passed()
# 79 px over (2 px at a step): MARQUEE_HOLD_MS, 40 steps to the end, a hold there, back to the start, a hold.
m,win,sv,source,s,fill=genre_page('Rock',300); pause,ms=O['MARQUEE_HOLD_MS'],50
def shift(): return m.nodes[s]['hscroll_label_set_xoffset']
fill(); assert len(m.timers)==1 and shift()==0; fill(); assert len(m.timers)==1
m.advance(pause-1); assert shift()==0; m.advance(1); assert shift()==2; m.advance(ms); assert shift()==4
m.advance(ms*37); assert shift()==78; m.advance(ms); assert shift()==79  # never past the end
m.advance(pause-1); assert shift()==79; m.advance(1); assert shift()==0
m.advance(pause-1); assert shift()==0; m.advance(1); assert shift()==2; passed()
# Dark, covered or off the page: the chain ends where it is, a fill doesn't start it, and one in front does.
for off_front,on_front in ((lambda:m.byte(syms['g_backlight_status'],0),lambda:m.byte(syms['g_backlight_status'],1)),
                           (lambda:setattr(m,'top',m.node('window','dialog')),lambda:setattr(m,'top',win)),
                           (lambda:m.nodes[sv].update(value=0),lambda:m.nodes[sv].update(value=2))):
    was=shift(); off_front(); m.advance(ms); assert not m.timers and shift()==was
    fill(); assert not m.timers
    on_front(); fill(); assert len(m.timers)==1; m.advance(pause); assert shift()==was+2; passed()
# A short genre after a long one, on the same page: back at the start, its timer gone once measured, none again.
m.nodes[source]['text']='Genre:Pop'; fill(); assert m.nodes[s]['text']=='Genre: Pop' and shift()==0 and len(m.timers)==1
m.word(s+O['HSCROLL_TEXT_W'],60); m.advance(pause); assert shift()==0 and not m.timers
fill(); assert not m.timers; passed()
# A long one after a long one holds at its start; a page closing, or a new one replacing it, ends the chain.
m,win,sv,source,s,fill=genre_page('Rock',300); fill(); m.advance(pause+ms); assert shift()==4
m.nodes[source]['text']='Genre:Metal'; fill(); assert shift()==0 and len(m.timers)==1 and min(t[0] for t in m.timers.values())==m.now+pause
m.call(address=np_page,args=(m.node('window','playing_page'),7,0,0),gap=0); assert not [t for t in m.timers.values() if t[1]==payload_syms['genre_step']]
m,win,sv,source,s,fill=genre_page('Rock',300); fill(); assert len(m.timers)==1
f,ctx=m.handler(win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert not m.timers
m.top=0; was=len(m.calls); m.call(address=payload_syms['genre_step'],args=(0,0,0,0),gap=0)
assert not m.timers and not any(c[0]=='hscroll_label_set_xoffset' for c in m.calls); passed()
# A missing player_location ("(key)") reads Location; a long (CJK) folder, past 512 bytes, stays whole.
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'; m.location=b'(player_location)\0'
folder='SD:/Music/'+'\u97f3\u697d'*150; label=m.node('label','label_location',text=folder)
m.call(address=payload_syms['ringnav_np_info'],args=(m.node('window','playing_page',[label]),0,0,0),gap=0)
assert m.nodes[label]['text']=='Location: '+folder; passed()
