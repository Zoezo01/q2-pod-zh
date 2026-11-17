"""Power: idle sleep by screen and playback state, the dark wheel, the watchdog record and Smart Wi-Fi."""
from q2emu import *

# The screen-off pass idles SCREEN_OFF_SLEEP_MS, and a screen-on pass with no input or animation for
# IDLE_MS idles IDLE_SLEEP_MS. No file is touched.
inp_hook=HOOKS['window_manager_dispatch_input_event'][0]
m=PowerMachine()
m.byte(syms['g_backlight_status'],0); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and not m.writes and not m.opens; passed()
# An update from a build with Low Power on: LOWPOWER=1 left in config.ini and its marker on flash do nothing.
m=PowerMachine({'LOWPOWER':'1'},files={'/mnt/data/q2pod-cpu1':b''}); m.byte(syms['g_backlight_status'],0)
assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and not m.writes and not m.opens and '/mnt/data/q2pod-cpu1' in m.files; passed()
# Dark with nothing playing and the DAC off (check_dacoff_state's -1), a pass idles DARK_IDLE_SLEEP_MS;
# playing, a DAC still counting, or a Wake press that may get its second, SCREEN_OFF_SLEEP_MS.
m=PowerMachine(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.byte(syms['g_backlight_status'],0)
off=O['SCREEN_OFF_SLEEP_MS']
for play,dac,want in ((3,-1,O['DARK_IDLE_SLEEP_MS']),(2,-1,off),(3,5,off),(1,-1,O['DARK_IDLE_SLEEP_MS'])):
    m.word(0xa50084,play); m.word(syms['g_dacoff_time'],dac&0xffffffff)
    assert m.pass_(1)[1]==[want],(play,dac); passed()
m=PowerMachine({'WAKEDOUBLE':'1'}); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,3)
m.byte(syms['g_backlight_status'],0); m.word(syms['g_dacoff_time'],0xffffffff); m.handlers[inp_hook+12]='stock_input'
assert m.pass_(1)[1]==[O['DARK_IDLE_SLEEP_MS']]  # reads Wake
m.call(O['KEY_CENTER'],address=inp_hook,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False)
assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and m.pass_(O['WAKE_MS'])[1]==[O['DARK_IDLE_SLEEP_MS']]; passed()
m=PowerMachine(); m.animating=1; m.pass_(1)  # idle pacing
assert m.pass_(O['IDLE_MS'])[1]==[]; m.animating=0
assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']] and not m.writes; passed()
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
m.call(address=inp,args=(m.wm,m.event,0,0),gap=0); assert m.pass_(1)[1]==[]; passed()  # input: full speed again
# A wheel press tick_gate drops is no input to the idle clock; one with a frame behind it is.
m=PowerMachine(); m.handlers[inp+12]='stock_input'; m.animating=1; m.pass_(1); m.pass_(O['IDLE_MS']); m.animating=0; assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]
m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0); assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]
m.word(payload_syms['wheel_frame_at'],m.now); m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0); assert m.pass_(1)[1]==[]; passed()
# A dark wheel turn keeps stock's pace: no idle for DARK_TURN_MS after a tick tick_gate passes, the same
# millisecond included; a tick it drops (no ring frame) leaves the dark idle.
m=PowerMachine(); m.handlers[inp+12]='stock_input'; m.byte(syms['g_backlight_status'],0); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']]
m.word(payload_syms['wheel_frame_at'],(m.now-O['WHEEL_FRESH_MS'])&0xffffffff)  # no finger behind this one
m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']]
m.word(payload_syms['wheel_frame_at'],m.now); m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False)
assert m.pass_(0)[1]==[] and m.pass_(O['DARK_TURN_MS']-1)[1]==[] and m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']]; passed()
# Now Playing keeps stock's pace only while its visualizer repaints; idle, it idles like any page.

