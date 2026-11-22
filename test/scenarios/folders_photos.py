"""Podcasts and Audiobooks folders, Folder visits and the card going; Photos' names, orientation and placing."""
from q2emu import *

# Podcasts and Audiobooks: a row each, above upkeep, only for the card's top-level folder of that name (case
# aside, a directory). A press opens folder_page there as a deeper folder (layer 3, stock reload, title
# and rebuild); Back there leaves the page (layer 1 before stock's step down), and only for that root.
m=ShuffleMachine(); m.card=[('Music',4),('PODCASTS',4),('Audiobooks',8)]
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==20
button=m.nodes[m.nodes[view]['children'][-5]]['children'][0]; icon,label,into=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_podcasts' and m.nodes[label]['text']=='Podcasts' and not m.nodes[button].get('name')
f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['folder_page']
win=m.node('window','folder_page'); m.calls=[]
assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
assert m.text(syms['g_folder_path'])=='/mnt/mmc/PODCASTS' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
assert [c[0] for c in m.calls]==['stock_folder','folder_reset_variable','strlen@GLIBC_2.0','memcpy','folder_reload_data','folder_reinit_navbarname','folder_refresh'] and m.calls[-1][1]==win
assert m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)==0 and m.calls[-1][0]=='stock_folder_back'
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1; passed()
m.byte(syms['g_folder_layer'],3); assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0  # the Folder view
m.folder('/mnt/mmc/PODCASTS'); m.byte(syms['g_folder_layer'],3); m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==3; passed()

# Audiobooks uses the same shortcut path, even with OTG connected; a nested Back stays in Folder.
m=ShuffleMachine(); m.card=[('Audiobooks',4)]; m.byte(syms['g_otg_status'],1)
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.top=m.node('window','localmusic_page',[view])
m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
button=m.nodes[m.nodes[view]['children'][-5]]['children'][0]
f,ctx=m.handler(button,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
win=m.node('window','folder_page'); m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.text(syms['g_folder_path'])=='/mnt/mmc/Audiobooks' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
m.folder('/mnt/mmc/Audiobooks/Author'); m.byte(syms['g_folder_layer'],4)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==4
m.folder('/mnt/mmc/Audiobooks'); m.byte(syms['g_folder_layer'],3)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1; passed()
# Shortcut: Podcasts (8) and Audiobooks (9) open the card's folder as Local Music's rows do; without it a toast says so.
CONFIG['SHORTCUT']='8'; m=ShuffleMachine(); del CONFIG['SHORTCUT']; m.card=[('Podcasts',4)]
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
assert m.text(m.call(address=payload_syms['ipod_home_shortcut'],args=(0,0,0,0),gap=0))=='Podcasts'
m.calls=[]; assert m.call(address=payload_syms['home_shortcut_open'],args=(0,m.event,0,0),gap=0)==0
assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['folder_page']
win=m.node('window','folder_page'); assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
assert m.text(syms['g_folder_path'])=='/mnt/mmc/Podcasts' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
CONFIG['SHORTCUT']='9'; m=ShuffleMachine(); del CONFIG['SHORTCUT']; m.card=[('Podcasts',4)]
assert m.text(m.call(address=payload_syms['ipod_home_shortcut'],args=(0,0,0,0),gap=0))=='Audiobooks'
m.calls=[]; assert m.call(address=payload_syms['home_shortcut_open'],args=(0,m.event,0,0),gap=0)==0
assert not [c for c in m.calls if c[0]=='navigator_to'] and m.toasts[-1][3]=='No Audiobooks folder on the card'; passed()

# Folder visits bypass the storage selector only when OTG was absent and the card present on entry
# (stock's check_tfstatus: /tmp/mmc_add).
for otg,card in ((0,1),(1,1),(0,0)):
    m=Machine()
    if card: m.present.add('/tmp/mmc_add')
    m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
    m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
    win=m.node('window','folder_page'); m.byte(syms['g_otg_status'],otg); m.byte(syms['g_folder_layer'],1)
    assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
    direct=card and not otg; calls=[c[0] for c in m.calls if not c[0].startswith('access')]
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==(2 if direct else 1)
    if direct:
        assert m.text(syms['g_folder_path'])=='/mnt/mmc'
        assert calls==['stock_folder','memcpy','toolsReadConfig','tk_snprintf','folder_reset_variable','strlen@GLIBC_2.0','memcpy',
                       'folder_reload_data','folder_reinit_navbarname','folder_refresh'],calls
    else: assert calls==['stock_folder','toolsReadConfig','tk_snprintf'][:1+2*card],calls
    # Nested Back stays stock. Plugging/unplugging OTG mid-visit does not change root Back.
    m.folder('/mnt/mmc/Music'); m.byte(syms['g_folder_layer'],3)
    m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==3
    m.folder('/mnt/mmc'); m.byte(syms['g_folder_layer'],2); m.byte(syms['g_otg_status'],not otg)
    m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==(1 if direct else 2)
    passed()
# A direct visit left without Back (as by Home), then a selector visit with OTG: the old root is gone, so
# Back at the card's root on the selector stays stock.
m=Machine(); m.present.add('/tmp/mmc_add')
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
win=m.node('window','folder_page'); m.byte(syms['g_otg_status'],0); m.byte(syms['g_folder_layer'],1)
m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.text(syms['g_folder_path'])=='/mnt/mmc' and m.u.mem_read(syms['g_folder_layer'],1)[0]==2
m.byte(syms['g_otg_status'],1); m.byte(syms['g_folder_layer'],1)
m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1
m.folder('/mnt/mmc'); m.byte(syms['g_folder_layer'],2)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==2; passed()

# Folder Root (#49): with a card, Folder opens in the saved top-level folder (Q2POD FOLDERROOT) as a
# layer-3 folder, a fresh visit (stock folder_reset_variable, run here: no enter history, no M3U).
# Stock folder_back, run here, walks up to the card at layer 2 with stock's title; its history being
# empty, the position stays the caller's (the list's top). Back there leaves without a drive and
# reaches the storage selector with one. A missing, unsafe or file root opens as before.

for otg in (0,1):
    m=RootMachine('Music',otg=otg); m.push(5,6); m.u.mem_write(syms['g_m3u_path'],b'/mnt/mmc/Music/list.m3u\0')  # a visit left deep
    assert m.open()==('/mnt/mmc/Music',3) and not any(m.stacks.values()) and m.text(syms['g_m3u_path'])==''
    calls=[c[0] for c in m.calls if c[0] in ('stock_folder','folder_reload_data','folder_reinit_navbarname','folder_refresh')]
    assert calls==['stock_folder','folder_reload_data','folder_reinit_navbarname','folder_refresh'] and m.calls[-1][1]==m.win
    m.calls=[]; assert m.back()==(1,'/mnt/mmc',2,(7,9)) and m.text(syms['g_navbar_name'])=='SD'
    assert [c[1] for c in m.calls if c[0]=='toolsLoadDirectory']==[syms['g_folder_path']]
    assert m.back()[::2]==((1,1) if otg else (0,0)); passed()  # the selector, or out of the page
# Gone, a file, hidden, a path, or no card: as before Folder Root (the card at layer 2, or stock's page).
for root in ('Gone','notes.txt','.Trash','Music/..',''):
    assert RootMachine(root).open()==('/mnt/mmc',2),root
for m in (RootMachine('Gone',otg=1),RootMachine('Music')):
    m.missing=m.config['FOLDERROOT']=='Music'  # no card: CoverflowMachine's access finds nothing
    assert m.open()[1]==1 and 'folder_reload_data' not in [c[0] for c in m.calls]
passed()
# Podcasts keeps its own root, Back there leaving the page, whatever Folder Root says.
m=RootMachine('Music'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.top=m.node('window','localmusic_page',[view]); m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][-5]]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.open()==('/mnt/mmc/Podcasts',3) and m.back()[::2]==(0,0); passed()

