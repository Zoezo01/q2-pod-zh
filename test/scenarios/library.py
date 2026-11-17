"""The Library: song dates, Artists rows, sorts, the boot resume, refusals and empty class lists."""
from q2emu import *

# A song's date (toolsGetFileCreateTime) is the card's creation stamp from statx, which reads never
# move (#170, #188); stock's own date stands in without one: a failed call, no btime or a zero stamp.
for btime,mask,error,expect in ((1700000000,0x800,0,1700000000),(1700000000,0x7ff,0,0),(0,0x800,0,0),(1700000000,0x800,2,0)):
    m=Machine(); m.handlers[HOOKS['toolsGetFileCreateTime'][0]+12]='stock_created'; seen=[]
    def statx(u,interrupt,unused):
        a,b,c,d=[u.reg_read(r) for r in REGS]; buf=m.get(u.reg_read(UC_MIPS_REG_SP)+16)  # o32's fifth argument
        seen.append((u.reg_read(UC_MIPS_REG_V0),a,m.text(b),c,d))
        if error: u.reg_write(UC_MIPS_REG_A3,1); u.reg_write(UC_MIPS_REG_V0,error); return
        u.mem_write(buf,bytes(256)); u.mem_write(buf,struct.pack('<I',mask)); u.mem_write(buf+80,struct.pack('<Q',btime))  # the kernel writes all 256
        u.reg_write(UC_MIPS_REG_A3,0); u.reg_write(UC_MIPS_REG_V0,0)
    m.u.hook_add(UC_HOOK_INTR,statx); path=m.string('/m/a.flac')
    assert m.call(address=payload_syms['ringnav_file_created'],args=(path,0,0,0),gap=0)==expect
    assert seen==[(4366,0xffffff9c,'/m/a.flac',0,0x800)] and [c[1] for c in m.calls if c[0]=='stock_created']==([] if expect else [path]); passed()
# Library Artists rows hide the head picture (CLASS_ICON); Composers and Genres, sharing the rebound
# rows, show theirs again through stock's setter.
from build import CLASS_ICON
assert struct.unpack_from('<I',demo,fileoff(demo,CLASS_ICON))[0]==jal(payload_syms['ringnav_class_icon'])
m=Machine(); m.mock('class_icon'); icon=m.node('image','img_icon'); row=m.node('view','',[icon])
for cls,shown in ((0xf004,0),(0xf006,1),(0xf005,1),(0xf004,0)):
    m.word(syms['g_class_type'],cls); m.calls=[]
    m.call(address=payload_syms['ringnav_class_icon'],args=(row,3,0,0),gap=0)
    assert m.nodes[icon]['visible']==shown and [c[1:3] for c in m.calls if c[0]=='class_icon']==([(row,3)] if shown else [])
passed()
# A song with no artist tag (an empty REC_ARTIST): Classic's second line is empty, under Row Art On and Off, and a rebound
# row loses the last song's artist; a record that says Unknown reads so. Classic keeps stock's "Unknown Artist".
from build import SONG_UNKNOWN_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,SONG_UNKNOWN_CALL))[0]==jal(payload_syms['ringnav_song_unknown'])

for boot,want in ((lambda k:classic_light_boot(k,'1'),''),(lambda k:classic_light_boot(k,'0'),''),(lambda k:k.config.update(THEME='1'),'Unknown Artist')):
    m=SongBindMachine(); boot(m); m.formats=[]; m.handlers[syms['getFormatString']]='b:format'
    m.mock('deque_at','tk_snprintf','widget_set_text_utf8','widget_use_style','file_is_playing','toolsTrimLeft','toolsTrimRight')
    title,meta,badge=(m.node('hscroll_label','scrlabel_name'),m.node('hscroll_label','scrlabel_artist'),m.node('image','img_pic'))
    view_info=m.node('view','view_info',[title,meta,badge]); button=m.node('button','',[view_info]); m.row_record=m.alloc(0x80)
    m.mock('list_get_img_pic'); m.word(m.row_record+8,m.string('Song')); m.word(m.row_record+12,m.string('/mnt/mmc/x.flac')); m.word(m.row_record+0x48,1)
    for artist,text in (('Mac Quayle','Mac Quayle'),('',want),('Unknown','Unknown'),('',want)):
        m.word(m.row_record+0x18,m.string(artist))
        rc=m.call(address=0x4ab208,args=(button,0,0,0)); assert rc==0,(rc,artist,[c[:2] for c in m.calls][-6:])
        assert m.nodes[meta].get('text')==text,(artist,m.nodes[meta].get('text'),want)
    assert m.formats==(['Unknown Artist']*2 if want else []),m.formats
