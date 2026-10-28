#!/usr/bin/env python3
"""Grouped playback against the actual MIPS payload, using the regression suite's UI/deque mocks.
Run: python3 test/playback.py BUILD_DIRECTORY
"""
import pathlib
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_CODE
# Load only fixture definitions; the full UI suite remains independently runnable.
source = pathlib.Path(__file__).with_name('patch.py').read_text()
exec(compile(source[:source.index('checks=0')], 'test/patch.py', 'exec'))
exec(compile(source[source.index('class QueueMachine'):source.index('sel=lambda m:')], 'test/patch.py', 'exec'))
ps = payload_syms

class PlaybackMachine(QueueMachine):
    def __init__(self, files=None, **kw):
        super().__init__(**kw)
        self.files = dict(files or {}); self.handles = {}; self.fd = 100
        self.fail = ''; self.elapsed = 37; self.random_calls = 0; self.rng = 71
        self.allocations = 0; self.write_opens = 0; self.time_reads = 0; self.eq_boots = 0
        self.path_comparisons = 0; self.library_modes = []
        self.missing = set(); self.starts = []; self.stops = 0; self.seek = None; self.stock_resume = None
        self.handlers.pop(syms['mclLoadPlayList'], None)
        # EQ storage uses FP64 odd registers Unicorn cannot execute; test/peq.py checks the real reset.
        self.handlers[ps['peq_boot']] = 'p:eq_boot'
        self.handlers[ps['stock_memory_trampoline']] = 'p:fallback'
        self.handlers[ps['stock_savequeue_trampoline']] = 'p:saved'
        self.handlers[ps['stock_speed_trampoline']] = 'p:speed'
        self.handlers[ps['stock_open_preload_trampoline']] = 'p:mcl_open_preload'
        self.handlers[ps['stock_start_queue_trampoline']] = 'p:start_queue'
        self.handlers[ps['toast']] = 'p:toast'
        self.sent_speed = None; self.toasts = []
        for name in ('calloc@GLIBC_2.0','qsort@GLIBC_2.0','memcmp@GLIBC_2.0','strcmp@GLIBC_2.0',
                     'fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2',
                     'rename@GLIBC_2.0','unlink@GLIBC_2.0','access@GLIBC_2.0','mclGetPlayTime',
                     'mclStartPlayer','mclStop','mclSetStartSeekTime','mcl_open_preload','toolsRandnum','getAllMusic'):
            self.handlers[syms[name]] = 'p:'+name.split('@')[0]
        self.word(0xa2638c, 0x1000010); self.handlers[0x1000010] = 'p:free'
        self.word(ps['rng'], 0x2545f491)  # rand_below's generator, preseeded: every machine shuffles alike
        self.install([
            ('a2','/A/02.flac','Album A',1,2,0,0),
            ('b1','/B/01.flac','Album B',1,1,0,0),
            ('a1','/A/01.flac','album a',1,1,0,0),
            ('cue1','/C/image.flac','',0,0,20,40),
            ('cue2','/C/image.flac','',0,0,40,60),
            ('solo','/D/single.flac','Solo',0,0,0,0),
            ('dup','/A/01.flac','ALBUM A',1,1,0,0),
        ], 2)
    def install(self, tracks, at=0):
        rows=[]
        for name,path,album,disc,track,start,end in tracks:
            r=self.song(name)
            for key,v in [('REC_PATH',self.string(path)),('REC_ALBUM',self.string(album)),('REC_DISC',disc),
                          ('REC_TRACK',track),('REC_CUE_START',start),('REC_CUE_END',end)]: self.word(r+O[key],v)
            rows.append(r)
        self.word(syms['mcl_pdeqplaylist'],self.deque(rows)); self.word(O['MCL_POS'],at)
        self.library_rows=list(self.items(self.get(syms['mcl_pdeqplaylist'])))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('p:'): return super().hook(u,address,size,unused)
        name=name[2:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='eq_boot': self.eq_boots+=1
        elif name=='calloc':
            self.allocations+=1
            ret=0 if self.fail=='alloc' or (self.fail=='lookup' and a==len(self.library_rows) and b==8) else self.alloc((a*b+7)&~3)
        elif name=='memcmp':
            x,y=bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c)); ret=(x>y)-(x<y)
        elif name=='strcmp':
            x,y=self.text(a),self.text(b); ret=(x>y)-(x<y)
            self.path_comparisons += x.startswith('/') and y.startswith('/')
        elif name=='qsort': self.qsort(a,b,c,d)
        elif name=='fopen':
            path,mode=self.text(a),self.text(b)
            if mode=='wb': self.write_opens+=1
            if self.fail!='open' and (mode=='wb' or path in self.files):
                self.fd+=1; ret=self.fd; self.handles[ret]=[path,0]
                if mode=='wb': self.files[path]=b''
        elif name in ('fread','fwrite'):
            path,off=self.handles[d]; length=b*c
            if name=='fread':
                chunk=self.files[path][off:off+length]; u.mem_write(a,chunk); ret=len(chunk)//b
                self.handles[d][1]+=len(chunk)
            else:
                chunk=bytes(u.mem_read(a,length)); ret=c if self.fail!='write' else 0
                self.files[path]+=chunk if ret else chunk[:1]
        elif name=='fclose': ret=-1 if self.fail=='close' else 0; del self.handles[a]
        elif name=='rename':
            if self.fail=='rename' or (self.fail=='checkpoint-rename' and self.text(b).endswith('-elapsed')): ret=-1
            else: self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.files.pop(self.text(a),None)
        elif name=='access': ret=-1 if self.text(a) in self.missing else 0
        elif name=='speed': self.sent_speed=a; ret=1
        elif name=='toast': self.toasts.append(self.text(a))
        elif name=='mp4_fits': self.mp4_checks.append(self.text(a)); ret=self.fits
        elif name=='mclGetPlayTime': self.time_reads+=1; self.word(a,self.elapsed); self.word(b,300)
        elif name=='mclStartPlayer': self.starts.append(self.mcl('MCL_POS')); ret=1
        elif name=='mclStop': self.stops+=1; ret=1
        elif name=='start_queue': self.word(O['MCL_POS'],a); self.starts.append(a); ret=1  # mclSetPlayPos, mclStartPlayer
        elif name=='mclSetStartSeekTime': self.seek=a; ret=1
        elif name=='mcl_open_preload': self.word(O['MCL_PRELOAD'],1); ret=1
        elif name=='toolsRandnum':
            self.random_calls+=1; self.rng=(self.rng*1103515245+12345)&0x7fffffff; ret=self.rng%a
        elif name=='getAllMusic':
            self.library_modes.append(a)
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.library_rows]; ret=len(self.library_rows)
        elif name=='folder_skip': self.folder_skips+=1
        elif name=='fallback':  # stock resume: stock_resume's (class, rows, index) into out, else none
            cls,rows,ret=self.stock_resume or (0,[],-1)
            self.deqs[a][1]=[self.copy('stSongInfo',e) for e in rows]; self.word(syms['g_memory_info'],cls)
        self.returns(ret,clobber=True)
    def fn(self,name,*args): return signed(self.call(address=ps[name],args=tuple(args)+(0,)*(4-len(args)),gap=0))
    def options(self,shuffle,repeat,folder):
        for kind,value in ((2,folder),(0,shuffle),(1,repeat)): assert self.fn('playback_set',kind,value)==1
    def advance_song(self,auto=1): return self.fn('ringnav_next',auto)
    def current(self): return self.mcl('MCL_POS')
    def sequence(self,auto=1,limit=30):
        out=[self.current()]
        for _ in range(limit):
            if self.advance_song(auto)<0: break
            out.append(self.current())
        return out

checks=0
for grouping in (0,1):
    for shuffle in range(5):
        for repeat in range(6):
            m=PlaybackMachine(); m.options(shuffle,repeat,grouping)
            calls=m.random_calls; peek=m.fn('playback_successor',1)
            assert m.fn('playback_successor',1)==peek and m.random_calls==calls
            if peek>=0:
                m.fn('ringnav_preload'); assert m.mcl('MCL_PREPOS')==peek and m.random_calls==calls
                # Run the real stock gapless branch down to its mocked mutex/lyric boundaries.
                m.word(O['MCL_PRELOAD'],0)
            seq=m.sequence(limit=21)
            category={0,2,6}
            if repeat==0: assert seq==[2]
            elif repeat==1: assert set(seq)==category and len(seq)==3
            elif repeat==2: assert set(seq)==set(range(7)) and len(seq)==7
            elif repeat==3: assert set(seq)=={2} and len(seq)==22
            elif repeat==4:
                assert set(seq)<=category
                for start in range(0,18,3): assert set(seq[start:start+3])==category
            elif repeat==5:
                for start in range(0,21,7): assert set(seq[start:start+7])==set(range(7))
            if shuffle==3 and repeat==2:
                # Each album/folder stays contiguous; ordered tracks and CUE starts survive.
                groups=[0 if i in category else 1 if i==1 else 2 if i in (3,4) else 3 for i in seq]
                assert len([g for i,g in enumerate(groups) if not i or g!=groups[i-1]])==4
                assert seq.index(3)<seq.index(4)
            m=PlaybackMachine(); m.options(shuffle,repeat,grouping)
            if repeat in (0,3): assert m.advance_song(0)==1 and m.current()!=2
            if repeat in (1,4): assert set(m.sequence(0,10))<={0,2,6}
            checks+=1

# Up Next lists exactly the tracks the following advances play, in order, and listing draws nothing.
for grouping in (0,1):
    for shuffle in range(5):
        for repeat in range(6):
            m=PlaybackMachine(); m.options(shuffle,repeat,grouping)
            out=m.alloc(24*4); calls=m.random_calls
            n=m.fn('playback_upcoming',out,24); up=[m.get(out+4*i) for i in range(n)]
            assert m.random_calls==calls and m.fn('playback_upcoming',out,24)==n
            if repeat in (0,3): assert n==0
            else: assert m.sequence(limit=n)[1:]==up, (shuffle,repeat,grouping)
            checks+=1

