"""Library requests (seq, scan, import), About's versions and the network update's download and notice."""
from q2emu import *

# A new seq's request is read whole once, and that read's own first line must be the seq. One replaced between the
# poll's cheap first-line read and the whole read runs its own lines under its own seq, at the next poll; one gone
# between them is answered nothing (either was answered "<seq> ok": seq 500 with 501's lines, or with none).
m=lib_machine(); scans=lambda: [o for o in m.opened if o[0]=='dialog/updatemusic_dialog']
def second_open(new):
    opens=[]
    def hook(path):
        if path!=LIB: return
        opens.append(path)
        if len(opens)==2 and new is None: m.files.pop(LIB)
        elif len(opens)==2: m.files[LIB]=bytearray(new)
    m.on_fopen=hook
second_open(f'501\nimport {EXP}/Mix.m3u\n'.encode()); m.files[LIB]=bytearray(b'500\nscan\n'); m.poll()
assert DONE not in m.files and not m.imports and not scans(),(m.files.get(DONE),m.imports)
m.poll(); assert m.files[DONE]==b'501 ok\n' and m.imports==[f'{EXP}/Mix.m3u\n'] and not scans(),(m.files[DONE],m.imports)
second_open(None); m.files[LIB]=bytearray(b'502\nscan\n'); m.poll(); m.poll()
assert m.files[DONE]==b'501 ok\n' and not scans(),m.files[DONE]; m.on_fopen=None; passed()
# CRLF lines are read as LF lines.
m.files[LIB]=bytearray(f'503\r\nimport {EXP}/Mix.m3u\r\n'.encode()); m.poll()
assert m.files[DONE]==b'503 ok\n' and len(m.imports)==2,m.files[DONE]; passed()
# An empty first line has no seq to run under: answered " error Missing seq" (the seq empty) once, as a seq too long
# is, never run.
m.files[LIB]=bytearray(b'\r\nscan\n'); m.poll(); assert m.files[DONE]==b' error Missing seq\n' and not scans(),m.files[DONE]
d=m.files[DONE]; m.poll(); assert m.files[DONE] is d; passed()
# A playlist's name (its file's less .m3u) of 64 bytes or more is refused, nothing run: stock's create cuts it into
# char title[64] and skips its duplicate check, so the cut name never matches the file again and every sync would
# add a copy. 63 bytes import, as do 21 three-byte characters; 22 don't.
for x in ('n'*63,'n'*64,'音'*21,'音'*22): m.files[f'{EXP}/{x}.m3u']=bytearray(b'#EXTM3U\n')
k,n=len(m.imports),len(scans())
m.files[LIB]=bytearray(f'504\nscan\nimport {EXP}/{"n"*64}.m3u\n'.encode()); m.poll()
assert m.files[DONE]==f'504 error Name too long: {"n"*64}\n'.encode() and len(scans())==n,m.files[DONE]
m.files[LIB]=bytearray(f'505\nadd {EXP}/{"音"*22}.m3u\n'.encode()); m.poll(); assert m.files[DONE]==f'505 error Name too long: {"音"*22}\n'.encode()
m.files[LIB]=bytearray(f'506\nimport {EXP}/{"n"*63}.m3u\nadd {EXP}/{"音"*21}.m3u\n'.encode()); m.poll()
assert m.files[DONE]==b'506 ok\n' and m.imports[k:]==[f'{EXP}/{"n"*63}.m3u\n{EXP}/{"音"*21}.m3u\n'],(m.files[DONE],m.imports[k:]); passed()
# A scan's request ends only once its scan was seen, its thread running or its dialog open (right after the
# dialog's call, which returns at once here; on a device the dialog's own loop polls while it runs): a dialog that
# never opened, no scan seen, is "<seq> error Couldn't start the scan", not an ok without a scan.
m=lib_machine(); m.files[LIB]=bytearray(b'507\nscan\n'); m.poll(); assert len(scans())==1 and DONE not in m.files
m.poll(); assert m.files[DONE]==b"507 error Couldn't start the scan\n",m.files[DONE]
m.on_open=lambda page: m.open_pages.add('updatemusic_dialog')  # stays open after the call: seen at once
m.files[LIB]=bytearray(b'508\nscan\n'); m.poll(); m.poll(); assert m.files[DONE].startswith(b'507 ')
m.open_pages.discard('updatemusic_dialog'); m.poll(); assert m.files[DONE]==b'508 ok\n',m.files[DONE]; m.on_open=None; passed()

