"""An artist's albums: the grid, its membership, the folder-cover path and Return after touch."""
from q2emu import *

# Artist albums: Grid shows the artist page's hidden stock toolbar (its multi-select) on either
# tab, the tabs moved down under it; other modes, pages or a toolbar already up stay as they are.
for on,tab,page,up in ((0,1,'artistinfo_page',0),(0,0,'artistinfo_page',0),(1,1,'artistinfo_page',0),
                       (2,1,'artistinfo_page',0),(0,1,'localclass_page',0),(0,1,'artistinfo_page',1)):
    m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=1); m.handed=[]; m.found=[]
    m.config['ARTISTCF']=str(on); m.artist_page(page)
    album=m.node('list_view','list_view_album'); pages=m.node('pages','pages_artistinfo',[album])
    bar=m.node('tab_control','tab_control',[pages]); nav=m.node('view','view_navbar',visible=up)
    m.nodes[nav]['enable']=up; m.nodes[m.top]['children']+=[nav,bar]
    geo=lambda w:[m.get(w+O[o]) for o in ('W_X','W_Y','W_W','W_H')]
    for w,g in ((nav,(0,0,375,50)),(bar,(0,0,375,290)),(pages,(0,40,375,250)),(album,(0,0,375,250))):
        for o,v in zip(('W_X','W_Y','W_W','W_H'),g): m.word(w+O[o],v)
    m.call(address=HOOKS['load_localartist_list'][0],args=(tab,0,0,0),gap=0)
    want=not on and page=='artistinfo_page' and not up
    assert bool(m.nodes[nav]['visible'])==bool(want or up) and bool(m.nodes[nav]['enable'])==bool(want or up),(on,tab,page,up)
    assert geo(bar)==([0,50,375,240] if want else [0,0,375,290]) and geo(album)[3]==(200 if want else 250),(on,tab,page,up)
    assert m.nodes[pages].get('self_layout')==(('0','40','100%','200') if want else None),(on,tab,page,up)
    passed()
# Same-named albums by other artists stay out, in both artist modes. Membership follows
# the artist's songs, so a guest artist and multiple albums of one name work too.
for mode in (0,1):
    m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=1); m.handed=[]; m.config['ARTISTCF']='1'
    m.word(syms['artist_type'],mode)
    m.u.mem_write(syms['g_local_classinfo_save']+0x10d,b'Artist\0')
    m.word(m.row(0)+O['REC_ARTIST'],m.string('Guest singer'))  # selected album artist comes from page context
    m.split=[m.song(name) for name in ('Selected','Other','Guest')]
    for r in m.split:
        m.word(r+O['REC_ALBUM_ARTIST'],m.get(r+O['REC_NAME']))
    m.found=[m.split[0],m.split[2],m.split[0]]  # duplicates must not create extra cards
    staging=m.names(m.get(syms['tools_pdeq_directory'])); m.artist_page('artistinfo_page')
    assert m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)==1
    assert m.query[:2]==(('getMusicByAlbumArtist' if mode else 'getMusicBySonger'),'Artist')
    assert m.names(m.get(syms['tools_pdeq_directory']))==staging
    m.advance(0)
    assert m.covers==['Selected','Guest'] and m.handed==[2],(mode,m.covers)
    assert m.names(m.get(syms['p_deque_showlist']))==['Row 0']  # the Library grid stays intact
    passed()
# No albums in the selected artist query leaves the grid rather than opening an empty Coverflow.
m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=1); m.handed=[]; m.config['ARTISTCF']='1'; m.found=[]
m.artist_page('artistinfo_page'); m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)
m.advance(0)
assert m.handed==[0] and m.shown(); passed()  # coverflow_artist rejects the empty list (test/coverflow.py)
# A local art job that finishes after a list rebuild must not mark the new row ready.
# Run the worker's real completion call site, including its stack-buffer argument, then stock's
# readiness check (used by the renderer and by the request queue to skip completed covers).

m=CoverQueueMachine()
def cover_requests(start,big):
    name='local_albumcover_task' if big else 'local_albumsmallcover_task'
    m.call(address=syms[name],args=(start,9,0,0),gap=0)
    return [row for row,kind,size in m.pending if kind==0 and size==big]
def cover_ready(row,big):
    return m.call(address=syms['check_albumcover_flag'],args=(row,big,0,0),gap=0)
def cover_done(row,big,key):
    sp=0x7000d000
    m.u.mem_write(sp+0x1688,key.encode()+b'\0')
    m.word(sp+0x1828,row); m.word(sp+0x1830,big); m.word(sp+0x18,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_SP,sp); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
    m.u.emu_start(0x4f84b8,0x4f84c8,count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC)==0x4f84c8
