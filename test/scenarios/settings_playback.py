"""Playback settings: stock's rows and ours, Library & Folders, Album View, Artists and embedded art."""
from q2emu import *

# Playback: stock V1.36's thirteen rows less its last two (Volume and Album Display, whose page's rows are here, and
# Folder skip, Library & Folders'), then ours; stock's Max Volume and Default Volume pages open from their rows, and
# Enlarged Album Art flips stock's own byte and key.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
assert len(rows)==8 and icons==['playset_gapless','playset_memplay','playset_balance','playset_balance','playset_folderjump','playset_maxvol','playset_bootvol','playset_covermode']
assert texts()==['Crossfade: Off','Resume Songs: 30 min','Mono Audio','Swap L/R','Library & Folders','playset_maxvol','playset_bootvol','playset_covermode']
assert len(m.nodes[m.settings_view]['children'])==19 and not set(m.entries[11:])&set(m.nodes[m.settings_view]['children'])
assert [click(5),m.opened[-1],click(6),m.opened[-1]]==[[],('playset/maxvol_page',None),[],('playset/bootvol_page',None)]
m.byte(syms['g_bigcovermode'],0); assert click(7)==[(1,'PLAYSET','COVERMODE')] and m.u.mem_read(syms['g_bigcovermode'],1)==b'\1' and switch_image(m,rows[7])=='switch_on'
assert click(7)==[(0,'PLAYSET','COVERMODE')] and m.u.mem_read(syms['g_bigcovermode'],1)==b'\0' and switch_image(m,rows[7])=='switch_off'
# Library & Folders: its row opens stock's page again as the sub-page, stock's Folder skip kept, ours built.
assert click(4)==[] and m.opened[-1]==('playset/playset_page',None) and m.get(payload_syms['playset_sub'])==0
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,sub=1)
assert len(rows)==6 and icons==['playset_covermode','playset_folderjump','playset_covermode','playset_folderjump','playset_foldercover','playset_covermode']
assert texts()==['Year Order: Newest First','Artists: Artist','Artist Album Order: Year','Folder Root: Card','playset_foldercover','Album View: List']
assert [(m.entries+rows).index(c) for c in m.nodes[m.settings_view]['children']]==[14,18,15,13,16,12,17]  # Album View, built last, under Artists
# Album View: Albums as stock's list or its grid, stock's album_modetype and PLAYSET ALBUMTYPE, whose switch is on the
# toolbar the themes hide. With no Albums page open the value and the key alone change (an open one takes stock's own
# click: test/fw test_album_view_runs_stocks_switch_on_an_open_albums_page).
assert click(5)==[(1,'PLAYSET','ALBUMTYPE')] and m.get(syms['album_modetype'])==1 and texts()[5]=='Album View: Grid'
assert click(5)==[(0,'PLAYSET','ALBUMTYPE')] and m.get(syms['album_modetype'])==0 and texts()[5]=='Album View: List'
m.byte(syms['g_foldercover_flag'],0); assert click(4)==[(1,'PLAYSET','FOLDERCOVER')] and m.u.mem_read(syms['g_foldercover_flag'],1)==b'\1' and switch_image(m,rows[4])=='switch_on'
assert click(4)==[(0,'PLAYSET','FOLDERCOVER')] and switch_image(m,rows[4])=='switch_off'
assert click(2)==[(1,'Q2POD','ARTISTAZ')] and texts()[2]=='Artist Album Order: A–Z'
assert click(2)==[(0,'Q2POD','ARTISTAZ')] and texts()[2]=='Artist Album Order: Year'
assert click(0)==[(1,'Q2POD','YEAROLD')] and texts()[0]=='Year Order: Oldest First'
assert click(0)==[(0,'Q2POD','YEAROLD')] and texts()[0]=='Year Order: Newest First'
assert click(1)==[(1,'PLAYSET','ARTISTTYPE')] and m.get(syms['artist_type'])==1 and texts()[1]=='Artists: Album Artist'
assert click(1)==[(0,'PLAYSET','ARTISTTYPE')] and m.get(syms['artist_type'])==0; passed()
# Artists is Album Artist until a choice is saved (read_artistype_value); a saved Artist stays.
for config,want in (({},1),({'ARTISTTYPE':'0'},0),({'ARTISTTYPE':'1'},1)):
    a=Machine(); a.config.update(config); a.word(syms['artist_type'],7)
    a.call(address=HOOKS['read_artistype_value'][0],args=(0,0,0,0),gap=0)
    assert a.get(syms['artist_type'])==want and a.config_reads[-1][1:3]==('PLAYSET','ARTISTTYPE'),config