passed()
# Library sorts: both stock name comparators, on the patched trim calls, skip a leading article.
from build import SORT_TRIMS
for a in SORT_TRIMS: assert struct.unpack_from('<I',demo,fileoff(demo,a))[0]==jal(payload_syms['ringnav_sort_key'])

m=SortMachine()
def sort_rec(name):
    r=m.alloc(0x60); m.word(r+0x10,m.string(name)); m.word(r+8,m.string(name+'.flac')); return r
def ordered(names,cmp):
    out=m.alloc(4)
    def less(x,y):  # the comparator stores x < y
        m.call(address=cmp,args=(x,y,out,0),gap=0,count=2_000_000); return m.get(out)
    rs={sort_rec(n):n for n in names}
    return [rs[r] for r in sorted(rs,key=cmp_to_key(lambda x,y: -1 if less(x,y) else 1 if less(y,x) else 0))]
names=['Daft Punk','The Cure','Coldplay','Them Crooked Vultures','A Tribe Called Quest','Anthrax','The','an Orchestra','Toto']
for cmp in (0x5cd188,0x5cc870):
    assert ordered(names,cmp)==['Anthrax','Coldplay','The Cure','Daft Punk','an Orchestra','The','Them Crooked Vultures','Toto','A Tribe Called Quest'],ordered(names,cmp)
    passed()
key=lambda t: (m.u.mem_write(0x1100000,t.encode()+b'\0'), m.call(address=payload_syms['ringnav_sort_key'],args=(0x1100000,0,0,0),gap=0), m.text(0x1100000))[2]
assert [key(t) for t in ('  The Cure','THE CURE','The ','The  Cure','A','An ','Ant','周杰伦','The 周杰伦')]==['Cure','CURE','The ','The  Cure','A','An ','Ant','周杰伦','周杰伦']; passed()
# iPod boots to Home: home_page_init's memory-play resume, run from its stock context build, starts
# the restored queue paused (mode 3) through player_start as playing_page_init would, and opens no
# page; a 0xff class starts nothing, as the page's init. Car mode (mode 2) opens Now Playing as stock.
boot=payload_syms['ringnav_boot']
assert struct.unpack_from('<I',demo,fileoff(demo,0x534cf8))[0]==jal(boot)
for car,cls,want in ((0,1,[(0x5550,4,1,3)]),(0,0xff,[]),(1,0xf003,[])):
    m=Machine(); m.word(syms['g_memory_info'],cls); m.byte(syms['g_carmode'],car)
    m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0); m.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
    m.u.reg_write(UC_MIPS_REG_S0,0x5550); m.u.reg_write(UC_MIPS_REG_S1,4)
    # Headphones Required on: stock's test ahead of the resume (0x534c44) skipped it, whatever was connected; it
    # falls through to Memory Playback's own test now (0x534c54), and the context's build runs as with it off.
    m.byte(syms['g_block_withoutphone_flag'],1); m.u.emu_start(0x534c44,0x534c58,count=10)
    assert 0x534c50<=m.u.reg_read(UC_MIPS_REG_PC)<0x534c5c  # not 0x534d40, past the resume
    m.u.emu_start(0x534cc4,0x534d00,count=1000)
    nav=[c for c in m.calls if c[0]=='navigator_to_with_context']
    assert m.started==want
    if car:
        assert len(nav)==1 and m.text(nav[0][1])=='playing_page' and nav[0][2]==0x7000f018
        assert [m.get(0x7000f018+4*i) for i in range(4)]==[0x5550,4,0xf003,2]
    else: assert not nav
    passed()
# Refusals leave the queue alone: a stream queue, or a row whose record changed under the menu.
m=QueueMachine(); m.word(O['MCL_TYPE'],2); assert m.run(0)=='Queue unchanged' and m.names()==['A','B','C']
m=QueueMachine(); m.press(1); m.hold(); m.release(); m.word(m.row(0)+O['REC_NAME'],m.string('other'))
m.pick(0); m.advance(0); assert m.toasts[-1][3]=='Queue unchanged' and m.names()==['A','B','C']; passed()
# Unsupported pages and states keep the stock long key: no menu, and the release is stock.
for setup,toggles in ((lambda m:m.byte(syms['g_lockscreen_pageflag'],1),1),(lambda m:m.byte(syms['g_backlight_status'],0),1),
        (lambda m:m.byte(syms['g_navbar_status'],1),1),
        (lambda m:m.word(m.surface+O['TABLE_ROWS'],7),1),(lambda m:m.word(m.row(0)+O['REC_TYPE'],4),1),
        (lambda m:setattr(m,'airplay',2),0),
        (lambda m:setattr(m,'top',m.node('window','home_page',[m.node()])),1)):
    m=QueueMachine(); setup(m); m.press(3)
    assert m.hold()==0 and len(m.stack)==1 and m.release()==toggles
    passed()