for visit in range(5):
    keys=[f'Artist {visit} album {i}' for i in range(12)]
    covers=[]
    for key in keys:
        c=m.alloc(0x60); m.word(c+4,m.string(key)); covers.append(c)
    m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
    if visit==0:
        fixed=bytes(m.u.mem_read(0x4f84bc,8))
        m.u.mem_write(0x4f84bc,struct.pack('<II',0x8f99b760,0x0411ff44))
        m.u.ctl_remove_cache(0x4f84b8,0x4f84c8)
        cover_done(11,1,'previous artist')
        assert cover_ready(11,1)==1  # stock falsely completes the last album
        assert cover_requests(9,1)==[9,10]  # scrolling cannot request its missing art
        m.byte(m.items(m.get(syms['pdeq_albumcoverlist']))[11]+1,0)
        m.u.mem_write(0x4f84bc,fixed)
        m.u.ctl_remove_cache(0x4f84b8,0x4f84c8)
    for row in (0,8,9,11):  # first screen, scroll boundary and last album
        for big in (0,1):
            cover_done(row,big,f'Artist {visit-1} album {row}')
            assert cover_ready(row,big)==0, (visit,row,big)
            assert row in cover_requests(row//9*9,big), (visit,row,big)
            cover_done(row,big,keys[row])
            assert cover_ready(row,big)==1, (visit,row,big)
            assert row not in cover_requests(row//9*9,big), (visit,row,big)
    for row in (-1,12): cover_done(row,1,'gone')
# The folder-cover path (0x4f94fc) reaches the same completion call with its own copied key, not
# whatever its last call left in a2.
def folder_done(row,key,a2):
    sp=0x7000d000
    m.u.mem_write(sp+0x1688,key.encode()+b'\0'); m.word(sp+0x1828,row); m.word(sp+0x18,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_SP,sp); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_S2,1); m.u.reg_write(UC_MIPS_REG_A2,m.string(a2))
    m.u.emu_start(0x4f94f4,0x4f84c8,count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC)==0x4f84c8
for key,a2,ready in ((keys[3],'left over',1),('previous folder',keys[3],0)):
    m.byte(m.items(m.get(syms['pdeq_albumcoverlist']))[3],0)
    folder_done(3,key,a2); assert cover_ready(3,0)==ready, (key,a2)
passed()
# Scrolling never leaves the shared cover worker paused (albumcover_taskstate 2): stock resumed it only
# when scroll end saw a new first row, so a wheel tick or a return to a scrolled grid stopped every
# grid's art. An artist's albums scroll end takes rows as 160 px, three a row, not 256.

m=ScrollEndMachine(); m.mock('widget_lookup','widget_get_prop_int')
for pause in (0x4ace30,0x4a3910):  # an artist's albums, Library Albums
    m.word(syms['albumcover_taskstate'],1)
    m.call(address=pause,args=(0,0,0,0),gap=0); assert m.get(syms['albumcover_taskstate'])==1, hex(pause)
covers=[]
for i in range(30):
    c=m.alloc(0x60); m.word(c+4,m.string(f'album {i}')); covers.append(c)
m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
view=m.node('scroll_view','scroll_view_album',yoffset=800); win=m.node('window','artistinfo_page',[view])
m.word(0xa3b5ac,1); m.word(0xa3b5b0,0)  # the Albums tab; its last requested first row
for tick in range(2):  # the second, at the same offset, requests nothing and leaves the worker running
    m.pending.clear(); m.call(address=0x4b0a24,args=(win,0,0,0),gap=0)
    assert [row for row,kind,size in m.pending]==([] if tick else list(range(15,24))), m.pending
    assert m.get(0xa3b5b0)==15 and m.get(syms['albumcover_taskstate'])==1
passed()
# A row opens its name's songs (0xff10, getMusicByAlbum): only its own album's stay.
m=ClassMachine(page='album_page',cls=0xf003,rows=2); m.word(m.row(1)+O['REC_PATH'],m.string('/q/Row 1'))
assert m.call(address=HOOKS['load_album_detaillist'][0],args=(0xf003,m.song('Album'),0,0),gap=0)==1
assert m.names(m.get(syms['p_deque_showlist']))==['Row 0'] and ('stock_localclass',0xff10,0,0) in m.calls; passed()

# Coverflow (docs/internals.md): the Home card, the runtime coverflow_page over a stock slide_menu,
# the tracks query and handoff. The art thread itself runs on the host (test/coverflow.py).

cards=[c[2]['name'] for c in decode((B/'ui'/HOME_PAGE).read_bytes())[3][1][3][0][3]]  # list_view_home's scroll_view's rows
assert len(cards)==7 and cards[2]=='btn_coverflow', cards

assert struct.unpack_from('<I',demo,fileoff(demo,home_hook[0]))[0]==j(payload_syms['coverflow_home'])
passed()

# Return is a button too: after a touch, the page it goes back to shows its selection again.
m=Machine(); w,es=m.page_list(3); m.paint(w); m.touch(); m.paint(w,gap=0); assert not m.drawn()
assert m.call(O['KEY_RETURN'])==0; m.advance(0); m.paint(w,gap=0); assert m.drawn(); passed()

# Page slides (navigation.c slides()): the page under a sliding top window draws the row it holds; no load, recall or scroll.

m,w,w2=under_slide(); m.paint(w,gap=0)
assert m.drawn() and m.sel()[1]==48-m.get(w+O['SCROLL_Y']) and m.selected(w)==1 and not m.moved(); passed()
m,w,w2=under_slide(''); m.paint(w,gap=0); assert not m.drawn(); passed()  # under a page that does not slide
# Touch mode hides it there too, until a Return by button, which shows the page behind its row
# while the closing page keeps hiding its own.
m,w,w2=under_slide(); m.paint(w2); m.touch(); m.paint(w,gap=0); assert not m.drawn()
assert m.call(O['KEY_RETURN'])==0; m.paint(w,gap=0); assert m.drawn()
m.paint(w2,gap=0); assert not m.drawn(); passed()