# Shuffle Songs takes the loaded queue over as Shuffle Songs, Repeat All: an order Up Next lists,
# every track once a pass from the playing one, then the next pass, without end.
m=PlaybackMachine(); assert m.fn('playback_shuffle')==1
assert [m.fn('playback_option',k) for k in range(3)]==[1,5,0]
out=m.alloc(24*4); n=m.fn('playback_upcoming',out,24); up=[m.get(out+4*i) for i in range(n)]
seq=m.sequence(limit=20); assert n==13 and seq[1:14]==up and len(seq)==21 and sorted(seq[:7])==list(range(7)) and seq[0]==2; checks+=1

# Shuffle Songs is one-shot: the next other queue plays in the mode it replaced (twice over, too),
# without the shuffle; a mode choice in between is kept instead.
for shuffles,chosen in ((1,None),(2,None),(1,0)):
    m=PlaybackMachine(); m.handlers.pop(syms['config_playmode'],None)
    mode=lambda n,notify=0: m.call(address=syms['config_playmode'],args=(n,notify,0,0),gap=0)
    mode(3)
    for _ in range(shuffles):  # shuffle_play's order
        was=m.fn('playback_unshuffle'); mode(2,1); m.fn('ringnav_load',m.deque([m.song('a'),m.song('b')]),0,0xf001)
        assert m.fn('playback_shuffle',was)==1 and m.mcl('MCL_MODE')==2
    if chosen is not None: mode(chosen)
    m.fn('ringnav_load',m.deque([m.song('album')]),0,1)
    assert m.mcl('MCL_MODE')==(3 if chosen is None else chosen) and m.fn('playback_option',0)==0; checks+=1
# The album class (0xff10: Coverflow, the stock album page and Go to Album) restores it the same way, its shuffle ended.
m=PlaybackMachine(); m.handlers.pop(syms['config_playmode'],None); mode(3)
was=m.fn('playback_unshuffle'); mode(2,1); m.fn('ringnav_load',m.deque([m.song('a'),m.song('b')]),0,0xf001); m.fn('playback_shuffle',was)
m.fn('ringnav_load',m.deque([m.song('album')]),0,0xff10)
assert m.mcl('MCL_MODE')==3 and m.fn('playback_option',0)==0; checks+=1
# Shuffle Albums straight after restores it too, and keeps its groups.
m=PlaybackMachine(); m.handlers.pop(syms['config_playmode'],None); all=m.deque(list(m.items(m.get(syms['mcl_pdeqplaylist'])))); mode(3)
was=m.fn('playback_unshuffle'); mode(2,1); m.fn('ringnav_load',m.deque([m.song('a'),m.song('b')]),0,0xf001); m.fn('playback_shuffle',was)
assert m.fn('playback_groups',all,0)==1 and m.mcl('MCL_MODE')==3 and m.fn('playback_option',0)==3; checks+=1

# Over SAVE_LIMIT (999) tracks nothing is saved, and an older snapshot goes.
for n,kept in ((999,True),(1000,False)):
    m=PlaybackMachine({'/mnt/data/ringnav-queue':b'old'}); m.budget=100000000; m.install([(f't{i}',f'/L/{i}.flac','L',1,i,0,0) for i in range(n)])
    assert m.fn('playback_shuffle')==1 and ('/mnt/data/ringnav-queue' in m.files)==kept and m.files.get('/mnt/data/ringnav-queue')!=b'old'; checks+=1

# A failed rebuild ends advanced play and drops the older queue's snapshot, so a reboot cannot
# restore it.
m=PlaybackMachine(); assert m.fn('playback_shuffle')==1 and '/mnt/data/ringnav-queue' in m.files
m.fail='alloc'; assert m.fn('playback_shuffle')==0 and '/mnt/data/ringnav-queue' not in m.files and m.fn('playback_option',0)==0; checks+=1

# The Now Playing hold's View Queue opens the Up Next list only while a shuffle is on (stock's page
# otherwise); it lists those rows, in play order, and opens nothing else. No separate Up Next row.
def now_playing():
    m=PlaybackMachine(page='playing_page',pos=1); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa3beac,2); return m
m=now_playing(); m.options(0,2,0); m.press(5000); assert m.release()==1
m.press(6000); assert m.hold()==11 and m.release()==0
m.press(7000); assert m.release()==1; checks+=1
m=now_playing(); m.options(1,2,0); calls=m.random_calls
m.press(6000); assert m.hold()==11 and m.release()==0 and 'Up Next' not in m.labels()
m.pick(m.labels().index('View Queue')); m.advance(0)
assert 'window_create' in [c[0] for c in m.calls] and m.random_calls==calls and not m.playback(); checks+=1

# Album identity (name, then album artist, else folder) crosses folders with an album artist;
# folder grouping does not. Disc/track and CUE order differ from source order, and categories keep
# their first appearance without shuffle.
tracks=[('disc2','/x/01','SAME',2,1,0,0),('first','/x/02','same',1,1,0,0),
        ('other-folder','/y/01','Same',1,2,0,0),('unknown','/z/01','',0,0,0,0)]
for folder,artist,want in ((0,1,[1,2,0]),(1,1,[1]),(0,0,[1,0])):
    m=PlaybackMachine(); m.install(tracks,1)
    for r in m.items(m.get(syms['mcl_pdeqplaylist']))[:3*artist]: m.word(r+O['REC_ALBUM_ARTIST'],m.string('Artist'))
    m.options(0,1,folder)
    assert m.sequence()==want
# Without an album artist, an album's disc folders are one album group and another folder of the
# name is not; Shuffle Folders keeps each disc folder apart.
tracks=[('disc2','/m/Purple/Disc 2/01','Purple',0,0,0,0),('cd1','/m/Purple/CD1/01','Purple',0,0,0,0),
        ('other','/m/STP Purple/01','Purple',0,0,0,0)]
for folder,want in ((0,[1,0]),(1,[1])):
    m=PlaybackMachine(); m.install(tracks,1); m.options(0,1,folder)
    assert m.sequence()==want
m=PlaybackMachine(); m.install([('a','/a/1','A',0,0,0,0),('b','/b/1','B',0,0,0,0),('c','/c/1','C',0,0,0,0)],1)
m.options(0,2,0); assert m.sequence()==[1,2]
# Play Next of an album track the order sorts before the cursor plays next, then the album goes on;
# a second Play Next before the first plays goes first and keeps the first.
album=[(f't{k}',f'/a/0{k}','A',1,k,0,0) for k in range(1,7)]
for copies,want in (((1,),[4,5,6]),((1,2),[4,5,6,7])):
    m=PlaybackMachine(); m.install(album,4); m.options(0,2,0); q=m.get(syms['mcl_pdeqplaylist'])
    for k in copies:
        m.items(q).insert(5,m.copy('stSongInfo',m.items(q)[k])); m.fn('playback_insert',5,1,1)
    seq=m.sequence(); assert seq==want and m.names()[5]=='t%d'%(copies[-1]+1), (copies,seq)
m=PlaybackMachine(); m.options(1,2,0); q=m.get(syms['mcl_pdeqplaylist'])
m.items(q).append(m.song('appended')); m.fn('playback_insert',7,1,0)
assert set(m.sequence())==set(range(8))
m.fn('playback_save'); assert '/mnt/data/ringnav-queue' in m.files
m.handlers[ps['stock_mode_trampoline']]='p:saved'; m.fn('ringnav_mode',2)
assert '/mnt/data/ringnav-queue' not in m.files and m.fn('playback_successor',1)==-1
checks+=1

# Explicit group skips bypass category boundaries and return to the previous group's start.
for folder in (0,1):
    for repeat in range(6):
        m=PlaybackMachine(); m.options(3,repeat,folder)
        assert m.fn('playback_group_skip',1)==1 and m.current() not in (0,2,6)
        assert m.fn('playback_group_skip',0)==1 and m.current()==2
        assert m.fn('playback_successor',0) in (0,6)
        checks+=1
m=PlaybackMachine(); m.install([('a','/a/1','A',0,0,0,0)],0); m.options(3,5,0)
assert m.fn('playback_group_skip',1)==0 and m.fn('playback_group_skip',0)==0
checks+=1

# Actual history, duplicates, option changes, and successor/preload consistency.
m=PlaybackMachine(); m.options(4,5,0)
seq=[m.current()]
for _ in range(6): m.advance_song(0); seq.append(m.current())
for want in reversed(seq[:-1]): assert m.fn('ringnav_prev')==1 and m.current()==want
assert m.fn('ringnav_prev')==1 and m.current()==seq[0]
pos=m.current(); elapsed=m.elapsed
assert m.fn('playback_set',2,1)==1 and m.current()==pos and m.elapsed==elapsed
m.fn('ringnav_preload'); assert m.mcl('MCL_PREPOS')==m.fn('playback_successor',1)
# Real stock handoff updates pos from the preload, without starting another decoder.
for name in ('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','mclLoadExLyric'): m.mock(name)
m.byte(0xa3be54,1); want=m.fn('playback_successor',1); starts=len(m.starts)
assert m.advance_song()==1 and m.current()==want and len(m.starts)==starts
checks+=1

# Up Next lists a multi-track Play Next back to back, as it plays.
m=PlaybackMachine(); m.options(1,5,0)
q=m.get(syms['mcl_pdeqplaylist']); old=m.current()
m.deqs[q][1][old+1:old+1]=[m.song('next%d'%i) for i in range(3)]
m.fn('playback_insert',old+1,3,1)
out=m.alloc(24*4); n=m.fn('playback_upcoming',out,24); up=[m.get(out+4*i) for i in range(n)]
assert up[:3]==[old+1,old+2,old+3] and m.sequence(limit=n)[1:]==up, up
checks+=1