m=Live(); m.handlers[payload_syms['visualizer_live']]='v:live'; m.live=1
m.top=m.node('window','playing_page'); m.pass_(1)
assert m.pass_(O['IDLE_MS'])[1]==[]; m.live=0; assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]; passed()
# Standby limit (docs/internals.md#standby-limit): with a limit, toolsIntoStandby's suspend runs
# between the RTC wake alarm's set (the limit past since_epoch) and its clear. A resume at or past the
# alarm powers off on the next pass (poweroff_page), once, unless a cable is in or the music plays; a
# key's resume before it, an unreadable RTC or a refused alarm leave it on. Off, no file is touched.

start=1792363806
m=StandbyMachine(key=600); assert m.standby()==['suspend'] and not m.opens and not m.off(); passed()
for i in (1,2,3):
    alarm=start+O[f'STANDBY_LIMIT_{i}']*3600; m=StandbyMachine({'STANDBYLIMIT':str(i)})
    assert m.standby()==[('alarm',0),('alarm',alarm),'suspend',('alarm',0)] and m.rtc==alarm
    assert m.off() and not m.off(); passed()
limit=O['STANDBY_LIMIT_2']*3600; m=StandbyMachine({'STANDBYLIMIT':'2'},key=600)
assert m.standby()==[('alarm',0),('alarm',start+limit),'suspend',('alarm',0)] and not m.alarm and not m.off()
m.key=None; assert m.standby()[1]==('alarm',start+600+limit) and m.off(); passed()  # the next one counts from zero
m=StandbyMachine({'STANDBYLIMIT':'1'}); m.alarm=start+60; assert m.standby()[:2]==[('alarm',0),('alarm',start+3600)]; passed()
m=StandbyMachine({'STANDBYLIMIT':'1'},key=7200,readable=False); assert m.standby()==['suspend'] and not m.off(); passed()
m=StandbyMachine({'STANDBYLIMIT':'1'},key=7200,refuse=True); assert m.standby()==[('alarm',0),'suspend'] and not m.off(); passed()
for usb,play in ((1,3),(0,2)):  # a cable in, or playing: it stays on, and no later pass powers off
    m=StandbyMachine({'STANDBYLIMIT':'1'}); m.standby(); m.byte(syms['g_usbdet_value'],usb); m.word(0xa50084,play)
    assert not m.off() and not m.off(); passed()
# Hardware watchdog and the state record (docs/internals.md#watchdog). The first pass opens
# /dev/jz_watchdog and starts it at WDT_MS, every pass feeds it, Standby stops it around the suspend
# and starts it again, and a clean end stops it and removes the record; nothing is fed or written
# after. No device, or a refused start: tried once, and the record is kept all the same. The record
# is rewritten only when it changes, and as suspending just before Standby's suspend.

