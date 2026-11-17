"""Slide menus, the fast-spin ramp, the stock canvas and rounded wrappers, panes and the stock key filter."""
from q2emu import *

# Centre arms the intended final icon while its slide is still unfinished.
m=Machine(); w=m.page('home_page','slide_menu')
for _ in range(5): m.call(gap=20)
m.release(0); m.advance(75); m.advance(O['DOUBLE_CLICK_MS']-75)
assert m.clicks==[m.nodes[w]['children'][5]] and not m.slides, (m.clicks,m.get(w+O['SLIDE_INDEX']),m.timers,m.slides); passed()
m=Machine(); w=m.page('home_page','slide_menu'); m.release(0); m.call(gap=20)
m.advance(300); assert not m.clicks; passed()
# Other slide menus retain stock next/previous handling.
m=Machine(); m.page('sysset_page','slide_menu')
assert m.call(gap=0)==11 and m.call(gap=1)==11 and m.moved(); passed()
# Even if the UI services a release before an overdue timer, both singles confirm once.
m=Machine(); w,es=m.page_list(3); m.release(); m.now+=O['DOUBLE_CLICK_MS']+1
assert m.release()==11 and m.clicks==[es[0]] and len(m.timers)==1
m.advance(300); assert m.clicks==[es[0],es[0]] and not m.screens; passed()
# Deadline expiry may navigate: the following release must resolve the new menu.
m=Machine(); w,es=m.page_list(3)
def navigate(a,b):
    m.on_click=None
    m.page_list(3,name='display_page')
m.on_click=navigate; m.release(); old=m.top
assert m.release(O['DOUBLE_CLICK_MS']+1)==11 and m.top!=old and len(m.clicks)==1
m.advance(300); assert len(m.clicks)==2 and m.clicks[0]!=m.clicks[1] and not m.screens; passed()
# The downstream stock cleanup still owns long-press and boot-key releases.
for addr in (syms['g_power_longkey'],syms['g_ingore_bootkey_flag'],O['BOOT_KEY_GUARD']):
    m=Machine(); m.page_list(3); m.byte(addr,1)
    assert m.release()==0
    m.advance(300)
    assert not m.clicks and not m.screens and not m.timers and m.u.mem_read(addr,1)==b'\0'; passed()
# Play/Pause reaches the real stock downstream handler, including on a supported page.
m=Machine(); m.page_list(3)
assert m.call(O['KEY_PLAY'])==0
m.call(O['KEY_PLAY'],address=syms['on_wm_keyup_fun'],gap=0)
assert any(c[0]=='playpause_quick_click' for c in m.calls) and not m.clicks; passed()
# A root-window paint observes leaving navigation even when the new page has no pane.
m=Machine(); w,es=m.page_list(3); m.release(); old=m.top
m.top=m.node('window','playing_page'); m.paint(m.top,gap=100)
m.top=old; m.advance(300); assert not m.clicks and not m.timers; passed()
m=Machine(); w=m.page('home_page','slide_menu'); home=m.top; m.call(gap=0)
m.top=m.node('window','playing_page'); m.paint(m.top,gap=20)
m.top=home; assert m.call(gap=1)==11 and m.moved(); passed()
# Local query and home selection changes invalidate pending confirmation too.
m=Machine(); w,rs,es=m.table_page(); m.release()
m.word(syms['g_class_type'],0xf002); m.advance(300); assert not m.clicks; passed()
m=Machine(); w=m.page('home_page','slide_menu')
m.word(w+O['SLIDE_INDEX'],0); m.nodes[w]['children']=[m.entry(w),m.entry(w)]
m.release(); m.word(w+O['SLIDE_INDEX'],1); m.advance(300); assert not m.clicks; passed()
# Unsigned milliseconds may wrap while confirmation is pending.
m=Machine(); m.now=0xfffffff0; m.page_list(3)
assert m.release()==11 and m.release(DC-1)==0
m.advance(300); assert not m.clicks and m.screens==[0]; passed()
# Short lists stay precise; virtual tables use total logical rows, not their row pool.
for table in (False,True):
    for count in (0,1,16,17):
        m=Machine()
        if table:
            # A dynamic page keeps hard ends, so this stays an acceleration check.
            w,rs,es=m.table_page(n=min(count,4),name='playerqueue_page',rebind=True)
            m.word(w+O['TABLE_ROWS'],count)
        else: w,es=m.page_list(count,extent=count*48)
        m.paint(w)
        if count==17:
            # Seventeen rows accelerate even at a 50ms cadence: the second tick is still one
            # row (50ms of spin), the third crosses 100ms and steps two. iPod's row lists wait
            # for 300ms of spin: six one-row ticks, then the seventh steps two.
            for want in (1,2,3,4,5,6,8):
                assert m.call(gap=50)==11 and m.selected(w)==want
            for _ in range(20): m.call(gap=50)
            assert m.selected(w)==count-1      # held at the end
            assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(w)==count-2 # ramp resets there
        else:
            for want in (1,2,3):
                assert m.call(gap=50)==11 and m.selected(w)==min(want,count-1)
        if count==16:
            for want in range(4,16):
                assert m.call(gap=50)==11 and m.selected(w)==want
            for want in range(14,-1,-1):
                assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(w)==want
            assert m.call(gap=50)==11 and m.selected(w)==1 # and at the start
        passed()

