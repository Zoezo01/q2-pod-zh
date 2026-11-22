"""Where the fast-scroll letter shows, scrolling labels and fonts, and the playlist editor."""
from q2emu import *

# Nor on Library's category list, which Home's split shows beside the pane (localmusic_page's own is CTX_FIXED).
m,w,rs,es=letter_machine(False); m.nodes[w]['name']='scroll_view_localmusic'
for _ in range(4): m.call(gap=100)
m.paint(w,gap=0); assert not m.letters
m.nodes[w]['name']=''; m.paint(w,gap=0); assert m.letters; passed()  # the same spin elsewhere shows it
# Nor over a page whose list is in an order of its own (own_order, where the wheel arms the letter): Up Next, Chapters,
# and Library's song list by the class it shows and Songs' Sort. That list in name order, and another page left with
# the song list's class and Sort, keep the letter.
# A playlist's Sort is its own flag (0 by name, 1 Time added), read in sort's place for 0xff13.
for name,cls,sort,shown in (('upnext_page',0xf001,0,False),('chapters_page',0xf001,0,False),('allmusic_page',0xf009,0,False),
                            ('allmusic_page',0xf008,0,False),('allmusic_page',0xf007,0,False),('allmusic_page',0xf006,0,True),
                            ('allmusic_page',0xf00a,0,True),('allmusic_page',0xff13,1,False),('allmusic_page',0xff13,0,True),
                            ('allmusic_page',0xf001,3,False),('allmusic_page',0xf001,4,False),('allmusic_page',0xf001,1,True),
                            ('allmusic_page',0xf001,2,True),('allmusic_page',0xf001,5,True),('allmusic_page',0xf002,3,True),
                            ('localclass_page',0xf009,3,True)):
    m=Machine(); m.word(syms['g_class_type'],cls); m.word(syms['g_playlistsort_flag' if cls==0xff13 else 'g_sort_flag'],sort)
    w,es=m.page_list(40,extent=40*48,name=name)
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0)
    for _ in range(4): m.call(gap=100)
    m.paint(w,gap=0); assert bool(m.letters)==shown,(name,hex(cls),sort); passed()
# Nor over Edit Playlist's songs (ple.mode 0, in the order they were added); its Add Songs picker (mode 1) is A to Z.
for mode,shown in ((0,False),(1,True)):
    m=Machine(); m.word(payload_syms['ple'],1); m.word(payload_syms['ple']+28,mode)  # ple.page, ple.mode
    w,es=m.page_list(40,extent=40*48,name='playlistedit_page')
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0)
    for _ in range(4): m.call(gap=100)
    m.paint(w,gap=0); assert bool(m.letters)==shown,mode; passed()
# Never at one row per detent, nor on a list of at most SHORT_LIST_MAX (16) rows.
for n,gap in ((40,141),(16,100)):
    m,w,rs,es=letter_machine(False,n)
    for _ in range(6): m.call(gap=gap)
    m.paint(w,gap=0); assert not m.letters and not m.timers; passed()
# Stock's own scroll tick (0x5fb744), executed from the image, not mocked: it stops an unfocused
# only_focus label at once and steps any other, which is why marq_moved clears only_focus.
for only_focus,want,stepped in ((1,7,False),(0,None,True)):
    m=Machine(); t=m.node('hscroll_label'); m.byte(t+O['HSCROLL_ONLY_FOCUS'],only_focus)
    m.handlers[0x5fad3c]='hscroll_label_cast'; m.handlers[0x5fb330]='hscroll_label_step'
    info=m.alloc(0x58); m.word(info+0x20,t)
    ret=m.call(address=0x5fb744,args=(info,0,0,0),gap=0); names=[c[0] for c in m.calls]
    assert ('hscroll_label_step' in names)==stepped and ('hscroll_label_stop' in names)==(not stepped) and want in (None,ret),(names,ret); passed()
# Every scrolling label's holds and pace (ringnav_hscroll_start, ringnav_hscroll_lull) through stock's
# own hscroll_label code, run from the image: start, its delay, tick (0x5fb744), step, lull and stop.
# A label shows its offset while it runs; stopped, stock paints its start with the ellipsis ('rest').

def shown(m,t,over=104):
    if not m.get(t+O['HSCROLL_TIMER']): return 'rest'
    x=m.get(t+0x90); return 'start' if x==0 else 'end' if x==over else 'moving'
