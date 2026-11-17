"""Stock list callbacks and the thumb, scrollbars, window-manager registration, search and artist detail."""
from q2emu import *

# Run the stock list callback and thumb setter: immediate wheel moves must sync the value.
for populated in (False,True):
    m=Machine(); w,es=m.page_list(20 if populated else 0,height=96,extent=960)
    bar=m.node('scroll_bar_m'); m.word(bar+0x78,1000)  # native bar range
    parent=m.node('list_view',children=[w,bar])
    m.word(parent+0x84,w); m.word(parent+0x88,bar)
    m.word(w+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.word(w+O['VIEW_ON_SCROLL'],0x5fef00)  # installed by stock list_view
    m.word(w+0x74,syms['g_scroll_view_vtable'])
    m.handlers.pop(syms['scroll_view_set_offset'])
    m.mock('scroll_bar_cast','scroll_bar_is_mobile','widget_set_opacity','widget_set_visible_only',
           'widget_dispatch_simple_event')
    m.paint(w)
    for key in [O['KEY_NEXT']]*20+[O['KEY_PREV']]*20:
        assert m.call(key)==11
        offset=m.get(w+O['SCROLL_Y'])
        assert m.get(bar+O['BAR_VALUE'])==int(offset/864*1000)
        events=[c[2] for c in m.calls if c[0]=='widget_dispatch_simple_event' and c[1]==w]
        if m.moved(): assert events[-2:]==[O['EVT_SCROLL'],O['EVT_SCROLL_END']]
    assert m.get(w+O['SCROLL_Y'])==m.get(bar+O['BAR_VALUE'])==0
    passed()

# Classic: a tap on a row the list cuts brings it whole, as the wheel's would, so it isn't left cut when a page over the live list closes. A whole row stays; Minimal's list stays.
for on,top,row,after in ((1,60,1,36),(1,60,2,60),(0,60,1,60)):
    m=Machine()
    if on: classic_light_boot(m)
    w,es=m.page_list(20,height=294,extent=960); m.word(w+O['SCROLL_Y'],top); m.paint(w); m.click(es[row])
    assert m.get(w+O['SCROLL_Y'])==after and m.selected(w)==row,(on,row)
passed()
# Classic's Library on Home, on to Songs and back, as the device went: a 15-row list at 32 px, the wheel to Most Played (offset 108, Songs cut 12 px by the bar), then Songs by a tap on what shows of it, or by a swipe and Centre. A page closing leaves the list as it was, so the row has to be whole before the page opens. The wheel's way is whole already; a swipe alone stays where it was put.

for route in ('tap','wheel','swipe'):
    m,w,es=home_library()
    if route=='tap': m.touch(); m.click(es[3])
    elif route=='wheel':
        for _ in range(7): m.call(O['KEY_PREV'],gap=200)
        m.confirm()
    else:
        for _ in range(7): m.call(O['KEY_PREV'],gap=200)
        m.touch(); m.word(w+O['SCROLL_Y'],108); m.paint(w); assert m.selected(w)==3 and m.get(w+O['SCROLL_Y'])==108  # 20 px of it show
        m.touch(); m.confirm()
    assert m.selected(w)==3 and m.get(w+O['SCROLL_Y'])==84,(route,m.get(w+O['SCROLL_Y']))
    to_songs_and_back(m,w)
    assert m.selected(w)==3 and m.get(w+O['SCROLL_Y'])==84,route
passed()
# Native scrollbar lifecycle, including a fully transparent/invisible mobile bar.
for kind in ('scroll_view','table_client'):
    m=Machine()
    w,es=m.page_list(20,extent=960) if kind=='scroll_view' else (lambda t:(t[0],t[2]))(m.table_page(20))
    bar=m.node('scroll_bar_m',visible=0); m.byte(bar+0x8d,1); m.word(bar+O['BAR_VALUE'],17)
    parent=m.node('list_view' if kind=='scroll_view' else 'table_view',children=[w,bar])
    m.word(w+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.handlers.pop(syms['scroll_bar_scroll_to'])
    m.mock('scroll_bar_cast','scroll_bar_is_mobile','widget_set_opacity','widget_set_sensitive',
           'widget_set_visible_only','widget_animator_prop_create','widget_animator_prop_set_params')
    m.paint(w); m.call(gap=0)
    assert m.nodes[bar]['visible'] and m.u.mem_read(bar+0x34,1)==b'\xff'
    animator=m.get(bar+0x98)
    assert animator and m.get(animator+O['ANIM_DURATION'])==500 and m.get(animator+0x24)==300
    assert m.get(bar+O['BAR_VALUE'])==17  # waking must not calculate or move the thumb
    m.touch(); m.call(gap=0)
    assert m.get(bar+0x98)!=animator
    assert any(c[0]=='widget_animator_destroy' and c[1]==animator for c in m.calls)
    # The stock opacity completion disables and hides the bar; no payload fade timer exists.
    animator=m.get(bar+0x98); callback,ctx=m.slide_callbacks[animator]
    m.byte(bar+0x34,0)
    assert m.call(address=callback,args=(ctx,0,0,0),gap=0)==7
    assert not m.nodes[bar]['visible'] and not m.nodes[bar]['sensitive'] and not m.get(bar+0x98)
    m.call(gap=0); assert m.nodes[bar]['visible']
    m.top=0; m.nodes.clear(); m.advance(1000)  # payload retains no native bar/animator pointer
    passed()
for count,bar_present in ((2,True),(20,False)):
    m=Machine(); w,es=m.page_list(count,height=96,extent=count*48)
    parent=m.node('list_view',children=[w]+([m.node('scroll_bar_m')] if bar_present else []))
    m.word(w+O['W_PARENT'],parent); m.paint(w); m.call()
    assert not any(c[0]=='scroll_bar_scroll_to' for c in m.calls)
    passed()

# Bump repaints at precisely 120 ms, independently of the pause-to-wrap arm.
m=Machine(); w,es=m.page_list(2,height=96,extent=96,name='playlist_page')
m.paint(w); m.call(); m.call(gap=0)
assert m.timers and min(t[0] for t in m.timers.values())==m.now+O['BUMP_MS']
m.advance(O['BUMP_MS']-1); assert not m.calls
m.advance(1); assert any(c[0]=='widget_invalidate_force' and c[1]==w for c in m.calls)
m.paint(w,gap=0); assert m.sel()==(0,48,240,48)
m.call(gap=O['EDGE_PAUSE_MS']); assert m.selected(w)==0
passed()
for cancel in ('touch','activate','recycle','destroy','scope'):
    m=Machine(); w,es=m.page_list(2,height=96,extent=96,name='folder_page')
    m.folder('/sd/a'); m.paint(w); m.call(); m.call(gap=0)
    if cancel=='touch': m.touch()
    elif cancel=='activate': m.click(es[-1])
    elif cancel=='recycle': m.nodes[w].pop('_ringnav_fx')
    elif cancel=='destroy': m.top=0; m.nodes.clear()
    else: m.folder('/sd/b'); m.paint(w,gap=0)
    m.advance(O['BUMP_MS'])
    assert not any(c[0]=='widget_invalidate_force' and c[1]==w for c in m.calls)
    passed()

for existing in (False,True):
    for empty in (False,True):
        m=NavigationMachine(); w,es=m.page_list(20,name='folder_page'); browse=m.top
        home=m.node('window','home_page',stage=3)
        playing=m.node('window','playing_page',stage=3) if existing else 0
        m.order=[home]+([playing] if playing else [])+[browse]; m.sync_order()
        m.folder('/sd/music'); m.paint(w)
        for _ in range(6):m.call()
        selected,offset=m.selected(w),m.get(w+O['SCROLL_Y'])
        for visit in range(3):
            long_return(m)
            assert m.nodes[m.top]['name']=='playing_page'
            assert not any(c[0]=='player_start' for c in m.calls)
            # The switch leaves the hold-release latch armed: stock's filter swallows the hold's
            # release where it is delivered (let go before the page's open event), and the first short Return goes back.
            assert m.u.mem_read(O['RETURN_RELEASE_LATCH'],1)==b'\1'
            assert m.call(170,gap=0)==11 and m.nodes[m.top]['name']=='playing_page'
            assert m.u.mem_read(O['RETURN_RELEASE_LATCH'],1)==b'\0'
            m.call(170,address=0x53da5c,args=(m.top,m.event,0,0),gap=0)
            assert m.top==browse, (existing,visit,[m.nodes[p]['name'] for p in m.order])
            m.paint(w,gap=0); assert (m.selected(w),m.get(w+O['SCROLL_Y']))==(selected,offset)
        passed()
m=NavigationMachine(); m.page('folder_page'); browse=m.top; m.order=[browse]; m.sync_order()
m.block_open=True; long_return(m); assert m.top==browse
assert m.call(170,gap=0)==11
m.block_open=False; m.animating=1; long_return(m); assert m.top==browse
passed()

# Stock constructors/dispatch establish these event numbers and fields, independently of mocks.
m=Machine()
for n in ('pointer_event_init','event_init'):m.handlers.pop(syms[n],None)
p=m.alloc(48);m.call(address=syms['pointer_event_init'],args=(p,O['EVT_POINTER_DOWN'],m.wm,37),stack=(91,))
assert m.get(p+O['EVENT_TARGET'])==m.wm and m.get(p+O['EVENT_X'])==37 and m.get(p+O['EVENT_Y'])==91
assert m.get(p+4)==48;passed()

# The stock wm registration fixes the type that reaches the hooked down callback. The
# move-before thunk forwards 0x102; 0xff belongs to the separate stock on_wm_tsdown_fun.
def wm_event_type(address):
    return struct.unpack('<I',m.u.mem_read(address,4))[0]&0xffff
assert wm_event_type(0x534e08)==O['EVT_POINTER_DOWN']==0x100
assert wm_event_type(0x534dec)==O['EVT_POINTER_MOVE_BEFORE']==0x102
assert wm_event_type(0x534e40)==0xff
assert wm_event_type(0x534d84)==O['EVT_KEY_DOWN_BEFORE']==0x112
assert wm_event_type(0x534dd0)==O['EVT_KEY_UP']==0x114
assert wm_event_type(0x534d98)==O['EVT_KEY_UP_BEFORE']==0x115
passed()

for page_name in ('folder_page','localmusic_page','allmusic_page','radio_page'):
    for empty in (False,True):
        for distance in (0,7,8,47,48,49):
            m=PullMachine(); m.handlers[payload_syms['radio_search']]='stock_radio_search'  # a payload call: no t9
            if page_name=='folder_page':
                w,rows,es=m.table_page(name=page_name)
                if empty:m.word(w+O['TABLE_ROWS'],0);m.nodes[w]['children']=[]
            else:w,es=m.page_list(0 if empty else 5,name=page_name)
            page=m.top;m.start_pull(page,w)
            m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=60+distance)
            # Local Songs' stock search is iPod's; Internet Radio's own search is in both.
            enabled=page_name in ('radio_page','localmusic_page')
            assert bool(m.prompt(page)['visible'])==(enabled and distance>=8)
            if enabled and distance>=8:
                assert m.prompt(page)['text']==('松开搜索' if distance>=48 else '下拉搜索')
                assert m.click(es[0])==11 if es else True
            # click() normally uses a one-second gap; gesture state still belongs to the page.
            m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=60+distance)
            radio=lambda: [c for c in m.calls if c[0]=='stock_radio_search']
            assert len(m.searches()+radio())==(1 if enabled and distance>=48 else 0) and not (radio() and page_name!='radio_page')
            m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
            assert len(m.searches()+radio())==(1 if enabled and distance>=48 else 0)
            assert not m.prompt(page)['visible']
            passed()