# Resizing across the short-list boundary cannot carry a previous fast run with it.
m=Machine(); w,es=m.page_list(17,height=960,extent=17*48)
m.nodes[w]['children']=es; m.paint(w)
for want in (1,2,3,5,7):  # iPod: two rows from 300ms of spin
    assert m.call(gap=100)==11 and m.selected(w)==want
m.nodes[w]['children']=es[:16]; m.paint(w)
assert m.call(gap=100)==11 and m.selected(w)==1   # fresh one-row run on the short list
for want in (2,3):
    assert m.call(gap=100)==11 and m.selected(w)==want
m.nodes[w]['children']=es; m.paint(w)
assert m.call(gap=100)==11 and m.selected(w)==1   # and again after growing back
for want in (2,3,5):
    assert m.call(gap=100)==11 and m.selected(w)==want
passed()

# Sustained ticks ramp one row per 100ms of same-direction spin up to eight rows, then hold;
# pauses, spacing past the 140ms window and reversal reset. Byte-exact boundaries, wrapped clock.
# iPod's row lists ramp gently (navigation.c LIST_FIRST_MS, LIST_RAMP_MS): one row until 300ms of
# spin, two from there and one more per further 200ms, eight from 1500ms. IPOD_SPIN is (gap since
# the last tick, rows per step then); every gap is inside the 140ms window, so the run is one.
IPOD_SPIN=((0,1),(100,1),(100,1),(99,1),(1,2),(100,2),(99,2),(1,3),(100,3),(99,3),(1,4),(100,4),(99,4),
           (1,5),(100,5),(99,5),(1,6),(100,6),(99,6),(1,7),(100,7),(99,7),(1,8),(100,8))
for table in (False,True):
    for start in (0,0xfffffff0):
        m=Machine(); m.now=start
        w,rs=m.list_surface(table,n=500)
        if table: m.word(w+O['TABLE_ROWS'],500)
        # 99ms of spin is still one row; the next millisecond enters two-row speed, and each
        # further 100ms adds one row until the eight-row ceiling holds.
        seq=((0,1),(99,2),(1,4),(99,6),(1,9),(99,12),(1,16),(99,20),(1,25),(99,30),(1,36),
             (99,42),(1,49),(99,56),(1,64),(99,72),(1,80))
        # 299/300, 499/500 ... 1499/1500ms
        seq=[(gap,sum(g for _,g in IPOD_SPIN[:i+1])) for i,(gap,_) in enumerate(IPOD_SPIN)]
        for gap,want in seq:
            assert m.call(gap=gap)==11 and m.selected(w)==want
        # 140ms still counts as spinning, 141ms breaks the run and drops back to one row.
        assert m.call(gap=140)==11 and m.selected(w)==want+8
        assert m.call(gap=141)==11 and m.selected(w)==want+9
        assert m.call(O['KEY_PREV'],gap=1)==11 and m.selected(w)==want+8
        passed()
# Pixel-scroll fallback keeps the 100ms ramp, and immediate offsets.
m=Machine(); w=m.page(t='table_client')
for want in (48,144,288,480,720,1008,1344,1728):
    assert m.call(gap=100)==11 and m.get(w+O['TABLE_TOP'])==want
assert m.call(gap=141)==11 and m.get(w+O['TABLE_TOP'])==1776; passed()