def timeline(m,t,ms):
    """What t shows each ms for ms, as [state, ms] runs."""
    runs=[]
    for _ in range(ms):
        m.advance(1); v=shown(m,t)
        if runs and runs[-1][0]==v: runs[-1][1]+=1
        else: runs.append([v,1])
    return runs
hold=O['MARQUEE_HOLD_MS']; scroll=int(104/struct.unpack('<f',struct.pack('<I',O['HSCROLL_PACE']))[0])  # 2079: NP's pace
assert scroll==2079
def pass_ok(runs,yoyo=False):
    """Its start held hold (until the second 16 ms tick moves it a whole pixel), a scroll at the pace, its end held hold."""
    assert len(runs)>=3, runs; (s,start),(v,moving),(e,end)=runs[:3]
    assert (s,v,e)==('start','moving','end') and hold<=start<hold+40 and abs(moving+start+end-2*hold-scroll)<40, runs
    assert end==hold+16*yoyo, runs  # a yoyo label's end moves on its first tick back
# A delay-0 label at stock's row pace (0.1 px/ms) and lull, Now Playing's title (lull and the old delay 1500,
# 0.05) and a label with no speed (its 5 s duration) run the same: hold, scroll, hold, snap back, hold, scroll.
seen=[]
for kw in (dict(),dict(speed=0.05,delay=1500,lull=1500),dict(speed=0.0)):
    m=hscroll_machine(); t=hscroll_label(m,**kw)
    m.call(address=syms['hscroll_label_start'],args=(t,),gap=0); runs=timeline(m,t,2*(hold+scroll+hold)+100)
    pass_ok(runs); pass_ok(runs[3:]); assert runs[6][0]=='start'
    assert m.get(t+O['HSCROLL_DELAY'])==hold and m.get(t+O['HSCROLL_SPEED'])==O['HSCROLL_PACE']; seen.append(runs)
assert seen[0]==seen[1]==seen[2]; passed()
# A slower label keeps its pace.
m=hscroll_machine(); t=hscroll_label(m,speed=0.02); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
runs=timeline(m,t,hold+5300); assert runs[1][0]=='moving' and runs[1][1]>5000; passed()
# A yoyo label scrolls back, holding hold at each end, not its lull as well.
m=hscroll_machine(); t=hscroll_label(m,yoyo=1); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
runs=timeline(m,t,2*(hold+scroll)+2*hold+100); pass_ok(runs,yoyo=True)
assert runs[3][0]=='moving' and runs[4][0]=='start' and hold<=runs[4][1]<hold+60,runs; passed()  # its last tick lands on 0
# An unfocused only_focus label stops on its first tick, as stock's, and rests on its start.
m=hscroll_machine(); t=hscroll_label(m,only_focus=1); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
assert timeline(m,t,hold+100)==[['start',hold+15],['rest',85]] and not m.timers and m.get(t+0x90)==0; passed()
# A label that doesn't loop (HSCROLL_ONCE) scrolls once, holds its end, and rests on its start; a yoyo one
# too, where stock parked it on its end; a running one isn't restarted.
from build import HSCROLL_ONCE
assert struct.unpack_from('<I',demo,fileoff(demo,HSCROLL_ONCE[0]))[0]==HSCROLL_ONCE[2]
for yoyo in (0,1):
    m=hscroll_machine(); t=hscroll_label(m,loop=0,yoyo=yoyo); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
    m.advance(100); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
    runs=timeline(m,t,hold+scroll+hold+100); runs[0][1]+=100; pass_ok(runs)
    assert runs[3:]==[['rest',200]] and not m.timers and m.get(t+0x90)==0,runs; passed()