# Classic's Library in Split is a list on Home's window (list_view_homelib), and takes the same pull. Home's own list does not,
# nor does any Home under a set without that list.
for boot,view,want in ((classic_light_boot,'scroll_view_localmusic',1),(classic_light_boot,'scroll_view_home',0),(lambda k:None,'scroll_view_home',0)):
    m=PullMachine(); boot(m); w,es=m.page_list(5,name='home_page'); m.nodes[w]['name']=view; page=m.top
    m.start_pull(page,w); m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
    assert bool(m.prompt(page)['visible'])==bool(want),(view,want)
    m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
    assert len(m.searches())==want and not m.prompt(page)['visible']
passed()
# A failed registration leaves no partial page callbacks; retry binds each event once.
for fail in ('EVT_WINDOW_BACKGROUND','EVT_POINTER_UP_BEFORE'):
    m=PullMachine();m.handlers[payload_syms['radio_search']]='stock_radio_search'
    w,es=m.page_list(5,name='radio_page');page=m.top
    before=list(m.nodes[page].get('handlers',[]));m.pull_bind_fail=O[fail]
    m.start_pull(page,w)
    assert m.nodes[page].get('handlers',[])==before and not m.nodes[page].get('_pull_bound',0)
    m.pull_bind_fail=None;m.start_pull(page,w)
    handlers=m.nodes[page]['handlers'];assert len(handlers)==len(before)+6 and len(handlers)==len(set(handlers))
    m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
    assert len([c for c in m.calls if c[0]=='stock_radio_search'])==1
    passed()