# A fast spin belongs to its live menu, pane and browsing scope.
for change in ('window','pane','scope','context','count','gesture','screen','unsupported','touch','click','missing','centre'):
    m=Machine(); w,es=m.page_list(40,extent=40*48,
        name='sysset_page' if change=='context' else 'allmusic_page')
    for _ in range(7): m.call(gap=100)
    assert m.selected(w)==13  # iPod: 1,1,1,2,2,3,3 rows
    w=disturb(m,w,change,40)
    m.paint(w,gap=0)
    before=m.selected(w)
    assert m.call(gap=50)==11 and m.selected(w)==before+1,change
    passed()
# A tick while a page slides is held and plays into it once the slide ends, leaving no timer;
# Centre or Return ends a held turn, and a held turn expires after HOLD_MS.
m=Machine(); w,es=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w)
assert m.call(gap=1000)==11 and m.selected(w)==1
m.animating=1; assert m.call(gap=1000)==11 and m.call(gap=20)==11 and m.selected(w)==1
m.advance(O['HOLD_POLL_MS']*3); assert m.selected(w)==1
m.animating=0; m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==2
m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==3 and not m.timers; passed()
for key in ('KEY_RETURN','KEY_CENTER',None):
    m=Machine(); w,es=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w)
    assert m.call(gap=1000)==11 and m.selected(w)==1
    m.animating=1; m.call(gap=1000)
    if key: m.call(O[key],gap=0)
    else: m.advance(O['HOLD_MS']+O['HOLD_POLL_MS'])
    m.animating=0; m.advance(1000); assert m.selected(w)==1 and not m.timers,key; passed()
# Rejected stock wheel input resets a sustained list run.
m=Machine(); w,es=m.page_list(40,extent=40*48)
for _ in range(7): m.call(gap=100)
fast=13
assert m.selected(w)==fast
m.byte(0xa4bbf8,8)
assert m.call(gap=10,debounce=True)==11 and m.selected(w)==fast
assert m.call(gap=40)==11 and m.selected(w)==fast+1; passed()
# A click on a clickable child selects its collected ancestor, not a stale row.
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
row1=m.entry(w,0); row2=m.entry(w,96); deep=m.entry(row1,0)
m.nodes[row1]['children']=[deep]; m.nodes[w]['children']=[row1,row2]
m.paint(w); assert m.selected(w)==0
assert m.call(O['KEY_NEXT'])==11 and m.selected(w)==1
m.click(deep); assert m.selected(w)==0
m.click(m.node('button')); assert m.selected(w)==0; passed()
# Real canvas ABI, translation and clip code execute; only the LCD rectangle sink is mocked.
# A 20px row at canvas origin (7,20) under a (10,30)-(229,199) clip. Classic's band is one-pixel rows, each reaching the
# LCD from the clip's left edge; Modern's Rounded pill only its row pixels inside the clip.
pill=[]
for band_y in range(10,20):
    n,frac=chord(band_y,20)
    pill.append((7+O['SEL_INSET']+n,20+band_y))
    if frac: pill.extend([(7+O['SEL_INSET']+n-1,20+band_y),(7+240-O['SEL_INSET']-n,20+band_y)])
for config,want in (({'THEME':'1'},[(10,y) for y in range(30,40)]),({'THEME':'2','ROUNDED':'1'},[(x,y) for x,y in pill if 10<=x<=229])):
    m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
    m.config.update(config)
    e=m.entry(w,0); m.word(e+O['W_H'],20); m.nodes[w]['children']=[e]
    sink='lcd_fill_rect'
    for name in ('canvas_get_clip_rect','canvas_set_clip_rect',
                 'canvas_fill_rect'):
        del m.handlers[syms[name]]
    m.mock(sink)
    m.word(m.lcd+0x3c,1); m.word(m.lcd+0xb0,240); m.word(m.lcd+0xb4,240)
    m.word(m.canvas+O['CANVAS_X'],7); m.word(m.canvas+O['CANVAS_Y'],20)
    for off,val in [(0x10,10),(0x14,30),(0x18,229),(0x1c,199)]: m.word(m.canvas+off,val)
    m.paint(w)
    assert [(c[2],c[3]) for c in m.calls if c[0]==sink]==want
    assert [m.get(m.canvas+off) for off in (0x10,0x14,0x18,0x1c)]==[10,30,229,199]
    assert m.lcd_colors()==LCD_COLORS