# A View Queue pick (stock's page or the shuffle list) plays now; the pass carries on without it, the tracks it jumped stay
# up next, Previous returns to it, and a Play Next it is not part of stays first.
m=PlaybackMachine(); m.options(1,5,0); out=m.alloc(24*4)
up=[m.get(out+4*i) for i in range(m.fn('playback_upcoming',out,24))]; x=up[3]; rest=[t for t in up[:6] if t!=x]
assert m.fn('ringnav_start_queue',x)==1 and m.current()==x and m.sequence(limit=5)[1:]==rest
m=PlaybackMachine(); m.options(1,5,0); x=m.fn('playback_successor',0); m.fn('ringnav_start_queue',x)
assert m.advance_song()==1 and m.fn('ringnav_prev')==1 and m.current()==x
m=PlaybackMachine(); m.options(1,5,0); q=m.get(syms['mcl_pdeqplaylist']); old=m.current()
m.deqs[q][1][old+1:old+1]=[m.song('next')]; m.fn('playback_insert',old+1,1,1)
out=m.alloc(24*4); up=[m.get(out+4*i) for i in range(m.fn('playback_upcoming',out,24))]; x=up[3]
m.fn('ringnav_start_queue',x); assert m.sequence(limit=1)[1:]==[old+1]
starts=len(m.starts); assert m.fn('ringnav_start_queue',m.current())==1 and len(m.starts)==starts+1  # the playing one: restarted
m=PlaybackMachine(); m.options(1,5,0); q=m.get(syms['mcl_pdeqplaylist']); old=m.current()  # the top of a Play Next keeps the rest
m.deqs[q][1][old+1:old+1]=[m.song('next%d'%i) for i in range(3)]; m.fn('playback_insert',old+1,3,1)
m.fn('ringnav_start_queue',old+1); assert m.sequence(limit=2)[1:]==[old+2,old+3]
m=PlaybackMachine(); m.options(0,2,0); m.fn('ringnav_start_queue',4)  # ordered: the tracks after it follow
assert m.sequence(limit=3)==[4,5]  # the play order is 2,6,0,1,3,4,5
checks+=1

# A hold on View Queue's shuffle list plays its track next (playback_move): not again at its old
# place, the rest of the order as it was, the queue and its indices untouched; with a Play Next
# pending the track goes ahead of it, and the pending list keeps its order behind.
def up_names(m,k=24):
    out=m.alloc(24*4); names=m.names(); return [names[m.get(out+4*i)] for i in range(m.fn('playback_upcoming',out,k))]
def played(m,limit): return [m.names()[i] for i in m.sequence(limit=limit)]
for shuffle,repeat,row in ((1,5,3),(1,5,5),(0,5,1),(0,5,2),(0,5,5),(2,5,2),(3,5,4),(4,5,3),(3,4,1),(2,1,1)):  # ordered plays 2,6,0,1,3,4,5
    m=PlaybackMachine(); m.options(shuffle,repeat,0); m.word(O['MCL_LASTPOS'],3)
    out=m.alloc(24*4); n=m.fn('playback_upcoming',out,24); x=m.get(out+4*row); pos=m.current()
    names=m.names(); now=names[pos]; up=up_names(m); pool=m.pool()
    assert row<n and m.fn('playback_move',x)==x and m.names()==names and m.current()==pos, (shuffle,repeat)
    assert m.mcl('MCL_LASTPOS')==3 and m.pool()==pool
    want=([up[row]]+up[:row]+up[row+1:])[:5]  # a short category's next pass repeats names
    assert up_names(m)[:5]==want and played(m,5)[1:]==want, (shuffle,repeat,row)
    for t in reversed([now]+want[:-1]): assert m.fn('ringnav_prev')==1 and m.names()[m.current()]==t
    checks+=1
for pick in (0,1):
    m=PlaybackMachine(); m.options(1,5,0); q=m.get(syms['mcl_pdeqplaylist']); old=m.current()
    m.deqs[q][1][old+1:old+1]=[m.song('next')]; m.fn('playback_insert',old+1,1,1)
    if pick: x=up_names(m)[3]; m.fn('ringnav_start_queue',m.names().index(x)); assert up_names(m)[0]=='next'
    m.word(O['MCL_LASTPOS'],3); up=up_names(m)[:7]; assert up[0]=='next'; now=m.names()[m.current()]; last=m.names()[m.mcl('MCL_LASTPOS')]
    names=m.names(); m.fn('playback_move',m.names().index(up[4]))
    assert m.names()==names and m.names()[m.current()]==now and m.names()[m.mcl('MCL_LASTPOS')]==last
    want=[up[4]]+[t for t in up if t!=up[4]]; assert up_names(m)[:7]==want and played(m,7)[1:]==want, pick
checks+=1
# Songs in Album with a Play Next pending: the hold leaves the queue, its album grouping and the
# current track alone, and the pending Play Next plays after the moved track, as before the move.
m=PlaybackMachine(); m.options(2,5,0); q=m.get(syms['mcl_pdeqplaylist']); old=m.current()
m.deqs[q][1][old+1:old+1]=[m.song('next')]; m.fn('playback_insert',old+1,1,1)
names=m.names(); now=names[m.current()]; up=up_names(m)[:8]; assert up[0]=='next'
x=up[4]; m.fn('playback_move',m.names().index(x))
want=[x,'next']+[t for t in up if t not in (x,'next')]
assert m.names()==names and m.names()[m.current()]==now
assert up_names(m)[:7]==want[:7] and played(m,7)[1:]==want[:7]
checks+=1
# Play Next's list holds 64: a 65th hold drops the oldest, and the list plays newest first.
m=PlaybackMachine(); m.budget=10000000; m.install([(f'p{k}',f'/p/{k:03}.flac','P',1,k,0,0) for k in range(70)],0)
m.options(0,2,0)
for k in range(1,66): assert m.fn('playback_move',k)==k
out=m.alloc(24*4); up=[m.get(out+4*i) for i in range(m.fn('playback_upcoming',out,70))]
assert up[:64]==list(range(65,1,-1)) and up[64]==1, up[:66]
checks+=1
# The playing track and stock's own queue do nothing; a move closes the preload, which then
# targets the moved track, and the saved queue resumes as moved.
m=PlaybackMachine(); m.options(1,5,0); m.word(O['MCL_PRELOAD'],1); m.word(O['MCL_FD'],9)
assert m.fn('playback_move',m.current())==-1 and m.mcl('MCL_PRELOAD')==1 and not m.sent
out=m.alloc(24*4); m.fn('playback_upcoming',out,24); at=m.fn('playback_move',m.get(out+8))
assert m.mcl('MCL_PRELOAD')==0 and m.sent==[(9,b'{mcl-closegapless\\null}',23,0)]
m.fn('ringnav_preload'); assert m.mcl('MCL_PREPOS')==at
r=PlaybackMachine(dict(m.files)); r.byte(syms['g_memory_play'],2)
same=lambda l:[t.replace('dup','a1') for t in l]  # the library's row for dup's path
assert r.fn('ringnav_memory',r.deque([]))==m.current() and r.names()==same(m.names()) and up_names(r)==same(up_names(m))
assert PlaybackMachine().fn('playback_move',4)==-1
checks+=1

# Add and Play next remap history and occurrences, including multiple priority tracks.
m=PlaybackMachine(); m.options(1,5,0)
q=m.get(syms['mcl_pdeqplaylist']); old=m.current()
added=[m.song('priority1'),m.song('priority2')]
m.deqs[q][1][old+1:old+1]=added
m.fn('playback_insert',old+1,2,1)
assert m.current()==old
assert m.advance_song(0)==1 and m.current()==old+1
assert m.advance_song(0)==1 and m.current()==old+2
assert m.fn('ringnav_prev')==1 and m.current()==old+1
m.fn('playback_insert',len(m.items(q)),0,0)
# Replacement resets history and retains options.
new=m.deque([m.song('replacement')]); m.fn('ringnav_load',new,0,1)
assert m.fn('ringnav_prev')==1 and m.current()==0
checks+=1

# Restore exact order/history/duplicates through stock loading; preserve previous snapshot on errors.
m=PlaybackMachine(); m.options(4,5,0); m.advance_song(0); m.advance_song(0); m.fn('playback_save')
files=dict(m.files); want=m.current(); expected=m.fn('playback_successor',1)
for fail in ('open','write','close','rename','alloc'):
    m.fail=fail; m.elapsed+=1; m.fn('playback_save'); assert m.files['/mnt/data/ringnav-queue']==files['/mnt/data/ringnav-queue']
m.fail=''
r=PlaybackMachine(files); r.byte(syms['g_memory_play'],2); out=r.deque([])
assert r.fn('ringnav_memory',out)==want and r.seek==37
assert r.names()==['a2','b1','a1','cue1','cue2','solo','a1']
assert r.fn('playback_successor',1)==expected
r.fn('ringnav_load',out,want,1) # home_page/player_start's second load keeps restored traversal
assert r.fn('playback_successor',1)==expected and len(r.names())==7
record=r.items(r.get(syms['mcl_pdeqplaylist']))[want]
assert r.fn('playback_resumed',record)==1 and r.fn('playback_resumed',record)==0
r.elapsed=0; r.fn('playback_save')
assert struct.unpack_from('<I',r.files['/mnt/data/ringnav-queue'],24)[0]==37
r.elapsed=38; r.fn('playback_save'); r.elapsed=0; r.fn('playback_save')
assert struct.unpack_from('<I',r.files['/mnt/data/ringnav-queue-elapsed'],12)[0]==0
assert r.fn('ringnav_prev')==1 and r.current()!=want
# Skip missing files; both CUE occurrences and duplicate files remain distinct.
r=PlaybackMachine(files); r.byte(syms['g_memory_play'],2); r.missing={'/B/01.flac'}
assert r.fn('ringnav_memory',r.deque([]))>=0 and len(r.names())==6
for corrupt in (b'',files['/mnt/data/ringnav-queue'][:-1],b'bad'+files['/mnt/data/ringnav-queue'][3:],files['/mnt/data/ringnav-queue']+b'x'):
    r=PlaybackMachine({'/mnt/data/ringnav-queue':corrupt}); r.byte(syms['g_memory_play'],2)
    before=r.names(); assert r.fn('ringnav_memory',r.deque([]))==-1 and r.names()==before