# The scan and the import run only while every open window (the window manager's children, the status bar aside)
# is one known to hold no library records: Home, Streaming and its Configuration, a card app's page and its QR code,
# Now Playing, and System and Playback settings' own pages, all together here. Any other holds the request until it
# closes: a page this list doesn't know (a new one's), the lock screen, the App Store, the Library menu, an app's
# songs and Up Next among them.
SAFE=['home_page','stream_page','streamconf_page','addon_page','qrcode_page','playing_page','sysset_page','display_page',
      'backlight_page','lighttime_page','keylock_page','language_page','datetime_page','autotime_page','manualtime_page',
      'screensaver_page','standby_page','powermanager_page','sleepshutdown_page','about_page','bluetooth_page',
      'btquality_page','btadjustvol_page','wifi_page','wifiset_page','netservice_page','playset_page','audiosetting_page',
      'playmode_page','replaygain_page','filter_page','dsdoutput_page','memplay_page','usbvol_page','maxvol_page',
      'bootvol_page','balance_page','equalizer_page']
m=lib_machine(); seq=600
for page in ('mystery_page','saverscreen_page','apps_page','localmusic_page','app_songs','upnext_page'):
    seq+=1; windows(m,page); k=len(m.imports); m.files[LIB]=bytearray(f'{seq}\nimport {EXP}/Mix.m3u\n'.encode()); m.poll(); m.poll()
    assert len(m.imports)==k and not m.files.get(DONE,b'').startswith(f'{seq} '.encode()),(page,m.files.get(DONE))
    windows(m); m.poll(); assert m.files[DONE]==f'{seq} ok\n'.encode() and len(m.imports)==k+1,(page,m.files[DONE])
windows(m,*SAFE); m.files[LIB]=bytearray(f'700\nimport {EXP}/Mix.m3u\n'.encode()); m.poll(); assert m.files[DONE]==b'700 ok\n',m.files[DONE]; passed()

# About: FW. Version shows the stock firmware's version again, not the updater tag in demo's
# literal, and a CFW. Version row follows it. Stock's own row builder (0x4c0de4) builds Model and FW.
# Version, so the added row is checked against the real stock widgets, geometry and styles. A page of another
# count of rows (V1.32's seven) is left as stock built it. Stock's thanks, V1.36's eighth child, is dropped.
m=ShuffleMachine(); m.handlers[int(manifest['patch_symbols']['stock_about_trampoline'],16)]='stock_about'
m.mock('strcpy@GLIBC_2.0','widget_set_tr_text')
def tree(w):
    n=m.nodes[w]
    return (n['type'],[m.get(w+O[k]) for k in ('W_X','W_Y','W_W','W_H')],n.get('style'),
            n.get('hscroll_label_set_only_focus'),n.get('hscroll_label_set_ellipses'),[tree(c) for c in n['children']])
view=m.node('scroll_view','scroll_view_about'); m.top=m.node('window','about_page',[view])
for i in range(8):  # stock's seven rows and its thanks, the eighth child
    row=m.node('list_item'); m.nodes[view]['children'].append(row)
    if i<2: assert m.call(address=0x4c0de4,args=(row,i,0,0),gap=0)==0
