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
        self.word(0xa3a270, 0x1000010); self.handlers[0x1000010] = 'p:free'
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

# View Queue's order (playback_listable): stock Shuffle (MCL_MODE 2, no advanced play) becomes Shuffle
# Songs, keeping no mode to restore; any other stock mode lists stock's queue. Advanced play keeps
# its own settings: Shuffle off (MCL_MODE still 2) lists in queue order, unchanged.
for mode_now,listed in ((2,1),(0,0),(3,0)):
    m=PlaybackMachine(); m.word(O['MCL_MODE'],mode_now)
    assert m.fn('playback_listable')==listed and m.fn('playback_option',0)==listed; checks+=1
m=PlaybackMachine(); m.word(O['MCL_MODE'],2); assert m.fn('playback_listable')==1
assert m.fn('playback_set',0,0)==1 and m.fn('playback_set',1,2)==1
assert m.fn('playback_listable')==0 and [m.fn('playback_option',k) for k in range(2)]==[0,2]; checks+=1

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
    m=PlaybackMachine(page='playing_page',pos=1); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,2); return m
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
m.byte(0xa5002c,1); want=m.fn('playback_successor',1); starts=len(m.starts)
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

# Without advanced play a local queue is kept plain (version 4) while Memory Playback is on, the only
# thing that reads it: an added track, the position and second come back, still without advanced
# play; a queue edit at the same track rewrites it, and a stream's queue leaves it alone.
m=PlaybackMachine(); m.word(O['MCL_TYPE'],0xff01); q=m.get(syms['mcl_pdeqplaylist'])
m.items(q).append(m.song('added')); m.fn('playback_insert',7,1,0); m.fn('playback_save')
assert '/mnt/data/ringnav-queue' not in m.files  # Memory Playback off: nothing written
m.byte(syms['g_memory_play'],2); m.fn('playback_save')
saved=struct.unpack_from('<14I',m.files['/mnt/data/ringnav-queue'])
assert (saved[1],saved[4],saved[5],saved[13])==(4,8,2,0xff01) and m.fn('playback_option',0)==0
r=PlaybackMachine(m.files); r.byte(syms['g_memory_play'],2); r.library_rows.append(r.song('added'))
out=r.deque([]); assert r.fn('ringnav_memory',out)==2 and r.seek==37
assert r.names()==['a2','b1','a1','cue1','cue2','solo','a1','added'] and r.fn('playback_option',1)==2
r.fn('ringnav_load',out,2,0xff01); assert r.fn('playback_option',1)==2 and len(r.names())==8
m.items(q).pop(); m.fn('playback_save')
assert struct.unpack_from('<I',m.files['/mnt/data/ringnav-queue'],16)[0]==7
plain=m.files['/mnt/data/ringnav-queue']; m.word(O['MCL_TYPE'],0xf1); m.items(q).pop(); m.fn('playback_save')
assert m.files['/mnt/data/ringnav-queue']==plain
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
m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,2)
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
    m.byte(0xa5002b,skip); m.fn('ringnav_mode',0)
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
                    m.byte(0xa5002b,skip)
                    callback=0x1000020; m.handlers[callback]='p:folder_skip'
                    m.word(0xa4ff80,callback); m.folder_skips=0
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
        r.byte(0xa5002b,skip)
        assert r.fn('ringnav_mode',0)==1 and r.mcl('MCL_PRELOAD')==0
        assert r.mcl('MCL_PREPOS')==-1 and r.mcl('MCL_MODE')==0
        # Run native List Play, including its folder-only callback at the boundary.
        callback=0x1000020; r.handlers[callback]='p:folder_skip'
        r.word(0xa4ff80,callback); r.folder_skips=0
        r.advance_song()
        assert r.folder_skips==(1 if cls==1 and skip else 0)
        assert r.current()==6 and r.stops==1
        checks+=1

# Boot applies the saved stock mode before restoring the queue; player_initconfig applies it again
# only when hciplayer reconnects after a disconnect. Execute config_init's actual call site: neither
# call is a mode choice.
for folder in (0,1):
    for mode in range(4):
        m=PlaybackMachine()
        assert m.fn('playback_groups',m.deque(list(m.items(m.get(syms['mcl_pdeqplaylist'])))),folder)==1
        m.advance_song(); m.fn('ringnav_savequeue'); want=m.sequence()
        r=PlaybackMachine(m.files); r.byte(syms['g_memory_play'],2); out=r.deque([])
        r.handlers.pop(syms['config_playmode'],None)
        r.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
        r.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        r.u.reg_write(UC_MIPS_REG_V0,mode)
        r.u.reg_write(UC_MIPS_REG_A1,0)
        r.u.emu_start(0x504184,0x504190,count=r.budget)
        assert r.u.reg_read(UC_MIPS_REG_PC)==0x504190 and r.eq_boots==1
        assert r.mcl('MCL_MODE')==mode
        assert r.files['/mnt/data/ringnav-queue']==m.files['/mnt/data/ringnav-queue']
        at=r.fn('ringnav_memory',out); assert at==want[0] and r.seek==37
        r.fn('ringnav_load',out,at,0xf001)
        r.word(O['MCL_FD'],9); r.sent=[]
        r.word(ps['peq_chained'],1) # the filter was attached under the last hciplayer
        r.call(address=0x51e024,args=(0,0,0,0),gap=0) # player_initconfig
        assert '/mnt/data/ringnav-queue' in r.files and r.sequence()==want
        assert r.get(ps['peq_chained'])==0 # a new hciplayer has no filter: peq_chain attaches again
        # A real mode choice still clears the snapshot after startup.
        r.call(address=syms['config_playmode'],args=(mode,0,0,0),gap=0)
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
# Capacity: a scan's 65,000 songs fit QUEUE_LIMIT (65,536); one occurrence past it stays a stock queue,
# refused before rebuild allocates for it.
# ponytail: the 65,000 side (sorts) takes minutes under emulation, so it is run by hand.
m=PlaybackMachine(); m.u.mem_map(0x1200000,0x1000000); q=m.get(syms['mcl_pdeqplaylist'])
m.deqs[q][1]=[m.song('t')]*65537; before=m.allocations
assert m.fn('playback_set',0,1)==0 and m.allocations==before and m.fn('playback_successor',1)==-1; checks+=1

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
# main_loop_queue_event (build.py QUEUE_LEAF, playback.c ringnav_queue_event): the UI loop's one way
# in. The touch thread posts an event of type 0 for every pressureless sample while it holds no
# finger, and twenty of them evict a queued release; they go nowhere, so they are not queued. Every
# other request reaches the loop's own poster; a missing loop, slot or request is RET_BAD_PARAMS.
class PostMachine(PlaybackMachine):
    slot=syms['main_loop_queue_event']+0x10  # the loop's poster: stock code never otherwise reached
    def __init__(self):
        super().__init__(); self.posted=[]; self.handlers[self.slot]='post'
    def hook(self,u,address,size,unused):
        if address!=self.slot: return super().hook(u,address,size,unused)
        self.posted.append(self.get(u.reg_read(REGS[1]))); self.returns(0,clobber=True)
def post(kind,loop_ok=1,slot_ok=1):
    m=PostMachine(); loop,r=m.alloc(0x60),m.alloc(0x30)
    if slot_ok: m.word(loop+O['LOOP_POST'],m.slot)
    m.word(r,kind)
    return m.fn('ringnav_queue_event',loop if loop_ok else 0,r),m.posted