r=PlaybackMachine(files); r.byte(syms['g_memory_play'],1)
assert r.fn('ringnav_memory',r.deque([]))>=0 and r.seek==0
r=PlaybackMachine(files); assert r.fn('ringnav_memory',r.deque([]))==-1
# A failed index allocation must leave the snapshot and existing queue intact.
r=PlaybackMachine(files); r.byte(syms['g_memory_play'],2); r.fail='lookup'
before=r.names(); assert r.fn('ringnav_memory',r.deque([]))==-1 and r.names()==before and r.files==files
# An empty library still restores files found by the stock directory fallback.
r=PlaybackMachine(files); r.byte(syms['g_memory_play'],2)
r.found=list(r.library_rows); r.library_rows=[]
assert r.fn('ringnav_memory',r.deque([]))==want and len(r.names())==7
# Larger restored queues must not compare every saved path with the whole library.
tracks=[(str(i),f'/perf/{i:04}.flac','Album',1,i,0,0) for i in range(256)]
m=PlaybackMachine(); m.budget=10000000; m.install(tracks,191); m.options(1,5,0); m.fn('playback_save')
r=PlaybackMachine(m.files); r.budget=10000000; r.install(tracks,191); r.library_rows.reverse(); r.byte(syms['g_memory_play'],2)
assert r.fn('ringnav_memory',r.deque([]))==191 and r.names()==[str(i) for i in range(256)]
assert r.library_modes==[3] and r.path_comparisons < 256*32, r.path_comparisons
print('256-track restore path comparisons:',r.path_comparisons)
# Without a snapshot, stock rebuilds an album queue (0xff10) from every album of its name: only
# the resumed track's album stays, at its index there. Other classes stay as stock built them.
for cls,want in ((0xff10,(1,['a2','a1'])),(0xf001,(2,['a2','b1','a1']))):
    r=PlaybackMachine(); r.byte(syms['g_memory_play'],2); rows=r.library_rows[:3]; out=r.deque([])
    for e in rows: r.word(e+O['REC_ALBUM'],r.string('Album A'))
    r.stock_resume=(cls,rows,2); assert (r.fn('ringnav_memory',out),r.names(out))==want
checks+=1

# Missing priority files retain the remaining Play Next block and a valid next-boot snapshot.
m=PlaybackMachine(); m.options(1,5,0)
q=m.get(syms['mcl_pdeqplaylist']); at=m.current()+1
m.items(q)[at:at]=[m.song('priority1'),m.song('priority2'),m.song('priority3')]
m.fn('playback_insert',at,3,1)
for missing in ({'priority1'}, {'priority3'}, {'priority1','priority3'},
                {'priority1','priority2','priority3'}):
    r=PlaybackMachine(m.files); r.byte(syms['g_memory_play'],2)
    r.library_rows.extend(r.song(n) for n in ('priority1','priority2','priority3'))
    r.missing={'/p/'+n for n in missing}
    assert r.fn('ringnav_memory',r.deque([]))==2
    r.fn('playback_save')
    saved=struct.unpack_from('<14I',r.files['/mnt/data/ringnav-queue'])
    remaining=[n for n in ('priority1','priority2','priority3') if n not in missing]
    assert saved[12]==len(remaining)
    for name in remaining:
        r.advance_song(0); assert r.names()[r.current()]==name
    again=PlaybackMachine(r.files); again.byte(syms['g_memory_play'],2)
    again.library_rows.extend(again.song(n) for n in ('priority1','priority2','priority3'))
    again.missing=r.missing
    assert again.fn('ringnav_memory',again.deque([]))==2
    checks+=1

# Unchanged settings preserve the shuffle, next decoder, history, and saved queue.
m=PlaybackMachine(); m.options(4,5,0); m.advance_song(0); m.fn('playback_save')
m.fn('ringnav_preload')
state=(m.random_calls,m.allocations,m.write_opens,m.fn('playback_successor',1),dict(m.files))
for kind,value in ((0,4),(1,5),(2,0)):
    assert m.fn('playback_set',kind,value)==1
    assert m.mcl('MCL_PRELOAD')==1
    assert (m.random_calls,m.allocations,m.write_opens,m.fn('playback_successor',1),m.files)==state
for _ in range(5):
    m.fn('playback_save'); m.now+=5000; m.fn('playback_poll')
assert (m.allocations,m.write_opens)==state[1:3]
# An elapsed-time change still checkpoints, while a dirty transition saves even at the same second.
m.elapsed+=1; m.now+=5000; m.fn('playback_poll')
assert m.write_opens==state[2]+1
m.advance_song(0); m.fn('playback_poll')
assert m.write_opens==state[2]+3
checks+=1

# Polling between checkpoints takes the playback-time mutex only once every five seconds.
# Frequent UI passes still save elapsed time at 30 seconds, and dirty transitions save at once.
m=PlaybackMachine(); m.options(0,5,0)
m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa3beac,2)
start=m.now; reads=m.time_reads; writes=m.write_opens
for elapsed in range(50,30000,50):
    m.now=start+elapsed; m.elapsed=37+elapsed//1000; m.fn('playback_poll')
    assert m.time_reads==reads+elapsed//5000, (elapsed,m.time_reads,reads)
    assert m.write_opens==writes
m.now=start+30000; m.elapsed=67; m.fn('playback_poll')
assert m.time_reads==reads+7 and m.write_opens==writes+1
m.advance_song(0); m.fn('playback_poll')
assert m.write_opens==writes+3
checks+=1

# Failed writes preserve the prior snapshot and retry, every 30 s, even if playback has since paused.
# Repeated UI passes do not hammer allocation/storage while the checkpoint is pending.
for failure in ('alloc','open','write','close','rename'):
    m=PlaybackMachine(); m.options(4,5,0)
    previous=m.files['/mnt/data/ringnav-queue']; m.advance_song(0)
    m.fail=failure; m.fn('playback_poll')
    attempts=(m.allocations,m.write_opens)
    assert m.files['/mnt/data/ringnav-queue']==previous
    for delta in (1,100,1000,3000,898,25000):
        m.now+=delta; m.fn('playback_poll')
        assert (m.allocations,m.write_opens)==attempts
    m.fail=''; m.now+=1; m.fn('playback_poll')
    assert m.files['/mnt/data/ringnav-queue']!=previous
    assert struct.unpack_from('<I',m.files['/mnt/data/ringnav-queue'],20)[0]==m.current()
    assert m.allocations==attempts[0]+1 and m.write_opens==attempts[1]+2
    m.fn('playback_save'); assert m.allocations==attempts[0]+1
    checks+=1

# Selecting a stock mode during playback also closes a stale advanced repeat preload.
for skip in (0,1):
    m=PlaybackMachine(); m.word(O['MCL_POS'],6); m.options(0,5,0)
    m.fn('ringnav_preload'); assert m.mcl('MCL_PRELOAD')==1
    m.byte(0xa3be53,skip); m.fn('ringnav_mode',0)
    assert m.mcl('MCL_PRELOAD')==0
    m.advance_song(); assert m.stops==1 and m.current()==6
    checks+=1

# Stock repeat modes still repeat after advanced playback is cleared.
for mode,want in ((1,6),(3,0)):
    m=PlaybackMachine(); m.word(O['MCL_POS'],6); m.options(0,5,0); m.fn('ringnav_preload')
    assert m.fn('ringnav_mode',mode)==1 and m.mcl('MCL_PRELOAD')==0
    m.advance_song(); assert m.current()==want and m.stops==0 and len(m.starts)==1
    checks+=1

# The Library's grouped shuffles are library queues, including when grouping by folders.
for folder in (0,1):
    m=PlaybackMachine(); all=m.deque(list(m.items(m.get(syms['mcl_pdeqplaylist']))))
    assert m.fn('playback_groups',all,folder)==1
    assert m.mcl('MCL_TYPE')==0xf001 and m.opened[-1][3]==0xf001
    checks+=1

# V1 snapshots (pre-1.0, no queue class) and a bad class are refused.
def fnv(data):
    value=2166136261
    for b in data: value=((value^b)*16777619)&0xffffffff
    return value
def snapshot_checksum(data):
    struct.pack_into('<I',data,12,0); struct.pack_into('<I',data,12,fnv(data))
legacy=bytearray(files['/mnt/data/ringnav-queue']); del legacy[52:56]
struct.pack_into('<I',legacy,4,1); struct.pack_into('<I',legacy,8,len(legacy)); snapshot_checksum(legacy)
r=PlaybackMachine({'/mnt/data/ringnav-queue':bytes(legacy)}); r.byte(syms['g_memory_play'],2)
assert r.fn('ringnav_memory',r.deque([]))==-1
bad=bytearray(files['/mnt/data/ringnav-queue']); struct.pack_into('<I',bad,52,5); snapshot_checksum(bad)
r=PlaybackMachine({'/mnt/data/ringnav-queue':bytes(bad)}); r.byte(syms['g_memory_play'],2)
assert r.fn('ringnav_memory',r.deque([]))==-1
# A version 2 snapshot (its Play Next one run, forced and force_end in the header) restores: the run
# comes back as the list, in order, with the queue and position, and the next save writes version 3.
m=PlaybackMachine(); m.options(1,5,0); q=m.get(syms['mcl_pdeqplaylist']); old=m.current()
m.deqs[q][1][old+1:old+1]=[m.song('next%d'%i) for i in range(3)]; m.fn('playback_insert',old+1,3,1); m.fn('playback_save')
v3=m.files['/mnt/data/ringnav-queue']; hdr=struct.unpack_from('<14I',v3); n,hn=hdr[4],hdr[11]
assert hdr[12]==3
body=bytearray(v3[56:]); at=(n*3+hn)*4; run=struct.unpack_from('<3I',body,at)
assert run==(old+1,old+2,old+3)
v2=struct.pack('<15I',0x5132524e,2,0,0,*hdr[4:12],run[0]+1,run[2]+1,hdr[13])+bytes(body[:at]+body[at+12:])
v2=bytearray(v2); struct.pack_into('<I',v2,8,len(v2)); snapshot_checksum(v2)
names=m.names(); want=m.current()
r=PlaybackMachine({'/mnt/data/ringnav-queue':bytes(v2)}); r.byte(syms['g_memory_play'],2)
r.library_rows.extend(r.song('next%d'%i) for i in range(3))
assert r.fn('ringnav_memory',r.deque([]))==want and r.names()==same(names)
assert up_names(r)[:3]==['next0','next1','next2'] and up_names(r)==same(up_names(m))
r.fn('playback_save'); saved=struct.unpack_from('<14I',r.files['/mnt/data/ringnav-queue'])
assert saved[1]==3 and saved[12]==3
checks+=1