m=WatchdogMachine(key=600); m.pass_(1)
assert m.wdt_opens==1 and m.ioctls==[(3,WDT_START,O['WDT_MS'])],(m.ioctls)
assert m.record()==b'screen:on power:running cable:out battery:0 play:paused out:jack\n',m.record()
m.pass_(1); m.pass_(1); assert m.ioctls[1:]==[(3,WDT_FEED)]*2 and m.wdt_opens==1 and len(m.records)==1; passed()
level(m,57,0); m.pass_(1); assert m.records[-1]==(RECORD,b'screen:on power:running cable:out battery:57 play:paused out:jack\n')
n=len(m.records); m.pass_(1); assert len(m.records)==n  # unchanged: not rewritten
m.byte(syms['g_backlight_status'],0); m.byte(syms['g_usbdet_value'],1); m.pass_(1)
assert m.records[-1][1]==b'screen:off power:running cable:in battery:57 play:paused out:jack\n'; passed()
m.ioctls=[]; assert m.standby()==[('wdt',WDT_STOP),'suspend',('wdt',WDT_START)],m.events
assert m.records[-1][1]==b'screen:off power:suspending cable:in battery:57 play:paused out:jack\n'
m.pass_(1); assert m.records[-1][1].startswith(b'screen:off power:running') and m.ioctls[-1]==(3,WDT_FEED); passed()
m=WatchdogMachine({'STANDBYLIMIT':'1'}); m.pass_(1)
assert m.standby()==[('alarm',0),('alarm',start+3600),('wdt',WDT_STOP),'suspend',('wdt',WDT_START),('alarm',0)],m.events; passed()
# a known long pass (an import, a folder add) counts WDT_LONG_MS; the next pass, after it, WDT_MS again
m=WatchdogMachine(key=600); m.pass_(1); m.ioctls=[]
for _ in range(2): m.call(address=payload_syms['ringnav_wdt_long'],args=(0,0,0,0),gap=0)  # once while it runs
assert m.ioctls==[(3,WDT_STOP),(3,WDT_START,O['WDT_LONG_MS'])],m.ioctls; m.ioctls=[]
m.pass_(1); assert m.ioctls==[(3,WDT_STOP),(3,WDT_START,O['WDT_MS'])],m.ioctls; m.ioctls=[]
m.pass_(1); assert m.ioctls==[(3,WDT_FEED)] and O['WDT_LONG_MS']>O['WDT_MS']==60000; passed()
# a clean end: the watchdog stops, the record goes, and nothing is fed or written after it
m=WatchdogMachine(key=600); m.pass_(1); m.ioctls=[]
m.call(address=payload_syms['ringnav_clean_end'],args=(0,0,0,0),gap=0)
assert m.ioctls==[(3,WDT_STOP)] and RECORD not in m.files; n=len(m.records); m.ioctls=[]
level(m,5,0); m.pass_(1); assert not m.ioctls and len(m.records)==n and RECORD not in m.files; passed()
m=WatchdogMachine(key=600,fd=-1); m.pass_(1); m.pass_(1)
assert m.wdt_opens==1 and not m.ioctls and m.record() and m.standby()==['suspend']; passed()
m=WatchdogMachine(key=600,refuse_start=True); m.pass_(1); m.pass_(1)
assert m.ioctls==[(3,WDT_START,O['WDT_MS'])] and m.record() and m.standby()==['suspend']; passed()

# The Wi-Fi signal (system.c ringnav_wifi_signal): networkGetWifiSignal, which the status bar and
# the network monitor thread each call about once a second, reads the driver's rx_signal and never
# sends stock's SIOCGIWSTATS, whose TX report takes the radio out of power save. It answers stock's
# dBm (the ioctl's unsigned level less 256, get_station's signal_strength less 100), except that
# 100% is 0 dBm, where stock wraps to -256; a missing file is 0, as stock's failed ioctl. The file
# is read again only after WIFI_SIGNAL_MS.

for pct in (0,20,35,64,99,100):
    stock,m=WifiMachine(pct,patched=False),WifiMachine(pct)
    assert stock.signal()==(pct-100 if pct<100 else -256) and stock.used()==['socket','ioctl','close']
    assert m.signal()==pct-100 and m.used()==['open','read','close']
    assert [m.text(c[1]) for c in m.calls if c[0]=='open']==[RX_SIGNAL]; passed()
m=WifiMachine(None); assert m.signal()==0 and m.used()==['open']; passed()  # no driver: as a failed ioctl
m=WifiMachine(60); assert m.signal()==-40; m.pct=30; m.calls=[]
m.now+=O['WIFI_SIGNAL_MS']-1; assert m.signal()==-40 and m.used()==[]
m.now+=1; assert m.signal()==-70 and m.used()==['open','read','close']; passed()