# Stock's label attributes (set_hscroll_label_attribute, run from the image): its speed, lull and ellipsis. Since V1.36 it
# also gives the label a loop_interval_distance of its width, with which a pass has no end to hold: HSCROLL_GAP drops that
# call, stock makes it. In Menu Mode alone it sets only_parent_focus, and WHEEL_MODE keeps stock out of Menu Mode.
from build import HSCROLL_GAP, ALBUM_LABELS, FOCUS_RESTORE
for patched in (True,False):
    m=Machine(patched); m.handlers.pop(syms['set_hscroll_label_attribute'],None)
    m.mock('hscroll_label_set_speed','hscroll_label_set_lull','hscroll_label_set_ellipses','widget_set_prop_int','hscroll_label_set_only_parent_focus')
    t=m.node('hscroll_label'); m.word(t+O['W_W'],200)
    for mode in (1,0):
        m.calls=[]; m.byte(syms['g_wheelmode'],mode); m.call(address=syms['set_hscroll_label_attribute'],args=(t,0,0,0),gap=0)
        want=[('hscroll_label_set_speed',t,O['HSCROLL_PACE']),('hscroll_label_set_lull',t,2000),('hscroll_label_set_ellipses',t,1)]
        gap=[c for c in m.calls if c[0]=='widget_set_prop_int']
        assert [c[:3] for c in m.calls if c[0]!='widget_set_prop_int']==want+[('hscroll_label_set_only_parent_focus',t,1)]*(not mode),(patched,mode,m.calls)
        assert [(m.text(c[2]),c[3]) for c in gap]==[('loop_interval_distance',200)]*(not patched),(patched,gap)
passed()
# Albums' artist line and grid titles keep a focus flag in Volume Mode too (ALBUM_LABELS; test/fw has the hook's two calls).
# Each site is the call for the label stock names beside it, not the list row's title, whose call is the same function's.

for at,name in zip(ALBUM_LABELS,('scrlabel2_name','scrlabel1_name1','scrlabel1_name2','scrlabel1_name3')):
    assert struct.unpack_from('<I',demo,fileoff(demo,at))[0]==jal(payload_syms['ringnav_album_label']) and name in named_near(at),(hex(at),named_near(at))
assert 'scrlabel2_name' not in named_near(0x4a6bfc)  # the list row's title's call
passed()
# Stock's focus doesn't come back to a reloaded page's row (FOCUS_RESTORE): with a focused widget in $v0, V1.36's album page and
# Playlists go on to read its name; patched, both go on as with none, their row index -1.

for patched in (True,False):
    m=Machine(patched)
    assert falls(m,FOCUS_RESTORE[1][0],(0x4b5954,0x4b5ad0))==((0x4b5954,-1) if patched else (0x4b5ad0,7)),patched
    assert falls(m,FOCUS_RESTORE[0][0],(0x4a8eec,0x4a8f18))[0]==(0x4a8f18 if patched else 0x4a8eec),patched
passed()
# The selected row's long title scrolls as soon as the selection settles, MARQUEE_PASSES of those
# passes, then rests on its start; it stops as the selection moves or the screen darkens; a title
# that fits or stock scrolls by itself is left alone; in a table's pool, a new row in the same
# widget counts as a move.

running=lambda m,labels: [t for t in labels if m.get(t+O['HSCROLL_TIMER'])]
focus_only=lambda m,t: m.u.mem_read(t+O['HSCROLL_ONLY_FOCUS'],1)[0]
m,w,labels=marquee_list(); sel=m.selected(w); assert sel>=0
runs=timeline(m,labels[sel],O['MARQUEE_PASSES']*(hold+scroll+hold)+100)
for k in range(O['MARQUEE_PASSES']): pass_ok(runs[3*k:])
assert runs[3*O['MARQUEE_PASSES']:]==[['rest',runs[-1][1]]] and focus_only(m,labels[sel])==1 and not m.timers
assert running(m,labels)==[] and all(m.get(t+0x90)==0 for t in labels)
m.paint(w,gap=0); m.advance(1); assert not m.timers; passed()  # passes spent: nothing until the selection moves
m,w,labels=marquee_list(); m.advance(hold+100); on=running(m,labels); assert on==[labels[m.selected(w)]] and shown(m,on[0])=='moving'
m.call(); m.paint(w,gap=0); m.advance(1); assert focus_only(m,on[0])==1 and shown(m,on[0])=='rest'
assert running(m,labels)==[labels[m.selected(w)]] and shown(m,labels[m.selected(w)])=='start'; passed()
m.byte(syms['g_backlight_status'],0); m.advance(hold+scroll+hold); assert running(m,labels)==[] and not m.timers
m.byte(syms['g_backlight_status'],1); m.paint(w,gap=0); m.advance(1); assert len(running(m,labels))==1; passed()
for kw in (dict(over=False),dict(only_focus=0)):
    m,w,labels=marquee_list(**kw); m.advance(hold+1); assert not running(m,labels) and not m.timers; passed()