# List Play stops both Next and natural completion at the selected library queue's end.
# Exercise actual stock loading, restored identity, and switching from advanced repeat.
for cls in (0xf001,0xff10,0xff11):
    for skip in (0,1):
        for automatic in (0,1):
            for count in (1,3):
                for origin in ('fresh','restored','advanced'):
                    m=PlaybackMachine()
                    tracks=[('first','/A/1','Album A',1,1,0,0),
                            ('second','/A/2','Album A',1,2,0,0),
                            ('last','/B/1','Album B',1,1,0,0)][:count]
                    m.install(tracks)
                    q=m.deque(list(m.items(m.get(syms['mcl_pdeqplaylist']))))
                    m.fn('ringnav_load',q,0,cls)
                    if origin!='fresh':
                        m.word(O['MCL_POS'],count-1); m.options(0,5,0)
                        m.fn('ringnav_preload'); assert m.mcl('MCL_PRELOAD')==1
                        if origin=='restored':
                            m.fn('playback_save')
                            m=PlaybackMachine(m.files); m.install(tracks)
                            m.byte(syms['g_memory_play'],2)
                            out=m.deque([])
                            assert m.fn('ringnav_memory',out)==count-1
                            m.fn('ringnav_load',out,count-1,1)
                            m.fn('ringnav_preload'); assert m.mcl('MCL_PRELOAD')==1
                        m.fn('ringnav_mode',0)
                        assert m.mcl('MCL_PRELOAD')==0 and m.mcl('MCL_PREPOS')==-1
                    m.byte(0xa3be53,skip)
                    callback=0x1000020; m.handlers[callback]='p:folder_skip'
                    m.word(0xa3bda8,callback); m.folder_skips=0
                    if origin=='fresh':
                        for at in range(1,count):
                            starts=len(m.starts)
                            assert m.advance_song(automatic)==1 and m.current()==at
                            assert len(m.starts)==starts+1 and m.stops==0
                    q=m.get(syms['mcl_pdeqplaylist'])
                    def identity():
                        rows=m.items(q)
                        return (m.get(syms['mcl_pdeqplaylist']),m.mcl('MCL_TYPE'),
                                m.get(syms['g_memory_info']),list(rows),
                                [bytes(m.u.mem_read(row,0x60)) for row in rows],
                                [[m.text(m.get(row+O[key])) for key in
                                  ('REC_NAME','REC_PATH','REC_ALBUM','REC_ARTIST')] for row in rows])
                    before=identity(); starts=len(m.starts); stops=m.stops
                    assert m.mcl('MCL_TYPE')==cls
                    # Even a pending stale preload must not hand off beyond the boundary.
                    m.word(O['MCL_PRELOAD'],1); m.word(O['MCL_PREPOS'],0); m.word(O['MCL_FD'],9)
                    sent=len(m.sent)
                    for action in (automatic,0,0):
                        result=m.advance_song(action)
                        assert result==-1, (cls,skip,automatic,count,origin,action,result,
                                            m.mcl('MCL_MODE'),m.current(),len(m.items(q)))
                        stops+=1
                        assert m.stops==stops and len(m.starts)==starts
                        assert m.current()==count-1 and m.folder_skips==0
                        assert m.mcl('MCL_PRELOAD')==0 and m.mcl('MCL_PREPOS')==-1
                        assert identity()==before
                    assert m.sent[sent:]==[(9,b'{mcl-closegapless\\null}',23,0)]
                    checks+=1

# Stock List Play must discard advanced repeat preloads and retain queue provenance on reboot.
for cls in (0xf001,0xff10,1):
    for skip in (0,1):
        m=PlaybackMachine(); m.word(O['MCL_TYPE'],cls); m.word(O['MCL_POS'],6)
        m.options(0,5,0); m.fn('ringnav_preload'); m.fn('playback_save')
        r=PlaybackMachine(m.files); r.byte(syms['g_memory_play'],2); out=r.deque([])
        assert r.fn('ringnav_memory',out)==6
        assert r.mcl('MCL_TYPE')==cls
        assert r.get(syms['g_memory_info'])==cls
        r.fn('ringnav_load',out,6,1) # legacy startup caller must not reclassify the restored queue
        assert r.mcl('MCL_TYPE')==cls
        r.fn('ringnav_preload'); assert r.mcl('MCL_PRELOAD')==1
        r.byte(0xa3be53,skip)
        assert r.fn('ringnav_mode',0)==1 and r.mcl('MCL_PRELOAD')==0
        assert r.mcl('MCL_PREPOS')==-1 and r.mcl('MCL_MODE')==0
        # Run native List Play, including its folder-only callback at the boundary.
        callback=0x1000020; r.handlers[callback]='p:folder_skip'
        r.word(0xa3bda8,callback); r.folder_skips=0
        r.advance_song()
        assert r.folder_skips==(1 if cls==1 and skip else 0)
        assert r.current()==6 and r.stops==1
        checks+=1

# Boot applies the saved stock mode before restoring the queue, then again when hciplayer
# connects. Execute config_init's actual call site: neither startup call is a mode choice.
for folder in (0,1):
    for mode in range(4):
        m=PlaybackMachine()
        assert m.fn('playback_groups',m.deque(list(m.items(m.get(syms['mcl_pdeqplaylist'])))),folder)==1
        m.advance_song(); m.fn('ringnav_savequeue'); want=m.sequence()
        r=PlaybackMachine(m.files); r.byte(syms['g_memory_play'],2); out=r.deque([])
        r.handlers.pop(syms['config_playmode'],None)
        r.u.reg_write(UC_MIPS_REG_GP,0xa26cc0)
        r.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        r.u.reg_write(UC_MIPS_REG_V0,mode)
        r.u.reg_write(UC_MIPS_REG_A1,0)
        r.u.emu_start(0x4f9998,0x4f99a4,count=r.budget)
        assert r.u.reg_read(UC_MIPS_REG_PC)==0x4f99a4 and r.eq_boots==1
        assert r.mcl('MCL_MODE')==mode
        assert r.files['/mnt/data/ringnav-queue']==m.files['/mnt/data/ringnav-queue']
        at=r.fn('ringnav_memory',out); assert at==want[0] and r.seek==37
        r.fn('ringnav_load',out,at,0xf001)
        r.word(O['MCL_FD'],9); r.sent=[]
        if mode==3: r.config['MQA']='1'
        r.call(address=0x513374,args=(0,0,0,0),gap=0) # player_initconfig
        assert '/mnt/data/ringnav-queue' in r.files and r.sequence()==want
        # The connected hciplayer gets MQA's mode: off (2) skips its start-of-track probe.
        mqa=lambda: [x[1] for x in r.sent if x[1].startswith(b'{mcl-mqa')]
        assert mqa()==[b'{mcl-mqadecodemode\\0}' if mode==3 else b'{mcl-mqadecodemode\\2}']
        # A real mode choice still clears the snapshot after startup, and sends no MQA mode.
        r.sent=[]
        r.call(address=syms['config_playmode'],args=(mode,0,0,0),gap=0)
        assert not mqa()
        # Stock Shuffle (2) keeps Shuffle Albums/Folders; every other mode ends them.
        assert ('/mnt/data/ringnav-queue' in r.files) == (mode == 2)
        checks+=1

# Previous then Next returns to the track Previous left instead of skipping it.
m=PlaybackMachine(); m.options(2,5,0); m.advance_song(0); m.advance_song(0)
left=m.current(); assert m.fn('ringnav_prev')==1 and m.current()!=left
m.fn('ringnav_next',0); assert m.current()==left
checks+=1

# Shuffle Albums/Folders end with a library queue (its hold menu gate follows them); Shuffle Tracks
# and repeat carry on, and the groups a shuffle starts survive the load it makes.
for shuffle,kept in ((3,0),(4,0),(2,2),(1,1)):
    m=PlaybackMachine(); m.options(shuffle,5,0); m.advance_song(0)
    m.fn('ringnav_load',m.deque([m.song('library')]),0,0xf001)
    assert m.fn('playback_option',0)==kept
    assert ('/mnt/data/ringnav-queue' in m.files)==(kept!=0)
m=PlaybackMachine(); m.options(3,5,0); m.advance_song(0)
assert m.fn('playback_groups',m.deque(list(m.items(m.get(syms['mcl_pdeqplaylist'])))),0)==1
assert m.fn('playback_option',0)==3
checks+=1

# Empty replacement cannot resurrect the previous saved queue at the next boot.
m=PlaybackMachine(); m.options(0,2,0); assert '/mnt/data/ringnav-queue' in m.files
m.fn('ringnav_load',m.deque([]),0,1); m.fn('playback_save')
assert '/mnt/data/ringnav-queue' not in m.files
checks+=1

# Empty and one-track queues, all combinations.
for n in (0,1):
    for shuffle in range(5):
        for repeat in range(6):
            m=PlaybackMachine(); m.install([('one','/one/song','',0,0,0,0)]*n)
            m.options(shuffle,repeat,0)
            assert m.fn('playback_successor',1)==(0 if n and repeat>=3 else -1)
            m.advance_song(); checks+=1