rows=list(m.nodes[view]['children'])
assert m.call(address=HOOKS['systemset_about_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and m.calls[0][:3]==('stock_about',m.top,5)
kids=m.nodes[view]['children']; assert kids[:2]==rows[:2] and kids[3:]==rows[2:7]
fw=m.nodes[m.nodes[rows[1]]['children'][0]]['children'][1]; assert m.nodes[fw]['text']=='V1.36'
item=kids[2]; button=m.nodes[item]['children'][0]; title,value=m.nodes[button]['children']
# Stock's row, title and value widgets alike.
want=tree(m.nodes[rows[1]]['children'][0])
assert m.nodes[item]['style']=='s_listitem_black' and tree(button)==want
assert m.nodes[title]['text']=='CFW. Version' and m.nodes[value]['text']==f"{VERSION}{f" dev {manifest['dev']}" if manifest['dev'] else f' beta {BETA}' if BETA else ''}"
assert not m.nodes[button].get('handlers') and not m.nodes[button].get('name'); passed()
m.nodes[view]['children']=rows[:7]; assert m.call(address=HOOKS['systemset_about_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and m.nodes[view]['children']==rows[:7]; passed()
# Under Classic the CFW. Version row's title takes the Classic font (about_label), its value a second line's: stock's regular.
am=classic_light_new(ShuffleMachine); am.handlers[int(manifest['patch_symbols']['stock_about_trampoline'],16)]='stock_about'
am.mock('strcpy@GLIBC_2.0','widget_set_tr_text')
aview=am.node('scroll_view','scroll_view_about'); am.top=am.node('window','about_page',[aview])
for i in range(8):
    arow=am.node('list_item'); am.nodes[aview]['children'].append(arow)
    if i<2: assert am.call(address=0x4c0de4,args=(arow,i,0,0),gap=0)==0
assert am.call(address=HOOKS['systemset_about_page_init'][0],args=(am.top,5,0,0),gap=0)==0
abutton=am.nodes[am.nodes[aview]['children'][2]]['children'][0]
assert [am.nodes[l].get('style:normal:font_name') for l in am.nodes[abutton]['children']]==['classic-light','default']; passed()

# Network Update (netupdate.c): System Update's second row, built by stock's own row builder
# (0x4c8660), is renamed "2" so stock's click (atoi of the name: 0 TF card, 1 Shanling) skips it, and
# fetches the latest GitHub release over verified TLS to the card's update.tar.part, carrying on from a
# part already there. Only a part of the size and SHA-256 that GitHub's record of the release names
# (stock's own SHA-256, run here on the MIPS build) is renamed update.tar; then stock's TF card
# update (0x4c7eb4) runs. A failure keeps the old update.tar.

def record(tar,size=None):
    """GitHub's record of a release whose update.tar is tar: its size and digest, after another asset's."""
    assets=[{'name':n,'label':'','size':len(d) if size is None or n!='update.tar' else size,'digest':'sha256:'+hashlib.sha256(d).hexdigest(),
             'browser_download_url':'https://h/'+n} for n,d in (('Q2-Pod-User-Guide.pdf',b'guide'),('update.tar',tar))]
    return json.dumps({'tag_name':'9.9.9','name':'9.9.9','assets':assets,'body':'notes'}).encode()
m=UpdateMachine(); resume=int(manifest['patch_symbols']['stock_fwupdate_trampoline'],16); m.handlers[resume]='stock_fwupdate'
m.u.hook_add(UC_HOOK_CODE,m.hook,begin=resume,end=resume)  # fast(): payload addresses hook one by one
m.handlers[0x4c7eb4]='fwupdate_tf'; m.mock('strcpy@GLIBC_2.0','widget_set_tr_text')
view=m.node('scroll_view','scroll_view_fwupdate'); m.top=m.node('window','fwupdate_page',[view])
for i in range(2):
    row=m.node('list_item'); m.nodes[view]['children'].append(row)
    assert m.call(address=0x4c8660,args=(row,i,0,0),gap=0)==0
tf,online=(m.nodes[r]['children'][0] for r in m.nodes[view]['children'])
assert m.call(address=HOOKS['systemset_fwupdate_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
assert m.calls[0][:3]==('stock_fwupdate',m.top,5) and m.nodes[tf]['name']=='0' and m.nodes[online]['name']=='2'
remind=m.nodes[m.nodes[view]['children'][-1]]['children'][0]  # the row's button, its label inside
assert len(m.nodes[view]['children'])==3 and [m.nodes[k].get('text') for k in m.nodes[remind]['children']]==['Update Notifications: On']  # under Network Update, On when config.ini has no key
label=m.nodes[online]['children'][0]; assert m.nodes[label]['type']=='hscroll_label'
def press():
    f,ctx=[(f,c) for t,f,c in m.nodes[online]['handlers'] if t==O['EVT_CLICK']][-1]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
UPDATE='/mnt/mmc/update.tar'; ASSET='update.tar'
RELEASES=f"https://github.com/{manifest['update_repo']}/releases"  # build.py --update-repo
m.files[UPDATE]=bytearray(b'old')
m.wifi=-1; press(); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads; m.wifi=3
def fetch(codes,tag,reply=b'',done=500,release=None):
    press(); assert m.nodes[label]['text']=='Checking…'
    m.codes=list(codes); m.location=f'{RELEASES}/tag/{tag}'; m.reply=reply; m.release=record(reply) if release is None else release; m.froms=[]
    m.advance(500,clear=False)  # while it runs: the percentage, and standby and auto power-off wait
    assert m.nodes[label]['text']=='Checking…' and ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls]
    worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
    threads=len(m.threads); press(); assert len(m.threads)==threads  # ended, not yet joined: nothing
    m.advance(done,clear=False)
    assert ('widget_set_tr_text',label) in [c[:2] for c in m.calls]
NEW=bytes(range(256))*70  # 17,920 bytes: more than one of the read-back's 16 KB pieces, and a padded last block
fetch([302,200,200],'9.9.9',NEW)
assert m.opts[10002]==f'{RELEASES}/download/9.9.9/{ASSET}' and m.opts[52]==1 and m.froms==[0,0] and 21 not in m.opts
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and m.opts[99]==1 and m.opts[20108]
assert bytes(m.files[UPDATE])==NEW and UPDATE+'.part' not in m.files and '/tmp/q2-update.json' not in m.files
assert 'fwupdate_tf' in [c[0] for c in m.calls]
# One byte off what the release's digest names, or another size: never update.tar, and the part goes.
m.files[UPDATE]=bytearray(b'old')
fetch([302,200,200],'9.9.9',NEW[:-1]+b'!',release=record(NEW))
assert m.toasts[-1][3]=='The download was damaged, try again' and m.files[UPDATE]==b'old' and UPDATE+'.part' not in m.files
fetch([302,200,200],'9.9.9',NEW,release=record(NEW,len(NEW)+1))
assert m.toasts[-1][3]=='The download was damaged, try again' and m.files[UPDATE]==b'old' and UPDATE+'.part' not in m.files
assert 'fwupdate_tf' not in [c[0] for c in m.calls]
# A release whose record lacks the tar's digest downloads nothing; a card without room neither.
fetch([302,200],'9.9.9',NEW,release=record(NEW).replace(b'"digest"',b'"digest_"'))
assert m.toasts[-1][3]=='No update for this build' and m.froms==[0] and m.files[UPDATE]==b'old'
# A part left on the card (a lost connection, a power cut, the page left) is carried on from: the rest
# alone is asked for, and the whole is checked as any other.
m.files[UPDATE+'.part']=bytearray(NEW[:5000])
fetch([302,200,206],'9.9.9',NEW)
assert m.froms==[0,5000] and m.opts[21]==5000 and bytes(m.files[UPDATE])==NEW and UPDATE+'.part' not in m.files
assert 'fwupdate_tf' in [c[0] for c in m.calls]
m.files[UPDATE]=bytearray(b'old')
fetch([302,200,404],'9.9.9'); assert m.toasts[-1][3]=='No update for this build' and m.files[UPDATE]==b'old' and UPDATE+'.part' not in m.files
m.word(syms['g_power_capacity'],15); threads=len(m.threads); press()  # under stock's own gate before the flash: said first
assert m.toasts[-1][3]=='Charge the battery first' and len(m.threads)==threads; m.word(syms['g_power_capacity'],80)
fetch([200],'9.9.9'); assert m.toasts[-1][3]=="Can't reach GitHub"  # no redirect: no tag
if not manifest['dev']:  # --dev builds have no release tag, so always fetch
    fetch([302],VERSION); assert m.toasts[-1][3]=='Latest firmware installed' and m.files[UPDATE]==b'old'
    fetch([302],'1.0.0'); assert m.toasts[-1][3]=='Latest firmware installed'  # older: never a downgrade
# Leaving the page cancels before the download starts: the card may be unmounted or shared next.
press(); m.codes=[302,200]; m.location=f'{RELEASES}/tag/9.9.9'; m.reply=b'NEW'
f,ctx=m.handler(label,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
m.advance(500,clear=False); assert m.toasts[-1][3]=='Download cancelled' and m.files[UPDATE]==b'old'
assert 'fwupdate_tf' not in [c[0] for c in m.calls]; passed()
# The notice (netupdate_poll, power_poll's, each pass of ringnav_sleep): with Wi-Fi on, a release build asks for
# the latest tag (Network Update's header-only request, no API) and tells of a newer one on Home, once, its tag
# kept in /mnt/data first. A --dev build has no tag to compare and checks nothing. The card is out meanwhile,
# so the boot logo's poll starts no thread of its own.
m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
TOLD='/mnt/data/q2-update.told'; mounts=m.files['/proc/mounts']; m.files['/proc/mounts']=bytearray()
m.top=m.node('window','home_page'); m.byte(syms['g_backlight_status'],1); toasts=len(m.toasts); nc_checks=len(m.checks)
def loop(): m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=2000,clear=False)
m.codes=[302]; m.location=f'{RELEASES}/tag/9.9.9'; loop()
if manifest['dev']:
    loop(); assert len(m.checks)==nc_checks and TOLD not in m.files and len(m.toasts)==toasts
else:
    assert len(m.checks)==nc_checks+1; worker,arg=m.checks[-1]
    assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
    assert m.opts[10002]==f'{RELEASES}/latest' and m.opts[44]==1 and 20011 not in m.opts
    loop(); assert m.toasts[-1][3]=='Q2 Pod 9.9.9 is out: System Update → Network update' and len(m.toasts)==toasts+1
    assert bytes(m.files[TOLD][:6])==b'9.9.9\0'
    loop(); loop(); assert len(m.toasts)==toasts+1 and len(m.checks)==nc_checks+1  # once; the next check is a day off
m.files['/proc/mounts']=mounts; passed()