# The user's switch, for the record: Quick Settings' tap with Wi-Fi on, saved through the hook.
user=SmartMachine(smart=''); connected(user); user.calls=[]
assert user.call(address=syms['wifi_switch'],args=(1,0,0,0),gap=0)==0; user_calls=wifi_calls(user)
assert [c[0] for c in user_calls]==['access','remove','stop_wifisynclink','memset','networkStop','write_int_config']
assert [(c[1],user.text(c[2]),user.text(c[3])) for c in user.calls if c[0]=='write_int_config']==[(0,'SYSSET','WIFI')]; passed()
# Smart Wi-Fi: armed on the first poll, nothing before SMART_IDLE_MS (no link here, as an unassociated
# radio), then exactly that switch, in its order, without the write; the same state after.
m=SmartMachine(); connected(m)
m.pass_(1); assert not m.switch()
m.pass_(SW_IDLE-SW_POLL); assert not m.switch() and m.flag()==1
m.pass_(SW_POLL); assert wifi_calls(m)==user_calls[:-1]
assert wifi_state(m)==wifi_state(user)==[0,0,0,0,30,b'\0\0\0\0']; passed()
# network_checkstatus (stock's 500 ms timer) then finds Wi-Fi's flag clear and no link, and with the
# latch its connect set (it sent ips=0 and lps=0) sends ips=1, as after the user's switch.
m.byte(0xa4db39,1); m.calls=[]; m.call(address=syms['network_checkstatus'],args=(0,0,0,0),gap=0)
assert [m.text(c[1]) for c in m.calls if c[0]=='system@GLIBC_2.0']==['rtwpriv wlan0 pm_set ips=1'] and m.u.mem_read(0xa4db39,1)==b'\0'; passed()
# A page needing the network wakes it at the next poll: the switch's on branch, networkStart's
# background connect, still without the write. Tidal Downloads (the card's) does not.
m.windows('home_page','tidal_downloads'); m.pass_(SW_POLL); assert not m.switch() and m.flag()==0
m.windows('home_page','addon_page'); m.pass_(SW_POLL)
assert m.switch()==['networkStart'] and [c[1] for c in m.calls if c[0]=='networkStart']==[1] and m.flag()==1
# A new link counts as a use: SMART_IDLE_MS from it, not from the wake.
m.windows('home_page'); m.link=1; m.pass_(SW_POLL); assert not m.switch()
m.pass_(SW_IDLE-SW_POLL); assert not m.switch(); m.pass_(SW_POLL); assert m.switch()==['networkStop'] and m.flag()==0; passed()
# The user's own tap after that (Quick Settings shows Wi-Fi off: the radio is): Wi-Fi on and saved as
# stock saves it, Smart Wi-Fi's hold gone, and SMART_IDLE_MS from the tap before it acts again.
m.link=0; m.calls=[]; assert m.call(address=syms['wifi_switch'],args=(1,0,0,0),gap=0)==0
assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(1,'SYSSET','WIFI')]
assert 'networkStart' in m.switch() and m.flag()==1
m.pass_(SW_IDLE-1); assert not m.switch(); m.pass_(SW_POLL); assert m.switch()==['networkStop']; passed()
# Any other write passes the hook untouched, Smart Wi-Fi's radio off or not.
m.calls=[]; assert m.call(address=syms['write_int_config'],args=(3,m.string('SYSSET'),m.string('BACKLIGHT'),0),gap=0)==0
assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(3,'SYSSET','BACKLIGHT')] and m.flag()==0; passed()
# The row, after Charge Limit: off by default and saved as SMARTWIFI. Turned off while Smart Wi-Fi has
# the radio off, the user's Wi-Fi comes back, unsaved, and is left alone.
m,rows,texts,icons,click=settings_page('power','scroll_view_powermanager',cls=SmartMachine,config={'SMARTWIFI':'0'})
assert texts()==['Standby Limit: Off','Charge Limit (80%)','USB DAC Charging: Off','Smart Wi-Fi','system_carmode','Battery History','Boot to: Rockbox'] and icons[3]=='wifiset_wifi'
assert switch_image(m,rows[3])=='switch_off' and click(3)==[(1,'Q2POD','SMARTWIFI')] and switch_image(m,rows[3])=='switch_on'
m.pass_(1); m.pass_(SW_IDLE); assert m.flag()==0
assert click(3)==[(0,'Q2POD','SMARTWIFI')] and switch_image(m,rows[3])=='switch_off'
m.pass_(SW_POLL); assert m.switch()==['networkStart'] and m.flag()==1
m.pass_(SW_IDLE); m.pass_(SW_IDLE); assert not m.switch(); passed()
# The user's own off: saved, and Smart Wi-Fi never brings it back.
m=SmartMachine(); m.pass_(1); m.calls=[]; m.call(address=syms['wifi_switch'],args=(1,0,0,0),gap=0)
assert m.switch()==['networkStop','write_int_config'] and m.flag()==0
m.windows('stream_page'); m.pass_(SW_IDLE); m.pass_(SW_IDLE); assert not m.switch(); passed()
# Off by default: nothing, however long.
m=SmartMachine(smart=''); m.pass_(1); m.pass_(SW_IDLE); assert not m.switch(); m.pass_(SW_IDLE); assert not m.switch() and m.flag()==1; passed()
# What keeps it on (or, for a few, what doesn't): USB in, AirPlay or DLNA on, SyncLink over Wi-Fi or a
# SyncLink session (not SyncLink over Bluetooth), Wi-Fi transfer; a stream queue (Tidal's: the types
# stock stops offline), paused or stopped, but not a local one; a network page; a time sync pending on
# a link (not without one); and stock's own gates on its switch.