passed()
# Wheel selection is immediate even when restoration animations would still be running.
m=Machine(); w,es=m.page_list(6)
m.paint(w); m.glide=False
m.call(); m.call(); assert m.selected(w)==2 and m.get(w+O['SCROLL_Y'])==60
m.paint(w); assert m.selected(w)==2
assert m.confirm()==11 and m.dispatched()[0][1]==es[2]
m.call(); assert m.selected(w)==3
m.call(O['KEY_PREV']); assert m.selected(w)==2
m.call(O['KEY_PREV']); assert m.selected(w)==1 and m.moved()[-1][3]==36
m.call(O['KEY_PREV']); assert m.selected(w)==0 and m.get(w+O['VIEW_ANIMATOR'])==0; passed()
# Empty menus never activate or turn off the screen; touch doesn't swallow its first event.
m=Machine(); w=m.page(); assert m.confirm()==11 and not m.dispatched()
assert m.touch()==0 and not m.dispatched(); passed()
# Clip an oversized target to its surface without losing its selection.
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w); m.word(e+O['W_H'],140); m.nodes[w]['children']=[e]
m.paint(w)
assert m.selected(w)==0 and m.sel()==(0,0,240,140)
assert {r['clip'] for r in m.rounded}|{b[5] for b in m.bands}=={(0,0,240,96)}; passed()
# Both list kinds leave breathing room, shrink it in tight viewports, and clamp at either end.
for virtual in (False,True):
    for height,margin in ((96,12),(60,6),(49,0),(48,0),(40,0)):
        m=Machine()
        if virtual:
            w,rs,es=m.table_page(n=10,rebind=True); m.word(w+O['TABLE_ROWS'],10); off=O['TABLE_TOP']
        else:
            w,es=m.page_list(10,extent=480); off=O['SCROLL_Y']
        m.word(w+O['W_H'],height); m.paint(w)
        assert m.get(w+off)==0 and not m.moved()
        for _ in range(3): m.call()
        assert m.selected(w)==3
        assert m.get(w+off)==(144 if height<48 else 192-height+margin)
        m.call(O['KEY_PREV'])
        assert m.selected(w)==2 and m.get(w+off)==96-margin
        m.call(O['KEY_PREV']); m.call(O['KEY_PREV'])
        assert m.selected(w)==0 and m.get(w+off)==0
        for _ in range(9): m.call()
        assert m.selected(w)==9 and m.get(w+off)==(432 if height<48 else 480-height)
        m.paint(w); assert not m.moved(); passed()
# Entering a tall row from either direction reveals its title, including after recreation.
m=Machine(); w,es=m.page_list(3,height=96,extent=400)
m.word(es[1]+O['W_H'],140); m.word(es[2]+O['W_Y'],188)
m.call(); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48
m.call(); m.call(O['KEY_PREV'])
assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48
w,es=m.page_list(3,height=96,extent=400)
m.word(es[1]+O['W_H'],140); m.word(es[2]+O['W_Y'],188)
m.paint(w); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48; passed()
# Row 0 brings back what heads the list above it (the EQ page's graph) when both fit; a head too tall for that
# leaves the margin, so row 0 is never pushed off.
for height,top in ((192,0),(144,84)):
    m=Machine(); w,es=m.page_list(6,height=height,extent=96+6*48)
    for i,e in enumerate(es): m.word(e+O['W_Y'],96+i*48)
    m.paint(w)
    for _ in range(4): m.call()
    assert m.selected(w)==4 and m.get(w+O['SCROLL_Y'])>top
    for _ in range(4): m.call(O['KEY_PREV'])
    assert m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==top; passed()

m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w,0); m.word(e+O['W_W'],4); m.word(e+O['W_H'],4); m.nodes[w]['children']=[e]
assert m.paint(w)==0 and not m.drawn() and m.clip==(0,0,240,240); passed()
# A page with no entries paints nothing and leaves every saved property alone.
m=Machine(); w=m.page()
assert m.paint(w)==0 and m.selected(w)==-1
assert not m.drawn() and m.global_alpha==0 and m.clip==(0,0,240,240)
assert m.lcd_colors()==LCD_COLORS; passed()