# Local Songs' virtual table keeps its rows and top offset across a search.
m=PullMachine();w,rows,es=m.table_page(name='localmusic_page');page=m.top
m.start_pull(page,w);count=m.get(w+O['TABLE_ROWS'])
m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
assert len(m.searches())==1 and m.get(w+O['TABLE_ROWS'])==count and m.get(w+O['TABLE_TOP'])==0
# Stock search opens a dialog on this page; resuming it needs no index translation.
m.start_pull(page,w);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=60)
assert m.click(es[0])==0
passed()
for cancel in ('below','horizontal','upward','midlist','outside','edge0','edge30','edge31',
               'wheel','button','screen','navigate','background','close','destroy','surface','abort','move_hook'):
    m=PullMachine();w,es=m.page_list(8,height=240,name='localmusic_page');page=m.top
    y=30 if cancel=='edge30' else 0 if cancel=='edge0' else 31 if cancel=='edge31' else 60
    if cancel=='midlist':m.word(w+O['SCROLL_Y'],20)
    m.start_pull(page,w,x=250 if cancel=='outside' else 100,y=y)
    if cancel=='horizontal':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],x=170,y=y+20)
    elif cancel=='upward':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y-10)
    m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y+60)
    if cancel=='below':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y+47)
    elif cancel in ('wheel','button'):m.call(O['KEY_NEXT'] if cancel=='wheel' else O['KEY_PLAY'],gap=0)
    elif cancel=='screen':m.byte(syms['g_backlight_status'],0);m.paint(w,gap=0)
    elif cancel=='navigate':m.page('home_page');m.paint(m.top,gap=0)
    elif cancel in ('background','close','destroy','surface','abort'):
        typ=O[{'background':'EVT_WINDOW_BACKGROUND','close':'EVT_WINDOW_CLOSE','destroy':'EVT_DESTROY',
               'surface':'EVT_DESTROY','abort':'EVT_POINTER_ABORT'}[cancel]]
        m.emit(w if cancel=='surface' else page,typ)
    elif cancel=='move_hook':
        # The native wm move-before (0x102) tail-calls the down-before hook (0x100).
        # The move type must not restart the pull.
        m.word(m.event+O['EVENT_Y'],y+60)
        m.call(key=100,address=syms['on_wm_tsmove_before_fun'],event_type=O['EVT_POINTER_MOVE_BEFORE'],gap=0,clear=False)
    m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=y+(47 if cancel=='below' else 60))
    assert len(m.searches())==(1 if cancel in ('edge31','move_hook') else 0),cancel
    assert not m.prompt(page)['visible'],cancel
    passed()