# Go to Folder: a card song's hold menu row, after Go to Artist, opens folder_page in the song's folder as a
# fresh visit at the layer stock's enters would have reached (the card 2, each folder one more), so stock
# folder_back, run here, climbs a folder at a time to the card and out of the page, or to the storage
# selector with a drive. The path is copied as it is: percent signs, format directives and UTF-8 stay literal.

GO=[*SONG_MENU,'前往文件夹']
DEEP='/mnt/mmc/Music/100% %s%n/Ünï cødé'
for otg in (0,1):
    m=GoMachine(DEEP+'/song.flac',['/mnt/mmc/Music/100% %s%n',DEEP],otg=otg); m.push(5,6)  # a visit left deep
    assert m.menu()==('Row 0',GO); m.go(); assert m.opened[-1][0]=='folder_page'
    assert (m.text(syms['g_folder_path']),m.u.mem_read(syms['g_folder_layer'],1)[0])==(DEEP,5) and not any(m.stacks.values())
    for want,layer in (('/mnt/mmc/Music/100% %s%n',4),('/mnt/mmc/Music',3),('/mnt/mmc',2)): assert RootMachine.back(m)[:3]==(1,want,layer)
    assert RootMachine.back(m)[::2]==((1,1) if otg else (0,0)); passed()  # the selector, or out of the page