CASES=[('usb',setb('g_usbdet_value',1),True),('airplay',setb('g_airplayflag',1),True),('dlna',setb('g_dlnaflag',1),True),
       ('synclink wifi',setb('g_synclinkflag',2),True),('synclink session',setb('g_syncLink_status',1),True),
       ('synclink bt',setb('g_synclinkflag',1),False),('wifi transfer',setb('g_wifitransport_flag',1),True),
       ('paused tidal',stream_queue(0xf1,3),True),('stopped stream',stream_queue(2,1),True),
       ('paused local',stream_queue(0xf001,3),False),('streaming page',lambda m: m.windows('home_page','stream_page'),True),
       ('tidal page',lambda m: m.windows('tidal_main_page'),True),('wifi page',lambda m: m.windows('wifi_page'),True),
       ('card app page',lambda m: m.windows('addon_page'),True),('card app qr',lambda m: m.windows('qrcode_page'),True),
       ('apps',lambda m: m.windows('sysset_page','apps_page'),True),
       ('configuration',lambda m: m.windows('streamconf_page'),False),
       ('time sync',lambda m: (m.byte(syms['g_syncnettime_flag'],1),setattr(m,'link',1)),True),
       ('time sync, no link',setb('g_syncnettime_flag',1),False),
       ('module down',lambda m: m.word(O['NET_TYPE'],1),True),('first scan',setb('wifi_initstatus',2),True),
       ('connecting',setb('wifi_connectstatus',1),True),('test mode',setb('g_testmode_flag',1),True),
       ('Wi-Fi off',setb('g_wififlag',0),True),
       ('now playing more',lambda m: m.windows('playing_page','playermore_page'),False),
       ('firmware update',lambda m: m.windows('fwupdate_page'),False)]
for case,setup,kept in CASES:
    m=SmartMachine(); setup(m); m.pass_(1); m.pass_(SW_IDLE)
    assert (not m.switch())==kept and (kept or m.flag()==0),case
passed()
# The payload's own users hold it on while they run: librespot, a station, a card app, an upload,
# Network Update's download or an Apps job, Download Album Lyrics and a Tidal download.

for name in ('spot_running','radio_status','addon_running','scrobble_busy','netupdate_busy','album_lyrics_busy','tidal_busy'):
    for busy in (0,2):  # radio_status: 2 a station playing
        m=SmartBusy(name,busy); m.pass_(1); m.pass_(SW_IDLE); assert (not m.switch())==bool(busy),(name,busy)