for distance in (7,48):
    m=PullDispatchMachine();w,es=m.page_list(5,height=240,name='localmusic_page');page=m.top
    m.start_pull(page,w)
    m.word(page+0x74,1);m.word(page+O['W_EMITTER'],page)
    m.word(m.event+O['EVENT_Y'],60+distance)
    for fn,typ in (('widget_on_pointer_move',0x101),('widget_on_pointer_up',0x103)):
        m.calls=[]
        ret=m.call(key=100,address=syms[fn],args=(page,m.event,0,0),event_type=typ,gap=0,clear=False)
        child_calls=[c for c in m.calls if c[0]=='input:'+fn+'_children']
        assert (ret==11 and not child_calls) if distance>=8 else bool(child_calls)
    assert len(m.searches())==(distance>=48)
    passed()
# Native abort delivery follows the pressed target chain; it is not an EVT_CLICK.
m=PullMachine();w,es=m.page_list(3,height=240,name='localmusic_page');page=m.top
m.start_pull(page,w);m.handlers.pop(syms['widget_dispatch_event_to_target_recursive'])
m.word(page+0x4c,w);m.word(w+0x4c,es[0])
m.calls=[];m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
assert [c[1] for c in m.dispatched()]==[w,es[0]] and not m.clicks
m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=100);assert not m.searches()
assert m.click(es[0])==11
passed()