assert post(0x103)==(0,[0x103]) and post(0xff)==(0,[0xff]) and post(0x110)==(0,[0x110]) and post(0x1000)==(0,[0x1000])
assert post(0)==(0,[]) and post(0x103,loop_ok=0)==(0x10,[]) and post(0x103,slot_ok=0)==(0x10,[])
m=PostMachine(); loop=m.alloc(0x60); m.word(loop+O['LOOP_POST'],m.slot); assert m.fn('ringnav_queue_event',loop,0)==0x10 and not m.posted
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
# Wheel sensitivity (Q2POD WHEELSENSITIVITY, 10-200% in tens, 1.0.6's key and scale): the travel
# per tick is 50/pct of U, in sixteenths of a unit, so 50% is exactly U (28), the wheel before the
# setting, and every step differs. A fresh card, a sample before the first read and any value that
# is not a canonical ten in range all get 50%.
SIXTEENTHS={pct:(U*16*50+pct//2)//pct for pct in range(10,201,10)}
assert U==28 and SIXTEENTHS[50]==16*U and SIXTEENTHS[100]==8*U and SIXTEENTHS[200]==4*U
assert len(set(SIXTEENTHS.values()))==20 and SIXTEENTHS[130]==172  # 10.75 units: 130% is 10 3/4
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
for config,pct in ((None,50),('50',50),('130',130),('200',200),('0',50),('40',40),('20',20),('210',50),('135',50),
                   ('-50',50),('050',50),(' 50',50),('50x',50),('abc',50),('',50)):
    m,got=sensitivity(config); assert got==pct and travel(m)==-(-SIXTEENTHS[pct]//16),(config,got)
for pct in (50,100,130,150,160,200):  # the carry keeps the sixteenths: 150% and 160% differ
    assert spin(sensitivity(str(pct))[0])==600*16//SIXTEENTHS[pct],pct
assert spin(sensitivity('150')[0])==64 and spin(sensitivity('160')[0])==68
checks+=1
# Setting it: the nearest ten, clamped, live on the next sample; the page saves it (crossfade.c's
# refresh, test/patch.py), not wheel_set_pct.
for n,pct in ((130,130),(74,70),(75,80),(20,20),(34,30),(250,200),(50,50)):
    m,_=sensitivity(); m.calls=[]; m.fn('wheel_set_pct',n)
    saved=[(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
    assert m.fn('wheel_pct')==pct and travel(m)==-(-SIXTEENTHS[pct]//16) and saved==[],n
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
import random,zlib
# The encoder thread whole, with no finger assumed: stock's own loop (0x6388c8, get_direction's tail,
# with ringnav_ts_read in it) reads a mock ts_read and posts to a 20-event queue that drops its oldest,
# as main_loop_simple_queue_event does. A UI that drains after each read is one keeping up.
INPUT=HOOKS['window_manager_dispatch_input_event'][0]; LOG='/mnt/data/q2-wheel.log'; DEV=manifest['dev']
class EncoderMachine(PlaybackMachine):
    GP=0xa3aae0
    def __init__(self,pct=50):
        super().__init__(); self.q=[]; self.sample=None; self.keys=[]
        self.sp=self.alloc(0x4000)+0x3000; self.info=self.sp+0x20
        loop,self.qw=self.alloc(0x60),self.alloc(8)
        self.word(self.info+O['WHEEL_INFO_LOOP'],loop); self.word(loop+O['LOOP_QUEUE'],self.qw); self.word(self.sp+0x2c,1); self.word(self.sp+0x18,self.GP)
        self.handlers[O['WHEEL_POST']]='enc:post'; self.handlers[self.get(0xa3a120)]='enc:read'  # ts_read's GOT slot
        # the path a button takes, ringnav_queue_event, posting through the loop's own poster
        self.loop=self.alloc(0x60); self.word(self.loop+O['LOOP_POST'],0x1000020); self.handlers[0x1000020]='enc:loop'; self.loop_posts=[]
        self.handlers[ps['stock_input_trampoline']]='stock_input'
        self.log=b''; self.log_opens=0; self.synced=0; self.log_fail=False; self.threads=[]; self.spawned=0; self.hold=False; self.thread_fail=0
        for name,short in (('fopen@GLIBC_2.2','fopen'),('fclose@GLIBC_2.2','fclose'),('fputs@GLIBC_2.0','fputs'),('fseek@GLIBC_2.0','fseek'),
                           ('ftell@GLIBC_2.0','ftell'),('fflush@GLIBC_2.0','fflush'),('fileno@GLIBC_2.0','fileno'),('fsync@GLIBC_2.0','fsync'),
                           ('pthread_create@GLIBC_2.2','thread'),('pthread_detach@GLIBC_2.0','detach')):
            self.handlers[syms[name]]='enc:'+short
        self.sync(); self.fn('wheel_set_pct',pct)
        self.start=self.now; self.now=1<<30  # long past any frame's stamp, which is what the gate is about
    def sync(self):
        n=len(self.q); self.u.mem_write(self.qw,struct.pack('<4H',15,(15+n)%20,n==20,20))
    def push(self,event):
        self.q.append(event); self.q[:-20]=[]; self.sync()
    def drain(self): self.q=[]; self.sync()
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('enc:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='enc:post':
            event=self.get(self.info+O['WHEEL_INFO_EVENT']),self.get(self.info+O['WHEEL_INFO_KEY'])
            self.push(event)
            if event[0]==0x110: self.keys.append(event[1])
        elif name=='enc:read':
            x,p,ret=self.sample
            for i,v in enumerate((x,0,p,self.now//1000,self.now%1000*1000)): self.word(b+4*i,v)
        elif name=='enc:loop': self.loop_posts.append((self.get(b),self.get(b+O['EVENT_KEY'])))
        elif name=='enc:fopen':
            assert self.text(a)==LOG and self.text(b)=='ab'
            self.log_opens+=1; ret=0 if self.log_fail else 0x777
        elif name=='enc:fputs': self.log+=self.text(a).encode()
        elif name=='enc:ftell': ret=len(self.log)
        elif name=='enc:fsync': self.synced+=1
        elif name=='enc:thread':
            ret=self.thread_fail
            if not ret: self.threads.append(c); self.spawned+=1
        return self.returns(ret)
    def run_threads(self):  # a flush's thread, once its creator is off the emulator
        if self.hold: return
        while self.threads: self.call(address=self.threads.pop(),args=(0,0,0,0),gap=0,clear=False)
    def read(self,x,p,ret=1,gap=10):  # one pass of the thread's loop: the ticks it posted
        self.now+=gap; self.sample=(x,p,ret); self.keys=[]
        for r,v in ((UC_MIPS_REG_SP,self.sp),(UC_MIPS_REG_GP,self.GP),(UC_MIPS_REG_S0,O['WHEEL_POST']),(UC_MIPS_REG_S1,self.info),
                    (UC_MIPS_REG_S4,self.get(self.GP-0x7f44)),(UC_MIPS_REG_RA,0x1000000),(REGS[0],1)): self.u.reg_write(r,v)
        self.u.emu_start(0x6388cc,0x6388c8,count=500000)
        assert self.u.reg_read(UC_MIPS_REG_PC)==0x6388c8 and self.u.reg_read(UC_MIPS_REG_SP)==self.sp
        keys=self.keys; self.run_threads(); return keys
    def turn(self,n,step,gap,x=100):  # a finger going round: the ticks, stamped
        ticks=[]
        if x: self.read(x,1,gap=gap); self.x=x
        for _ in range(n):
            self.x=(self.x+step-1)%200+1
            ticks+=[(self.now+gap,k) for k in self.read(self.x,1,gap=gap)]; self.drain()
        return ticks
    def press(self,key=O['KEY_RETURN'],kind=0x110):  # a button's event, as its thread posts it
        r=self.alloc(0x40); self.word(r,kind); self.word(r+O['EVENT_KEY'],key)
        self.call(address=ps['ringnav_queue_event'],args=(self.loop,r,0,0),gap=0,clear=False); self.run_threads()
    def input(self,kind,key):  # what ringnav_input lets on to stock's window manager
        self.call(key,address=INPUT,args=(self.wm,self.event,0,0),event_type=kind,gap=0)
        n=sum(c[0]=='stock_input' for c in self.calls); self.run_threads(); return n
    def gated(self):  # the queue, through ringnav_input only
        through=[(kind,key) for kind,key in self.q if self.input(kind,key)]
        self.drain(); return through
    def deliver(self):  # the same, and a release it passes reaches ringnav, as stock's would
        through=[]
        for kind,key in self.q:
            if self.input(kind,key):
                through.append((kind,key))
                if kind==0x114: self.call(key,gap=0)
        self.drain(); return through
    def mute(self): return self.get(ps['wheel_mute'])
SIXTEENTHS_MOST=lambda pct: 100*16//SIXTEENTHS[pct]+1  # half a turn's ticks, and the carry's
# Ordinary use, stamped, at four sensitivities: a slow turn over the seam, a fast one, a reversal, a
# rest, a tap and a flick. The same ticks at the same times mean the same list ramp downstream; this
# number is the code before the gate and Return's stop, so normal feel did not move. It covers what's
# in it: none of Volume Guard, the raw travel, a fractional step or a lost lift.
ORDINARY_WANT=0x1ac3e3c3
def ordinary(pct):
    m=EncoderMachine(pct); m.now=m.start; out=[]
    out+=m.turn(300,1,20); m.read(m.x,0); m.now+=300
    out+=m.turn(40,15,10,x=30); m.read(m.x,0); m.now+=150
    out+=m.turn(60,3,15,x=150); out+=m.turn(60,-3,15,x=0); m.now+=800; out+=m.turn(30,2,15,x=0); m.read(m.x,0); m.now+=500
    assert not m.read(40,1)+m.read(40,0); m.now+=90
    out+=m.turn(4,12,10,x=60); m.read(m.x,0)
    return out
assert zlib.crc32(repr([ordinary(pct) for pct in (50,100,130,200)]).encode())==ORDINARY_WANT
# The same with what the CRC left out, against its own number from the code before: a lost lift (the
# next touch goes on from it) and a fractional step, 130%'s, over a mid-turn change; Volume Guard's
# own checks follow this block.
ORDINARY2_WANT=0xb7bde5ad
def ordinary2(pct):
    m=EncoderMachine(pct); m.now=m.start; out=m.turn(50,3,15); m.now+=2000; out+=m.turn(50,3,15,x=0); m.fn('wheel_set_pct',130); out+=m.turn(50,3,15,x=0); m.read(m.x,0)
    return out
assert zlib.crc32(repr([ordinary2(pct) for pct in (50,200)]).encode())==ORDINARY2_WANT
checks+=1
# The driver's x is a byte, and no arc of it is dead: past 200 and at 0 a frame ticks like any other.
for x,want,back in ((200,[P],200-U),(201,[P],201-U),(255,[P],255-U),(1,[N],1+U),(0,[N],U)):
    m=EncoderMachine(); m.read(back,1)
    assert m.read(x,1)==want,x
# A real lift leaves stock's path nothing to tick on, however many samples with no pressure follow.
m=EncoderMachine(); m.turn(3,U,10); m.read(m.x,0)
for x in (21,50,99,101,150,179): assert not m.read(x,0)
checks+=1
r=random.Random(0x5117)
# A fixed seed's hostile samples, any byte (a lift in any state, the seam, repeats, a lost lift, bursts,
# a busy UI, Return at any moment): no lift, however many or wherever they say the finger is, posts a
# tick; one sample is at most half a turn's ticks; a busy UI always leaves a button's press and release
# their slots; and after a lift nothing is stopped.
def hostile(last):
    k=r.randrange(6)
    return last if k==0 else (last+r.randint(-3,3)-1)%200+1 if k==1 else r.choice((1,2,199,200)) if k==2 else r.choice((0,201,255)) if k==3 else r.randint(0,255)
for pct in (50,100,130,200):
    m=EncoderMachine(pct); most=SIXTEENTHS_MOST(pct); last=100
    for touch in range(50):
        for _ in range(r.randrange(1,25)):
            x=hostile(last); gap=r.choice((0,1,10,10,10,40,500)); ret=r.random()>.05
            k=m.read(x,1,ret=int(ret),gap=gap)
            assert len(k)<=most,(pct,x,k)
            if ret: last=x
            assert len(m.q)<=20-O['WHEEL_QUEUE_FREE'],len(m.q)  # the queue as a stalled UI leaves it
            if r.random()<.5: m.drain()
            if r.random()<.1: m.press()
        m.drain()
        if r.random()<.2: continue  # a lost lift: the next touch goes on from it
        for _ in range(r.randrange(1,25)):  # the lift, then whatever follows it with no finger
            k=m.read(hostile(last),0,ret=1,gap=r.choice((0,1,10,1000,6000)))
            assert not k and not m.mute(),(pct,k)
checks+=1
# ringnav_input's gate, from the frame on. The encoder's ticks pass, with their frames fresh; a tick
# whose last frame is WHEEL_FRESH_MS old goes nowhere, its release with it; a release follows its own
# press's decision whatever the frames did in between.
FRESH=O['WHEEL_FRESH_MS']; DOWN_,UP_=0x110,0x114
for kind in (P,N):
    move=100+(U if kind==P else -U)
    m=EncoderMachine()
    m.read(100,1); m.read(move,1); assert len(m.q)==2
    q=list(m.q); assert m.deliver()==q
    m.read(100,1); m.drain(); m.read(move,1); q=list(m.q); m.now+=FRESH-1; assert m.deliver()==q  # just in time
    m.read(100,1); m.drain(); m.read(move,1); m.now+=FRESH; assert m.deliver()==[]
    # a frame stamped just after the gate read its clock: no wrap, no tick dropped mid-spin
    m.word(ps['wheel_frame_at'],m.now+5); assert m.input(DOWN_,kind) and m.input(UP_,kind)
    # a tick with no frame ever, and frames with no pressure: nothing lets it in
    m=EncoderMachine(); assert not m.input(DOWN_,kind) and not m.input(UP_,kind)
    m.read(100,1); m.read(100,0); m.now+=FRESH
    for x in (50,60,70): m.read(x,0,gap=1)
    m.drain(); assert not m.input(DOWN_,kind) and not m.input(UP_,kind)
    # a dropped press's release is dropped with a frame in between, a passed one's passes with none
    m=EncoderMachine(); assert not m.input(DOWN_,kind)
    m.read(100,1); assert not m.input(UP_,kind)
    assert m.input(DOWN_,kind); m.now+=FRESH*5; assert m.input(UP_,kind)
    assert m.input(UP_,kind)  # a release with no press stays stock's business
    # a dropped press whose release was lost: the next press passes, and owns its release
    m=EncoderMachine(); assert not m.input(DOWN_,kind); m.read(100,1); assert m.input(DOWN_,kind) and m.input(UP_,kind)
    # the two wheel keys keep their own decision
    other=N if kind==P else P
    m.now+=FRESH; assert not m.input(DOWN_,kind); m.read(100,1); assert m.input(DOWN_,other) and m.input(UP_,other) and not m.input(UP_,kind)
# Every other key and event goes by, with no frame; a tick on a page that holds it replays with its
# frames long gone (replay_wheel calls ringnav itself): the queue's tick was decided at the gate.
m=EncoderMachine()
for key in (O['KEY_RETURN'],O['KEY_CENTER'],O['KEY_PLAY'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
    assert m.input(DOWN_,key) and m.input(UP_,key),key
w,_=m.page_list(); m.animating=1; m.call(N,gap=0); assert any(v[1]==ps['replay_wheel'] for v in m.timers.values())
before=m.selected(w); m.animating=0; m.word(ps['wheel_frame_at'],0); m.advance(100); assert m.selected(w)!=before and not m.timers
checks+=1
# A real finger, however long: ten minutes at 21 ticks a second one way (and a minute the other), every
# frame's ticks through the gate: never a tick lost or a stop, and a --dev log writes once, past 30 s.
for step,minutes in ((6,10),(-6,1)):
    m=EncoderMachine(); m.read(100,1); m.x=100; n=0; frames=minutes*6000
    for i in range(frames):
        m.x=(m.x+step-1)%200+1; m.read(m.x,1,gap=10); q=len(m.q); assert len(m.gated())==q; n+=q//2
    assert not m.mute() and abs(n-frames*abs(step)//U)<=1 and 21<=n/(frames/100)<22,(n,frames)
    assert m.log_opens==(1 if DEV else 0) and m.log.count(b'== wheel: a run past 30 s')==(1 if DEV else 0)
# A turn slower than any tick gap, the thumb resting up to 999 ms between frames: every tick arrives.
m=EncoderMachine(); m.read(100,1); m.x=100; n=0
for i in range(100):
    m.x=(m.x+U-1)%200+1; k=m.read(m.x,1,gap=FRESH-1); assert k==[P]; n+=len(m.deliver())
assert n==200
# And past that: a tick is made by the frame that stamps it, so a 5 s rest between frames loses none.
m=EncoderMachine(); m.read(100,1); m.x=100; n=0
for i in range(10):
    m.x=(m.x+U-1)%200+1; assert m.read(m.x,1,gap=5000)==[P]; n+=len(m.deliver())
assert n==20
checks+=1
# Return's stop takes two. One Return is a rim press: a thumb can slide to it and carry on with no
# lift, and every tick arrives (35 in 3 s, a 0.8 s rest, on); a lift just after it arms nothing for the
# next touch; a normal Back never stops anything.
def ghost(m,n,step,gap=10,ui=False):
    out=[]
    for _ in range(n):
        m.x=(m.x+step-1)%200+1; out+=m.read(m.x,1,gap=gap)
        if len(m.q)>=8: m.deliver() if ui else m.drain()
    return out
R=O['KEY_RETURN']; TICK=O['WHEEL_ESCAPE_TICK_MS']; STOP=O['WHEEL_STOP_TICKS']
m=EncoderMachine(); m.read(100,1); m.x=100; assert ghost(m,20,5); m.press(); assert not m.mute()
for i in range(35):
    assert ghost(m,1,U,gap=85)==[P] and not m.mute(),i
    m.drain()
m.x=(m.x+U-1)%200+1; assert m.read(m.x,1,gap=800)==[P]; assert ghost(m,3,U,gap=85)==[P]*3
m=EncoderMachine(); m.read(100,1); m.x=100; ghost(m,20,5); m.press(); m.read(m.x,0,gap=1)    # lifted straight after it
m.read(100,1); m.x=100; ghost(m,20,5); m.press(); assert not m.mute() and ghost(m,20,5)
m=EncoderMachine()
for i in range(20):                                                                          # scroll and Back, over and over
    m.read(100,1); m.x=100; assert ghost(m,20,5); m.drain(); m.press(); m.read(m.x,0); assert not m.mute()
assert m.log_opens==0
# A ghost, ring reporting pressure and a moving x with no lift, on a list, on Home each way and under
# Now Playing's volume_dialog. The first Return does its job and ticks carry on; the second, its press
# posted as ever, stops them: what is queued goes nowhere, and no tick for as long as the frames run. A
# lift, and the next touch works, with nothing left behind: no replay timer, the row where it was.
for name,make,step in (('list',lambda m:m.page_list()[0],5),('home next',lambda m:m.page('home_page','slide_menu'),-5),('home prev',lambda m:m.page('home_page','slide_menu'),5),('now playing',lambda m:m.page('playing_page'),5)):
    m=EncoderMachine(); w=make(m); m.read(100,1); m.x=100
    assert ghost(m,50,step,ui=True),name; m.deliver()
    if name=='now playing': dlg=m.node('dialog','volume_dialog'); page=m.top; m.top=dlg  # the tick opened it
    m.press(); m.press(kind=UP_); m.call(R,gap=0); assert not m.mute(),name
    if name=='now playing': m.top=page  # Return closes it (0x4a3790)
    assert ghost(m,60,step),name; m.drain()
    ghost(m,12,step); assert m.q,name; m.press(); assert m.loop_posts[-1]==(DOWN_,R) and m.mute(),name
    assert not m.deliver(),name   # the ticks queued ahead of it
    m.press(kind=UP_); assert m.loop_posts[-1]==(UP_,R); m.call(R,gap=0)
    shown=m.selected(w) if name=='list' else None
    for _ in range(3000):  # 30 s: nothing comes to move a row or reopen the bar
        m.x=(m.x+step-1)%200+1; assert not m.read(m.x,1),name
    assert not m.q and m.mute(),name; m.advance(5000)
    assert not any(v[1]==ps['replay_wheel'] for v in m.timers.values()),name
    if shown is not None: assert m.selected(w)==shown
    assert not m.read(m.x,0) and not m.mute(),name
    m.byte(O['KEY_LOCKOUT'],0)  # Return's own lockout, long over
    k=m.read(100,1)+m.read(100-U,1); assert k==[N] and m.deliver()==[(DOWN_,N),(UP_,N)],name
    if shown is not None: assert m.selected(w)!=shown,name
# Exactly when: ticks since the first Return (the count runs on through a third press), a tick just now
# and a frame just now; its release, another key or no finger arm nothing; and a stop's Returns count once.
def armed():
    m=EncoderMachine(); m.read(100,1); m.x=100; ghost(m,20,5); m.drain(); m.press(); return m
def since(m,n):
    for _ in range(n): assert ghost(m,1,U)==[P]; m.drain()
m=armed(); since(m,STOP-1); m.press(); assert not m.mute(); since(m,1); m.press(); assert m.mute()
m=armed(); since(m,STOP); m.now+=TICK-1; m.press(); assert m.mute()
m=armed(); since(m,STOP); m.now+=TICK; m.press(); assert not m.mute()
m=armed(); since(m,STOP); m.word(ps['wheel_frame_at'],m.now-FRESH); m.press(); assert not m.mute()
m=armed(); m.read(m.x,0); m.read(100,1); m.x=100; since(m,STOP); m.press(); assert not m.mute()         # a lift disarms
m=armed(); m.now+=FRESH; since(m,STOP); m.press(); assert not m.mute()                                  # so does a gap in frames
ARM=O['WHEEL_ARM_MS']  # the two within this of each other; a later press is a new first, with its own count
m=armed(); since(m,STOP); m.word(ps['wh_armed_at'],m.now-ARM+1); m.press(); assert m.mute()
m=armed(); since(m,STOP); m.word(ps['wh_armed_at'],m.now-ARM); m.press(); assert not m.mute(); since(m,STOP-1); m.press(); assert not m.mute(); since(m,1); m.press(); assert m.mute()
m=armed()                                                                                                # a long touch's early Return, then an ordinary one
for _ in range(ARM//85+1): assert ghost(m,1,U,gap=85)==[P]; m.drain()
m.press(); assert not m.mute(); assert ghost(m,3,U,gap=85)==[P]*3
m=EncoderMachine(); m.read(100,1); m.x=100; m.press(kind=UP_); m.press(O['KEY_CENTER']); m.press(O['KEY_PLAY']); since(m,STOP); m.press(); assert not m.mute()
m=EncoderMachine(); m.press(); m.read(100,1); m.x=100; since(m,STOP); m.press(); assert not m.mute() and m.loop_posts==[(DOWN_,R),(DOWN_,R)]  # nothing on the ring
m=EncoderMachine(); m.read(100,1); m.x=100; since(m,STOP); m.read(m.x,0); m.press(); m.read(100,1); m.x=100; since(m,STOP); m.press(); assert not m.mute()  # Back between touches: no first press
m=armed(); since(m,STOP); m.press(); assert m.mute(); m.loop_posts=[]; m.press(); ghost(m,60,5); m.press(); ghost(m,20,5); assert m.mute() and len(m.loop_posts)==2
if DEV: assert m.get(ps['wl_stops'])==1
# The stop ends at a real lift, or a rest in frames from the very first frame after it, however long,
# and with the log's flush done or not: 999 ms is the ghost going on, 1000 a run that ended with no
# lift seen. Then it takes two Returns again.
def stopped():
    m=armed(); since(m,STOP); m.press(); assert m.mute(); return m
for rest in (FRESH,1500,5000,60000):
    m=stopped(); m.x=(m.x+U-1)%200+1; assert m.read(m.x,1,gap=rest)==[P] and not m.mute(),rest   # the first frame after it
    m.drain(); since(m,STOP); assert not m.mute(); m.press(); assert not m.mute(); since(m,STOP); m.press(); assert m.mute()
m=stopped()
for _ in range(3): m.x=(m.x+U-1)%200+1; assert not m.read(m.x,1,gap=FRESH-1) and m.mute()
# A ghost that never lifts: stopped for as long as it lasts, with the buttons free. The clock stands
# still across a Standby, so that is no rest: the stop outlasts it. An hour's rest awake ends it.
m=stopped(); ghost(m,100,5); assert m.mute(); m.press(O['KEY_CENTER']); assert m.loop_posts[-1][1]==O['KEY_CENTER'] and m.input(DOWN_,O['KEY_CENTER'])
m.x=(m.x+4)%200+1; assert not m.read(m.x,1,gap=0) and m.mute()
m.x=(m.x+4)%200+1; m.read(m.x,1,gap=3600*1000); assert not m.mute() and ghost(m,20,5)
# Volume Guard: no shield from a ghost. Screen off, it holds the first WHEEL_DARK_GUARD units of any touch, then a
# ghost with no lift ticks on (docs/internals.md); the stop works dark too.
m=EncoderMachine(); m.word(ps['wheel_guard'],1); m.byte(syms['g_backlight_status'],0)
m.read(100,1); m.x=100; assert not m.read(100+U,1) and ghost(m,100,5); m.drain(); m.press(); since(m,STOP); m.press(); assert m.mute() and not ghost(m,20,5)
# Vortex and the lyrics read the raw travel with a thumb held down as long as they like: not ticks.
m=EncoderMachine(); m.word(ps['wheel_raw'],1); n=m.turn(3000,2,20)
assert m.get(ps['wheel_travel'])==2*3000 and not m.q and not n; m.word(ps['wheel_raw'],0)
m=armed(); m.word(ps['wheel_raw'],1); m.x=100; m.turn(STOP*U,1,20); m.press(); assert not m.mute(); m.word(ps['wheel_raw'],0)  # no stop for them
# Cover Flow's thumb (2) has no ticks either, and a ghost still moves covers: its travel counts for the second
# Return, a tick's worth a tick. Fewer, and a Return is only a Return. Nothing's queued, reversals count too.
m=armed(); m.word(ps['wheel_raw'],2); m.turn(STOP*U-1,1,20); m.press(); assert not m.mute()
m.x=100; m.turn(1,1,20); assert not m.q; m.press(); assert m.mute() and not m.q; m.word(ps['wheel_raw'],0)
checks+=1
# A normal build keeps no trace: none of the log's state, and no thread or file for a stop or a drop.
if not DEV:
    assert not {'wl_frames','wl_flush','wheel_gate_note','wl_stops'}&set(ps)
    m=stopped(); m.drain(); m.read(m.x,0); m.now+=FRESH; assert not m.input(DOWN_,N)
    assert m.spawned==0 and m.log_opens==0 and not m.log
else:
    # The frame log. A frame is stores: no call but the clock; x is noted as it came, whatever it is.
    m=EncoderMachine(); m.read(100,1); m.read(255,1); m.read(0,0); assert m.spawned==0 and not m.log
    # The stop writes once, off the wheel's thread, synced, with the frames in order, the oldest of the
    # last 64, and the counts.
    m=EncoderMachine(); m.read(100,1); m.x=100; ghost(m,99,5); m.read(250,1); m.read(100,1); m.x=100; m.press(); since(m,STOP); m.press(); assert m.mute() and m.spawned==1 and m.log_opens==1 and m.synced==1
    rows=m.log.decode().splitlines(); assert rows[0].startswith('== wheel: Return stopped a run') and 'stops 1' in rows[0] and len(rows)==65
    ms=[int(l.split()[0]) for l in rows[1:]]; assert ms==sorted(ms) and 'x=250 p=1' in m.log.decode()
    ghost(m,3100,5); assert m.log_opens==1  # past 30 s, the same run: nothing more
    # One writer: a flush while one writes is lost, not raced; the next after it goes. A thread that can't
    # start leaves the way clear.
    m=EncoderMachine(); m.hold=True; m.read(100,1); m.x=100; ghost(m,20,5); m.press(); since(m,STOP); m.press(); assert m.spawned==1 and not m.log
    m.read(m.x,0); m.read(100,1); m.x=100; ghost(m,20,5); m.press(); since(m,STOP); m.press(); assert m.spawned==1 and m.mute()
    m.hold=False; m.run_threads(); assert m.log_opens==1
    m.read(m.x,0); m.read(100,1); m.x=100; ghost(m,20,5); m.press(); since(m,STOP); m.press(); assert m.spawned==2 and m.log_opens==2
    m=EncoderMachine(); m.thread_fail=11; m.read(100,1); m.x=100; ghost(m,20,5); m.press(); since(m,STOP); m.press(); assert m.mute() and m.spawned==0
    m.thread_fail=0; m.read(m.x,0); m.read(100,1); m.x=100; ghost(m,20,5); m.press(); since(m,STOP); m.press(); assert m.spawned==1
    # A run past 30 s writes once; a lift and another, up to eight a boot, never past the size; gaps of a
    # second in ticks never get there; a log it cannot open costs nothing else.
    m=EncoderMachine(); m.read(100,1); m.x=100; assert ghost(m,2999,5); assert m.log_opens==0; ghost(m,300,5); assert m.log_opens==1 and m.log.startswith(b'== wheel: a run past 30 s')
    for i in range(20):
        m.drain(); m.read(m.x,0); m.read(100,1); m.x=100; ghost(m,3300,5)
    assert m.log_opens==8 and len(m.log)<=32768+64*40
    m=EncoderMachine(); m.log=b'x'*32768; m.read(100,1); m.x=100; ghost(m,3300,5); assert m.log_opens==1 and m.log==b'x'*32768 and not m.synced
    m=EncoderMachine(); m.read(100,1); m.x=100
    for _ in range(6): ghost(m,1000,5); m.now+=FRESH
    assert m.log_opens==0
    m=EncoderMachine(); m.log_fail=True; m.read(100,1); m.x=100; assert ghost(m,3300,5) and m.log_opens==1
    # The gate's first drop of a run leaves its own line, once a run however many follow; a second's quiet
    # is a new run; the ticks a stop refuses are the stop's, not the gate's.
    m=EncoderMachine(); m.read(100,1); m.now+=FRESH
    for _ in range(50):
        assert not m.input(DOWN_,N) and not m.input(UP_,N); m.now+=50
    assert m.log_opens==1 and m.log.startswith(b'== wheel: a tick with no ring frame') and m.get(ps['wl_drops'])==50
    m.now+=FRESH; assert not m.input(DOWN_,N); assert m.log_opens==2
    m=stopped(); n=m.log_opens; m.read(m.x,1,gap=1); assert not m.input(DOWN_,N) and not m.input(UP_,N) and m.log_opens==n and m.get(ps['wl_drops'])==0
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
assert D==130 and wheel([dark,(10,1)]+turn(10,D-1))[0]==[]           # a brush under the guard
assert wheel([dark,(10,1)]+turn(10,D-1),sensitivity('200')[0])[0]==[]  # at any sensitivity
assert wheel([dark,(10,1)]+turn(10,D+2*U-1))[0]==[P]                  # past it: every U ticks
assert wheel([dark,(10,1)]+turn(10,D+2*U))[0]==[P,P]
assert wheel([dark,(150,1)]+turn(150,D+U,-1))[0]==[N]                 # either way, over the seam
assert wheel([dark,(150,1),(90,1),(90,1)]+turn(90,D+U-60,-1))[0]==[N]  # still samples do not reverse
for step,key in ((1,P),(-1,N)):
    samples=[dark,(100,1),((100+90*step-1)%200+1,1),((100+170*step-1)%200+1,1)]
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
# UI wiring: stock-style choice menu, selected marker, touch and wheel slider.
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
O_RESTART=2900  # inside RESTART_ASK_MS
class ChangeMachine(PlaybackMachine):
    TAIL=('strncmp@GLIBC_2.0','strcpy@GLIBC_2.0','toolsCheckMount','config_outputchannel','toolsLoadNextDir',
          'toolsLoadPrevDir','player_refresh_playqueue','mclSetPlayPos','player_get_id3info','notifyPlayStatus',
          'dmrNotifyPlayStatus','sendBtHeadsetPlayStatus','notifyPlayInfo','sendBtHeadsetPlayInfo',
          'notifyRefreshLyric','dlnaRenderSaveUrlMetadata','reset_repeatinfo','initializeDmrQCurrentInfo')
    def __init__(self):
        super().__init__(); self.seen=[]; self.held=0
        for n in self.TAIL: self.handlers[syms[n]]='x:'+n.split('@')[0]
        self.handlers[ps['resume_held']]='x:resume_held'
    def hook(self,u,address,size,unused):
        if address in (syms['mclNextSong'],syms['mclPrevSong']): self.seen.append(self.handlers.get(address,'mcl'))
        name=self.handlers.get(address,'')
        if not name.startswith('x:'): return super().hook(u,address,size,unused)
        name=name[2:]; a=u.reg_read(REGS[0]); self.seen.append((name,a) if name=='mclSetPlayPos' else name)
        ret=1 if name in ('toolsCheckMount','toolsLoadNextDir','toolsLoadPrevDir','player_refresh_playqueue') else self.held if name=='resume_held' else 0
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

# A manual press that would start the playing file again, on a track holding a place, only asks; a
# second within RESTART_ASK_MS goes ahead. Stock moves on in every mode, so only a one-track queue restarts.
def single(k=1,mode=0,cls=1,skip=0,pos=0,held=1):
    m=ChangeMachine(); m.install([(str(i),'/a/'+str(i),'A',0,i,0,0) for i in range(k)],0); m.held=held
    m.word(O['MCL_TYPE'],cls); m.word(O['MCL_POS'],pos); m.word(O['MCL_MODE'],mode); m.byte(O['MCL_JUMPFOLDER'],skip)
    m.word(O['MCL_POOL'],m.deque([]))  # a one-track queue's shuffle pool, under stock's two
    return m
def change(m,nxt):
    del m.seen[:]; r=signed(m.call(address=syms['player_change_music'],args=(nxt,0,0,0),gap=0))
    return r,m.seen==['resume_held'],m.get(ps['restart_ask'])  # result, stopped before stock, toast waiting
ask=(0,True,1)
for nxt,mode,cls in ((0,0,1),(1,0,1),(0,1,1),(1,1,1),(0,2,1),(1,2,1),(0,3,1),(1,3,1),(0,0,0xf001),(1,3,0xf001)):
    m=single(mode=mode,cls=cls); assert change(m,nxt)==ask,(nxt,mode,cls)
    m.word(ps['restart_ask'],0); m.now+=O_RESTART; r=change(m,nxt); assert r[0]==1 and not r[1] and not r[2]  # the second press restarts
    m.now+=1; assert change(m,nxt)==ask; m.word(ps['restart_ask'],0); m.now+=3001; assert change(m,nxt)==ask  # later: asks again
    checks+=1
m=single(held=0); assert change(m,0)[:2]==(1,False); m=single(k=3,mode=1); assert change(m,0)[:2]==(1,False) and m.current()==2  # nothing to lose; moves on
m=single(k=3); assert change(m,1)[:2]==(1,False) and m.current()==1
m=single(skip=1); r=change(m,0); assert r[:2]==(1,False) and 'toolsLoadPrevDir' in m.seen  # Folder Skip leaves the queue
m=single(cls=0xf001); r=change(m,1); assert r[0]==0 and not r[2] and m.stops==1  # List Play's Next stops, asking nothing
m=single(k=3); m.options(0,2,0); assert change(m,0)[2]==0  # advanced play with no history moves nowhere
m=single(); m.fn('ringnav_change',0); assert m.get(ps['restart_ask'])==1; m.fn('playback_poll'); assert m.get(ps['restart_ask'])==0  # toasted once
checks+=6
# It asks exactly when stock, run bare, would start the file again: not when it leaves for a sibling folder or stops.
M3U_FLAG=0xa50029  # mclGetPlayM3uFlag's byte
for nxt in (0,1):
    for mode in (0,1,2,3):
        for cls in (1,0xff10,0xf001):
            for skip in (0,1):
                for m3u in (0,1):
                    for last in (-1,0):  # Shuffle's last position: none, or this track
                        m=single(mode=mode,cls=cls,skip=skip,held=0); m.byte(M3U_FLAG,m3u); m.word(O['MCL_LASTPOS'],last); change(m,nxt)
                        moved=m.stops or 'toolsLoadNextDir' in m.seen or 'toolsLoadPrevDir' in m.seen
                        a=single(mode=mode,cls=cls,skip=skip); a.byte(M3U_FLAG,m3u); a.word(O['MCL_LASTPOS'],last)
                        assert (change(a,nxt)==ask)==(not moved),(nxt,mode,cls,skip,m3u,last,m.seen)
                        checks+=1

# Through the real Resume poll, with its place file.
class HeldMachine(ChangeMachine):
    def __init__(self):
        super().__init__(); self.handlers.pop(ps['resume_held']); self.play=(0,0); self.seeks=[]
        self.handlers[ps['stock_sleep_trampoline']]='stock_sleep'
        for n in ('player_playtime_and_length','player_seek_time'): self.handlers[syms[n]]='h:'+n
        self.handlers[ps['stock_seek_time_trampoline']]='h:player_seek_time'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('h:'): return super().hook(u,address,size,unused)
        a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); ret=-1
        if name=='h:player_seek_time': self.seeks.append(signed(a)); self.play=(signed(a),self.play[1]); ret=0
        elif self.play[1]: self.word(a,self.play[0]); self.word(b,self.play[1]); ret=1
        self.returns(ret,clobber=True)
    def poll(self,sec,n=1):  # n polls a second apart, the time standing at sec
        for _ in range(n):
            self.play=(sec,3600); self.now+=1000; self.fn('ringnav_sleep',0x1234); sec=self.play[0]
    def places(self):
        return [(k,signed(v)) for k,v in struct.iter_unpack('<II',self.files.get('/mnt/data/ringnav-resume',b'')) if k]
# A refused press keeps the place and toasts; the second within RESTART_ASK_MS restarts, which the poll forgets it for.
m=HeldMachine(); m.install([('book','/a/book.m4b','B',0,0,0,0)],0); m.word(O['MCL_TYPE'],1)
m.poll(0,2); m.poll(100,3); place=m.places(); assert [s for _,s in place]==[100]
def press(): del m.seen[:]; return signed(m.call(address=syms['player_change_music'],args=(0,0,0,0),gap=0))
assert press()==0 and not m.seen and m.get(ps['restart_ask'])==1
m.poll(100); assert m.places()==place and m.toasts[-1]=='Press again to restart'
assert press()==1 and m.seen; m.poll(0,3); assert m.places()==[] and not m.seeks
checks+=1

# The same guard from the real keys: on_wm_keyup_fun's prev_quick_click and next_quick_click, through stock's
# player_prev_music, whose first move on anything playing or paused is player_seek_start: past 10 s it seeks to the
# start, in any queue and mode, without reaching player_change_music. Everything above enters below that seek, and
# with one key at a time.
PREV_KEY,NEXT_KEY=syms['prev_quick_click'],syms['next_quick_click']
class KeyMixin:
    def keys(self,status=2,sec=100):
        for n in ('mclGetPlayStatus','mclGetPlayPos','mclGetPlayType','prev_quick_click','next_quick_click','player_prev_music','player_next_music','player_seek_start'): self.handlers.pop(syms[n],None)
        self.word(0xa50084,status); self.sec=sec; self.set_seeks=[]
        for n in ('toolsmtimer','player_playtime','mclSetSeek','mclSetResume','mclSetMute','mclGetCueFlag','dmrSendPause','dmrSendPlay','usleep@GLIBC_2.0'):
            self.handlers[syms[n]]='k:'+n.split('@')[0]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('k:'): return super().hook(u,address,size,unused)
        ret=0; name=name[2:]; a=signed(u.reg_read(REGS[0]))
        if name=='toolsmtimer': ret=self.now
        elif name=='player_playtime': ret=self.sec
        elif name=='mclSetSeek': self.set_seeks.append(a); self.sec=a; ret=1
        self.returns(ret,clobber=True)
    def key(self,which,gap=600):  # above prev_quick_click's own 500 ms filter
        self.now+=gap; del self.seen[:]; del self.set_seeks[:]; self.word(ps['restart_ask'],0)
        self.call(address=which,args=(0,0,0,0),gap=0)
        moved=[e for e in self.seen if e!='resume_held']
        if self.set_seeks: assert self.set_seeks==[0] and not self.get(ps['restart_ask']); return 'seek'
        if self.get(ps['restart_ask']): assert not moved,moved; return 'ask'
        return 'change' if moved else 'none'
class KeyMachine(KeyMixin,ChangeMachine): pass
def keyed(k=1,mode=0,cls=1,skip=0,status=2,sec=100,held=1):
    m=KeyMachine(); m.install([(str(i),'/a/'+str(i),'A',0,i,0,0) for i in range(k)],0); m.held=held; m.keys(status,sec)
    m.word(O['MCL_TYPE'],cls); m.word(O['MCL_MODE'],mode); m.byte(O['MCL_JUMPFOLDER'],skip); m.word(O['MCL_POOL'],m.deque([]))
    return m
IN,OUT=O_RESTART,3001
# Only the same key confirms: another key inside the window asks and arms for itself.
m=keyed(); assert m.key(NEXT_KEY)=='ask' and m.key(PREV_KEY)=='ask' and m.key(PREV_KEY)=='seek'
m=keyed(); assert m.key(PREV_KEY)=='ask' and m.key(NEXT_KEY)=='ask' and m.key(NEXT_KEY)=='change'
m=keyed(); assert m.key(NEXT_KEY)=='ask' and m.key(PREV_KEY)=='ask' and m.key(NEXT_KEY)=='ask'  # back and forth never confirms
for which,done in ((PREV_KEY,'seek'),(NEXT_KEY,'change')):
    m=keyed(); assert m.key(which)=='ask' and m.key(which,IN)==done  # the same key inside the window
    m=keyed(); assert m.key(which)=='ask' and m.key(which,OUT)=='ask' and m.key(which,IN)==done  # outside it: asks again, and arms
# The window ends when a press no longer would restart: resume_held false, or change_restarts false, then back.
m=keyed(); assert m.key(NEXT_KEY)=='ask'; m.held=0; assert m.key(NEXT_KEY)=='change'; m.held=1; assert m.key(NEXT_KEY)=='ask'
m=keyed(); assert m.key(PREV_KEY)=='ask'; m.held=0; assert m.key(PREV_KEY)=='seek'; m.held=1; m.sec=100; assert m.key(PREV_KEY)=='ask'
m=keyed(); assert m.key(NEXT_KEY)=='ask'; m.word(O['MCL_TYPE'],0xf001); assert m.key(NEXT_KEY)=='none' and m.stops==1
m.word(O['MCL_TYPE'],1); assert m.key(NEXT_KEY)=='ask'
# Previous asks past 10 s in any queue, mode, class, Folder Skip, playing or paused; the second press seeks to the start.
for k in (1,3):
    for mode in (0,1,2,3):
        for cls in (1,0xff10,0xf001):
            for skip in (0,1):
                for status in (2,3):
                    m=keyed(k=k,mode=mode,cls=cls,skip=skip,status=status); assert m.key(PREV_KEY)=='ask',(k,mode,cls,skip,status)
                    assert m.key(PREV_KEY,IN)=='seek'; checks+=1
# Nothing held: stock's seek at once. Under 11 s stock changes track: a queue moves on, and Next in one moves on too.
m=keyed(k=3,held=0); assert m.key(PREV_KEY)=='seek'
m=keyed(k=3,mode=1,sec=10); r=m.key(PREV_KEY); assert r=='change' and m.current()==2,(r,m.seen,m.current())
m=keyed(k=3,sec=11); assert m.key(PREV_KEY)=='ask'
m=keyed(k=3); assert m.key(NEXT_KEY)=='change' and m.current()==1
m=keyed(k=3,status=1); assert m.key(PREV_KEY)=='change'  # stopped: no seek
m=keyed(status=1); assert m.key(PREV_KEY)=='ask'  # stopped, alone in the queue: player_change_music's restart
checks+=8
# Through the real Resume poll: the place stays and the toast shows; the second Previous seeks; the poll forgets it.
class RealKeyMachine(KeyMixin,HeldMachine):
    sec=property(lambda self:self.play[0],lambda self,v:setattr(self,'play',(v,self.play[1])))
m=RealKeyMachine(); m.install([('book','/a/book.m4b','B',0,0,0,0)],0); m.word(O['MCL_TYPE'],1); m.keys()
m.poll(0,2); m.poll(100,3); place=m.places(); assert [s for _,s in place]==[100]
assert m.key(PREV_KEY)=='ask'; m.word(ps['restart_ask'],1); m.poll(100); assert m.places()==place and m.toasts[-1]=='Press again to restart'
assert m.key(PREV_KEY,600)=='seek'; m.poll(0,3); assert m.places()==[]
# The time is live, not the poll's: a scrub to 100 s before the next poll asks; a third Previous after a restart, with no
# poll yet, restarts unasked; Previous within a second of Next, on a track at 0, doesn't ask for the one left.
def book(sec=100):
    m=RealKeyMachine(); m.install([('book','/a/book.m4b','B',0,0,0,0)],0); m.word(O['MCL_TYPE'],1); m.keys(); m.poll(0,2); m.poll(sec,3); return m
m=book(20); m.play=(100,3600); assert m.key(PREV_KEY)=='ask'
m=book(); m.play=(20,3600); assert m.key(PREV_KEY)=='seek'
m=book(); assert m.key(PREV_KEY)=='ask' and m.key(PREV_KEY)=='seek' and m.key(PREV_KEY)=='change'
m=book(); m.play=(0,3600); assert m.key(PREV_KEY)=='change'
m=book(3580); assert m.key(PREV_KEY)=='seek'  # the last 30 s keep no place
# Started again after a stop, with the same key: back to the place it stopped at, the place kept. A load's -1 (still
# playing) is no stop, a confirmed restart isn't sent back, and a stop in the last 30 s has nothing to go back to.
def stopped(m,status=1): m.word(0xa50084,status); m.play=(0,0); m.now+=1000; m.fn('ringnav_sleep',0x1234)
m=book(); kept=m.places(); stopped(m); m.word(0xa50084,2); m.poll(1); assert m.seeks==[100] and m.places()==kept; m.poll(100,3); assert m.places()==kept
m=book(); m.seeks=[]; stopped(m,2); m.poll(1); assert not m.seeks
m=book(); m.seeks=[]; stopped(m); assert m.key(PREV_KEY)=='ask'; assert m.key(PREV_KEY)=='change'; m.word(0xa50084,2); m.poll(1); assert not m.seeks
m=book(3580); m.seeks=[]; stopped(m); m.word(0xa50084,2); m.poll(1); assert not m.seeks
# A remote's Previous on a DLNA or online queue stays stock's: its seek at once.
for cls in (2,3,0xf1,0xf2):
    m=keyed(cls=cls); m.call(address=syms['player_prev_music'],args=(0,0,0,0),gap=0); assert m.set_seeks==[0] and not m.get(ps['restart_ask']),cls
# The toast's msginfo_dialog stops Previous, Next and Play itself, in stock's own words (0x49b7c8), so a second press under
# it would never reach on_wm_keyup_fun; the before-hook sends the same key on. Another key stays stopped, and armed.
m=keyed(); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
for key,stop in ((O['KEY_BACK_BTN'],1),(O['KEY_FWD_BTN'],1),(0xab,1),(0x40,0)):
    assert m.call(key,address=0x49b7c8,args=(0,m.event,0,0),gap=0)==(11 if stop else 0),key
def under_toast(m,key,gap=600):
    m.top=m.node('dialog','msginfo_dialog'); m.now+=gap; del m.set_seeks[:]; del m.seen[:]; m.word(ps['restart_ask'],0)
    return m.call(key,gap=0)
m=keyed(); assert m.key(PREV_KEY)=='ask'; assert under_toast(m,O['KEY_BACK_BTN'])==11 and m.set_seeks==[0]
m=keyed(); assert m.key(NEXT_KEY)=='ask'; assert under_toast(m,O['KEY_FWD_BTN'])==11 and m.seen and not m.get(ps['restart_ask'])
m=keyed(); assert m.key(NEXT_KEY)=='ask'; m.mock('on_wm_keyup_fun'); under_toast(m,O['KEY_BACK_BTN']); assert not m.set_seeks and m.get(ps['restart_at'])
m=keyed(); assert m.key(PREV_KEY)=='ask'; m.mock('on_wm_keyup_fun'); under_toast(m,O['KEY_BACK_BTN'],3100); assert not m.set_seeks  # the window over
m=keyed(); assert m.key(PREV_KEY)=='ask' and under_toast(m,O['KEY_BACK_BTN'],100) is not None and not m.set_seeks  # prev_quick_click's 500 ms filter
# The wheel's gate and stop don't come between the toast and its confirm: ticks with no frame behind them go nowhere and
# leave the window armed, and the buttons aren't ticks, whatever the wheel is doing.
m=keyed(); assert m.key(PREV_KEY)=='ask'; at=m.get(ps['restart_at']); m.word(ps['wheel_mute'],1)
for kind in (DOWN_,UP_): m.call(O['KEY_PREV'],address=INPUT,args=(m.wm,m.event,0,0),event_type=kind,gap=0)
assert m.get(ps['restart_at'])==at and under_toast(m,O['KEY_BACK_BTN'])==11 and m.set_seeks==[0] and m.get(ps['wheel_mute'])==1
m=EncoderMachine(); m.word(ps['wheel_mute'],1)
for key in (O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
    for kind in (DOWN_,UP_): assert m.input(kind,key)==1 and m.mute(),(key,kind)
checks+=24
# A CUE track holds no place to lose at a chapter's start: Previous restarts it unasked.
m=RealKeyMachine(); m.install([('ch','/a/book.m4b','B',0,0,600,1200)],0); m.word(O['MCL_TYPE'],1); m.keys(); m.poll(0,2); m.poll(100,3)
assert m.key(PREV_KEY)=='seek'
checks+=2

# A list pick of the playing file, from each caller's own words into player_start. No caller reads its result: the same
# row of the same list starts nothing, what the page does after depends on who called. A short song keeps stock's restart.
class PickMachine(ChangeMachine):
    PAGE=(0x53e4f4,0x53e514); ALBUM=(0x4a97a8,0x4a97c0); INFO=(0x4abff0,0x4ac008); SYNC=(0x5264ac,0x5264c4); SYNC2=(0x52669c,0x5266b4); ROW=(0x53acec,0x53acf8)
    def __init__(self,status=2,cls=1):
        super().__init__(); self.install([('x','/a/x','A',0,0,0,0),('1','/a/1','A',0,1,0,0)],1); self.held=1; self.word(O['MCL_TYPE'],cls); self.word(O['MCL_MODE'],0); self.byte(O['MCL_JUMPFOLDER'],0)
        for n in ('player_start','player_start_queue','player_play_pause','navigator_back'): self.handlers.pop(syms[n],None)
        self.handlers[ps['stock_player_start_trampoline']]='x:stock_start'; self.handlers[ps['stock_start_queue_trampoline']]='x:stock_queue'
        self.handlers[ps['stock_pause_trampoline']]='x:unpause'; self.handlers[ps['resume_return']]='x:go_back'
        self.handlers[syms['navigator_switch_to_with_context']]='pick:np'; self.handlers[syms['navigator_back']]='x:back'
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa50084,status)
        self.ctx=self.alloc(16); self.cell=self.alloc(4); self.np=[]
        self.same=self.rows_of('/a/x','/a/1'); self.other=self.rows_of('/a/y','/a/z')
    def rows_of(self,*paths):
        rows=[]
        for p in paths: r=self.song(p); self.word(r+O['REC_PATH'],self.string(p)); rows.append(r)
        return self.deque(rows)
    def hook(self,u,address,size,unused):
        if address==self.enter_at:
            self.keep=[u.reg_read(r) for r in self.SAVED]
            for r,v in zip(self.SAVED,self.regs): u.reg_write(r,v)
        if address==self.stops_at or (address==0x53e51c and self.stops_at==0x53e514):  # the page's 0xff skips the call
            for r,v in zip(self.SAVED,self.keep): u.reg_write(r,v)
            return u.reg_write(UC_MIPS_REG_PC,0x1000000)
        if self.handlers.get(address)=='pick:np':
            self.np.append((self.text(u.reg_read(REGS[0])),self.get(u.reg_read(REGS[1])+8))); self.seen.append('np'); return self.returns(0,clobber=True)
        return super().hook(u,address,size,unused)
    enter_at=stops_at=None; SAVED=(UC_MIPS_REG_S0,UC_MIPS_REG_S1,UC_MIPS_REG_S2,UC_MIPS_REG_S3)
    def at_site(self,site,q=None,at=1,cls=1):
        self.enter_at,self.stops_at=site; [watch(a) for a in (*site,0x53e51c)]; del self.seen[:]; del self.np[:]; self.now+=600; self.word(ps['restart_ask'],0); queue=self.get(syms['mcl_pdeqplaylist'])
        q=q or self.same; self.word(self.cell,cls)
        if site==self.PAGE:
            for i,v in enumerate((q,at,cls,2)): self.word(self.ctx+4*i,v)
            self.regs=(0,self.ctx,0); args=(0,0,cls,0)
        elif site in (self.ALBUM,self.INFO): self.regs=(q,at,self.cell); args=(0,0,0,0)
        elif site in (self.SYNC,self.SYNC2): self.regs=(q,at,cls,cls); args=(0,0,0,0)  # the class in $s2 at the first site, $s3 at the second
        else: self.regs=(0,at,0); args=(0,0,0,0)
        self.u.reg_write(UC_MIPS_REG_GP,0xa3aae0); self.call(address=site[0],args=args,gap=0)
        assert not self.get(ps['restart_ask']) and self.get(syms['mcl_pdeqplaylist'])==queue
        return [e for e in self.seen if e!='resume_held']
    def picked(self,**kw): return self.at_site(self.PAGE,**kw)
START,GO=['stock_start'],['go_back','stock_start']
for site in (PickMachine.PAGE,PickMachine.SYNC):
    m=PickMachine(); assert m.at_site(site)==[] and m.at_site(site)==[]  # the same row, twice, without asking or arming
# Album pages stay on their page after a start: refused, they open Now Playing, class 0xff as on_backplay_click.
for site in (PickMachine.ALBUM,PickMachine.INFO):
    m=PickMachine(); assert m.at_site(site)==['np'] and m.np==[('playing_page',0xff)]
    m=PickMachine(status=3); assert m.at_site(site)==['np','unpause']  # paused: a pick means play this
# SyncLink, off the UI thread: silent, no window and no Play.
for site in (PickMachine.SYNC,PickMachine.SYNC2):
    m=PickMachine(status=3); assert m.at_site(site)==[],site
m=PickMachine(status=3); assert m.picked()==['unpause']  # the page opens itself
m=PickMachine(status=1); assert m.picked()==START  # stopped: a real start, never Now Playing in silence
m=PickMachine(); m.held=0; assert m.picked()==START and PickMachine().picked()==[]  # a short song, or no place to lose
m=PickMachine(); assert m.picked(at=0)==START  # another row
m=PickMachine(); assert m.picked(at=5)==START  # stock's to refuse
# Another list with the same file: stock loads it and the poll goes back. By position, by size, by class.
m=PickMachine(); assert m.picked(q=m.other,at=1)==START
m=PickMachine(); assert m.picked(q=m.rows_of('/a/1'),at=0)==GO
m=PickMachine(); assert m.picked(q=m.rows_of('/a/x','/a/1','/a/w'),at=1)==GO
m=PickMachine(); assert m.picked(q=m.rows_of('/a/1','/a/x'),at=0)==GO
m=PickMachine(); assert m.picked(cls=0xff10)==GO
# A CUE row of the file playing whole is another track, the first chapter too (start 0, an end): stock, nothing to go back to.
for start,end in ((600,1200),(0,600),(600,0)):
    m=PickMachine(); q=m.rows_of('/a/x','/a/1'); r=list(m.items(q))[1]; m.word(r+O['REC_CUE_START'],start); m.word(r+O['REC_CUE_END'],end); assert m.picked(q=q)==START,(start,end)
for cls in (2,3,0xf1,0xf2): m=PickMachine(); m.word(O['MCL_TYPE'],cls); assert m.picked(cls=cls)==START,cls  # DLNA, online: nobody pressed here
m=PickMachine(); m.word(O['MCL_TYPE'],2); assert m.picked(cls=1)==START  # a local list over a remote queue
m=PickMachine(); m.word(ps['press_natural'],1); assert m.picked()==START  # an app's .play
m=PickMachine(); m.word(ps['s']+24,1); assert m.picked()==START  # Shuffle Albums' load
m=PickMachine(); assert m.picked(cls=0xff)==[]  # 0xff: Now Playing's own open, no player_start
# View Queue's row of the playing file: Now Playing, unpaused; another row, or anyone else's call, stock's.
m=PickMachine(); assert m.at_site(m.ROW)==['np']
m=PickMachine(status=3); assert m.at_site(m.ROW)==['np','unpause']
m=PickMachine(); assert m.at_site(m.ROW,at=0)==['stock_queue']
m=PickMachine(status=1); assert m.at_site(m.ROW)==['stock_queue']
m=PickMachine(); m.held=0; assert m.at_site(m.ROW)==['stock_queue']
m=PickMachine(); m.handlers.pop(syms['player_start_queue'],None); m.fn('ringnav_start_queue',1); assert m.seen==['resume_held','stock_queue'] or m.seen==['stock_queue'],m.seen  # DLNA, a delete
# Up Next's row of it closes the page, then opens Now Playing. Any other row plays, as before.
m=PickMachine(); m.word(O['MCL_POS'],0); del m.seen[:]; m.fn('un_play',0); assert [e for e in m.seen if e!='resume_held']==['back','np'],m.seen
m=PickMachine(); m.held=0; m.word(O['MCL_POS'],0); del m.seen[:]; m.fn('un_play',0); assert [e for e in m.seen if e!='resume_held']==['stock_queue','back'],m.seen
checks+=30
# player_start's real body: another list loads and starts through the hook, past stock's mclLoadPlayList and mclStartPlayer.
class BodyMachine(PickMachine):
    def __init__(self):
        super().__init__(); self.handlers.pop(ps['stock_player_start_trampoline'])
        for a in (0x519e54,0x50134c,0x51f3a4,0x5242f8,0x50f94c,0x5246f0,0x5275b4,0x525dc0,0x510e98,0x51d3b0,0x50ec24,0x777f10): self.handlers[a]='x:body'
        for name in ('mclSetPlayMode','sendBtHeadsetPlayStatus','sendBtHeadsetPlayInfo','dmrRenderQplayQueueSize'):
            if name in syms: self.handlers[syms[name]]='x:body'
        self.handlers[ps['stock_load_trampoline']]='x:loaded'; self.handlers[syms['mclStartPlayer']]='x:started'
m=BodyMachine(); r=[e for e in m.picked(q=m.rows_of('/a/1'),at=0) if e!='body']; assert r==['go_back','loaded','started'],r
m=BodyMachine(); r=[e for e in m.picked() if e!='body']; assert r==[],r
# The return itself, through the real poll: stock's load plays the file from 0, the poll seeks back and the place stands.
class ReturnMachine(RealKeyMachine):
    def __init__(self):
        super().__init__(); self.install([('book','/a/book.m4b','B',0,0,0,0)],0); self.word(O['MCL_TYPE'],1); self.keys(); self.poll(0,2); self.poll(100,3)
        self.handlers.pop(syms['player_start'],None); self.handlers[ps['stock_player_start_trampoline']]='r:start'; self.started=0; self.seeks=[]
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='r:start': self.started+=1; self.play=(0,3600); return self.returns(1,clobber=True)
        return super().hook(u,address,size,unused)
    def pick(self,*paths,at=0,cls=0xff10):
        rows=[]
        for p in paths: r=self.song(p); self.word(r+O['REC_PATH'],self.string(p)); rows.append(r)
        self.call(address=syms['player_start'],args=(self.deque(rows),at,cls,2),gap=0)
m=ReturnMachine(); kept=m.places(); m.pick('/a/book.m4b'); assert m.started==1 and m.places()==kept
m.poll(0); assert not m.seeks; m.poll(1); assert m.seeks==[100] and m.places()==kept; m.poll(100,3); assert m.places()==kept
m=ReturnMachine(); kept=m.places(); m.pick('/a/book.m4b'); m.poll(100,9); assert not m.seeks and m.started==1  # it never restarted: the wait runs out
m=ReturnMachine(); m.pick('/a/other'); assert m.started==1; m.poll(0,2); assert not m.seeks  # another file: nothing to go back to here
checks+=5

print(f'{checks} advanced playback/wheel MIPS checks passed')

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
# A snapshot with a time wins over the track's own place; Memory Playback: Track has none to give.
for memory,taken in ((2,1),(1,0)):
    r=PlaybackMachine({'/mnt/data/ringnav-queue':full}); r.byte(syms['g_memory_play'],memory); assert r.fn('ringnav_memory',r.deque([]))>=0
    q=r.items(r.get(syms['mcl_pdeqplaylist']))[r.current()]
    assert (r.fn('playback_resumed',q)!=0)==bool(taken) and r.fn('playback_resumed',q)==0,memory  # asked once
# And through the poll: Track goes back to the track's own place once it plays, Location's time keeps it.
for memory,seeks in ((1,[1000]),(2,[])):
    r=HeldMachine(); r.files['/mnt/data/ringnav-queue']=full; r.byte(syms['g_memory_play'],memory); assert r.fn('ringnav_memory',r.deque([]))>=0
    path=r.get(r.items(r.get(syms['mcl_pdeqplaylist']))[r.current()]+O['REC_PATH'])
    r.files['/mnt/data/ringnav-resume']=struct.pack('<II',r.fn('fnv',2166136261,path)&0xffffffff,1000)+bytes(8*O['RESUME_SLOTS']-8)
    r.poll(0,2); assert not r.seeks  # at 0, paused or not started: it waits
    r.poll(1); assert r.seeks==seeks,memory
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
m=PlaybackMachine(); m.handlers[ps['mp4_fits']]=m.handlers[ps['mp4_start_fits']]='p:mp4_fits'; m.fits=0; m.mp4_checks=[]
r=m.items(m.get(syms['mcl_pdeqplaylist']))[2]
m.word(r+O['REC_PATH'],m.string('/A/book.m4b'))
assert m.fn('ringnav_start_player')==-1 and m.mp4_checks==['/A/book.m4b']
m.fn('playback_poll'); m.fn('playback_poll')
assert m.toasts==['Too long to play on the Q2: split it into parts']
m.fits=1; assert m.fn('playback_start_fits')==1
# The file last started, started again (a CUE book's next chapter), is not checked: its index is
# freed first. Another file is, and so is a preload of the same file, which opens beside it.
m.fits=0; m.mp4_checks=[]
assert m.fn('playback_start_fits')==1 and not m.mp4_checks
m.word(r+O['REC_PATH'],m.string('/A/other.m4b'))
assert m.fn('playback_start_fits')==0 and m.mp4_checks==['/A/other.m4b']
m.fn('playback_poll'); m.word(r+O['REC_PATH'],m.string('/A/book.m4b'))
m.word(O['MCL_PREPOS'],2); m.word(O['MCL_PRELOAD'],0)
assert m.fn('ringnav_open_preload')==1 and m.mp4_checks[-1]=='/A/book.m4b' and m.mcl('MCL_PRELOAD')==-1
# A nonlocal start closes the book, so the book after it is checked again.
m.word(r+O['REC_TYPE'],0); m.mp4_checks=[]
assert m.fn('playback_start_fits')==1 and not m.mp4_checks
m.fn('playback_poll'); assert len(m.toasts)==2
m.word(r+O['REC_TYPE'],8)
assert m.fn('playback_start_fits')==0 and m.mp4_checks==['/A/book.m4b']
m.fn('playback_poll')
# Equal-speed gapless preloads inspect the successor, not the current track. Refusal
# marks stock's failed-preload state; fitting successors still reach the stock opener.
for fits,want in ((0,-1),(1,1)):
    m=PlaybackMachine(); m.handlers[ps['mp4_fits']]=m.handlers[ps['mp4_start_fits']]='p:mp4_fits'; m.fits=fits; m.mp4_checks=[]
    m.word(O['MCL_PREPOS'],1); m.word(O['MCL_PRELOAD'],0)
    assert m.fn('ringnav_open_preload')==1
    assert m.mp4_checks==['/B/01.flac'] and m.mcl('MCL_PRELOAD')==want
    m.fn('playback_poll'); assert not m.toasts
# Stock's final-five-seconds gate must not retry a refused successor on every player tick.
m=PlaybackMachine(); m.options(0,5,0)
m.handlers[ps['mp4_fits']]=m.handlers[ps['mp4_start_fits']]='p:mp4_fits'; m.fits=0; m.mp4_checks=[]
m.handlers.pop(syms['mcl_open_preload'])
m.byte(O['MCL_GAPLESS'],1); m.byte(0xa5002d,1); m.elapsed=296  # stock CUE end-time path
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
pc=0x51e044
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

# Crossfade's policy (crossfade.c xfade_poll, from playback_poll each UI pass): the next change's
# fade in ms, in the int hciplayer's filter maps (XFADE_FILE), only to another album with Gapless
# on; while one is set, the filter goes into the chain (peq_chain).
class XfadeMachine(PlaybackMachine):
    def __init__(self, on='1'):
        super().__init__(); self.flag=self.alloc(4); self.attaches=0; self.written=4
        self.config.update(XFADE=on, XFADESEC='7')
        for n in ('open','write','mmap','close','__xstat'): self.handlers[syms[n+'@GLIBC_2.0']]='xf:'+n
        self.handlers[syms['navigator_window_is_exist']]='xf:exist'; self.handlers[syms['mcl_shuffle_pick']]='xf:pick'
        self.missing|={'/tmp/notfile','/tmp/notsupport'}; self.toast=0; self.empty=set(); self.drawn=0
        self.handlers[ps['peq_attach']]='xf:attach'
        self.handlers.pop(syms['mclGetPlayStatus']); self.word(0xa50084,2)  # playing
        self.word(syms['g_equalizer_flag'],0); self.byte(O['MCL_GAPLESS'],1)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('xf:'): return super().hook(u,address,size,unused)
        if name=='xf:open': self.files[self.text(u.reg_read(REGS[0]))]=b''
        self.attaches+=name=='xf:attach'
        if name=='xf:__xstat': self.word(u.reg_read(REGS[2])+48,0 if self.text(u.reg_read(REGS[1])) in self.empty else 4096)
        if name=='xf:exist': return self.returns(self.toast)
        if name=='xf:pick':  # as stock, forward: the current index out of the pool, a random one left is the position
            pool=self.get(O['MCL_POOL']); left=[self.get(e) for e in self.items(pool) if self.get(e)!=self.mcl('MCL_POS')]
            self.word(O['MCL_LASTPOS'],self.mcl('MCL_POS')); self.word(O['MCL_POOL'],self.deque(left,'int'))
            self.drawn=left[0]; self.word(O['MCL_POS'],left[0]); return self.returns(1)
        if name=='xf:prevstub': self.word(O['MCL_POS'],1); return self.returns(1)
        self.returns(self.flag if name=='xf:mmap' else 3 if name=='xf:open' else self.written if name=='xf:write' else 0)
    def fade(self,pos=None):
        if pos is not None: self.word(O['MCL_POS'],pos)
        self.fn('playback_poll'); return self.get(self.flag)
m=XfadeMachine()
assert m.fade()==7000 and '/tmp/q2xfade' in m.files  # 'album a' to the untagged /C/ image
assert m.fade()==7000 and m.attaches==1
assert m.fade(3)==0 and m.fade(0)==7000  # the image's two CUE tracks; Album A to Album B
m.byte(O['MCL_GAPLESS'],0); assert m.fade()==0
m.byte(O['MCL_GAPLESS'],1); assert m.fade()==7000
m.word(O['MCL_MODE'],1); assert m.fade()==0  # Repeat One: no next file
m.word(O['MCL_MODE'],2); assert m.fade()==7000  # stock Shuffle: the next is drawn at the change, another album's
m.byte(O['MCL_GAPLESS'],0); assert m.fade()==0; m.byte(O['MCL_GAPLESS'],1)
m.word(O['MCL_MODE'],0); assert m.fade()==7000
m.word(O['MCL_MODE'],0); assert m.fade(6)==0  # the last track
# Repeat All at the queue's end: stock preloads position 0, so the wrap fades when that's another album's.
m.word(O['MCL_MODE'],3); assert m.fade(6)==0  # 'ALBUM A' back to 'album a': gapless
m.word(m.library_rows[6]+O['REC_ALBUM'],m.string('Album C')); assert m.fade(3)==0 and m.fade(6)==7000 and m.fade(0)==7000
m.word(O['MCL_MODE'],0); assert m.fade(6)==0
# Play Next moves rows' bytes in place (playback_queue_insert): the next row's address stays, its song changes.
m=XfadeMachine(); assert m.fade(3)==0  # the image's two CUE tracks
r=m.library_rows[4]; m.word(r+O['REC_PATH'],m.string('/B/01.flac')); m.word(r+O['REC_ALBUM'],m.string('Album B'))
assert m.fade(3)==7000
# A DSD next never fades (the filter can't mix under it): any case of the three extensions. DSD going
# out is the filter's to pass through, so the policy still fades out of one.
for name in ('/B/x.dsf','/B/x.DFF','/B/x.iso'):
    m=XfadeMachine(); m.word(m.library_rows[1]+O['REC_PATH'],m.string(name))
    assert m.fade(0)==0,name
m=XfadeMachine(); m.word(m.library_rows[0]+O['REC_PATH'],m.string('/A/x.dsf')); assert m.fade(0)==7000
# Each stop moves the count above the fade's ms: a natural change, which never reads as stopped, doesn't.
m=XfadeMachine(); assert m.fade(0)==7000 and m.fade(0)==7000
m.word(0xa50084,1); assert m.fade()==7000|1<<16 and m.fade()==7000|1<<16
m.word(0xa50084,2); assert m.fade()==7000|1<<16
m.word(0xa50084,3); assert m.fade()==7000|1<<16
m.word(0xa50084,1); assert m.fade()==7000|2<<16
# The count alone is no fade: with none asked, the filter isn't put in the chain for it.
m.word(0xa50084,2); m.byte(O['MCL_GAPLESS'],0); assert m.fade()==2<<16
n=m.attaches; assert m.fade()==2<<16 and m.fade()==2<<16 and m.attaches==n
# A file error's status 1 isn't a stop: the error file there, then gone with stock's modal toast up,
# then Next. Nothing moves the count, so the next track mixes the tail. A stop right after still counts.
m=XfadeMachine(); assert m.fade(0)==7000
m.word(0xa50084,1); m.missing.discard('/tmp/notfile'); assert m.fade()==7000
m.missing.add('/tmp/notfile'); m.missing.discard('/tmp/notsupport'); assert m.fade()==7000
m.missing.add('/tmp/notsupport'); m.toast=1; assert m.fade()==7000 and m.fade()==7000
m.toast=0; m.word(0xa50084,2); assert m.fade()==7000
m.word(0xa50084,1); assert m.fade()==7000|1<<16
# A next file missing or empty asks no fade: it never plays, so that song ends whole. A URL isn't looked at.
m=XfadeMachine(); m.missing.add('/B/01.flac'); assert m.fade(0)==0,m.fade(0)
m=XfadeMachine(); m.empty.add('/B/01.flac'); assert m.fade(0)==0
m=XfadeMachine(); m.word(m.library_rows[1]+O['REC_PATH'],m.string('http://x/y.flac')); m.missing.add('http://x/y.flac'); assert m.fade(0)==7000
# Stock Shuffle's next drawn ahead: the file asked about is the one that plays, the pick overridden; a
# DSD or missing draw asks no fade, any other fades whichever album. Uniform over the pool but the current,
# which the draw leaves alone; Previous, a Play next and a new queue go their own way.
def shuffled(pool=None):
    m=XfadeMachine(); m.word(O['MCL_MODE'],2); m.word(O['MCL_POS'],0)
    m.word(O['MCL_POOL'],m.deque(pool if pool is not None else list(range(7)),'int')); return m
m=shuffled(); assert m.fade()==7000
seen=[]
for _ in range(120):  # one machine: its generator runs on, as the device's
    m.word(O['MCL_POOL'],m.deque(list(range(7)),'int')); m.word(O['MCL_POS'],0)
    m.fn('playback_poll'); assert m.fn('ringnav_shuffle',1)==1
    x=m.mcl('MCL_POS'); seen.append(x); assert 0<x<7,x
    assert sorted(m.get(e) for e in m.items(m.get(O['MCL_POOL'])))==list(range(1,7))  # the pick's erase only
    m.word(O['MCL_POS'],x); m.fn('playback_poll')  # the next pass redraws from the new track
assert set(seen)==set(range(1,7)) and max(seen.count(x) for x in range(1,7))<60,seen
for bad in ('dsd','missing'):
    m=shuffled([0,1]); r=m.library_rows[1]
    if bad=='dsd': m.word(r+O['REC_PATH'],m.string('/B/x.iso'))
    else: m.missing.add('/B/01.flac')
    assert m.fade()==0,bad; assert m.fn('ringnav_shuffle',1)==1 and m.mcl('MCL_POS')==1
m=shuffled([0,1]); assert m.fade()==7000 and m.fn('ringnav_shuffle',1)==1 and m.mcl('MCL_POS')==1
# Under two in the pool: the refill's draw is unknown, and still fades, with no override.
m=shuffled([0]); assert m.fade()==7000
# A new queue under the draw: the old index isn't forced on it (the records no longer match).
m=shuffled([0,1,2]); m.fn('playback_poll'); rows=list(m.library_rows); rows[1],rows[2]=rows[2],rows[1]
m.word(syms['mcl_pdeqplaylist'],m.deque(rows)); m.word(O['MCL_POOL'],m.deque([0,5],'int'))
assert m.fn('ringnav_shuffle',1)==1 and m.mcl('MCL_POS')==m.drawn==5
# Previous then Next returns to the track left, and the policy asks about that one: a DSD one asks no fade.
m=shuffled(); m.word(O['MCL_POS'],6); m.handlers[ps['stock_prev_trampoline']]='xf:prevstub'
m.word(m.library_rows[6]+O['REC_PATH'],m.string('/A/left.dsf'))  # one row in six: a draw would fade
m.fn('ringnav_prev'); assert m.mcl('MCL_POS')==1,m.mcl('MCL_POS'); assert m.fade()==0
assert m.fn('ringnav_shuffle',1)==1 and m.mcl('MCL_POS')==6
# A Play Next changes the answer mid-track, both ways: the poll reads the next record each pass.
m=XfadeMachine(); rows=list(m.library_rows); assert m.fade(3)==0
m.word(syms['mcl_pdeqplaylist'],m.deque(rows[:4]+[rows[0]]+rows[4:])); assert m.fade()==7000
m.word(syms['mcl_pdeqplaylist'],m.deque(rows)); assert m.fade()==0
m=XfadeMachine('0'); assert m.fade()==0 and not m.files and not m.attaches  # off: no file
# Shuffle Songs (the reports' mode, #164): the next is the shuffle's successor, not pos + 1, and
# the fade follows album_cmp of the two records at every automatic change.
m=XfadeMachine(); assert m.fn('playback_shuffle',2)==1
rows=m.library_rows; fades=set()
for _ in range(6):
    pos,nxt=m.current(),m.fn('playback_successor',1); assert 0<=nxt<len(rows)
    want=7000 if m.fn('album_cmp',rows[pos],rows[nxt]) else 0
    assert m.fade()==want,(pos,nxt,m.fade(),want); fades.add(want)
    assert m.advance_song(1)>=0
assert fades=={0,7000},fades  # both cases met along the way
print('Crossfade: the next change\'s fade, album and Gapless gates, the shared file, the filter and Shuffle Songs passed')

# What waits for the DAC (crossfade.c press_stamp, docs/internals.md#wired-start): demo's stamp in PRESS_FILE.
class PressMachine(XfadeMachine):
    def __init__(self):
        super().__init__('0'); self.now=5000; self.handlers.pop(ps['press_stamp']); self.handlers.pop(ps['mute_stamp'])
        self.handlers[syms['clock_gettime@GLIBC_2.2']]='xf:clock'; self.handlers[syms['rename@GLIBC_2.0']]='xf:rename'
        self.handlers[0x4f4ddc]='xf:dialog'; self.handlers[syms['mclStartPlayer']]='xf:start'
        self.stock=[]; self.nested_load=0; self.held=0; self.sec=100
        for name in ('change','next','load','pause','seek_time','seek_start','player_start'): self.handlers[ps[f'stock_{name}_trampoline']]='xf:'+name
        self.handlers[ps['resume_held']]='xf:held'; self.handlers[syms['player_playtime']]='xf:playtime'
        self.word(O['MCL_TYPE'],0xf001)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='xf:clock':
            b=u.reg_read(REGS[1]); self.word(b,self.now//1000&0xffffffff); self.word(b+4,self.now%1000*1000000); return self.returns(0)
        if name=='xf:rename': self.files[self.text(u.reg_read(REGS[1]))]=b''; return self.returns(0)
        if name=='xf:dialog': return u.reg_write(UC_MIPS_REG_PC,0x1000000)  # back in on_wm_timer_dialog
        if name=='xf:held': return self.returns(self.held)
        if name=='xf:playtime': return self.returns(self.sec)
        if name[3:] in ('change','next','load','pause','seek_time','seek_start','start','player_start'):
            self.stock.append(name); self.seen=self.get(self.flag)
            if name=='xf:start': self.start_natural=self.get(ps['press_natural'])
            if name in ('xf:next','xf:change') and self.nested_load:  # Folder Skip's load, as stock's do within them
                self.nested_load=0; self.nested(ps['ringnav_load'],self.get(syms['mcl_pdeqplaylist']),0,1)
            return self.returns(0)
        return super().hook(u,address,size,unused)
    def at(self): return self.get(self.flag)
    def nothing(self): return not {'/tmp/q2press','/tmp/q2press.tmp'}&set(self.files)
    def enter(self,name,*args):  # through the hook, not the fixtures' mock
        self.handlers.pop(syms[name],None); return self.call(address=syms[name],args=tuple(args)+(0,)*(4-len(args)),gap=0)
# Nothing is created until the first press or start_mute's mark: a poll, ringnav_next(1)'s forget and the Stop that ends a queue make no file.
m=PressMachine(); m.fn('playback_poll'); m.advance_song(1); assert m.nothing() and m.stock==['xf:next']
# A manual Next or Previous stamps, odd and so never 0, the low 32 bits of the monotonic clock's ms, before stock runs.
m.enter('player_next_music'); assert '/tmp/q2press' in m.files and m.at()==5001 and m.seen==5001
m.now=6000; m.fn('ringnav_change',0); assert m.at()==6001
m.now=(1<<32)+1002; m.fn('ringnav_change',1); assert m.at()==1003
# A natural change forgets it and stamps nothing, before stock's next or advanced play's mclStartPlayer; a rollover too.
m=PressMachine(); m.fn('ringnav_change',1); m.advance_song(1); assert m.at()==0 and m.seen==0 and m.get(ps['press_natural'])==0
m=PressMachine(); m.options(1,5,0); m.fn('ringnav_change',1); m.stock=[]; m.advance_song(1); assert m.stock==['xf:start'] and m.seen==0
m=PressMachine(); m.fn('ringnav_change',1); m.nested_load=1; m.advance_song(1); assert m.at()==0 and 'xf:load' in m.stock
m=PressMachine(); m.nested_load=1; m.advance_song(1); assert m.nothing() and 'xf:load' in m.stock
# ringnav_next(0) is reached only under ringnav_change, which has stamped: alone it neither stamps nor forgets.
m=PressMachine(); m.advance_song(0); assert m.nothing()
m=PressMachine(); m.fn('ringnav_change',1); m.now=7000; m.advance_song(0); assert m.at()==5001
# A list pick stamps, after stock's load. Memory Playback's restore doesn't, in its load or player_start's second.
m=PressMachine(); m.fn('ringnav_load',m.get(syms['mcl_pdeqplaylist']),0,0xf001); assert m.at()==5001 and m.seen==0
s=PlaybackMachine(); s.options(4,5,0); s.advance_song(0); s.fn('playback_save')
m=PressMachine(); m.handlers.pop(ps['stock_load_trampoline']); m.files.update(s.files); m.byte(syms['g_memory_play'],2); out=m.deque([])
at=m.fn('ringnav_memory',out); assert at>=0 and m.nothing(); m.fn('ringnav_load',out,at,1); assert m.nothing()
# Only a local queue's press: DLNA's (2, 3) and the online ones' (0xf1, 0xf2) start tracks by themselves.
for kind,stamps in ((1,True),(0xff10,True),(0xf001,True),(2,False),(3,False),(0xf1,False),(0xf2,False)):
    m=PressMachine(); m.word(O['MCL_TYPE'],kind); m.fn('ringnav_load',m.get(syms['mcl_pdeqplaylist']),0,kind); assert m.nothing()!=stamps,kind
    m=PressMachine(); m.word(O['MCL_TYPE'],kind); m.fn('ringnav_change',1); assert m.nothing()!=stamps,kind
# Stock's own player_start_queue hook doesn't stamp (DLNA, SyncLink's URL plays); Up Next's row does, before it.
m=PressMachine(); m.handlers[ps['stock_start_queue_trampoline']]='xf:none'; m.fn('ringnav_start_queue',2); assert m.nothing()
m=PressMachine(); m.handlers[syms['player_start_queue']]='xf:start'; m.handlers[syms['navigator_back']]='xf:none'
m.fn('un_play',0); assert m.stock==['xf:start'] and m.seen==5001
# Play from a stop stamps, a headset's own too (media_set_playing, through the same hook); playing or paused doesn't.
m=PressMachine(); m.word(0xa50084,1); m.enter('player_play_pause'); assert m.at()==5001 and m.stock==['xf:pause']
m=PressMachine(); m.handlers.pop(syms['player_play_pause'],None); m.word(0xa50084,1); m.fn('media_set_playing',1); assert m.at()==5001 and m.stock==['xf:pause']
for status in (2,3):
    m=PressMachine(); m.word(0xa50084,status); m.enter('player_play_pause'); assert m.nothing(),status
# The fade's start (docs/internals.md#unpause): a Pause from playing stamps MUTE_FILE before stock's mute(2), and nothing else does.
m=PressMachine(); m.word(0xa50084,2); m.enter('player_play_pause'); assert '/tmp/q2mute' in m.files and m.seen==5001 and m.stock==['xf:pause']
for status in (1,3):
    m=PressMachine(); m.word(0xa50084,status); m.enter('player_play_pause'); assert '/tmp/q2mute' not in m.files,status
m=PressMachine(); m.options(3,0,0); m.fn('playback_group_skip',1); assert m.at()==5001  # Next Group, a press
# A user's scrub and Previous restarting the track stamp, through the hooks, and go on to stock.
m=PressMachine(); m.enter('player_seek_time',50); assert m.at()==5001 and m.stock==['xf:seek_time'] and m.seen==5001
m=PressMachine(); m.enter('player_seek_start'); assert m.at()==5001 and m.stock==['xf:seek_start'] and m.seen==5001
# The skip past a file stock can't play: on_wm_timer_dialog's own words into stock's player_next_music, and nobody
# pressed, for a Folder Skip load in it too. The same Next from anywhere else stamps (above).
for load in (0,1):
    m=PressMachine(); m.handlers.pop(syms['player_next_music'],None); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0); m.nested_load=load
    m.call(address=0x4f5020,args=(0,0,0,0),gap=0); assert 'xf:change' in m.stock and m.nothing(),load
    assert m.get(ps['press_natural'])==0
# A full tmpfs: the file isn't made and nothing is stored, so no SIGBUS; the next press tries again.
m=PressMachine(); m.written=-1; m.word(m.flag,0xdead); m.fn('ringnav_change',1); assert m.at()==0xdead and '/tmp/q2press' not in m.files
m.written=4; m.fn('ringnav_change',1); assert m.at()==5001
# A press the restart guard refuses plays nothing, so it stamps nothing, not even the file; its confirmation stamps before
# stock runs. Under 11 s the seek hook stamps nothing either: player_change_music does, if it goes ahead.
def asks(m,name,*args): m.handlers.pop(syms[name],None); return signed(m.call(address=syms[name],args=tuple(args)+(0,)*(4-len(args)),gap=0))
def guarded(machine=None):
    m=(machine or PressMachine)(); m.held=1; m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,2); m.install([('a','/a/a','A',0,0,0,0)],0); m.word(O['MCL_TYPE'],1); m.word(O['MCL_MODE'],0); m.byte(O['MCL_JUMPFOLDER'],0); return m
m=guarded(); assert asks(m,'player_seek_start')==1 and m.nothing() and not m.stock
m.now+=2000; asks(m,'player_seek_start'); assert m.stock==['xf:seek_start'] and m.seen==7001 and m.at()==7001
m=guarded(); assert asks(m,'player_change_music',1)==0 and m.nothing() and not m.stock
m.now+=2000; asks(m,'player_change_music',1); assert m.stock==['xf:change'] and m.seen==7001 and m.at()==7001
# A pick that starts nothing stamps nothing; one of a short song goes on to stock, where ringnav_load stamps.
m=guarded(); q=m.get(syms['mcl_pdeqplaylist']); assert asks(m,'player_start',q,0,1,2)==1 and m.nothing() and not m.stock
m.now+=2000; assert asks(m,'player_start',q,0,1,2)==1 and m.nothing() and not m.stock
m=guarded(); m.word(0xa50084,3); q=m.get(syms['mcl_pdeqplaylist']); assert asks(m,'player_start',q,0,1,2)==1 and m.nothing()
m=guarded(); m.held=0; q=m.get(syms['mcl_pdeqplaylist']); asks(m,'player_start',q,0,1,2); assert m.stock==['xf:player_start']
m=guarded(); m.sec=10; asks(m,'player_seek_start'); assert m.stock==['xf:seek_start'] and m.nothing()
# An earlier stamp stands through a refusal: the press it was for, not this one.
m=guarded(); m.held=0; asks(m,'player_change_music',1); assert m.at()==5001; m.held=1; m.now=9000; asks(m,'player_seek_start'); assert m.at()==5001
# Never an automatic change: on_wm_timer_dialog's skip past an unplayable file goes to stock, unasked and unstamped.
m=guarded(); m.handlers.pop(syms['player_next_music'],None); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
m.call(address=0x4f5020,args=(0,0,0,0),gap=0); assert m.stock==['xf:change'] and m.nothing() and not m.get(ps['restart_ask'])
print('Press: Next, Previous, a list pick and a user\'s seek stamp; a natural change forgets, stamps nothing and makes no file')
# What mclStartPlayer does on the jack from the idle player, its own code run from the way's test to the send (0x5bf8b0-0x5bf65c).
class StartMachine(PressMachine):
    def __init__(self):
        super().__init__(); self.quiet=[]
        self.handlers[syms['mclSetMute']]='xf:mute'; self.handlers[syms['usleep@GLIBC_2.0']]='xf:usleep'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name in ('xf:mute','xf:usleep'): self.quiet.append((name[3:],u.reg_read(REGS[0]))); return self.returns(0)
        return super().hook(u,address,size,unused)
    def site(self):
        self.quiet=[]; sp=self.u.reg_read(UC_MIPS_REG_SP)-0x100
        self.word(sp+0x20,0xa3aae0); self.u.reg_write(UC_MIPS_REG_SP,sp); self.u.reg_write(UC_MIPS_REG_GP,0xa3aae0); self.u.reg_write(UC_MIPS_REG_S7,0xa50000)
        self.u.emu_start(0x5bf8b0,0x5bf8ec,count=200000); self.u.reg_write(UC_MIPS_REG_SP,sp+0x100)
        return self.quiet
STOCK=[('mute',1),('usleep',100000)]
m=StartMachine(); assert m.get(0x5bf8c4)==jal(ps['start_mute']) and m.get(0x5bf8dc)==jal(ps['start_sleep'])
assert m.site()==STOCK and m.nothing()  # outside a natural change: a press, a boot, a resume, an app
m.word(ps['press_natural'],1); assert m.site()==STOCK and m.nothing()  # the natural starts ringnav_next(1) doesn't make
m.word(ps['press_natural'],2); assert m.site()==[] and m.at()==5000  # even: not a press's stamp
assert m.site()==[] and m.at()==5000
m.fn('start_sleep',150000); assert m.quiet==[('usleep',150000)]  # the skip is one sleep's, whatever comes next
m.now=(1<<32)+1; m.quiet=[]; assert m.site()==[] and m.at()==2  # and never 0
m.now=5000
m.word(ps['press_natural'],0); assert m.site()==STOCK and m.at()==0  # the skip doesn't outlast its start, and a start that mutes clears an unspent mark
m=StartMachine(); m.fn('ringnav_change',1); assert m.at()==5001; m.site(); assert m.at()==5001  # a press's stamp stays
m.word(ps['press_natural'],2); m.now=7001; assert m.site()==[] and m.at()==7000
for cls in (0xf1,0xf2,2,3,5):  # DLNA and the online queues stay stock's
    m=StartMachine(); m.word(ps['press_natural'],2); m.word(O['MCL_TYPE'],cls); assert m.site()==STOCK and m.nothing(),cls
m=StartMachine(); m.word(ps['press_natural'],2); m.written=-1; assert m.site()==STOCK and '/tmp/q2press' not in m.files  # no word to tell hciplayer
m=StartMachine(); m.word(ps['press_natural'],2); m.word(0xa50088,1); assert m.site()==[] and m.nothing()  # stock's own test: the jack's only
# ringnav_next(1) is what sets it, and only for its own span; a rollover's load inside it keeps the local class.
m=PressMachine(); m.options(1,5,0); m.stock=[]; m.advance_song(1); assert m.stock==['xf:start'] and m.start_natural==2 and m.get(ps['press_natural'])==0
m=PressMachine(); m.nested_load=1; m.advance_song(1); assert m.get(ps['press_natural'])==0
# The restart guard around the marks: a refused press or a pick that starts nothing leaves a natural start's even mark as
# it was, so it can't read as a press, and the next natural start marks afresh. A confirmed press stamps odd, its own start
# mutes as stock's, and a natural change after it forgets the word, so no wait comes of it.
m=guarded(StartMachine); q=m.get(syms['mcl_pdeqplaylist']); m.word(ps['press_natural'],2); assert m.site()==[] and m.at()==5000; m.word(ps['press_natural'],0)
assert asks(m,'player_seek_start')==1 and m.at()==5000 and not m.stock
assert asks(m,'player_change_music',1)==0 and m.at()==5000 and not m.stock  # another key: it asks again
assert asks(m,'player_start',q,0,1,2)==1 and m.at()==5000 and not m.stock
m.word(ps['press_natural'],2); m.now=6000; assert m.site()==[] and m.at()==6000; m.word(ps['press_natural'],0)
m=guarded(StartMachine); assert asks(m,'player_change_music',1)==0 and m.nothing()
m.now+=2000; asks(m,'player_change_music',1); assert m.at()==7001 and m.stock==['xf:change'] and m.site()==STOCK and m.at()==7001
m.handlers[syms['player_change_music']]='xf:none'; m.advance_song(1); assert m.at()==0 and m.get(ps['press_natural'])==0
m=guarded(StartMachine); assert asks(m,'player_seek_start')==1; m.now+=2000; asks(m,'player_seek_start'); assert m.at()==7001 and m.site()==STOCK and m.at()==7001
m.advance_song(1); assert m.at()==0
m=guarded(StartMachine); m.word(ps['press_natural'],2); m.site(); m.word(ps['press_natural'],0); m.held=0; asks(m,'player_start',m.get(syms['mcl_pdeqplaylist']),0,1,2)
assert m.stock==['xf:player_start'] and m.site()==STOCK and m.at()==0  # a real pick's start mutes, and clears the mark it isn't for
print('Start: a natural start on the jack skips demo\'s mute and sleep and marks the word; everything else keeps stock\'s')