passed()
# The Wi-Fi page builds its switch from g_wififlag: its init (systemset_wifi_page_init) brings the
# radio back first, so it shows the user's setting.
m=SmartMachine(); m.pass_(1); m.pass_(SW_IDLE); assert m.flag()==0
m.handlers[int(manifest['patch_symbols']['stock_wifi_page_trampoline'],16)]='stock_wifi_page'
m.calls=[]; assert m.call(address=HOOKS['systemset_wifi_page_init'][0],args=(m.top,0,0,0),gap=0)==0
names=[c[0] for c in m.calls]; assert names.index('networkStart')<names.index('stock_wifi_page') and 'write_int_config' not in names
assert m.flag()==1; passed()
# An automatic scrobble upload due while Smart Wi-Fi has the radio off: [SCROBBLE] AUTO read from the
# card, the radio woken, and the next check (a minute on) uploads on the link; a wake that brings no
# link, or AUTO off, waits SA_RETRY_MIN as an upload that failed.
SA=payload_syms.get('sa') or payload_syms['sa.0']  # the compiler may split the static struct into its fields, in order
assert all(payload_syms.get(f'sa.{i}',SA+4*i)==SA+4*i for i in range(5))
def sa_due(m): m.word(SA,1); m.word(SA+4,m.now-4*3600*1000); m.word(SA+8,m.now-120000)  # pending, first_at, check_at
def auto(m,ms=60000): m.pass_(ms)  # ringnav_sleep's pass runs scrobble_auto

m=SmartScrobble(); m.pass_(1); m.pass_(SW_IDLE); assert m.flag()==0
sa_due(m); auto(m,0); assert m.flags==1 and m.flag()==1 and not m.starts  # woken, nothing sent yet
m.link=1; auto(m); assert m.starts==1; passed()
m=SmartScrobble(); m.pass_(1); m.pass_(SW_IDLE); sa_due(m); auto(m,0); assert m.flag()==1
auto(m); assert not m.starts and m.get(SA+4)==(m.now-m.call(address=payload_syms['scrobble_auto_ms'],args=(1,0,0,0),gap=0)+30*60000)&0xffffffff
m.pass_(SW_IDLE); assert m.flag()==0; auto(m); assert m.flag()==0 and m.flags==1; passed()  # not again before the retry
m=SmartScrobble(on=False); m.pass_(1); m.pass_(SW_IDLE); sa_due(m); auto(m,0); assert m.flags==1 and m.flag()==0 and not m.starts; passed()
# Without Smart Wi-Fi's radio off, no Wi-Fi reads nothing from the card, as before.
m=SmartScrobble(); m.config.pop('SMARTWIFI'); m.byte(syms['g_wififlag'],0); m.pass_(1); sa_due(m); auto(m,0); assert not m.flags; passed()

# Actions that check the link once, held while Smart Wi-Fi brings the radio back (smart_hold): stock's
# Streaming's Tidal row, Network service's Wi-Fi transfer and Now Playing More's Download Cover &
# Lyrics. A press toasts and wakes the radio; stock's own action runs once from a timer when the link is
# up, or SMART_CONNECT_MS on, and only while its page is still the one in use (toasts and the volume
# overlay aside).

HELD=[('stream_row','stream_row','stream_page','0'),('netservice_row','netservice_row','netservice_page','2'),
      ('playermore_online','online_match','playermore_page',None)]