# Use the stock search callback, navigator and Back path. Only resource initialization
# and window storage are mocked, as in the Now Playing navigation cases above.

m=SearchNavigationMachine();w,es=m.page_list(6,height=240,name='localmusic_page');page=m.top
m.order=[page];m.sync_order()
m.start_pull(page,w);m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
assert m.nodes[m.top]['name']=='dialog/search_dialog' and m.order[0]==page
# The stock dialog owns its Back handling and its callbacks are outside this harness;
# model the window manager popping it. The Local Songs page must still be the same
# instance with its offset and rows untouched.
m.order.remove(m.top);m.sync_order()
assert m.top==page
assert m.get(w+O['SCROLL_Y'])==0 and len(m.nodes[w]['children'])==6
passed()

# Artist detail opens on Albums: the real init reaches the stock Albums tab handler, which
# selects tab 1 and queries albums before building; stock builds Songs instead.
for patched in (True, False):
    m=Machine(patched)
    for address,name in ((0x4af8ac,'songs_view'),(0x4ae3cc,'albums_view'),(0x4b0374,'timer_idex'),(0x4f77e4,'query_run')):
        m.handlers[address]=name
    m.mock('load_artistinfo_list','load_localartist_list','widget_get_window','widget_foreach','strcpy@GLIBC_2.0')
    arg=m.alloc(8); m.word(arg+4,m.alloc(0x40))
    assert m.call(address=syms['localmusic_artistinfo_page_init'],args=(m.node('window','artistinfo_page'),arg,0,0))==0
    names=[c[0] for c in m.calls if c[0] in ('songs_view','albums_view','load_localartist_list','query_run')]
    tab=m.get(0xa3b5ac)  # the artist page's tab, which the Albums tab's handler sets (0x4aeb60)
    if patched:
        assert names==['query_run','load_localartist_list','query_run','albums_view'] and tab==1, names
        assert [c[1] for c in m.calls if c[0]=='load_localartist_list']==[1]
    else: assert names==['query_run','songs_view'] and tab==0, names
    passed()