# Unknown rows (docs/internals.md#unknown-rows): stock's list (here the showlist as built, its size
# returned) keeps its trailing Unknown row (id -1) only when the query its press runs finds a song;
# the staging deque is left cleared. Other rows and classes stay stock.

def unknown(cls,found,last=-1,artist=0,rows=3):
    m=ClassMachine(page='localclass_page',cls=0xf001,rows=rows); m.found=[m.song('T1')]*found; m.query=None
    m.word(syms['artist_type'],artist); m.word(syms['g_artist_type'],1-artist)
    if rows: m.word(m.row(rows-1)+O['REC_ID'],last)
    n=m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
    assert m.get(payload_syms['library_music']) == 0
    shown=m.names(m.get(syms['p_deque_showlist']))
    # Artists follow the artist page's own switch (artist_type), which stock never copied to g_artist_type
    assert n==len(shown) and m.calls[0][:2]==('stock_localclass',cls) and m.get(syms['g_artist_type'])==artist
    return shown,m.query,m.items(m.get(syms['tools_pdeq_directory']))
for cls,artist,query in ((0xf003,0,'getMusicByAlbum'),(0xf004,0,'getMusicBySonger'),(0xf004,1,'getMusicByAlbumArtist'),
                         (0xf005,0,'getMusicByComposer'),(0xf006,0,'getMusicByGenre')):
    shown,q,staging=unknown(cls,0,artist=artist)
    assert shown==['Row 0','Row 1'] and q[:2]==(query,None) and not staging, (cls,artist)
    shown,q,staging=unknown(cls,1,artist=artist)
    assert shown==['Row 0','Row 1','Row 2'] and q[:2]==(query,None) and not staging, (cls,artist); passed()
for cls,last,rows in ((0xf004,1,3),(0xff01,-1,3),(0xff10,-1,3),(0xff11,-1,3),(0xf007,-1,3),(0xf004,-1,0)):
    shown,q,_=unknown(cls,0,last=last,rows=rows)
    assert len(shown)==rows and q is None, (cls,last,rows); passed()
# The check needs one song, not all of them: its query (stock's where tag="", through the Library
# wrapper, its SQL run here) reads the first at most, and the row stays exactly when a music song
# lacks the tag. The Queue menu's query on that row still reads every one.

for cls,artist in ((0xf003,0),(0xf004,0),(0xf004,1),(0xf005,0),(0xf006,0)):
    for untagged in (0,1,2):
        m=FirstMachine(untagged,page='localclass_page',cls=0xf001,rows=3); m.word(m.row(2)+O['REC_ID'],-1)
        m.word(syms['artist_type'],artist)
        assert m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)==2+(untagged>0)
        assert m.read==[min(untagged,1)],(cls,artist,untagged,m.read)
    passed()
m=FirstMachine(2,page='localclass_page',cls=0xf004,rows=3); m.word(m.row(2)+O['REC_ID'],-1)  # the flag ends with its check
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf004,0,0,0),gap=0)==3
m.word(m.row(0)+O['REC_ID'],-1); m.run(1)
assert m.read==[1,2],m.read; passed()
# An empty class list: its page, once the window manager marks it opened (stage 3, after the open
# animation), is force-closed from the next loop pass, then the toast says so. Until then it is
# polled (EMPTY_POLL_MS) until it opens or stops being on top, then the toast alone; a page already left, or
# another class's, keeps the toast only.
def empty(cls,page,stage=O['WINDOW_STAGE_OPENED'],leave=False,shown=None):
    m=ClassMachine(page=page,cls=cls,rows=0); m.found=[]; m.query=None
    assert m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)==0 and len(m.timers)==1
    m.nodes[m.top]['stage']=stage
    if shown is not None: m.word(syms['g_class_type'],shown)
    if leave: m.top=m.node('window','localmusic_page')
    m.calls=[]; m.advance(0)
    return m
closes=lambda m: [c[:3] for c in m.calls if c[0]=='window_manager_close_window_force']
for cls,page in ((0xf003,'album_page'),(0xf003,'localclass_page'),(0xf004,'localclass_page'),(0xf005,'localclass_page'),(0xf006,'localclass_page')):
    m=empty(cls,page); top=m.stack[-1]
    assert closes(m)==[('window_manager_close_window_force',m.wm,top)]
    assert m.toasts==[('dialog/msginfo_dialog',1,2000,{0xf003:'No albums',0xf004:'No artists',0xf005:'No composers',0xf006:'No genres'}[cls])] and not m.timers
    passed()