for hook,t,page,row in HELD:
    # Waits: toasted, the radio woken, no stock action; a second press toasts again, runs nothing and
    # keeps the one timer; once the link is up, stock's action runs once.
    m,win=held_machine(page); assert m.flag()==0
    target=held_press(m,hook,win,row)
    assert m.toasts[-1][3]==SW_WAIT and m.flag()==1 and not held_runs(m,t) and len(m.timers)==1,hook
    m.advance(500); assert not held_runs(m,t)
    n=len(m.toasts); held_press(m,hook,win,row)
    assert len(m.toasts)==n+1 and m.toasts[-1][3]==SW_WAIT and not held_runs(m,t) and len(m.timers)==1,hook
    m.link=1; m.advance(500,clear=False); runs=held_runs(m,t)
    assert runs==[target] and not m.timers,hook
    m.advance(2000); assert not held_runs(m,t); passed()
    # A press after the link is up but before the tick joins the wait: stock's action still runs once.
    m,win=held_machine(page); held_press(m,hook,win,row); m.advance(500); m.link=1
    held_press(m,hook,win,row); assert not held_runs(m,t) and len(m.timers)==1
    m.advance(500,clear=False); assert len(held_runs(m,t))==1 and not m.timers,hook; passed()
    # The bars up before stock's connect status (each its own poll): it waits for both, as stock's
    # action checks the one and the payload's the other.
    m,win=held_machine(page); held_press(m,hook,win,row); m.link=1; m.status=0
    m.advance(500,clear=False); assert not held_runs(m,t) and len(m.timers)==1,hook
    m.status=None; m.advance(500,clear=False); assert len(held_runs(m,t))==1 and not m.timers,hook; passed()
    # No link SMART_CONNECT_MS on: stock's action anyway (its own msg_notnetwork).
    m,win=held_machine(page); held_press(m,hook,win,row); m.advance(SW_CONNECT-500); assert not held_runs(m,t)
    m.advance(500); assert len(held_runs(m,t))==1 and not m.timers; passed()
    # Another page opened over it, or it closed: dropped at the next tick, link or not, never run when
    # the link comes, and the radio let go.
    for cover in ('playing_page','closed'):
        m,win=held_machine(page); held_press(m,hook,win,row)
        if cover=='closed': m.nodes[m.wm]['children'].remove(win)
        else: m.nodes[m.wm]['children'].append(m.node('window',cover))
        m.advance(500); assert not held_runs(m,t) and not m.timers,(hook,cover)
        m.link=1; m.advance(500); assert not held_runs(m,t)
        m.link=0; m.nodes[m.wm]['children']=[m.node('window','home_page')]; m.poll(SW_IDLE); m.poll(SW_POLL)
        assert m.flag()==0,(hook,cover); passed()
    # A toast, or stock's volume overlay (a wheel turn over the toast), over it is no other page.
    for overlay in ('msginfo_dialog','volume_dialog'):
        m,win=held_machine(page); held_press(m,hook,win,row); m.nodes[m.wm]['children'].append(m.node('dialog',overlay))
        m.link=1; m.advance(500); assert len(held_runs(m,t))==1,(hook,overlay); passed()
    # A page sliding (iPod's close, Back mid-wait) waits for the slide: closed, it is dropped;
    # still there, it runs.
    for closed in (True,False):
        m,win=held_machine(page); held_press(m,hook,win,row); m.animating=1; m.link=1
        m.advance(500); assert not held_runs(m,t) and len(m.timers)==1
        if closed: m.nodes[m.wm]['children'].remove(win)
        m.animating=0; m.advance(500); assert len(held_runs(m,t))==(not closed) and not m.timers,(hook,closed); passed()
    # Not Smart Wi-Fi's radio to wait for (the row off), or the link already up: stock's, at once.
    for smart,link in (('',0),('1',1)):
        m,win=held_machine(page,smart=smart); m.link=link; n=len(m.toasts); held_press(m,hook,win,row)
        assert len(held_runs(m,t))==1 and not m.timers and SW_WAIT not in [x[3] for x in m.toasts[n:]],(hook,smart,link)
        passed()