passed()
# A FLAC's "ALBUM ARTIST" comment (foobar2000's spelling) when stock's libtag found no ALBUMARTIST (ringnav_music_info).

t=TagMachine({'/mnt/mmc/a.flac':flac(b'ARTIST=Chikoi The Maid',b'Album Artist=Chikoi The Maid',picture=70000),
              '/mnt/mmc/b.flac':flac(b'ARTIST=Evan Call'),'/mnt/mmc/c.FLAC':flac(b'ALBUM ARTIST='+b'x'*300),
              '/mnt/mmc/d.mp3':flac(b'ALBUM ARTIST=No')})
t.files['/mnt/mmc/e.flac']=flac(b'METADATA_BLOCK_PICTURE='+b'A'*100000,b'ALBUM ARTIST=Past the cover')  # a block over 64 KB
assert t.album_artist('/mnt/mmc/a.flac')=='Chikoi The Maid'
assert t.album_artist('/mnt/mmc/a.flac',b'Stock')=='Stock'  # stock's ALBUMARTIST wins
assert t.album_artist('/mnt/mmc/b.flac')=='' and t.album_artist('/mnt/mmc/c.FLAC')=='x'*255
assert t.album_artist('/mnt/mmc/d.mp3')=='' and t.album_artist('/mnt/mmc/none.flac')==''
assert t.album_artist('/mnt/mmc/e.flac')=='Past the cover'
assert not t.handles  # every file closed
passed()
# An Ogg file whose comment header is over OGG_TAGS_MAX (4 MB; MusicBee's base64 pictures) is read by
# xiph.c, not stock's TagLib, which held it ~4 times over and got demo OOM-killed in a scan: each text
# field through stock's toolsFixId3ErrorCode, as libtag_c's (isLatin1, Latin-1, UTF-8) would reach it.

big=opus(b'TITLE=Born Dead Buried Alive',b'ARTIST=Hypocrisy','artist=ピーター'.encode(),b'ALBUM=The Arrival',b'GENRE='+b'x'*200,
         'COMPOSER=Peter Tägtgren'.encode(),b'ALBUMARTIST=Hypocrisy',b'DATE=2004',b'TRACKNUMBER=1/11',b'DISCNUMBER=2',
         b'MQAENCODER=MQAEncode',picture=4500000)
t=OggTagMachine({'/mnt/mmc/big.opus':big,'/mnt/mmc/cut.opus':big[:big.rfind(b'OggS',0,big.rfind(b'OggS'))],'/mnt/mmc/small.opus':opus(b'TITLE=Small',picture=100000),
                 '/mnt/mmc/flac.opus':flac(b'TITLE=no')})
for a in (0,1):
    info=t.info('/mnt/mmc/big.opus',a)
    assert t.stock_calls==0 and [t.text(info+o) for o in (0x280,0x100,0,0x200,0x480,0x580)]==['Born Dead Buried Alive',
        'Hypocrisy / ピーター','The Arrival','x'*127,'Peter Tägtgren','Hypocrisy']  # the genre through GENRE_COPY
    assert [t.get(info+o) for o in (0x680,0x684,0x688,0x68c,0x690)]==[2004,2,1,0x110 if a else 0x100,48000 if a else 0]  # disc, track
    assert [f[:2] for f in t.fixes]==[(k,k!=2) for k in range(1,7)] and t.fixes[1][2]==b'Hypocrisy / \xd4\xfc\xbf\xfc' and t.fixes[4][2:]==(b'Peter T\xe4gtgren','Peter Tägtgren'.encode())