m=empty(0xf003,'album_page',stage=2)  # created, its open animation still running
assert not closes(m) and not m.toasts and len(m.timers)==1
m.advance(O['EMPTY_POLL_MS']*5); assert not m.toasts and len(m.timers)==1
m.nodes[m.top]['stage']=O['WINDOW_STAGE_OPENED']; m.calls=[]; m.advance(O['EMPTY_POLL_MS'])
assert closes(m) and m.toasts and not m.timers; passed()
m=empty(0xf003,'album_page',stage=2); m.advance(O['EMPTY_POLL_MS']*100); assert not m.toasts and len(m.timers)==1  # slow to open: still waits
m.top=m.node('window','localmusic_page'); m.calls=[]; m.advance(O['EMPTY_POLL_MS'])
assert not closes(m) and m.toasts and not m.timers; passed()  # left before it opened: the toast alone
m=empty(0xf003,'album_page',leave=True)
assert not closes(m) and m.toasts and not m.timers; passed()
m=empty(0xf004,'localclass_page',shown=0xf006)  # Genres' page on top by now: not Artists' to close
assert not closes(m) and m.toasts==[('dialog/msginfo_dialog',1,2000,'No artists')]; passed()
# Library -> Songs reads straight into the showlist: stock's load holds its copy and the staging deque
# together, the library twice. The staging pointer comes back, its deque cleared as stock leaves it.

m=SongsMachine(page='localclass_page',cls=0xf001,rows=3); m.found=[m.song('T1'),m.song('T2')]; m.word(syms['g_sort_flag'],2)
staging=m.get(syms['tools_pdeq_directory']); m.deqs[staging][1]=[m.song('stale')]; m.calls=[]
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf001,0,0,0),gap=0)==2
assert m.names(m.get(syms['p_deque_showlist']))==['T1','T2'] and m.get(syms['tools_pdeq_directory'])==staging and not m.items(staging)
assert ('getAllMusic',2,1) in m.calls and ('title',0xf001,['T1','T2']) in m.calls
assert not [c for c in m.calls if c[0] in ('stock_localclass','deque_assign','deque_init_copy')]; passed()
# Songs' cover tasks: stock's albumcoverinfo_init at the page's open (0x4acda8) and both branches of
# its return (0x4ab788 reloads, 0x4ab830 keeps the list) malloc'd each row's name and path for a task
# that the cover list's copy (0x5c639c) duplicates, and never freed them. With malloc and free
# counted, and the list's elements copied and destroyed (0x5c5c48) as libcstl does, what stays
# allocated after each is what the list holds.

m=SongCoversMachine(); rows=[m.song(f'Row {i}') for i in range(3)]
for i in (0,2): m.word(rows[i]+O['REC_ALBUM'],m.string(f'Album {i}'))
m.word(rows[1]+O['REC_ALBUM'],0)  # no name: no copy
m.word(syms['p_deque_showlist'],m.deque(rows))
for start,end in ((0x4acda4,0x4acdb0),(0x4ab780,0x4ab790),(0x4ab828,0x4ab838)):
    m.u.reg_write(UC_MIPS_REG_SP,0x7000f000); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_T9,syms['albumcoverinfo_init'])
    m.u.emu_start(start,end,count=m.budget); assert m.u.reg_read(UC_MIPS_REG_PC)==end
    covers=m.items(m.get(syms['pdeq_albumcoverlist']))
    assert [(m.get(c+4) and m.text(m.get(c+4)),m.text(m.get(c+8))) for c in covers]==[
        ('Album 0','/p/Row 0'),(0,'/p/Row 1'),('Album 2','/p/Row 2')]
    held={m.get(c+off) for c in covers for off in (4,8)}-{0}
    assert m.live==held, (hex(end-8),len(m.live-held),'blocks nothing holds')
passed()
# Folder view's foldercoverinfo_init builds the same task for a folder's or a song's row, at its own push.
m=SongCoversMachine(); m.handlers.pop(syms['foldercoverinfo_init'],None)
rows=m.deque([m.song('Sub',kind=4),m.song('Song'),m.song('Skipped',kind=1)])
m.call(address=syms['foldercoverinfo_init'],args=(rows,0,0,0),gap=0)
covers=m.items(m.get(syms['pdeq_albumcoverlist']))
assert [(m.text(m.get(c+4)),m.text(m.get(c+8))) for c in covers]==[('Sub','/p/Sub'),('Song','/p/Song')]
assert m.live=={m.get(c+off) for c in covers for off in (4,8)},(len(m.live),'blocks nothing holds'); passed()
# Every Library class and detail load scopes SQL, including empty results and failures, and
# restores an already-scoped caller. The trampoline supplies stock's displayed copies.

for cls in (*range(0xf001,0xf00c),*range(0xff01,0xff15),0xff20):
    for scoped,rows,failed in ((0,1,False),(1,0,False),(1,0,True),(0,0,True)):
        m=ScopedClassMachine(rows=rows); m.failed=failed
        m.word(payload_syms['library_music'],scoped)
        m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
        assert m.get(payload_syms['library_music'])==scoped
