"""Cover Flow's sort, queue menu and caption; tag, genre and CUE text; the scan's hidden folders; lyric follow."""
from q2emu import *

# Display settings' Cover Flow → Sort (navigation.c's pod rows): each press saves the next of Album, Artist,
# Year, Recently Added, Most Played and Random, round again, which Coverflow takes at its next open; the
# next album hold follows the reordered deque.
m=AlbumRateMachine(); m.word(m.albums[2]+O['REC_ARTIST'],m.string('Aardvark'))
sort_name=lambda: m.text(m.call(address=payload_syms['coverflow_sort_name'],args=(0,0,0,0),gap=0)&0xffffffff)
assert sort_name()=='排序：专辑'
for want in ('排序：表演者','排序：年份','排序：最近添加','排序：最多播放','排序：随机','排序：专辑','排序：表演者'):
    m.call(address=payload_syms['coverflow_sort_step'],args=(0,0,0,0),gap=0); assert sort_name()==want
assert [v for k,v in m.files.items() if k.endswith('ringnav-coversort')]==[struct.pack('<i',1)]  # SORT_ARTIST
m.open(); m.slide=m.find('slide_menu'); m.word(m.slide+O['SLIDE_INDEX'],0); m.press(101); assert m.hold()==11
assert m.nodes[m.title]['text']=='Album 2'; m.release(); m.pick(1); m.advance(0)
assert m.query[:2]==('getMusicByAlbum','Album 2'); passed()

# Coverflow's own song deque feeds the shared queue menu, independently of stock browsing state.
for action,want in ((0,['A','T2','B','C']),(1,['A','B','C','T2'])):
    m=CoverflowMachine(cls=O['CLASS_ALBUMS']); m.open(); view=m.tracks()
    m.call(); assert m.selected(view)==1
    m.press(100); assert m.hold()==11 and m.nodes[m.title]['text']=='T2' and m.release()==0
    assert m.labels()==['下一首播放','加入播放队列','加入收藏','前往表演者']
    assert m.hold()==0  # a repeated hold while the dialog is open cannot open another
    m.pick(action); m.pick(action); m.advance(0)
    assert m.names()==want and m.top==m.page and m.selected(view)==1 and not m.playback() and not m.plays
    assert m.names(m.get(syms['p_deque_showlist']))==['Row 0','Row 1','Row 2','Row 3']
    m.press(200); assert m.release()==1; passed()
# Its favourite is batch-select's over Coverflow's own deque, its songs taken as All Songs rows.
m=CoverflowMachine(cls=O['CLASS_ALBUMS']); m.open(); view=m.tracks(); assert m.run(2) is None
assert [c[1:3] for c in m.calls if c[0]=='batch_add_file']==[(0xf001,0xf00a)] and [c[1] for c in m.calls if c[0]=='batch_init_selectrecord']==[2]
assert [c[3] for c in m.calls if c[0]=='batch_add_file'][0]!=m.get(syms['p_deque_showlist']); passed()
m=CoverflowMachine(queue=0,cls=O['CLASS_ALBUMS']); m.open(); m.tracks()
assert m.run(1) is None and m.names()==['T1'] and m.mcl('MCL_TYPE')==0xff10 and not m.playback(); passed()
m=CoverflowMachine(mode=2); m.open(); m.tracks(); m.run(0)
m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0)
assert m.names()==['A','T1','B','C'] and m.mcl('MCL_POS')==1; passed()
# Return cancels without losing the highlighted song. Changed or closed sources refuse.
m=CoverflowMachine(); m.open(); view=m.tracks(); m.call(); m.press(100); m.hold(); m.release()
f,ctx=m.handler(m.top,O['EVT_KEY_UP']); ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==11 and m.top==m.page
assert m.names()==['A','B','C'] and m.selected(view)==1 and not m.playback(); passed()
for invalidate in ('changed','closed','covers'):
    m=CoverflowMachine(); m.open(); m.tracks(); m.press(100); m.hold(); m.release()
    if invalidate=='changed':
        tracks=next(d for d in m.deqs if m.names(d)==['T1','T2'] and m.deqs[d][0]=='stSongInfo')
        m.word(m.items(tracks)[0]+O['REC_NAME'],m.string('changed'))
    elif invalidate=='closed': m.close()
    else: m.key()  # simulate the underlying page returning to covers before the action
    m.pick(0); m.advance(0)
    assert m.toasts[-1][3]=='队列未更改' and m.names()==['A','B','C'] and not m.playback(); passed()