info=t.info('/mnt/mmc/cut.opus',1)  # its last pages gone, the header never ends: TagLib's no tag, info untouched
assert t.stock_calls==0 and not t.fixes and t.u.mem_read(info,0x6a0)==bytes(0x6a0)
for path in ('/mnt/mmc/small.opus','/mnt/mmc/flac.opus','/mnt/mmc/none.opus'):  # under the cap, not Ogg, missing: stock's
    t.info(path); assert t.stock_calls==1 and not t.fixes,path
assert not t.handles; passed()
# Embedded art (toolsGetAlbumCover's entry, so Now Playing's, the Albums grids' and Coverflow's): an .ogg whose
# comment header is over OGG_TAGS_MAX gets none from stock (get_oggfile_albumpic parses TagLib's whole header),
# so the grid's fallback hands it to ffmpeg; one under the cap goes to stock, as an .opus does, never measured.
assert struct.unpack_from('<I',demo,fileoff(demo,HOOKS['toolsGetAlbumCover'][0]))[0]==j(payload_syms['ringnav_album_cover'])

t=OggCoverMachine({'/mnt/mmc/big.ogg':big,'/mnt/mmc/small.ogg':opus(b'TITLE=Small',picture=100000),'/mnt/mmc/big.opus':big})
assert t.cover('/mnt/mmc/big.ogg')==1 and not t.stock_covers and t.ffmpeg==1 and t.files[t.dst]==b'jpeg'
for path in ('/mnt/mmc/small.ogg','/mnt/mmc/big.opus'):
    assert t.cover(path)==1 and t.stock_covers==[path] and not t.ffmpeg,path
assert not t.handles; passed()
# The crash log's scan record (tags.c crash_note): each tag read names its file in /tmp/q2-scan-file
# before stock reads it, and every 500th appends "<count> <VmRSS kB> <path>" to /tmp/q2-scan.log, which
# each demo starts afresh and stops past 64 KB.

t=ScanNoteMachine({'/mnt/mmc/a.flac':flac(b'ALBUM ARTIST=A'),'/tmp/q2-scan.log':bytearray(b'a past demo\n')})
assert t.album_artist('/mnt/mmc/a.flac')=='A' and t.seen==[b'/mnt/mmc/a.flac\n']  # named before stock reads it
assert t.files['/tmp/q2-scan-file']==b'/mnt/mmc/a.flac\n' and t.files['/tmp/q2-scan.log']==b'a past demo\n'
for i in range(2,500): t.album_artist(f'/mnt/mmc/{i}.opus')
assert t.files['/tmp/q2-scan-file']==b'/mnt/mmc/499.opus\n' and t.files['/tmp/q2-scan.log']==b'a past demo\n'
t.album_artist('/mnt/mmc/500.opus')
assert t.files['/tmp/q2-scan.log']==b'500 91000 /mnt/mmc/500.opus\n'  # this demo's first line starts the file
t.STATM=b'30000 23000 1200 1 0 25000 0\n'
for i in range(501,1001): t.album_artist(f'/mnt/mmc/{i}.opus')
assert t.files['/tmp/q2-scan.log']==b'500 91000 /mnt/mmc/500.opus\n1000 92000 /mnt/mmc/1000.opus\n'
long='/mnt/mmc/'+'x'*700  # a line holds 500 bytes of path at most
t.word(payload_syms['scan_counts'],1499); t.album_artist(long)
assert t.files['/tmp/q2-scan.log'].split(b'\n')[2]==b'1500 92000 '+long[:500].encode()
t.word(payload_syms['scan_counts']+4,65536); before=bytes(t.files['/tmp/q2-scan.log'])
t.word(payload_syms['scan_counts'],1999); t.album_artist('/mnt/mmc/2000.opus')
assert t.files['/tmp/q2-scan.log']==before and t.files['/tmp/q2-scan-file']==b'/mnt/mmc/2000.opus\n'  # capped
assert not t.fds and not t.handles; passed()
# Coverflow's shared folders (coverflow.c folders_shared, one sorted pass): exactly the old rule, an album
# shares when another album key has a track in its folder, the folder compared whole, so an FNV collision
# never shares.