# Capacity: a scan's 65,000 songs fit QUEUE_LIMIT (65,536); one occurrence past it stays a stock queue.
# ponytail: the 65,000 side (sorts and snapshot) takes minutes under emulation, so it is run by hand.
m=PlaybackMachine(); m.u.mem_map(0x1200000,0x1000000); q=m.get(syms['mcl_pdeqplaylist'])
m.deqs[q][1]=[m.song('t')]*65537
assert m.fn('playback_set',0,1)==0 and m.fn('playback_successor',1)==-1; checks+=1

# The wheel's ticks (wheel_sample): one per WHEEL_TICK_UNITS of travel, the rest carried; a reversal
# starts again; the 200 -> 1 seam wraps; stock is handed the touch-down x, so its own logic sees a
# still finger. Ticks go to stock's poster as key down, then up, while the main loop's 20-event
# queue (u16 r, w, full, capacity) keeps WHEEL_QUEUE_FREE slots for a touch's release.
def wheel(samples,m=None,used=0):
    m=m or PlaybackMachine(); m.handlers[O['WHEEL_POST']]='wheel_post'
    info,s,loop,q=m.alloc(0x60),m.alloc(20),m.alloc(0x60),m.alloc(8); posted=[]; seen=[]
    m.word(info+O['WHEEL_INFO_LOOP'],loop); m.word(loop+O['LOOP_QUEUE'],q)
    m.u.mem_write(q,struct.pack('<4H',15,(15+used)%20,used==20,20))
    m.u.hook_add(UC_HOOK_CODE,lambda u,a,n,_: posted.append((m.get(info+O['WHEEL_INFO_EVENT']),
                 m.get(info+O['WHEEL_INFO_KEY']))),begin=O['WHEEL_POST'],end=O['WHEEL_POST'])
    for sample in samples:
        if callable(sample): sample(m); continue  # something the UI does between samples
        x,p=sample; m.word(s,x); m.word(s+8,p); m.fn('wheel_sample',s,1,info); seen.append(m.get(s))
    assert posted[0::2]==[(0x110,k) for _,k in posted[0::2]] and posted[1::2]==[(0x114,k) for _,k in posted[0::2]]
    return [k for _,k in posted[0::2]],seen