# A song at the card's root opens the card itself.
m=GoMachine('/mnt/mmc/x.mp3',['/mnt/mmc']); assert m.menu()[1]==GO; m.go()
assert (m.text(syms['g_folder_path']),m.u.mem_read(syms['g_folder_layer'],1)[0])==('/mnt/mmc',2) and RootMachine.back(m)[::2]==(0,0); passed()
# No row off the card, for a path stock's enters never build, a gone folder, one too long for g_folder_path
# (1023 bytes fit), or while a folder_page is open.
for path,dirs,row in (('/mnt/mmcx/a.mp3',['/mnt/mmcx'],0),('/mnt/mmc/A/../a.mp3',['/mnt/mmc/A/..'],0),
        ('/mnt/mmc/./a.mp3',['/mnt/mmc/.'],0),('/mnt/mmc/A//a.mp3',['/mnt/mmc/A/'],0),('/mnt/mmc/Gone/a.mp3',[],0),
        ('/mnt/mmc/'+'d'*1015+'/a.mp3',['/mnt/mmc/'+'d'*1015],0),('/mnt/mmc/'+'d'*1014+'/a.mp3',['/mnt/mmc/'+'d'*1014],1),
        ('/mnt/mmc/..a/.b/a.mp3',['/mnt/mmc/..a/.b'],1)):
    assert GoMachine(path,dirs).menu()[1]==(GO if row else SONG_MENU),path; passed()
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.open_pages.add('folder_page'); assert m.menu()[1]==SONG_MENU; passed()
# Gone between the menu and the pick: a toast, no page, and the next Folder visit opens as before.
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.menu(); m.dirs.clear(); m.go()
assert m.toasts[-1][3]=='找不到文件夹' and m.opened[-1][0]!='folder_page' and m.open()==('/mnt/mmc',2); passed()
# The card gone as the page opens: stock's page, the pending folder dropped.
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.menu(); m.missing=True; m.go()
assert m.opened[-1][0]=='folder_page' and m.u.mem_read(syms['g_folder_layer'],1)[0]==1; m.missing=False
assert m.open()==('/mnt/mmc',2); passed()
# Now Playing: the playing song's row sits after Go to Artist, before the play modes.
m=playing_menu(); m.dirs.add('/mnt/mmc/Music'); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_PATH'],m.string('/mnt/mmc/Music/B'))
m.press(6000); assert m.hold()==11 and m.labels()==SONG_MENU[:2]+['查看队列']+SONG_MENU[2:]+['前往文件夹','随机播放','重复播放','分组：专辑']
passed()

# Photos (patch/photos.c): the helpers first. Names: .jpg, .jpeg and .png, case aside, not hidden.
m=Machine(); m.mock('memcmp@GLIBC_2.0',prefix='cmp:')
def ph_call(name,*args): return m.call(address=ps[name],args=(*args,0,0,0,0)[:4],gap=0,count=5_000_000)
assert [ph_call('photo_file',m.string(n)) for n in ('a.JPG','b.jpeg','c.Png','d.gif','._e.jpg','.f.jpg','g','h.jpg.txt')]==[1,1,1,0,0,0,0,0]
passed()
# EXIF orientation: either byte order, after another segment, within the bytes read; else 1.

def orientation(data): a=m.alloc(len(data)+4); m.u.mem_write(a,data); return ph_call('photo_orientation',a,len(data))
assert [orientation(d) for d in (exif(6),exif(8,le=False),exif(3,pad=JFIF),exif(9),exif(6)[:30],b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff\xda')]==[6,8,3,1,1,1,1]
passed()
# Orientation: each of the eight against its EXIF meaning (PIL's exif_transpose), the channels from
# the format's byte order, alpha over black, and a crop to the box.
W,H=3,2
def stored(x,y): return (10*x+y+1,100+x,200+y,255)
def shown(o,x,y):  # the stored pixel shown at x, y
    return {1:(x,y),2:(W-1-x,y),3:(W-1-x,H-1-y),4:(x,H-1-y),5:(y,x),6:(y,H-1-x),7:(W-1-y,H-1-x),8:(W-1-y,x)}[o]
src=m.alloc(64); at=m.alloc(4); dst=m.alloc(256); size=m.alloc(8)
for o in range(1,9):
    for y in range(H):
        for x in range(W): r,g,b,a=stored(x,y); m.u.mem_write(src+y*16+x*4,bytes([b,g,r,a]))  # BGRA, format 3
    m.u.mem_write(at,bytes([2,1,0,3]))
    m.call(address=ps['photo_orient'],args=(src,W,H,16),stack=(at,o,dst,4,size),gap=0,count=5_000_000)
    sw,sh=(H,W) if o>=5 else (W,H)
    assert (m.get(size),m.get(size+4))==(sw,sh),o
    for y in range(sh):
        for x in range(sw):
            r,g,b,_=stored(*shown(o,x,y)); assert m.get(dst+(y*4+x)*4)==0xff000000|b<<16|g<<8|r,(o,x,y)
m.u.mem_write(src,bytes([200,100,50,128]))  # half transparent, over black
m.call(address=ps['photo_orient'],args=(src,W,H,16),stack=(at,1,dst,2,size),gap=0,count=5_000_000)
assert (m.get(size),m.get(size+4))==(2,2) and m.get(dst)==0xff000000|(200*128//255)<<16|(100*128//255)<<8|50*128//255
passed()
# Placing: fitted into the box, centred, never enlarged.
r=m.alloc(16)
for (w,h,bw,bh),want in (((100,50,85,72),(0,15,85,42)),((40,30,85,72),(22,21,40,30)),((50,100,85,72),(24,0,36,72)),
                         ((281,375,375,290),(79,0,217,290))):
    m.call(address=ps['photo_place'],args=(w,h,0,0),stack=(bw,bh,r),gap=0); assert tuple(signed(m.get(r+4*i)) for i in range(4))==want,(w,h,want)
passed()