# Every saved value is unusual: the rounded path restores fill, stroke and clip exactly and
# never touches the canvas or LCD global-alpha bytes.
m=Machine(); w,es=m.page_list(3,extent=1000)
m.word(m.lcd+O['LCD_FILL_COLOR'],0x00000001); m.word(m.lcd+O['LCD_STROKE_COLOR'],0x80ffffff)
m.byte(m.canvas+0x0e,0x7f); m.byte(m.lcd+0xe4,0x11)
m.paint(w)
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x00000001 and m.get(m.lcd+O['LCD_STROKE_COLOR'])==0x80ffffff
assert m.u.mem_read(m.canvas+0x0e,1)[0]==0x7f and m.u.mem_read(m.lcd+0xe4,1)[0]==0x11
assert m.global_alpha==0 and m.clip==(0,0,240,240); passed()

# Taps choose their pane even with no initial owner or with both panes previously selected.
for seeded in ((),(0,),(0,1)):
    m=Machine(); a,b,ae,be=m.panes()
    nested=m.entry(be[1]); m.nodes[be[1]]['children']=[nested]
    for i in seeded: m.nodes[(a,b)[i]]['_ringnav_index']=0
    m.touch(); m.click(nested)
    assert m.selected(a)==-1 and m.selected(b)==1
    m.paint(a); assert not m.drawn()
    m.paint(b); assert not m.drawn()
    m.confirm(); assert m.dispatched()[0][1]==be[1]
    m.call(); assert m.selected(b)==2
    m.touch(); m.click(ae[0]); m.confirm()
    assert m.selected(b)==-1 and m.dispatched()[0][1]==ae[0]
    passed()

# An inactive pane is never painted, so the outline cannot appear on two panes at once.
m=Machine(); a,b,_,_=m.panes(96); m.nodes[a]['_ringnav_index']=0
m.paint(b); assert not m.drawn()
m.paint(a); assert m.sel()==(0,0,240,48); passed()