# Album identity survives closing/reopening and library reordering; song positions are per album.
m=CoverflowMachine(); m.open()
m.call(address=syms['slide_menu_set_value'],args=(m.slide,2,0,0),gap=0)
f,ctx=m.handler(m.slide,O['EVT_VALUE_CHANGED']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.close(); m.albums.reverse(); m.rescan(); m.open()
assert m.get(m.slide+O['SLIDE_INDEX'])==0 and 'Album 2' in m.texts(); passed()
view=m.tracks(0); m.call(); assert m.selected(view)==1
m.key(); other=m.tracks(1); assert m.selected(other)==0
m.key(); view=m.tracks(0); assert m.selected(view)==1
m.close(); m.open(); view=m.tracks(0); assert m.selected(view)==1; passed()
# Long metadata and track titles use the looping stock scrolling label, with the complete text.
m=CoverflowMachine(); long='A very long title or artist name ' * 8
m.word(m.albums[0]+O['REC_ALBUM'],m.string(long)); m.word(m.albums[0]+O['REC_ARTIST'],m.string(long))
m.word(m.found[0]+O['REC_NAME'],m.string(long)); m.open(); assert m.texts().count(long)==2
m.tracks(); assert m.texts().count(long)==4  # covers stay alive, hidden behind the tracks
labels=[n for w,n in m.nodes.items() if m.alive(w) and n.get('text')==long]
assert all(n['type']=='hscroll_label' and n['loop']==1 and n['set_hscroll_label_attribute'] for n in labels); passed()
# Wheel only: the covers' slide_menu takes no touch.
m=CoverflowMachine(); m.open()
assert m.nodes[m.slide].get('sensitive')==0; passed()
# Text geometry: album over artist under the covers, over their reflections' tail (CF_TEXT_Y), the album larger and white,
# the artist grey, CF_EDGE from the sides and clear of the rounded glass, as is every other label
# (the track list's title and rows, whose last visible row is lowest).

def clear(x,top,w,px):  # a label's text band, in screen rows (the window starts at y 30)
    return max(corner_inset(30+top),corner_inset(30+top+px))<=x and x+w<=375-max(corner_inset(30+top),corner_inset(30+top+px))
m=CoverflowMachine(); page=m.open(); labels=[w for w in m.nodes[m.get(m.slide+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label']
name,artist=labels
E=O['CF_EDGE']; y=O['CF_TEXT_Y']
assert y>=O['CF_TOP']+160 and y+O['CF_NAME_H']+O['CF_ARTIST_H']<=290
assert [cf_geometry(m,w) for w in labels]==[(E,y,375-2*E,O['CF_NAME_H']),(E,y+O['CF_NAME_H'],375-2*E,O['CF_ARTIST_H'])]
assert m.nodes[name]['style:normal:font_size']==O['CF_NAME_PX']==24 and m.nodes[artist]['style:normal:font_size']==O['CF_ARTIST_PX']==20
assert m.nodes[artist]['style:normal:text_color']==signed(O['CF_GREY']) and 'style:normal:text_color' not in m.nodes[name]
for w,px in ((name,O['CF_NAME_PX']),(artist,O['CF_ARTIST_PX'])):
    x,top,wd,h=cf_geometry(m,w); assert clear(x,top+(h-px)//2,wd,px)
view=m.tracks(); lv=m.get(view+O['W_PARENT']); title=next(w for w in m.nodes[m.get(lv+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label')
assert cf_geometry(m,title)[::2]==(E,375-2*E) and clear(E,14,375-2*E,20)
rows=(290-48)//48
for item in m.nodes[view]['children']:
    label=m.nodes[item]['children'][0]; assert cf_geometry(m,label)[::2]==(E,375-2*E)
assert clear(E,48+(rows-1)*48+14,375-2*E,20)  # the lowest visible row
passed()

# Cover Flow's caption, Classic: album and artist both in its face at 27 px, their middles on screen rows 262 and 286, each box holding the font; the artist the album's ink in
# the light half, #8a8a8a in the dark. A Cyrillic title falls to bold, by its text. Minimal's stays as above.
for half,ink in (('1',0xffffffff),('0',O['CF_GREY_BOLD'])):
    CONFIG['LIGHT']=half; m=classic_light_new(CoverflowMachine); del CONFIG['LIGHT']; m.open(); name,artist=[w for w in m.nodes[m.get(m.slide+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label']
    ng,ag=cf_geometry(m,name),cf_geometry(m,artist)
    assert 26+ng[1]+ng[3]//2==262 and 26+ag[1]+ag[3]//2==286 and ng[3]>=27 and ag[3]>=27
    for w in (name,artist): assert m.nodes[w]['style:normal:font_name']=='classic-light' and m.nodes[w]['style:normal:font_size']==27
    assert m.nodes[artist]['style:normal:text_color']&0xffffffff==ink&0xffffffff,half
passed()

from build import PLAYER_COVER
assert struct.unpack_from('<I',demo,fileoff(demo,PLAYER_COVER[0]))[0]==jal(payload_syms['coverflow_player_cover'])
for sizes in ((2,2,1,1),(1,1,1,1)):
    m=RolloverMachine(sizes); m.setup_ui(); m.artwork(); m.ui()
    paths=[path for path in m.files]
    assert m.text(syms['g_play_id3_info'])==paths[0]
    for index,path in enumerate(paths[1:],1):
        old=m.text(syms['g_play_id3_info']); m.byte(0xa5002d,0) # hciplayer EOF
        # Delay the UI scheduler after stock queue replacement on alternate transitions.
        if index%2:
            m.call(address=syms['mclAutoChange'],args=(0,0,0,0),gap=0)
            assert m.text(syms['g_play_id3_info'])==old
            m.ui()
            assert m.nodes[m.albumlabel]['text']=='' and m.nodes[m.art]['image']=='default_album_home'
        m.tick() # native auto-change, sibling traversal, queue reload, parsing and notification
        assert m.text(syms['g_play_id3_info'])==path
        assert m.text(syms['g_play_id3_info']+O['ID3_ALBUM'])==m.files[path][0]
        assert m.text(syms['g_play_cover_info']+8)==path
        assert m.text(syms['g_lastcover_url'])==old # worker has not completed yet
        m.ui()
        assert m.nodes[m.albumlabel]['text']==m.files[path][0]
        assert m.nodes[m.art]['image']=='default_album_home'
        forks=m.forks; m.artwork()
        assert m.text(syms['g_lastcover_url'])==path
        assert m.u.mem_read(syms['g_playcover_finishflag'],1)==b'\1'
        m.ui()
        # No art: the folder-image call went through coverflow_player_cover to ffmpeg (refused here).
        assert (m.forks>forks)!=m.files[path][1]
        want='file:///tmp/coverpic.jpg' if m.files[path][1] else 'default_album_home'
        assert m.nodes[m.cover]['image']==(want if m.files[path][1] else 'play_defaultcover'),(path,m.nodes[m.cover])
        assert m.nodes[m.songlabel]['text']==path.rsplit('/',1)[1]
        assert m.nodes[m.art]['image']==want
        assert m.nodes[m.poslabel]['text']==f"{m.mcl('MCL_POS')+1} of {len(m.names())}"
        queue=m.items(m.get(syms['mcl_pdeqplaylist']))
        assert all(not m.get(r+O['REC_ALBUM']) for r in queue) # tags came from parsing, not library rows
        assert ('tags',path) in m.trace and ('decoder_start',path) in m.trace
        passed()
    assert [path for event,path in m.trace if event=='directory']==['/mnt/mmc','/mnt/mmc/1','/mnt/mmc','/mnt/mmc/2','/mnt/mmc','/mnt/mmc/3']
    events=[event for event,_ in m.trace]
    for event in ('on_player_autochange','toolsLoadNextDir','player_refresh_playqueue'): assert events.count(event)==3
    assert events.count('mclLoadPlayList')==4 and events.count('mclClearChangeFlag')==len(paths)-1
    # Folder Skip's rollover is a natural change: its load stamps no press (docs/internals.md#wired-start).
    assert 'press_open' not in events
# A CUE track (issue #59): its record's PERFORMER is kept, a missing sheet TITLE takes the file's album.
from build import ID3_PATH
assert struct.unpack_from('<I',demo,fileoff(demo,ID3_PATH[0]))[0]==jal(payload_syms['ringnav_id3_path'])
m=RolloverMachine((1,1)); m.handlers.pop(syms['mclGetCueFlag'])  # stock's, on the queue
r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; id3=syms['g_play_id3_info']
m.word(r+O['REC_CUE_START'],10); m.word(r+O['REC_TITLE'],m.string('Chapter')); m.word(r+O['REC_ARTIST'],m.string('Author'))
m.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
assert [m.text(id3+O[k]) for k in ('ID3_TITLE','ID3_ARTIST','ID3_ALBUM')]==['Chapter','Author','Album 0']
assert m.text(id3)=='/mnt/mmc/0/0.flac'; passed()
# ...and a sheet TITLE without PERFORMER keeps the album and takes the file's artist, not Unknown Artist.
m=RolloverMachine((1,1)); m.handlers.pop(syms['mclGetCueFlag'])
r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_CUE_START'],10); m.word(r+O['REC_TITLE'],m.string('Chapter')); m.word(r+O['REC_ALBUM'],m.string('Book'))
m.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
assert [m.text(id3+O[k]) for k in ('ID3_TITLE','ID3_ARTIST','ID3_ALBUM')]==['Chapter','Artist','Book']; passed()

# Tag text (TAG_TEXT): stock toolsFixId3ErrorCode, run as patched, took a field TagLib found all Latin-1
# for GBK when its Latin-1 bytes went to UTF-8 and back unchanged ("Lo que la música me enseñó" does) and
# showed Chinese; now only when they also read as GBK (ringnav_gbk_tag): a GBK-tagged MP3's Latin-1 frame
# still shows Chinese, Western text its UTF-8 copy. Either is stored through its tidy-up (0x5c84c0) into
# the 256-byte field.
from build import TAG_TEXT, GENRE_COPY, CUE_TEXT, SCAN_DIRS
assert struct.unpack_from('<I',demo,fileoff(demo,TAG_TEXT[0]))[0]==jal(payload_syms['ringnav_gbk_tag'])

for utf8,raw in (('Lo que la música me enseñó',None),('Ciencia Ficción',None),('Mateo Nieto',None),('今','今'.encode()),
                 ('Ölüdeniz',None),('Canção do Mar',None),('Informações',None),('Grüße',None),('周杰伦','周杰伦'.encode('gbk')),('Jay 周杰伦 - 晴天','Jay 周杰伦 - 晴天'.encode('gbk'))):
    m=TagTextMachine(); out=m.alloc(0x100); latin1=raw is None or raw!=utf8.encode()
    raw=utf8.encode('latin-1') if raw is None else raw  # taglib_tag_<field>(tag, 0): its Latin-1 bytes
    copy=raw.decode('latin-1') if latin1 else utf8  # (tag, 1): TagLib's UTF-8, of the bytes as Latin-1
    rawp=m.alloc(len(raw)+4); m.u.mem_write(rawp,raw+b'\0')
    m.call(address=0x5d8950,args=(int(latin1),rawp,m.string(copy),out),stack=(1,),gap=0)
    assert m.raw(out).decode()==utf8,(utf8,m.raw(out)); passed()
# Multi-value genres (GENRE_COPY): TagLib's " / " join must fit style's 128 bytes without touching
# the title at +0x80. Keep whole entries; an oversized first entry alone is cut at a whole character
# within 127 bytes.
assert struct.unpack_from('<I',demo,fileoff(demo,GENRE_COPY[0]))[0]==jal(payload_syms['ringnav_genre_copy'])
genres = ['Sludge Metal', 'Alternative Metal', 'Country', 'Death Metal', 'Gothic Rock',
          'Groove Metal', 'Grunge', 'Metal', 'Rock', 'Stoner Metal', 'Alternative Country',
          'Gothic Country']
joined = ' / '.join(genres)
m = Machine()
for given, want in (('', ''), ('Rock', 'Rock'), ('A / B', 'A / B'),
                    ('x' * 127, 'x' * 127), ('x' * 128, 'x' * 127),
                    ('A / ' + 'x' * 124, 'A'), (joined, ' / '.join(genres[:10])),
                    ('語' * 60, '語' * 42), ('x' + '語' * 60, 'x' + '語' * 42),
                    ('xx' + '語' * 60, 'xx' + '語' * 41), ('😀' * 40, '😀' * 31),
                    ('語' * 42 + ' / ' + 'x' * 9, '語' * 42)):
    out = m.alloc(0x100)
    m.u.mem_write(out, b'\xa5' * 0x100)
    assert m.call(address=payload_syms['ringnav_genre_copy'],
                  args=(out, m.string(given), 0, 0), gap=0) == out
    assert m.raw(out) == want.encode(), (given, m.raw(out))
    assert m.u.mem_read(out + 0x80, 0x80) == b'\xa5' * 0x80
bad = m.alloc(0x100); m.u.mem_write(bad, b'\x80' * 200 + b'\0')  # no character starts: nothing to keep
out = m.alloc(0x100); m.u.mem_write(out, b'\xa5' * 0x100)
assert m.call(address=payload_syms['ringnav_genre_copy'], args=(out, bad, 0, 0), gap=0) == out and m.raw(out) == b''
passed()
# CUE sheets (CUE_TEXT): its seven field conversions go to ringnav_cue_text: UTF-8 stays, GBK goes through
# stock's conversion, any other text is Windows-1252 re-encoded as UTF-8, cut at a whole character to 511 bytes.
for address in CUE_TEXT: assert struct.unpack_from('<I',demo,fileoff(demo,address))[0]==jal(payload_syms['ringnav_cue_text'])
for given,want in ((b'Caf\xc3\xa9 \xc3\x91and\xc3\xba','Café Ñandú'),(b'Caf\xe9 \x93Hi\x94 \x80','Café “Hi” €'),
                   (b'\xc3(','Ã('),(b'a\xe9'*200,'aé'*170+'a'),(b'plain','plain'),(b'\xed\xa0\x80','í\xa0€'),
                   ('周杰伦 晴天'.encode('gbk'),'周杰伦 晴天'),('Lo que la música'.encode('cp1252'),'Lo que la música'),
                   ('Canção'.encode('cp1252'),'Canção'),('Beyoncé’s'.encode('cp1252'),'Beyoncé’s'),('周'.encode('gbk')*171,('ÖÜ'*171)[:255])):
    m=TagTextMachine(); buf=m.alloc(512); m.u.mem_write(buf,given+b'\0')
    assert m.call(address=payload_syms['ringnav_cue_text'],args=(buf,m.string('utf-8'),m.string('gbk'),0),gap=0)==1 and m.raw(buf).decode()==want,(given,m.raw(buf)); passed()
# The library scan (SCAN_DIRS) skips every hidden folder: q2-deezer's .deezer/stream never joins the Library.
assert struct.unpack_from('<I',demo,fileoff(demo,SCAN_DIRS[0]))[0]==jal(payload_syms['ringnav_scan_dir'])
m=Machine(); dot=m.string('.')
assert [m.call(address=payload_syms['ringnav_scan_dir'],args=(m.string(n),dot,0,0),gap=0)!=0 for n in ('.','..','.deezer','.Trashes','Music','Deezer')]==[False,False,False,False,True,True]; passed()

# Now Playing's lyric follow (LYRIC_FOLLOW): stock's scroll_to past the end clamps to a row top, the
# content padded to it, not to 275 - 178 = 97 through a row; repeated it stays; a short page is left.
from build import LYRIC_FOLLOW
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_FOLLOW[0]))[0]==jal(payload_syms['ringnav_lyric_follow'])
m=Machine(); rows=[m.node('label') for _ in range(12)]
for i,(y,h) in enumerate(zip((0,20,70,90,110,135,155,175,195,215,235,255),(20,50,20,20,25)+(20,)*7)):
    m.word(rows[i]+O['W_Y'],y); m.word(rows[i]+O['W_H'],h)
lrc=m.node('scroll_view','scroll_lrc',rows); m.word(lrc+O['W_H'],178); m.word(lrc+O['VIEW_CONTENT_H'],275)
ys=[]
for y in (195,0,20,195): m.call(address=payload_syms['ringnav_lyric_follow'],args=(lrc,0,y,10),gap=0); ys.append(m.get(lrc+O['SCROLL_Y']))
assert ys==[110,0,20,110] and m.get(lrc+O['VIEW_CONTENT_H'])==288,ys
m.word(lrc+O['VIEW_CONTENT_H'],100); m.call(address=payload_syms['ringnav_lyric_follow'],args=(lrc,0,0,10),gap=0)
assert m.get(lrc+O['VIEW_CONTENT_H'])==100 and m.get(lrc+O['SCROLL_Y'])==0; passed()

# A looping label's lull (HSCROLL_LULL) comes to the payload; its holds are tested with the marquee's.
from build import HSCROLL_LULL
assert struct.unpack_from('<I',demo,fileoff(demo,HSCROLL_LULL[0]))[0]==jal(payload_syms['ringnav_hscroll_lull']); passed()

# A refresh's lyric rebuild (LYRIC_ROWS), which stock ends at offset 0, keeps the view where it was.
from build import LYRIC_ROWS
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_ROWS[0]))[0]==jal(payload_syms['ringnav_lyric_rows'])

for y in (110,0):
    m=LyricRowsMachine(); m.lrc=m.node('scroll_view','scroll_lrc'); win=m.node('window','playing_page',[m.lrc])
    m.word(m.lrc+O['SCROLL_Y'],y); m.call(address=payload_syms['ringnav_lyric_rows'],args=(win,),gap=0)
    assert m.get(m.lrc+O['SCROLL_Y'])==y; passed()