assert fnv1a(b'/m/1062789')==fnv1a(b'/m/1279192')  # one folder hash, two folders
m=CoverflowMachine()
fixture=[('/m/Mix/a.mp3','A','X'),('/m/Mix/b.mp3','B','Y'),  # two albums in one folder
         ('/m/Purple/CD1/1.flac','Baroness','Purple'),('/m/Purple/CD1/2.flac','Baroness','Purple'),  # one album, one folder
         ('/m/Purple/Disc 2/1.flac','Baroness','Purple'),('/m/Other Purple/1.flac','STP','Purple'),  # disc folders; same name elsewhere
         (None,'C','Z'),('loose.mp3','D','Z'),('/top.mp3','E','W'),('/top2.mp3','E','W'),  # no path, no folder, the root's
         ('/m/Three/1.mp3','F','V'),('/m/Three/2.mp3','F','V'),('/m/Three/3.mp3','G','V'),  # two keys among three
         ('/m/1062789/a.mp3','H','U'),('/m/1279192/b.mp3','I','U')]  # colliding folders
recs=[]
for path,artist,album in fixture:
    r=m.song('t'); m.word(r+O['REC_PATH'],m.string(path) if path else 0)
    m.word(r+O['REC_ARTIST'],m.string(artist)); m.word(r+O['REC_ALBUM'],m.string(album)); recs.append(r)
def folder(p): return p[:p.rfind('/')] if p and '/' in p else None
old=[int(folder(p) is not None and any(folder(q)==folder(p) and (a2,b2)!=(a,b) for q,a2,b2 in fixture)) for p,a,b in fixture]
assert old==[1,1,0,0,0,0,0,0,0,0,1,1,1,0,0]
marks=m.call(address=payload_syms['folders_shared'],args=(m.deque(recs),len(recs),0,0),gap=0)
assert list(m.u.mem_read(marks,len(recs)))==old
assert m.call(address=payload_syms['folders_shared'],args=(m.deque([]),0,0,0),gap=0); passed()  # no albums: no marks, not a failure
# Boot reads it too (ringnav_bootmode), so Audio settings shows it before any Library page has opened.
for config,text in (({},'Artists: Album Artist'),({'ARTISTTYPE':'0'},'Artists: Artist')):
    shown=settings_page('audioset','scroll_view_playset',config,stock_rows=13,boot=True,sub=1)[2]
    assert shown()[1]==text,config
passed()
# Mono Audio (crossfade.c mono_click): saved as Q2POD MONO, the row's text following, and the int
# hciplayer and q2video map (MONO_FILE) set live. No file until it is first turned on.

m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=MonoMachine)
assert not [c for c in m.calls if c[0]=='mono:open']
assert click(2)==[(1,'Q2POD','MONO')] and texts()[2]=='Mono Audio' and m.get(m.flag)==1
assert click(2)==[(0,'Q2POD','MONO')] and texts()[2]=='Mono Audio' and m.get(m.flag)==0 and switch_image(m,rows[2])=='switch_off'
assert [c[0] for c in m.calls if c[0]=='mono:open']==[]; passed()  # mapped once
# A failed map must never publish an inode that consumers could retain forever.

m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=FailedMapMachine)
assert click(2)==[(1,'Q2POD','MONO')]
assert 'mono:mmap' in [c[0] for c in m.calls] and 'mono:rename' not in [c[0] for c in m.calls]
passed()