# Network service's DLNA and AirPlay switches never wait, nor wake the radio: stock starts their
# services once there is a link. Stock gets the pressed button.
for row in '01':
    m,win=held_machine('netservice_page'); held_press(m,'netservice_row',win,row)
    assert held_runs(m,'netservice_row')==[m.nodes[held_row(m,win,row)]['children'][0]] and not m.timers and m.flag()==0; passed()
# A press from a page that is not the one expected (no page in use, say) is stock's at once.
m,win=held_machine('stream_page'); m.nodes[m.wm]['children']=[]; held_press(m,'stream_row',win,'0')
assert len(held_runs(m,'stream_row'))==1 and not m.timers; passed()
# Home's Split Streaming list (home.c stream_rows) has stock's Tidal row too, in Home's window beside
# its Library list, whose rows stock also names by index: held the same, and stock is given the
# Streaming list's row.
m,home=held_machine('home_page')  # the Library list's rows
b=m.node('button','0'); tidal=m.node('list_item','0',[b])
for w in (tidal,b): m.word(w+0x10,m.string('0'))
m.nodes[home]['children'].append(m.node('view','list_view_homestream',[m.node('scroll_view','scroll_view_streamsset',[tidal])]))
m.nodes[m.wm]['children']=[home]
assert m.call(address=HOOKS['stream_row'][0],args=(b,m.event,0,0),gap=0)==0
assert m.toasts[-1][3]==SW_WAIT and not held_runs(m,'stream_row') and len(m.timers)==1
m.link=1; m.advance(500,clear=False); assert held_runs(m,'stream_row')==[tidal] and not m.timers; passed()
# Stock's own code after the wait (the trampolines unmocked), given the row's list_item: Streaming's
# Tidal row opens its login page with the link, and after SMART_CONNECT_MS without one, stock's
# msg_notnetwork; Network service's Wi-Fi transfer switches to its page.
m,win=held_machine('stream_page',mock=False); m.mock('widget_get_window','navigator_switch_to')
held_press(m,'stream_row',win,'0'); m.link=1; m.advance(500)
assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['tidal/tidal_login_page']; passed()
m,win=held_machine('stream_page',mock=False); m.mock('widget_get_window'); held_press(m,'stream_row',win,'0')
n=len(m.toasts); m.advance(SW_CONNECT)
assert [m.text(c[1]) for c in m.calls if c[0]=='getFormatString']==['msg_notnetwork'] and m.toasts[n:]==[('dialog/msginfo_dialog',1,2000,'')]
assert not [c for c in m.calls if c[0]=='navigator_to']; passed()
m,win=held_machine('netservice_page',mock=False); m.mock('widget_get_window','navigator_switch_to')
held_press(m,'netservice_row',win,'2'); m.link=1; m.advance(500)
assert [(m.text(c[1]),c[2]) for c in m.calls if c[0]=='navigator_switch_to']==[('systemset/wifitransport_page',0)]; passed()
# The payload's own presses wait the same way: Upload Scrobbles (Library's row), from its page.
m=SmartRadio(); m.config['TOKEN']='t'; m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
m.windows('localmusic_page'); m.calls=[]
assert m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0)==0
assert m.toasts[-1][3]==SW_WAIT and m.flag()==1 and not m.threads
m.advance(500); assert not m.threads
m.link=1; m.advance(500); assert m.toasts[-1][3]=='Uploading scrobbles' and len(m.threads)==1; passed()
# A press after the link is up but before the tick joins the wait: one upload, started once (a
# second run would have answered Already uploading, or cancelled Network Update's or Apps' job).
m=SmartRadio(); m.config['TOKEN']='t'; m.poll(1); m.poll(SW_IDLE); m.windows('localmusic_page'); m.calls=[]
m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0); m.advance(500); m.link=1
assert m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0)==0 and not m.threads and m.toasts[-1][3]==SW_WAIT
m.advance(500); assert len(m.threads)==1 and [x[3] for x in m.toasts].count('Uploading scrobbles')==1; passed()