# The real stock rounded wrappers run with canvas services mocked. This firmware declines with
# RET_FAIL when the canvas has no vgcanvas, and changes no fill/stroke color or alpha byte.
m=Machine(); m.probe_canvas(rect=(1,1,238,46))
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,))==2
assert m.call(address=syms['canvas_stroke_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,1))==2
assert m.lcd_colors()==LCD_COLORS
assert m.global_alpha==0 and not m.vg_calls; passed()

# With a vgcanvas present the same wrappers take the AGGE-facing path the Q2 firmware links:
# the color pointer and the radius/border-width stack slots reach the backend, canvas colors and
# the canvas alpha byte stay put. (The fill call then enters stock FP64 dither math that Unicorn's
# MIPS32 FR=0 FPU cannot execute; its vgcanvas calls up to that point are still asserted.)
m=Machine(); m.probe_canvas(vg=0x1000400,color=0xffffffff)
assert m.call(address=syms['canvas_stroke_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,2))==0
names=[c[0] for c in m.vg_calls]
assert 'vgcanvas_set_stroke_color' in names and 'vgcanvas_set_line_width' in names
assert [c[2]&0xffffffff for c in m.vg_calls if c[0]=='vgcanvas_set_stroke_color']==[0xffffffff]
assert [struct.unpack('<f',struct.pack('<I',c[2]&0xffffffff))[0] for c in m.vg_calls if c[0]=='vgcanvas_set_line_width']==[2.0]
assert m.lcd_colors()==LCD_COLORS
assert m.global_alpha==0 and m.u.mem_read(m.canvas+0x0e,1)[0]==0; passed()
m=Machine(); m.probe_canvas(vg=0x1000400)
try: m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,))
except UcError: pass
assert [c[2] for c in m.vg_calls if c[0]=='vgcanvas_set_fill_color']==[FILL]
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x9abcdef0 and m.global_alpha==0
assert not m.allocs; passed()

# CPU-LCD branch: the stock fill has a real rasterizer that Unicorn can run end to end. With a
# non-color LCD type it fills the rounded rect through LCD sinks, frees every allocation, and
# declines radius <= 2 exactly as the stock code does.
m=Machine(); m.probe_canvas(lcd_type=0)
m.mock('lcd_fill_rect','lcd_draw_hline','lcd_draw_vline',prefix='sink:')
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,))==0
ops=[c for c in m.calls if c[0].startswith('sink:')]
assert ops and all(10<=signed(c[2])<=109 and 10<=signed(c[3])<=49 for c in ops)
assert not m.allocs
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(2,))==2
assert not [c for c in m.calls if c[0].startswith('sink:')]; passed()
# The actual stock filter runs first, including each screen-off lock mode.
for backlight in [0,1]:
    for mode in range(4):
        for key in [170,O['KEY_PLAY'],O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER'],222,223,42]:
            results=[]
            for patched in [False,True]:
                m=Machine(patched); m.page('playing_page')
                m.byte(syms['g_backlight_status'],backlight)
                m.byte(syms['g_keylock_flag'],1); m.byte(syms['g_keylock_mode'],mode)
                results.append(m.call(key))
            assert results[0]==results[1],(backlight,mode,key,results)
            passed()
# Non-ring keys on supported pages must pass through unchanged.
for key in [0,13,170,O['KEY_PLAY'],222,223,0xffffffff]:
    m=Machine(); m.page(); assert m.call(key)==0 and not m.moved(); passed()
# A tap on a recreated table must not recall/rebind its target before native delivery.
for pointer_down in (False,True):
    for virtual in (False,True):
        m=Machine()
        if virtual: w,rs,es=m.table_page(n=20)
        else: w,es=m.page_list(20)
        m.paint(w)
        for _ in range(12): m.call()
        if virtual: w,rs,es=m.table_page(rebind=True)
        else: w,es=m.page_list(20)
        off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
        if pointer_down:
            m.touch()
            assert m.get(w+off)==0 and not m.moved(), 'pointer-down recalled selection'
            m.pressed=1; m.paint(w)
            assert m.get(w+off)==0 and not m.moved(), 'paint recalled under pointer'
            m.pressed=0
        def check_tapped_row(a,b):
            if virtual: assert m.get(rs[0]+O['ROW_INDEX'])==0, 'tap rebound'
        m.on_click=check_tapped_row
        m.click(es[0])
        assert m.selected(w)==0 and m.get(w+off)==0 and m.clicks==[es[0]]
        assert not m.moved(); passed()

# Saturate before adding: large valid tables cannot wrap their viewport or logical selection.
for pooled in (False,True):
    m=Machine(); w,rs,es=m.table_page(n=1 if pooled else 0,name='playerqueue_page')
    m.word(w+O['ROW_HEIGHT'],1); m.word(w+O['TABLE_ROWS'],0x7fffffff)
    m.word(w+O['W_H'],1); m.word(w+O['TABLE_TOP'],0x7ffffffe)
    if pooled:
        m.word(rs[0]+O['ROW_INDEX'],0x7ffffffe)
        m.word(rs[0]+O['W_Y'],0x7ffffffe); m.word(es[0]+O['W_H'],1)
    m.paint(w)
    for _ in range(9):
        assert m.call(gap=50)==11 and m.get(w+O['TABLE_TOP'])==0x7ffffffe
        if pooled: assert m.selected(w)==0x7ffffffe
    passed()

# A pages widget inside a navigation surface exposes only its active child's targets.
for active in (-1,0,1,2):
    m=Machine(); w=m.page()
    tabs=m.node('pages',active=active); m.word(tabs+O['W_PARENT'],w)
    es=[m.entry(tabs) for _ in range(2)]
    m.nodes[tabs]['children']=es; m.nodes[w]['children']=[tabs]
    m.paint(w); m.confirm()
    assert m.clicks==([es[active]] if active in (0,1) else []),active
    passed()

# An overdue confirmation can change power state before the next release is processed.
for flag in ('g_backlight_status','g_power_longkey','g_ingore_bootkey_flag'):
    m=Machine(); w,es=m.page_list(3)
    m.on_click=lambda a,b: m.byte(syms[flag],0 if flag=='g_backlight_status' else 1)
    m.release(); m.now+=O['DOUBLE_CLICK_MS']+1
    assert m.call(O['KEY_CENTER'],gap=0)==0,flag
    assert m.clicks==[es[0]] and not m.timers
    passed()

# Reject a missing key event before calling the stock filter, cancelling pending input.
m=Machine(); w,es=m.page_list(3); m.release()
assert m.call(args=(0,0,0,0),gap=0)==0
m.advance(300); assert not m.clicks and not m.timers; passed()

# Any native click supersedes delayed confirmation, even without a pointer-down callback.
for target_kind in ('same', 'nested', 'outside'):
    m=Machine(); w,es=m.page_list(3); m.release()
    target=es[0] if target_kind=='same' else m.entry(es[0]) if target_kind=='nested' else m.node('button')
    m.click(target,gap=100)
    m.advance(300)
    assert m.clicks==[target] and not m.timers,target_kind
    passed()