m=hscroll_machine(); m.marquee=True; w,rs,es=m.table_page(rebind=False); labels={}
for e in es:
    t=hscroll_label(m,loop=0,only_focus=1); m.nodes[e]['children']=[t]; m.word(t+O['W_PARENT'],e); labels[e]=t
m.call(); m.paint(w,gap=0); m.advance(hold+100); on=running(m,labels.values()); assert len(on)==1 and shown(m,on[0])=='moving'
first=m.selected(w); m.call(); m.bind(rs,48); m.paint(w,gap=0); assert m.selected(w)==first+1  # the pool moved with it: the same widget
m.advance(1); assert running(m,labels.values())==on and shown(m,on[0])=='start'; passed()  # stopped, then started on its new row
# Touch drops the spin, and with it the letter.
m,w,rs,es=letter_machine(False)
for _ in range(5): m.call(gap=100)
m.paint(w,gap=0); assert m.letters
m.touch(); m.paint(w,gap=0); assert not m.letters; passed()
# Elsewhere (a folder sorts by its file name) the first character stays.
m=Machine(); w,es=m.page_list(40,extent=40*48,name='folder_page')
for e in es: m.nodes[e]['text']='The Cure'
for _ in range(5): m.call(gap=100)
m.paint(w,gap=0); assert [l['text'] for l in m.letters]==['T']; passed()
# A settings menu is not alphabetical: no letter however long.
m=Machine(); w,es=m.page_list(40,extent=40*48,name='playset_page')
for e in es: m.nodes[e]['text']='row'
for _ in range(5): m.call(gap=100)
m.paint(w,gap=0); assert not m.letters; passed()

# Cover Flow caption weight: execute the target helper after the real theme_font
# chooses each caption's face. Mock only the native widget self painter, not font selection
# or canvas translation. A failed native paint still restores the canvas for later widgets.