P,N,U=O['KEY_PREV'],O['KEY_NEXT'],O['WHEEL_TICK_UNITS']
assert wheel([(50,1),(50+U//2,1),(52+U,1),(50+2*U,1),(50+2*U,0)])==([P,P],[50]*5)  # the 2 past a tick carries
assert wheel([(50,1),(48+U,1),(44+U,1),(48,1),(48,0)])[0]==[N]       # U-2 on, then U back from the turn
assert wheel([(195,1),((195+U)%200,1)])[0]==[P] and wheel([((195+U)%200,1),(195,1)])[0]==[N]  # the seam
assert wheel([(10,1),(10+2*U,1)])[0]==[P,P]                          # one sample, two ticks
assert wheel([(10,1),(9+U,1)])[0]==[] and wheel([(10,1),(10+U,1)])[0]==[P]  # one tick is U of travel
assert wheel([(50,1),(52+U,1),(52+U,0),(52+U,1),(51+2*U,1)])[0]==[P]  # a lift drops the carry
F=O['WHEEL_QUEUE_FREE']  # a busy UI: a tick that would leave fewer free slots is dropped, not queued
assert [len(wheel([(10,1),(10+U,1)],used=n)[0]) for n in (20-F-2,20-F-1,20)]==[1,0,0]
checks+=1
if 'wheel_raw' in ps:  # iPod: while games.c's Vortex sets wheel_raw, nothing ticks and the travel adds up raw
    raw=lambda on: lambda m: m.word(ps['wheel_raw'],on)
    m=PlaybackMachine()
    assert wheel([raw(1),(195,1),(5,1),(5+2*U,1),(5+U,1),(5+U,0)],m)==([],[195]*5) and m.get(ps['wheel_travel'])==10+U
    assert wheel([raw(0),(10,1),(10+U,1)],m)[0]==[P] and m.get(ps['wheel_travel'])==10+U
    # wheel_held: set from a finger's first sample to its lift, raw or not (the lyrics' snap).
    for on in (0,1):
        held=[]; h=lambda m: held.append(m.get(ps['wheel_held']))
        wheel([raw(on),(10,0),h,(10,1),h,(10+U,1),h,(10+U,0),h],m); assert held==[0,1,1,0],(on,held)
    checks+=1
# Wheel sensitivity (Q2POD WHEELSENSITIVITY, 50-200% in tens, 1.0.6's key and scale): the travel
# per tick is 50/pct of U, in sixteenths of a unit, so 50% is exactly U (28), the wheel before the
# setting, and every step differs. A fresh card, a sample before the first read and any value that
# is not a canonical ten in range all get 50%.
SIXTEENTHS={pct:(U*16*50+pct//2)//pct for pct in range(50,201,10)}
assert U==28 and SIXTEENTHS[50]==16*U and SIXTEENTHS[100]==8*U and SIXTEENTHS[200]==4*U
assert len(set(SIXTEENTHS.values()))==16 and SIXTEENTHS[130]==172  # 10.75 units: 130% is 10 3/4
def sensitivity(config=None,read=True):
    m=PlaybackMachine()
    if config is not None: m.config['WHEELSENSITIVITY']=config
    return m,(m.fn('wheel_pct') if read else None)
def travel(m):  # the units of a turn's first tick, a unit per sample
    for u in range(1,100):
        if wheel([(10,1),(10+u,1),(10+u,0)],m)[0]: return u
def spin(m,units=600):  # ticks in three turns, a unit per sample, over the 200 -> 1 seam
    return len(wheel([((9+k)%200+1,1) for k in range(units+1)],m)[0])
assert travel(sensitivity(read=False)[0])==U  # not read yet: 50%
for config,pct in ((None,50),('50',50),('130',130),('200',200),('0',50),('40',50),('210',50),('135',50),
                   ('-50',50),('050',50),(' 50',50),('50x',50),('abc',50),('',50)):
    m,got=sensitivity(config); assert got==pct and travel(m)==-(-SIXTEENTHS[pct]//16),(config,got)
for pct in (50,100,130,150,160,200):  # the carry keeps the sixteenths: 150% and 160% differ
    assert spin(sensitivity(str(pct))[0])==600*16//SIXTEENTHS[pct],pct
assert spin(sensitivity('150')[0])==64 and spin(sensitivity('160')[0])==68
checks+=1
# Setting it: the nearest ten, clamped, live on the next sample and saved; an unchanged value is
# not written again.
for n,pct in ((130,130),(74,70),(75,80),(20,50),(250,200),(50,50)):
    m,_=sensitivity(); m.calls=[]; m.fn('wheel_set_pct',n)
    saved=[(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
    assert m.fn('wheel_pct')==pct and travel(m)==-(-SIXTEENTHS[pct]//16) and saved==([] if pct==50 else [(pct,'Q2POD','WHEELSENSITIVITY')]),n
    assert sensitivity(str(pct))[1]==pct  # read back after a reboot
checks+=1
# A change mid-turn (the sensitivity page takes the wheel) keeps the carry's share of a tick, so it
# fires no burst: 27 of 28 carried is 13.5 of 14 at 100%, and one more unit is one tick, not two.
fast,slow=(lambda m: m.fn('wheel_set_pct',100)),(lambda m: m.fn('wheel_set_pct',50))
assert wheel([(10,1),(10+U-1,1),fast,(10+U,1)])[0]==[P]
assert wheel([(10,1),(10+U-1,1),fast,(10+U,1),(9+U+U//2,1)])[0]==[P] and wheel([(10,1),(10+U-1,1),fast,(10+U,1),(10+U+U//2,1)])[0]==[P,P]
# From 100%, 13 of 14 is 26 of 28: two more units tick, one does not; a reversal still starts over.
for more,want in ((1,[]),(2,[P]),(-1,[])):
    assert wheel([(10,1),(23,1),slow,(23+more,1)],sensitivity('100')[0])[0]==want,more
checks+=1
# Volume guard on (Q2POD VOLGUARD, #46), screen off: a touch's first WHEEL_DARK_GUARD units one way
# tick nothing and are dropped; then it ticks as lit, reversals and new touches too, until WHEEL_DARK_IDLE_MS without travel. A
# reversal, lift or pause under the guard starts it again; travel is units, so sensitivity aside.
D,IDLE=O['WHEEL_DARK_GUARD'],O['WHEEL_DARK_IDLE_MS']
lit=lambda m: m.byte(syms['g_backlight_status'],1)
def dark(m,on=1):
    m.config['VOLGUARD']=str(on)
    m.byte(syms['g_backlight_status'],0); m.word(ps['wheel_guard'],on)
def later(ms): return lambda m: setattr(m,'now',m.now+ms)
def turn(x,n,step=1): return [((x+step*k-1)%200+1,1) for k in range(1,n+1)]  # a unit per sample from x
X=10+D-1  # where D-1 units up from 10 stop
assert D==100 and wheel([dark,(10,1)]+turn(10,D-1))[0]==[]           # a brush under half a turn
assert wheel([dark,(10,1)]+turn(10,D-1),sensitivity('200')[0])[0]==[]  # at any sensitivity
assert wheel([dark,(10,1)]+turn(10,D+2*U-1))[0]==[P]                  # past it: every U ticks
assert wheel([dark,(10,1)]+turn(10,D+2*U))[0]==[P,P]
assert wheel([dark,(150,1)]+turn(150,D+U,-1))[0]==[N]                 # either way, over the seam
assert wheel([dark,(150,1),(90,1),(90,1)]+turn(90,D+U-60,-1))[0]==[N]  # still samples do not reverse
for step,key in ((1,P),(-1,N)):
    samples=[dark,(100,1),((100+90*step-1)%200+1,1),((100+140*step-1)%200+1,1)]
    assert wheel(samples)[0]==[key]                                  # crossing carries its excess
    assert wheel(samples+[((100+(D+2*U)*step-1)%200+1,1)])[0]==[key,key]
assert wheel([(100,1),(100-U//2,1),(100-U//2,1),(100-U,1)])[0]==[N]  # lit carry survives still samples too
assert wheel([dark,(10,1)]+turn(10,D+U//4),sensitivity('200')[0])[0]==[P]
assert wheel([dark,(10,1)]+turn(10,D-1)+turn(X,D+U-1,-1))[0]==[]      # a reversal starts it again
assert wheel([dark,(10,1)]+turn(10,D-1)+turn(X,D+U,-1))[0]==[N]
assert wheel([dark,(10,1)]+turn(10,D-1)+[(X,0),(X,1)]+turn(X,D-1))[0]==[]  # so does a lift
assert wheel([dark,(10,1)]+turn(10,D-1)+[later(IDLE)]+turn(X,U))[0]==[]    # and a pause
assert wheel([dark,(10,1)]+turn(10,D-1)+[later(IDLE-1)]+turn(X,1+U))[0]==[P]
Y=10+D  # open: a new touch and a reversal tick at once, until the wheel idles
assert wheel([dark,(10,1)]+turn(10,D)+[(Y,0),later(IDLE-1),(Y,1)]+turn(Y,U))[0]==[P]
assert wheel([dark,(10,1)]+turn(10,D)+turn(Y,U,-1))[0]==[N]
assert wheel([dark,(10,1)]+turn(10,D)+[(Y,0),later(IDLE),(Y,1)]+turn(Y,D))[0]==[]
assert wheel([(10,1)]+turn(10,U))[0]==[P]                               # lit: as before
assert wheel([lambda m: dark(m,0),(10,1)]+turn(10,U))[0]==[P]           # the guard off (default): as before
assert wheel([dark,(10,1)]+turn(10,D-1)+[lit]+turn(X,U))[0]==[P]       # lit mid-turn ticks at once
assert wheel([dark,(10,1)]+turn(10,D)+[lit]+turn(Y,1)+[dark]+turn(Y+1,U))[0]==[]  # and dark again arms
def ui_pass(m):
    m.handlers[ps['stock_sleep_trampoline']]='stock_sleep'
    m.fn('ringnav_sleep',0x1234)
assert wheel([dark,ui_pass,(10,1)]+turn(10,D)+[(Y,0),lit,ui_pass,dark,(Y,1)]+turn(Y,U))[0]==[]  # wake without wheel travel re-arms
checks+=1
# UI wiring in both variants: stock-style choice menu, selected marker, touch and wheel slider.
m=PlaybackMachine(); m.top=m.node('window','playing_page'); m.stack=[m.top]; m.word(m.top+O['W_PARENT'],m.wm)
m.press(100); assert m.hold()==11
assert m.labels()[-3:]==['Shuffle','Repeat','Group by: Album']
m.pick(m.labels().index('Shuffle')); m.advance(0,clear=False)
assert m.labels()==['Off','All Songs','Songs in Album','Albums','Albums and Songs']
assert any(n.get('image')=='select' for n in m.nodes.values())
m.pick(3); m.advance(0,clear=False); assert m.fn('playback_option',0)==3
# Grouped by folder, the choices name folders.
m=PlaybackMachine(); m.options(3,2,1); m.top=m.node('window','playing_page'); m.stack=[m.top]; m.word(m.top+O['W_PARENT'],m.wm)
m.press(100); assert m.hold()==11; m.pick(m.labels().index('Repeat')); m.advance(0,clear=False)
assert m.labels()==['Play One Song','Play One Folder','Play All','Repeat Song','Repeat Folder','Repeat All']
for folder in (0,1):
    m=PlaybackMachine(); m.options(3,2,folder)
    m.top=m.node('window','playing_page'); m.stack=[m.top]; m.word(m.top+O['W_PARENT'],m.wm)
    m.press(100); assert m.hold()==11
    assert m.labels()[:2]==(['Next Folder','Previous Folder'] if folder else ['Next Album','Previous Album'])
    m.pick(0); m.advance(0,clear=False); assert m.current() not in (0,2,6)
    checks+=1
m=PlaybackMachine(); m.handlers[ps['stock_display_trampoline']]='stock_display'
view=m.node('scroll_view','scroll_view_display'); lst=m.node('list_view','list_view_display',[view])
m.top=m.node('window','display_page',[lst]); m.stack=[m.top]
for child,parent in ((view,lst),(lst,m.top),(m.top,m.wm)): m.word(child+O['W_PARENT'],parent)
m.word(ps['display_sub'],3)  # Display's Controls page
assert m.fn('ringnav_display',m.top,0)==0
assert [n['text'] for n in m.nodes.values() if 'Wheel Sensitivity' in str(n.get('text'))]==['Wheel Sensitivity: 50%']  # the row, test/patch.py's
checks+=1

# Manual Next/Prev at the queue ends runs stock player_change_music through ringnav_change. Stock
# keeps Folder Skip's sibling folder for class 1; other classes stop at the end (Next) or wrap (Prev); advanced
# playback takes mclNextSong/mclPrevSong, then restores MCL_MODE and Folder Skip. Its tail notifications are mocked.
class ChangeMachine(PlaybackMachine):
    TAIL=('strncmp@GLIBC_2.0','strcpy@GLIBC_2.0','toolsCheckMount','config_outputchannel','toolsLoadNextDir',
          'toolsLoadPrevDir','player_refresh_playqueue','mclSetPlayPos','player_get_id3info','notifyPlayStatus',
          'dmrNotifyPlayStatus','sendBtHeadsetPlayStatus','notifyPlayInfo','sendBtHeadsetPlayInfo',
          'notifyRefreshLyric','dlnaRenderSaveUrlMetadata','reset_repeatinfo','initializeDmrQCurrentInfo')
    def __init__(self):
        super().__init__(); self.seen=[]
        for n in self.TAIL: self.handlers[syms[n]]='x:'+n.split('@')[0]
    def hook(self,u,address,size,unused):
        if address in (syms['mclNextSong'],syms['mclPrevSong']): self.seen.append(self.handlers.get(address,'mcl'))
        name=self.handlers.get(address,'')
        if not name.startswith('x:'): return super().hook(u,address,size,unused)
        name=name[2:]; a=u.reg_read(REGS[0]); self.seen.append((name,a) if name=='mclSetPlayPos' else name)
        ret=1 if name in ('toolsCheckMount','toolsLoadNextDir','toolsLoadPrevDir','player_refresh_playqueue') else 0
        self.returns(ret,clobber=True)
for nxt,pos,wrap,folder in ((1,6,0,'toolsLoadNextDir'),(0,0,6,'toolsLoadPrevDir')):
    for active in (0,1):
        for cls in (1,0xff10,0xf001):
            m=ChangeMachine()
            if active: m.options(0,2,0); want=m.fn('playback_successor',0) if nxt else None
            m.word(O['MCL_TYPE'],cls); m.word(O['MCL_POS'],pos); m.word(O['MCL_MODE'],0); m.byte(O['MCL_JUMPFOLDER'],1)
            stop=not active and nxt and cls!=1 # List Play stops a library queue's manual Next at its end
            assert signed(m.call(address=syms['player_change_music'],args=(nxt,0,0,0),gap=0))==(0 if stop else 1)
            if active:
                assert 'mcl' in m.seen and folder not in m.seen and not any(type(e) is tuple for e in m.seen), m.seen
                assert m.mcl('MCL_MODE')==0 and m.u.mem_read(O['MCL_JUMPFOLDER'],1)==b'\1'
                assert not nxt or m.current()==want
            elif cls==1: assert folder in m.seen, m.seen
            elif stop: assert m.stops==1 and folder not in m.seen and not any(type(e) is tuple for e in m.seen), m.seen
            else: assert folder not in m.seen and ('mclSetPlayPos',wrap) in m.seen, m.seen
            checks+=1

print(f'{checks} advanced playback/wheel MIPS checks passed ({variant})')

# Elapsed checkpoints are fixed-size, atomic, and tied to both snapshot and position.
m=PlaybackMachine(); m.options(0,5,0)
full=m.files['/mnt/data/ringnav-queue']; m.elapsed=52; m.fn('playback_save')
checkpoint=m.files['/mnt/data/ringnav-queue-elapsed']
assert len(checkpoint)==16 and m.files['/mnt/data/ringnav-queue']==full
for failure in ('open','write','close','rename'):
    m.fail=failure; m.elapsed=53; m.fn('playback_save')
    assert m.files['/mnt/data/ringnav-queue-elapsed']==checkpoint
m.fail=''; m.fn('playback_save')
def checkpoint_checksum(data): struct.pack_into('<I',data,0,fnv(data[4:]))
for kind in ('valid','truncated','checksum','snapshot','position'):
    c=bytearray(checkpoint)
    if kind=='truncated': c=c[:-1]
    elif kind=='checksum': c[-1]^=1
    elif kind!='valid': struct.pack_into('<I',c,{'snapshot':4,'position':8}[kind],999); checkpoint_checksum(c)
    r=PlaybackMachine({'/mnt/data/ringnav-queue':full,'/mnt/data/ringnav-queue-elapsed':bytes(c)})
    r.byte(syms['g_memory_play'],2)
    assert r.fn('ringnav_memory',r.deque([]))>=0 and r.seek==(52 if kind=='valid' else 37),kind
# Interrupted full-snapshot publication leaves an old checkpoint harmless.
stale=bytearray(checkpoint); stale[4]^=1; checkpoint_checksum(stale)
r=PlaybackMachine({'/mnt/data/ringnav-queue':full,'/mnt/data/ringnav-queue-elapsed':bytes(stale)})
r.byte(syms['g_memory_play'],2); assert r.fn('ringnav_memory',r.deque([]))>=0 and r.seek==37
# Measure actual payload instructions: even exhausted/category-repeat scans occur only once.
for n in (8,128):
    for repeat in (1,4):
        m=PlaybackMachine(); m.budget=2000000
        m.install([(str(i),'/a/'+str(i),'A' if i==0 else 'B',0,i,0,0) for i in range(n)],0)
        m.options(0,repeat,0)
        cost=[0]; visits=[0]
        # playback's o32 layout: order/cycle follow 88 words (scalar state, then Play Next's list).
        tables=[m.get(ps['s']+352),m.get(ps['s']+356)]
        def count_traversal(u,access,address,size,value,unused):
            if any(start<=address<start+n*12 for start in tables): visits[0]+=1
        read_hook=m.u.hook_add(UC_HOOK_MEM_READ,count_traversal)
        def count_payload(*args): cost[0]+=1
        hook=m.u.hook_add(UC_HOOK_CODE,count_payload,begin=BASE,end=SCRATCH-1)
        expected=m.fn('playback_successor',0); uncached=cost[0]; scanned=visits[0]; cost[0]=0
        assert m.fn('playback_successor',1)==expected
        cold=cost[0]; cost[0]=0; visits[0]=0
        for _ in range(10): m.fn('playback_successor',1)
        warm=cost[0]//10; m.u.hook_del(hook)
        for _ in range(10): m.fn('playback_poll')
        m.u.hook_del(read_hook)
        assert scanned>=n-1 and visits[0]==0
        assert warm<100 and cold>=warm
        full_bytes=len(m.files['/mnt/data/ringnav-queue']); m.elapsed+=5; m.fn('playback_save')
        assert len(m.files['/mnt/data/ringnav-queue-elapsed'])==16
        print(f'queue={n} repeat={repeat}: successor uncached={uncached}, cold={cold}, cached={warm} instructions, traversal reads {scanned} -> 0; elapsed save {full_bytes} full -> 16 bytes')
print('Checkpoint and polling performance checks passed')

# A checkpoint publication failure after full commit retries without rewriting the queue.
m=PlaybackMachine(); m.options(0,5,0)
old_checkpoint=m.files['/mnt/data/ringnav-queue-elapsed']
m.advance_song(); m.elapsed=49; m.fail='checkpoint-rename'; m.fn('playback_save')
committed=m.files['/mnt/data/ringnav-queue']; writes=m.write_opens
assert m.files['/mnt/data/ringnav-queue-elapsed']==old_checkpoint
r=PlaybackMachine(m.files); r.byte(syms['g_memory_play'],2)
assert r.fn('ringnav_memory',r.deque([]))==m.current() and r.seek==49
m.fail=''; m.fn('playback_save')
assert m.files['/mnt/data/ringnav-queue']==committed and m.write_opens==writes+1
assert m.files['/mnt/data/ringnav-queue-elapsed']!=old_checkpoint

# Uncommitted checkpoint temporaries never override the committed full snapshot.
r=PlaybackMachine({'/mnt/data/ringnav-queue':full,'/mnt/data/ringnav-queue-elapsed.tmp':checkpoint})
r.byte(syms['g_memory_play'],2)
assert r.fn('ringnav_memory',r.deque([]))>=0 and r.seek==37

# MP4 admission uses the requested queue row; a refused start reports once, and a later
# fitting track can start. Nonlocal records never inspect a local file.
m=PlaybackMachine(); m.handlers[ps['mp4_fits']]='p:mp4_fits'; m.fits=0; m.mp4_checks=[]
r=m.items(m.get(syms['mcl_pdeqplaylist']))[2]
m.word(r+O['REC_PATH'],m.string('/A/book.m4b'))
assert m.fn('ringnav_start_player')==-1 and m.mp4_checks==['/A/book.m4b']
m.fn('playback_poll'); m.fn('playback_poll')
assert m.toasts==['Too long to play on the Q2: split it into parts']
m.fits=1; assert m.fn('playback_start_fits')==1
m.word(r+O['REC_TYPE'],0)
m.fits=0; m.mp4_checks=[]
assert m.fn('playback_start_fits')==1 and not m.mp4_checks
m.fn('playback_poll'); assert len(m.toasts)==1
# Equal-speed gapless preloads inspect the successor, not the current track. Refusal
# marks stock's failed-preload state; fitting successors still reach the stock opener.
for fits,want in ((0,-1),(1,1)):
    m=PlaybackMachine(); m.handlers[ps['mp4_fits']]='p:mp4_fits'; m.fits=fits; m.mp4_checks=[]
    m.word(O['MCL_PREPOS'],1); m.word(O['MCL_PRELOAD'],0)
    assert m.fn('ringnav_open_preload')==1
    assert m.mp4_checks==['/B/01.flac'] and m.mcl('MCL_PRELOAD')==want
    m.fn('playback_poll'); assert not m.toasts
# Stock's final-five-seconds gate must not retry a refused successor on every player tick.
m=PlaybackMachine(); m.options(0,5,0)
m.handlers[ps['mp4_fits']]='p:mp4_fits'; m.fits=0; m.mp4_checks=[]
m.handlers.pop(syms['mcl_open_preload'])
m.byte(O['MCL_GAPLESS'],1); m.byte(0xa3be55,1); m.elapsed=296  # stock CUE end-time path
rows=m.items(m.get(syms['mcl_pdeqplaylist']))
m.word(rows[m.current()]+O['REC_CUE_END'],300)
m.word(rows[m.fn('playback_successor',1)]+O['REC_PATH'],m.string('/A/book.m4b'))
for _ in range(10): m.call(address=syms['mclAutoChange'],args=(0,0,0,0),gap=0)
assert m.mp4_checks==['/A/book.m4b'] and m.mcl('MCL_PRELOAD')==-1 and not m.starts
print('MP4 admission: start refusal/recovery, nonlocal bypass, one-shot toast and gapless admission passed')

# Folder speed follows the file, including mixed queues, CUE entries and fresh processes.
def speed_at(m, at):
    m.word(O['MCL_POS'], at); m.fn('playback_speed_start')
    return m.u.mem_read(syms['g_playspeed_mode'],1)[0]

m=PlaybackMachine()
assert speed_at(m,0)==0
assert m.fn('ringnav_speed',2)==1 and m.sent_speed==2
assert speed_at(m,2)==2 and speed_at(m,6)==2  # same parent, different/duplicate tracks
assert speed_at(m,1)==0 and speed_at(m,3)==0
m.fn('ringnav_speed',3)
assert speed_at(m,4)==3  # both CUE chapters share their file's folder
fresh=PlaybackMachine(m.files)
assert speed_at(fresh,0)==2 and speed_at(fresh,1)==0 and speed_at(fresh,4)==3
# Returning to normal clears the effective preference, including after restart.
m.fn('ringnav_speed',0)
assert speed_at(PlaybackMachine(m.files),3)==0
# Only the exact parent matches: neither a sibling prefix nor a subfolder inherits it.
m.install([('nested','/A/sub/01.flac','',0,0,0,0),('prefix','/AB/01.flac','',0,0,0,0)])
assert speed_at(m,0)==0 and speed_at(m,1)==0
# A decoder reconnect reads the folder preference rather than persisting the stale global value.
m=PlaybackMachine(fresh.files); m.word(O['MCL_POS'],0)
m.u.mem_write(syms['g_playspeed_mode'],b'\x03')
# A MIPS jal to the hook gives it player_initconfig's audited return address.
pc=0x513394
m.u.mem_write(pc,struct.pack('<II',jal(ps['ringnav_speed']),0))
m.u.mem_write(pc+8,struct.pack('<II',j(0x1000000),0))
before=dict(m.files)
m.call(address=pc,args=(3,0,0,0),gap=0)
assert m.sent_speed==2 and m.files==before
# Speed boundaries cannot inherit the old gapless decoder; equal speeds still preload.
for next_at,want in ((1,-1),(2,1)):
    m.word(O['MCL_PREPOS'],next_at); m.word(O['MCL_PRELOAD'],0)
    m.fn('ringnav_open_preload'); assert m.mcl('MCL_PRELOAD')==want
# Changing speed closes an existing preload.
m.word(O['MCL_PRELOAD'],1); m.word(O['MCL_FD'],-1)
m.fn('ringnav_speed',1); assert m.mcl('MCL_PRELOAD')==0
# A failed atomic save preserves the old preference and reports the failure.
for failure in ('write','close','rename'):
    m=PlaybackMachine(fresh.files); speed_at(m,0); before=dict(m.files); m.fail=failure
    m.fn('ringnav_speed',1)
    assert m.sent_speed==(0 if failure=='close' else 2) and m.toasts==['Could not save folder speed']
    assert not [k for k,v in before.items() if m.files[k]!=v]
    assert not any(k.endswith(".tmp") for k in m.files)
# Truncated, invalid and wrong-folder records cannot change a track's speed.
path=next(k for k,v in fresh.files.items() if v.startswith(b'/A/\0'))
for data in (b'broken',b'/B/\0'+bytes(1020)+struct.pack('<I',2),fresh.files[path][:-4]+struct.pack('<I',99)):
    bad=dict(fresh.files); bad[path]=data
    assert speed_at(PlaybackMachine(bad),0)==0
for badpath in ('https://host/book.mp3','/A/','/'+'x'*1024+'/a.mp3'):
    m=PlaybackMachine(); m.install([('invalid',badpath,'',0,0,0,0)])
    assert speed_at(m,0)==0
    # No folder (a stream): the change applies to this track only, nothing is saved.
    m.fn('ringnav_speed',2); assert not m.files and m.sent_speed==2 and m.u.mem_read(syms['g_playspeed_mode'],1)[0]==2
# A track's start sends its own folder's speed to the running decoder: a song after a fast book
# plays at normal speed, not the decoder's last speed.
m=PlaybackMachine(); m.word(O['MCL_POS'],0); m.fn('ringnav_speed',2); m.sent_speed=None
m.word(O['MCL_POS'],1); m.fn('playback_track_speed')
assert m.sent_speed==0 and m.u.mem_read(syms['g_playspeed_mode'],1)[0]==0
m.word(O['MCL_POS'],0); m.sent_speed=None; m.fn('playback_track_speed')
assert m.sent_speed==2 and m.u.mem_read(syms['g_playspeed_mode'],1)[0]==2
print('Folder speed: persistence, exact parents, reconnect, gapless and failed saves passed')