for text,face,extra in (('Joe Hisaishi','classic-light',False),('佐咲紗花','default',True),
                        ('もののけ姫','default',True),('日本語 Title','default',True),
                        ('ハウルの動く城 サウンドトラック'*8,'default',True),('Навстречу','bold',False)):
    m=classic_light_new(CaptionWeightMachine)
    for field in ('REC_ALBUM','REC_ARTIST'): m.word(m.albums[0]+O[field],m.string(text))
    m.open()
    labels=[w for w in m.nodes[m.get(m.slide+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label']
    assert len(labels)==2 and all(m.nodes[w]['style:normal:font_name']==face for w in labels),(text,face)
    m.caption_passes=[]
    m.word(m.canvas+O['CANVAS_X'],7); m.word(m.canvas+O['CANVAS_Y'],9)
    for w in labels:
        m.call(address=payload_syms['font_weight_paint'],args=(w,m.canvas,0,0),gap=0)
        assert (m.get(m.canvas+O['CANVAS_X']),m.get(m.canvas+O['CANVAS_Y']))==(7,9)
    assert m.caption_passes==([(w,8,9) for w in labels] if extra else []),(text,m.caption_passes)
    passed()
# Shared font intent, including Now Playing's role, cached labels, panel headings and track rows.
for theme_id in ('0','1','2'):
    def weight_setup(machine): machine.config.update(THEME=theme_id)
    SETUP[:]=[weight_setup]
    try: m=CaptionWeightMachine()
    finally: SETUP.clear()
    w=m.node('hscroll_label',text='Your Song')
    english=m.call(address=payload_syms['theme_font'],args=(w,3,0xffffffff,0),gap=0)
    wanted=theme_id!='0'  # ROLE_NP: Classic and Modern ask for a bold face, Minimal for stock's
    m.nodes[w]['text']='日本語 Title'
    answer=m.call(address=payload_syms['theme_font'],args=(w,3,english,0),gap=0)
    assert bool(m.nodes[w].get('_q2_bold_fallback',0))==wanted,theme_id
    m.caption_passes=[]
    m.call(address=payload_syms['font_weight_paint'],args=(w,m.canvas,0,0),gap=0)
    assert len(m.caption_passes)==int(wanted),theme_id
    # The cache hit must repair a stale marker without restyling the font.
    m.nodes[w]['_q2_bold_fallback']=0
    m.call(address=payload_syms['theme_font'],args=(w,3,answer,0),gap=0)
    assert bool(m.nodes[w].get('_q2_bold_fallback',0))==wanted
    m.nodes[w]['text']='Your Song'
    m.call(address=payload_syms['theme_font'],args=(w,3,answer,0),gap=0)
    assert not m.nodes[w].get('_q2_bold_fallback',0)
    passed()
for title in ('小城故事','もののけ姫','日本語 English','한국어','العربية','देवनागरी'):
    m=classic_light_new(CaptionWeightMachine); w=m.node('hscroll_label',text=title)
    for bold in (1,0,1):
        m.call(address=payload_syms['panel_font'],args=(w,25,bold,0),gap=0)
        assert m.nodes[w].get('_q2_bold_fallback',0)==bold,title
        assert m.nodes[w]['style:normal:font_name']=='default'
        m.caption_passes=[]
        m.call(address=payload_syms['font_weight_paint'],args=(w,m.canvas,0,0),gap=0)
        assert len(m.caption_passes)==bold,title
    passed()

# Position bar against each real built-in theme's shelf geometry and each light/dark scheme.
def position_checks():
    for theme_id in ('0','1','2'):
        for light in ('0','1'):
            def position_setup(machine): machine.config.update(THEME=theme_id,LIGHT=light,CFPOSITION='1')
            SETUP[:]=[position_setup]
            try: m=CoverflowMachine()
            finally: SETUP.clear()
            m.open()
            covers=m.get(m.slide+O['W_PARENT'])
            paint_position=lambda w: m.call(address=payload_syms['coverflow_position_paint'],args=(w,m.canvas,0,0),gap=0)
            paint_position(m.slide)
            xs=[]
            for index in (1,2,0):
                m.word(m.slide+O['SLIDE_INDEX'],index); m.bands=[]
                paint_position(m.slide); paint_position(covers)
                assert len(m.bands)==5,(theme_id,light,m.bands)
                track,*thumb=m.bands
                labels=[w for w in m.nodes[covers]['children'] if m.nodes[w]['type']=='hscroll_label']
                bottom=max(m.get(w+O['W_Y'])+m.get(w+O['W_H']) for w in labels)
                assert track[:4]==(107,(bottom+m.get(covers+O['W_H']))//2-1,160,2),(theme_id,track,bottom)
                xs.append(min(b[0] for b in thumb))
                assert len(thumb)==4 and [b[2:4] for b in thumb]==[(7,1),(9,1),(9,1),(7,1)]
                assert 2*track[1]+track[3] == thumb[0][1]+thumb[-1][1]+1
            assert xs[1]>xs[0]>xs[2],(theme_id,light,xs)
            passed()

position_checks()

# iPod: the wheel reaches Edit Playlist below stock's Rename and Delete, and Centre opens the editor.
m=PlaylistEditMachine(); more=m.node('window','playlistsmore_page'); m.word(more+O['W_PARENT'],m.wm)
m.word(more+O['W_W'],375); m.word(more+O['W_H'],290); m.top=more; m.stack.append(more)
for i,name in enumerate(('label_rename','label_del')):
    row=m.node('list_item',children=[m.entry(0)]); m.word(row+O['W_Y'],10+i*72); m.word(row+O['W_PARENT'],more)
    m.word(m.nodes[row]['children'][0]+O['W_PARENT'],row); m.nodes[more]['children'].append(row)
m.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
m.on_click=lambda w,e: m.nested(*m.handler(w,O['EVT_CLICK']),e)
assert m.call()==11 and m.call()==11 and m.selected(more)==2
assert m.confirm()==11 and m.nodes[m.top]['name']=='playlistedit_page'; passed()

m=PlaylistEditMachine(); m.open_editor()
# The wheel still selects rows and Centre opens the selected song's actions.
m.on_click=lambda w,e: m.nested(*m.handler(w,O['EVT_CLICK']),e)
assert m.call()==11 and m.selected(m.view)==3
assert m.confirm()==11 and m.showlabels()==['上移','下移','从播放列表移除']
assert m.key()==11 and m.selected(m.find('scroll_view'))==3
m.close()
m=PlaylistEditMachine(); m.open_editor()
assert m.showlabels()==['添加歌曲','完成','A','B','C']
assert m.nodes[m.nodes[m.page]['children'][0]]['text']=='正在编辑: Mix'
# A repeat and long press cannot move again, and releases never reach playback.
for key,want in ((O['KEY_FWD_BTN'],['B','A','C']),(O['KEY_FWD_BTN'],['B','C','A']),(O['KEY_BACK_BTN'],['B','A','C'])):
    assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
    for _ in range(2):
        assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
        assert m.call(key,address=HOOKS['on_wm_keylong_fun'][0],event_type=O['EVT_KEY_LONG'],gap=0)==11
    assert m.order()==want
    assert m.call(key,gap=0)==11
    m.view=m.find('scroll_view'); assert m.selected(m.view)==want.index('A')+2
assert m.names()==m.before_queue and not m.playback(); passed()
# Touch actions and confirmation; cancel and Back leave the entry intact.
m.choose(3); assert m.showlabels()==['上移','下移','从播放列表移除']
m.choose(2); assert m.showlabels()==['从播放列表移除','取消']; m.choose(1)
assert m.order()==['B','A','C']; m.choose(3); m.choose(0); assert m.order()==['A','B','C']
m.choose(2); m.choose(1); assert m.order()==['B','A','C']
m.choose(3); m.choose(2); m.choose(0); assert m.order()==['B','C'] and m.selected(m.view)==3
assert m.order(1)==['Other']; passed()
# The fixed destination picker stays open; failed writes never report success.
saved=m.alloc(16); m.word(saved,0x12345678); m.word(syms['p_vector_select_record'],saved)
m.choose(0); assert m.showlabels()==['搜索','T1','T2']; m.choose(1)
assert m.get(syms['p_vector_select_record'])==saved and m.get(saved)==0x12345678
assert not any(c[0].startswith('batch_') for c in m.calls)
assert m.toasts[-1][3]=='已加入播放列表' and m.showlabels()==['搜索','T1','T2']
m.choose(1); assert m.toasts[-1][3]=='已在播放列表中'
m.fail='write'; m.choose(2); assert m.toasts[-1][3]=='无法保存播放列表'; m.fail=''
assert m.ticks()==[False,True,False]  # ticked on add, not on a failed write
assert m.key()==11 and m.showlabels()==['添加歌曲','完成','B','C','T1']
m.choose(1); assert m.top!=m.page and not m.get(payload_syms['ple'])
assert m.names()==m.before_queue; passed()
# Add Songs' Search row filters by tagged title, artist or album, case-insensitively; the query
# stays for adding several and an empty one restores the whole picker.

m=PlaylistEditMachine(); m.word(m.found[0]+O['REC_ARTIST'],m.string('Beatles'))
m.word(m.found[1]+O['REC_ALBUM'],m.string('Zebra')); m.open_editor(); m.choose(0)
assert m.showlabels()==['搜索','T1','T2']
ple_search(m,'T2'); assert m.showlabels()==['搜索：T2','T2'] and not m.find('edit')
m.choose(0); assert m.nodes[m.find('edit')]['text']=='T2'  # prefilled for editing
m.nodes[m.find('edit')]['text']='beat'; f,ctx=m.handler(m.find('edit'),O['EVT_IM_ACTION'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0); m.view=m.find('scroll_view')
assert m.showlabels()==['搜索：beat','T1']
m.choose(1); assert m.toasts[-1][3]=='已加入播放列表' and m.showlabels()==['搜索：beat','T1']
ple_search(m,'zeb'); assert m.showlabels()==['搜索：zeb','T2']
ple_search(m,''); assert m.showlabels()==['搜索','T1','T2'] and m.ticks()==[False,True,False]
ple_search(m,'zzz'); assert m.showlabels()==['搜索：zzz','无匹配歌曲']
# Return from the box applies its text, and a filtered pick can close the page cleanly.
m.choose(0); m.nodes[m.find('edit')]['text']='t1'; assert m.key()==11
assert m.showlabels()==['搜索：t1','T1']; m.close(); passed()
# Add Songs lists the first 500 songs, under ringnav's MAX_ENTRIES (512), then a row that opens the Search box.
m=PlaylistEditMachine(); m.found=[m.song(f'S{i:03}') for i in range(600)]; m.budget=1500000; m.open_editor()
cost=[0]; hook=m.u.hook_add(UC_HOOK_CODE,lambda *a: cost.__setitem__(0,cost[0]+1),begin=BASE,end=SCRATCH-1)
m.choose(0); m.u.hook_del(hook); cost_line('add songs 600',cost[0])
labels=m.showlabels(); assert labels==['搜索',*(f'S{i:03}' for i in range(500)),'搜索更多歌曲'],(len(labels),labels[-2:])
m.choose(501); edit=m.find('edit'); assert edit and m.nodes[edit]['name']=='search_edit' and m.order()==['A','B','C']; passed()

m=PlaylistLifecycleMachine(); assert m.create("O'Brien")==6
assert m.db.execute('select id,filename,name,cd_num from playlist where id=6').fetchone()==(6,"O'Brien","O'Brien",6)
assert m.create("O'Brien")==-2 and not m.db.in_transaction; passed()
# A name stored cut to 63 bytes never matches the whole one, as in stock: an M3U import takes any
# result but -1 as the playlist's ID.
m=PlaylistLifecycleMachine(); assert m.create('L'*70)==6 and m.create('L'*70)==7; passed()
for failure in ('open','begin','insert','commit'):
    m=PlaylistLifecycleMachine(); before=m.db.execute('select * from playlist').fetchall(); m.fail=failure
    assert m.create('New')==-1
    assert m.db.execute('select * from playlist').fetchall()==before and not m.db.in_transaction; passed()
m=PlaylistLifecycleMachine(); m.db.executemany('insert into playlist(id) values(?)',[(i,) for i in range(6,100)])
assert m.create('Full')==-1; passed()
for failure in ('','open','begin','delete','delete from playlist where id>100','commit'):
    m=PlaylistLifecycleMachine(); before=m.db.execute('select * from playlist').fetchall(); m.fail=failure; m.delete()
    if failure:
        assert m.db.execute('select * from playlist').fetchall()==before and m.backs==0
        assert m.toasts[-1][3]=='无法删除播放列表'
    else:
        assert m.db.execute('select id from playlist where id=0 or id=101').fetchall()==[]
        assert m.db.execute('select id from playlist where id=1 or id=102').fetchall()==[(1,),(102,)]
        assert m.backs==1 and bytes(m.u.mem_read(syms['g_delete_flag'],1))==b'\x01' and m.toasts[-1][3]=='播放列表已删除'
    assert m.reload_pending()==(not failure)
    assert not m.db.in_transaction; passed()
m=PlaylistLifecycleMachine(); m.db.execute('delete from playlist where id=101'); m.delete()
assert not m.db.execute('select id from playlist where id=0').fetchall() and m.toasts[-1][3]=='播放列表已删除'
assert m.create('Replacement')==0; passed()
m=PlaylistLifecycleMachine(); m.db.execute('delete from playlist where id=0'); m.delete()
assert m.db.execute('select id from playlist where id=101').fetchall()==[(101,)] and m.toasts[-1][3]=='无法删除播放列表'; passed()
m=PlaylistLifecycleMachine(); m.answer=0; before=m.db.execute('select * from playlist').fetchall(); m.delete()
assert m.db.execute('select * from playlist').fetchall()==before and not m.backs and not m.reload_pending(); passed()

# Holding Play/Pause on a playlist row opens its menu: Play Next, Add to Queue and Shuffle take its
# songs (getMusicByPlayList of its cd_num, spoken ones and folders left out as its opened list shows
# them); More… opens the same More page as its trailing dots. The Add to Playlist picker shares
# playlist_page's name and keeps stock's hold. The playlist is the index in the dots' name: row
# positions also count Import/Export.

m=playlist_hold(0); m.press(7000); assert m.hold()!=11 and not m.opened  # Import: no dots, stock keeps the hold
m=playlist_hold(); m.press(7100); assert m.hold()==11 and m.release()==0  # the locked hold's release never toggles playback
assert m.labels()==['下一首播放','加入播放队列','随机播放','更多…'] and m.nodes[m.title]['text']=='List 1'
m.pick(3); m.advance(0); assert m.opened[-1]==('localmusic/playlistsmore_page',1) and m.names()==['A','B','C']; passed()
for row,want in ((0,['A','T1','T2','B','C']),(1,['A','B','C','T1','T2'])):
    m=playlist_hold(); assert m.run(row) is None and m.names()==want and m.query[:2]==('getMusicByPlayList',8)
    assert not m.playback(); passed()
m=playlist_hold(1); m.found=[m.song('dir',4)]; assert m.run(0)=='队列未更改' and m.names()==['A','B','C']; passed()
m=playlist_hold(); m.press(7100); assert m.hold()==11 and m.release()==0
m.word(syms['p_deque_playlist'],m.deque([m.song('Other',4)])); m.pick(3); m.advance(0)
assert not [o for o in m.opened if o[0]=='localmusic/playlistsmore_page']; passed()  # the list changed: nothing
m=playlist_hold(1); m.nodes[m.top]['_pl_mode']=1; m.press(7300)  # the page's own mode, not the global
assert m.hold()!=11 and not [o for o in m.opened if o[0]=='localmusic/playlistsmore_page']; passed()
for count in (0,1):
    m=PlaylistEditMachine(count); m.open_editor(); before=m.order()
    m.move(O['KEY_BACK_BTN']); m.move(); assert m.order()==before
    assert m.key()==11 and not m.get(payload_syms['ple']); passed()
# Roll back failed statements/commits, stale IDs, wrong playlist, CUE/path changes and ignored writes.
for failure in ('open','begin','update','commit'):
    m=PlaylistEditMachine(); m.fail=failure
    assert m.mutate()==0 and m.order()==list('ABC') and not m.db.in_transaction
    assert m.closed_db==1; passed()
m=PlaylistEditMachine(); m.db.execute("create trigger no_move before update on playlist begin select raise(ignore); end")
assert m.mutate()==0 and m.order()==list('ABC'); passed()
for change in ("delete from playlist where id=101", "update playlist set cd_num=1 where id=101",
               "update playlist set fileurl='/new' where id=101", "update playlist set time=30 where id=101"):
    m=PlaylistEditMachine(); a=m.record(m.db.execute('select * from playlist where id=101').fetchone()); b=m.record(m.db.execute('select * from playlist where id=102').fetchone())
    m.db.execute(change); before=m.db.execute('select * from playlist order by id').fetchall()
    assert m.call(address=payload_syms['ple_mutate'],args=(0,a,b,0),gap=0)==0
    assert m.db.execute('select * from playlist order by id').fetchall()==before; passed()
# Duplicate paths and CUE segments are removed by entry ID; hidden records keep their positions.
m=PlaylistEditMachine(); m.db.execute("update playlist set fileurl='/same',time=id,time_end=id+1 where cd_num=0 and id>100")
m.db.execute("insert into playlist values(150,0,'/hidden',0,0,'Hidden')")
assert m.mutate(101,103)==1 and m.order()==['C','B','A','Hidden']
assert m.mutate(102,0)==1 and m.order()==['C','A','Hidden'] and m.order(1)==['Other']
# Reopening uses the database order and leaves the live queue unchanged.
m.open_editor(); assert m.showlabels()==['添加歌曲','完成','C','A','Hidden'] and m.names()==m.before_queue
assert m.mutate(101,200,0)==0
m.db.execute('update playlist set id=2147483647 where id=200'); assert m.mutate(101,103)==0
passed()
# Back cancels a deferred confirmation before returning to the editor.
m=PlaylistEditMachine(); m.open_editor(); m.choose(2); m.choose(2)
m.pick(0); assert m.key()==11 and m.order()==list('ABC')
assert m.showlabels()==['添加歌曲','完成','A','B','C']; passed()
# A side press during a deferred click cannot change the row that click refers to.
m.choose(2); assert m.key()==11
m.view=m.find('scroll_view')
m.pick(3); m.move(); m.advance(0)
assert m.order()==list('ABC') and m.showlabels()==['上移','下移','从播放列表移除']; passed()
# Simultaneous side-button presses retain independent repeat/release latches.
m=PlaylistEditMachine(); m.open_editor()
for key in (O['KEY_FWD_BTN'],O['KEY_BACK_BTN']):
    assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
assert m.order()==list('ABC')
assert m.call(O['KEY_BACK_BTN'],gap=0)==11
assert m.call(O['KEY_FWD_BTN'],address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
assert m.order()==list('ABC') and m.call(O['KEY_FWD_BTN'],gap=0)==11; passed()
# Failed removal and identical occurrences still target one entry only.
for failure in ('delete','commit'):
    m=PlaylistEditMachine(); m.fail=failure
    assert m.mutate(101,0)==0 and m.order()==list('ABC') and not m.db.in_transaction; passed()
m=PlaylistEditMachine(); m.db.execute("update playlist set fileurl='/same',time=0,time_end=0 where id in (101,102)")
assert m.mutate(102,0)==1 and m.order()==['A','C']; passed()
