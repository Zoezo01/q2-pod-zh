"""Rows: the hit test, Classic's row layouts, pooled rows and their selection; long keys and Hold Return."""
from q2emu import *

# With three rows in view the difference shows: the middle one wins, not the top edge.
m=Machine(); w,es=m.page_list(8,height=192,extent=1000)
m.paint(w); m.touch(); m.word(w+O['SCROLL_Y'],200); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==0
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==6; passed()
# Native click may destroy the current page. Nothing dereferences its target afterwards.
def destroy(a,b):
    m.nodes.clear(); m.top=0
m.on_click=destroy
assert m.confirm()==11 and len(m.dispatched())==1; passed()

def check_album_tile(m, button):
    def descendants(w):
        return [c for child in m.nodes[w]['children'] for c in [child,*descendants(child)]]
    def geometry(w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
    children=descendants(button)
    original={c:geometry(c) for c in children}
    draw_types=[c for c in children if m.nodes[c]['type']=='image' and original[c][2:]==(110,110)]
    tile=geometry(button)
    for c in children:
        if m.nodes[c]['type']=='hscroll_label': m.nodes[c]['text']='A very long album or artist label '*10
    for _ in range(3):
        # Both themes inset the cover inside the selection card; repeated paints never compound it.
        m.call(address=payload_syms['album_children'],args=(button,0,0,0),gap=0)
        assert geometry(button)==tile
        for c in draw_types: assert m.nodes[c].get('draw_type',0)==O['IMAGE_DRAW_SCALE_DOWN']
        for c,(x,y,w,h) in original.items():
            actual=geometry(c)
            if w==h==110:
                inset=6 if m.get(c+O['W_PARENT'])==button else 0
                assert actual==(inset,inset,98,98), actual
            elif m.nodes[c]['type']=='hscroll_label':
                assert actual==(6,y-2,98,h), actual
                assert actual[1]+h<=154
                assert m.nodes[c]['text'].startswith('A very long')
            else: assert actual==(x,y,w,h)
    m.calls=[]; m.call(address=payload_syms['album_children'],args=(button,0,0,0),gap=0)
    assert not any(c[0] in ('widget_move_resize','image_set_draw_type') for c in m.calls)
    # The public paint hook keeps the same geometry, in Classic too.
    m.config['THEME']='1'
    m.paint(button)
    assert all(geometry(c)[2]==98 for c in children if original[c][2]==110)
    passed()

m=Machine(); root=asset_node(m,decode((B/'ui/localmusic/album_page.bin').read_bytes()),m.wm); m.top=root
for button in [w for w,n in m.nodes.items() if n['type']=='button' and n['children']]:
    check_album_tile(m,button)

for address in (0x533bc4, 0x4ac588, 0x4b39f4, 0x4a6910):
    for grid in ((0, 1) if address == 0x4a6910 else (0,)):
        m = Machine(); w = m.page('artistinfo_page' if grid else 'folder_page', 'table_client')
        m.mock('table_row_create', 'button_create', 'image_create', 'view_create',
               'hscroll_label_create', 'gif_image_create', 'widget_use_style',
               'widget_set_name', 'widget_set_children_layout', 'image_set_draw_type',
               'image_base_set_image', 'set_hscroll_label_attribute')
        m.word(syms['album_modetype'], grid)
        assert m.call(address=address, args=(w, w, 4, 0)) == 0
        rows = m.nodes[w]['children']
        assert len(rows) == 4
        for row in rows:
            assert m.get(row+O['W_H']) == (210 if grid else 72)
            for button in m.nodes[row]['children']:
                assert m.get(button+O['W_H']) == (160 if grid else 68)
        if grid:
            m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
            for row in rows:
                for button in m.nodes[row]['children']: check_album_tile(m,button)
        if not grid:
            for name, stock, compact_geometry in ARTWORK[address]:
                arts = [n for n, v in m.nodes.items() if v.get('name') == name]
                assert arts, name
                for art in arts:
                    geometry = tuple(signed(m.get(art+O[off])) for off in ('W_X', 'W_Y', 'W_W', 'W_H'))
                    assert geometry == compact_geometry, (name, geometry)
        # Preparing an existing pool does not recreate or resize its rows.
        before = len(m.nodes)
        assert m.call(address=address, args=(w, w, 4, 0)) == 0 and len(m.nodes) == before
        if not grid:
            for row in rows[:2]:  # independently recreated rows as well as repeated reuse
                check_title_bounds(m,m.nodes[row]['children'][0])
            into=[n for n,v in m.nodes.items() if v.get('name')=='img_into']
            if into:  # the payload's chevron column is stock's, after layout
                button=m.nodes[rows[0]]['children'][0]
                assert m.get(into[0]+O['W_X'])==m.get(button+O['W_W'])-O['CHEVRON_W'] and m.get(into[0]+O['W_W'])==50
            if address==0x533bc4:
                # Execute the real folder text/style rebind after layout: its stock 140/190px
                # reset must no longer undo the computed width while a row pool is recycled.
                button=m.nodes[rows[0]]['children'][0]
                title=next(c for c in m.nodes[button]['children'] if m.nodes[c]['name']=='scrlabel_name')
                width=m.get(title+O['W_W'])
                m.row_record=m.alloc(0x80); m.word(m.row_record+0x34,8)
                m.mock('deque_at','tk_snprintf','widget_set_text_utf8','file_is_playing')
                for navbar,label in ((0,'再利用された長いフォルダー名 '*5), (1,'99% Invisible'),
                                     (0,'100% true %s %n.mp3'), (1,'plain.mp3'), (0,'x'*600)):
                    m.byte(syms['g_navbar_status'],navbar)
                    m.word(m.row_record+8,m.string(label))
                    assert m.call(address=0x532c30,args=(button,7,0,0))==0
                    assert m.nodes[title]['text']==label.encode()[:511].decode()
                    assert m.get(title+O['W_W'])==width
        else:
            assert all(m.get(n+O['W_CHILDREN_LAYOUT'])==0 for row in rows for n in m.nodes[row]['children'])
passed()

# Classic's Artists/Composers/Genres rows are one 32 px line: the leading artwork and playing gif
# and the second line hide, and the title sits centred in the row.
for address in (0x4b39f4,):
    m=Machine(); classic_light_boot(m); w=m.page('folder_page','table_client')
    m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
           'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
           'set_hscroll_label_attribute','widget_set_visible')
    assert m.call(address=address,args=(w,w,4,0))==0
    rows=m.nodes[w]['children']; button=m.nodes[rows[0]]['children'][0]
    assert {m.get(r+O['W_H']) for r in rows}=={32} and m.get(button+O['W_H'])==32
    native_row_layout(m,button)
    kids=m.nodes[button]['children']
    text=next(c for c in kids if m.nodes[c]['type']=='hscroll_label' or any(m.nodes[t]['type']=='hscroll_label' for t in m.nodes[c]['children']))
    title=next((c for c in m.nodes[text]['children'] if m.nodes[c]['type']=='hscroll_label'),text)
    assert [m.nodes[c]['name'] for c in kids[:kids.index(text)] if m.nodes[c]['visible'] and not m.nodes[c]['name'].startswith('img_choice')]==[],address
    assert [c for c in m.nodes[text]['children'] if c!=title and m.nodes[c]['visible']]==[],address
    y,h=(signed(m.get(title+O[k])) for k in ('W_Y','W_H')); assert 2*y+h==32,(address,y,h)
    # Outside multi-select (img_choice hidden) the title container starts at text_x on screen, clear of
    # the glass: the button sits at 20. In multi-select stock's box keeps its place and the text its own.
    kx=lambda c: signed(m.get(c+O['W_X']))
    multi=kx(text); assert multi>8 and kx(button)==20,address
    for c in kids:
        if m.nodes[c]['name'].startswith('img_choice'): m.nodes[c]['visible']=0
    native_row_layout(m,button); assert kx(button)+kx(text)==20,address
    m.word(button+O['W_X'],30); native_row_layout(m,button); assert kx(text)==0,address  # a row further in than text_x: its text at its start
    m.word(button+O['W_X'],20); native_row_layout(m,button)
    edge=kx(text)+m.get(text+O['W_W'])  # the right edge stays where the layout put it
    for c in kids:
        if m.nodes[c]['name'].startswith('img_choice'): m.nodes[c]['visible']=1
    native_row_layout(m,button); assert kx(text)==multi and kx(text)+m.get(text+O['W_W'])==edge,address
    for c in kids:
        if m.nodes[c]['name'].startswith('img_choice'): m.nodes[c]['visible']=0
    # A leading child stock left unnamed (a Playlists row's) hides like the others, and does not fault.
    unnamed=m.node('image'); m.nodes[button]['children'].insert(0,unnamed); native_row_layout(m,button)
    assert not m.nodes[unnamed]['visible'],address
passed()

# Classic's Songs rows are two lines at 52 px under Row Art On and Off alike: the artwork and playing gif hide, the title and
# stock's artist line stay at the set's y, in one box on screen from 20 (text at 22) to 352 (text to 350), the second line
# at its full width as well. In multi-select stock's box keeps its place and the text its own, inside the row.
for rowart in ('1','0'):
    m=Machine(); classic_light_boot(m,rowart); w=m.page('allmusic_page','table_client')
    m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
           'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
           'set_hscroll_label_attribute','widget_set_visible')
    assert m.call(address=0x4ac588,args=(w,w,4,0))==0
    rows=m.nodes[w]['children']; button=m.nodes[rows[0]]['children'][0]
    assert {m.get(r+O['W_H']) for r in rows}=={52} and m.get(button+O['W_H'])==52
    byname=lambda name: next(n for n in m.nodes[button]['children']+[g for c in m.nodes[button]['children'] for g in m.nodes[c]['children']]+[g for c in m.nodes[button]['children'] for t in m.nodes[c]['children'] for g in m.nodes[t]['children']] if m.nodes[n]['name']==name)
    info,title,view,meta,badge=(byname(n) for n in ('view_info','scrlabel_name','view_artist','scrlabel_artist','img_pic'))
    m.nodes[badge]['visible']=0  # the bind's ringnav_badge "null"
    box=lambda c: tuple(signed(m.get(c+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
    choice=next(c for c in m.nodes[button]['children'] if m.nodes[c]['name'].startswith('img_choice'))
    native_row_layout(m,button); multi=box(info)
    assert multi[0]>8 and multi[0]+multi[2]<=375,multi
    m.nodes[choice]['visible']=0; native_row_layout(m,button)
    kids=m.nodes[button]['children']
    assert [m.nodes[c]['name'] for c in kids[:kids.index(info)] if m.nodes[c]['visible']]==[],rowart
    rx=m.get(button+O['W_X'])
    assert rx+box(info)[0]==20 and box(info)[2]==332,(rx,box(info))
    assert m.nodes[title]['visible'] and m.nodes[view]['visible'] and m.nodes[meta]['visible']
    assert box(title)[2]==332 and box(view)[2]==332 and box(meta)[2]==332,(box(title),box(view),box(meta))
    assert box(title)[1]==1 and box(view)[1]==29,(box(title),box(view))
    native_row_layout(m,button); assert rx+box(info)[0]==20 and box(info)[2]==332 and box(meta)[2]==332  # a relayout keeps them
    assert (m.nodes[meta]['style:normal:font_name'],m.nodes[meta]['style:normal:font_size'])==('default',17)
    assert (m.nodes[title]['style:normal:font_name'],m.nodes[title]['style:normal:font_size'])==('classic-light',28)
passed()
# Classic's Songs rows stay stock's 72 px rows with their art.
m=Machine(); m.config.update(THEME='1'); w=m.page('allmusic_page','table_client')
m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
       'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
       'set_hscroll_label_attribute','widget_set_visible')
assert m.call(address=0x4ac588,args=(w,w,1,0))==0
button=m.nodes[m.nodes[w]['children'][0]]['children'][0]; assert m.get(button+O['W_H'])==68
native_row_layout(m,button); assert [m.nodes[c]['visible'] for c in m.nodes[button]['children']][1]; passed()

# Classic's Albums row: the second line, nested in stock's view_artist, takes the meta role: stock's regular face at
# meta_px, and stock's secondary grey in its one state a row uses, which the style hook turns to the theme's
# muted. Its colour is written once. Classic's second line stays the style's, its row's calls unchanged.

m,button=album_row(classic_light_boot); native_row_layout(m,button)
title,meta=(next(n for n,v in m.nodes.items() if v['name']==name) for name in ('scrlabel1_name','scrlabel2_name'))
for st_ in ('normal','focused','disable'):
    assert (m.nodes[title][f'style:{st_}:font_name'],m.nodes[title][f'style:{st_}:font_size'])==('classic-light',28),st_
    assert (m.nodes[meta][f'style:{st_}:font_name'],m.nodes[meta][f'style:{st_}:font_size'])==('default',17),st_
assert m.nodes[meta]['style:normal:text_color']==signed(0xffaaaaaa) and 'style:normal:text_color' not in m.nodes[title]
# Classic's columns, on screen: the cover at x 22 (the row sits at 20), the text's box from 80 (hscroll_label's 2 px: text at
# 82) to 312, whichever stock's layout gave them.
info=next(n for n,v in m.nodes.items() if v['name']=='view_info'); art_=next(n for n,v in m.nodes.items() if v['name']=='img_icon')
rx=m.get(button+O['W_X'])
assert rx+m.get(art_+O['W_X'])!=22  # stock's choice box showing (multi-select): stock's places
m.nodes[next(n for n,v in m.nodes.items() if v['name']=='img_choice')]['visible']=0; native_row_layout(m,button)
assert rx+m.get(art_+O['W_X'])==22 and rx+m.get(info+O['W_X'])==80 and m.get(info+O['W_W'])==232,(rx,m.get(art_+O['W_X']),m.get(info+O['W_X']),m.get(info+O['W_W']))
m.nodes[meta]['style:normal:text_color']=0; native_row_layout(m,button); assert m.nodes[meta]['style:normal:text_color']==0
m,button=album_row(lambda k: k.config.update(THEME='2')); native_row_layout(m,button)
assert not [k for n in m.nodes.values() for k in n if str(k).startswith('style:')]; passed()

# The three non-pooled constructors: album tracks, artist tracks and playlists.
# These use distinct title/container names and must receive the same native layout behavior;
# only a drill window (playlist_page) reserves the chevron's space.
for address,page in ((0x4a7f44,'artistinfo_page'),(0x4af8ac,'artistinfo_page'),(0x4b4ee8,'artistinfo_page'),(0x4b4ee8,'playlist_page')):
    for reopen in range(2):
        m=Machine(); w=m.page(page,'scroll_view')
        m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
        m.nodes[w]['name']='scroll_view_track'
        m.row_record=m.alloc(0x80)
        for off in (8,12,16,24): m.word(m.row_record+off,m.string('日本語 Title'))
        m.word(m.row_record+0x48,1)
        m.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
               'gif_image_create','widget_use_style','widget_set_name','widget_set_text_utf8',
               'widget_set_visible','widget_on','widget_destroy_children','image_set_draw_type',
               'image_base_set_image','set_hscroll_label_attribute','hscroll_label_set_only_focus',
               'hscroll_label_set_ellipses','hscroll_label_set_speed','gif_image_play','deque_at',
               'deque_size','tk_snprintf','batch_get_selectitem','file_is_playing','getFormatString',
               'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
               'strcmp@GLIBC_2.0','toolsTrimLeft','toolsTrimRight')
        m.handlers[0x4ad020]='row_toolbar'
        assert m.call(address=address,args=(w,0,0,0))==0
        buttons=[n for n,v in m.nodes.items() if v['type']=='button']
        assert len(buttons)==1
        check_title_bounds(m,buttons[0],drill=page=='playlist_page')
        if address==0x4b4ee8:
            button=buttons[0]
            more=next(c for c in m.nodes[button]['children'] if m.nodes[c]['name'].startswith('img_more'))
            native_row_layout(m,button)
            # Stock opens More for pointer x >= 286: the dots must keep that trailing position.
            assert m.get(button+O['W_X'])+m.get(more+O['W_X'])>=286
passed()

for page in ('home_page', 'folder_page', 'playing_page', 'sysset_page'):
    m = long_machine(); m.page(page)
    # No playback data is initialized: the switch must work with an empty queue as well.
    assert long_return(m) == 0
    dest = destinations(m)
    assert len(dest) == 1
    assert dest[0][0] == ('navigator_switch_to_with_context' if page != 'playing_page' else 'navigator_back_to_home')
    if page != 'playing_page':
        assert m.text(dest[0][1]) == 'playing_page'
        assert [m.get(dest[0][2] + 4*i) for i in range(4)] == [0, 0, 255, 2]
    # Here the navigator is only recorded, so the switch never lands and the latch is left
    # armed; the NavigationMachine checks below cover the landed switch that drops it.
    held = 11
    assert m.call(170, gap=0) == held
    assert m.call(170, gap=0) == 0   # next short Return still reaches stock Back
    for _ in range(3):
        assert long_return(m) == 0
        assert len(destinations(m)) == 1
    assert m.call(170, gap=0) == held
passed()

# The stock long-key gates stay effective, with the shared navigation restrictions added.
for flag, value in GATES:
    m = long_machine(); m.page('folder_page'); m.byte(syms[flag], value)
    long_return(m)
    # Stock itself blocks power-off, guide and test mode; usable()'s shared restrictions block the rest.
    assert not destinations(m), flag
for light in (0, 1):
    for lock in (0, 1):
        for mode in range(4):
            m = long_machine(); m.page('folder_page')
            m.byte(syms['g_backlight_status'], light)
            m.byte(syms['g_keylock_flag'], lock); m.byte(syms['g_keylock_mode'], mode)
            long_return(m)
            blocked = not light
            assert bool(destinations(m)) == (not blocked)
passed()
m = long_machine(); m.page_list()
assert m.call(O['KEY_CENTER']) == 11 and m.timers
long_return(m)
assert not m.timers
m.advance(201)
assert not m.dispatched()
# A hold blocked elsewhere still swallows its release, so no delayed page action runs.
m = long_machine(); m.page('folder_page'); m.byte(syms['g_usblink_status'], 2)
long_return(m)
assert not destinations(m)
assert m.call(170, gap=0) == 11
passed()
# Hold Return on Now Playing goes back to the page it opened over while that lies right under it; otherwise,
# or with no such page, Home.
for under,want in ((True,'navigator_back'),(False,'navigator_back_to_home')):
    m = long_machine(); m.mock('navigator_back'); m.page('folder_page'); folder = m.top
    assert long_return(m) == 0 and destinations(m)[0][0] == 'navigator_switch_to_with_context'
    np = m.node('window', 'playing_page'); other = m.node('window', 'sysset_page')
    m.nodes[m.wm] = {'children': [folder, np] if under else [folder, other, np]}; m.top = np
    m.call(170, gap=0); m.calls = []
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == [want]
    m.call(170, gap=0); m.calls = []
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == ['navigator_back_to_home']
passed()
# Hold Return: Home (Q2POD HOLDRETURN 1) goes Home from any page but Home, Now Playing included;
# Home still opens Now Playing. Going Home leaves the stock latch armed, as the stock Home call did:
# the hold's release is swallowed and the next short Return passes.
for page in ('folder_page', 'playing_page', 'sysset_page', 'home_page'):
    m = long_machine(); m.mock('navigator_back'); m.config['HOLDRETURN'] = '1'; m.page(page)
    want = 'navigator_switch_to_with_context' if page == 'home_page' else 'navigator_back_to_home'
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home', 'navigator_switch_to_with_context')] == [want], page
    if page != 'home_page': assert m.call(170, gap=0) == 11 and m.call(170, gap=0) == 0, page
passed()
# Over quick settings the hold closes it, as Return does, and acts on the page under it once the dialog
# has gone (quick_poll, every pass): Now Playing opened over it left it open underneath, and stock's
# pull-down starts none while one is. The release goes to that latch, not to closing it; a hold whose
# dialog is still up QS_HOLD_MS later is forgotten.

for hold_home, want in (('0', 'navigator_switch_to_with_context'), ('1', 'navigator_back_to_home')):
    m = long_machine(); m.mock('dialog_quit'); m.config['HOLDRETURN'] = hold_home
    m.page('folder_page'); page = m.top; quick = m.top = m.node('dialog', 'statusbar_dialog')
    assert long_return(m) == 0 and not destinations(m) and quits(m) == [(quick, 0)], hold_home
    assert m.call(170, gap=0) == 11 and not quits(m)
    ui_pass(m); assert not destinations(m) and not quits(m)  # still up: its own idle closes it
    m.top = page; m.animating = 1; ui_pass(m, 20); assert not destinations(m)  # its close still moving
    m.animating = 0; ui_pass(m, 20); assert [c[0] for c in destinations(m)] == [want], hold_home
    ui_pass(m, 20); assert not destinations(m)  # once
m = long_machine(); m.mock('dialog_quit'); m.page('folder_page'); page = m.top; m.top = m.node('dialog', 'statusbar_dialog')
assert long_return(m) == 0 and len(quits(m)) == 1
ui_pass(m, O['QS_HOLD_MS']); m.top = page; ui_pass(m, 20); assert not destinations(m)
# a short Return there closes it
m = long_machine(); m.mock('dialog_quit'); m.top = m.node('dialog', 'statusbar_dialog')
assert m.call(170, gap=0) == 11 and quits(m) == [(m.top, 0)]
passed()
# Return or Centre (Cancel) on the scan's dialog: stock's own while the scan runs (it stops it), closes it at once after.
for done, key in ((0, 170), (1, 170), (0, O['KEY_CENTER']), (1, O['KEY_CENTER'])):
    m = Machine(); m.mock('dialog_quit'); m.top = m.node('dialog', 'updatemusic_dialog'); m.word(O['SCAN_DONE'], done)
    m.call(key, gap=0); assert [c[1:3] for c in m.calls if c[0] == 'dialog_quit'] == ([(m.top, 0)] if done else []), (done, key)
passed()
# Now Playing opened over Home, which lies right under it: Recent would go Back, Home goes Home.
m = long_machine(); m.mock('navigator_back'); m.config['HOLDRETURN'] = '1'; m.page('home_page'); home = m.top
long_return(m); np = m.node('window', 'playing_page')
m.nodes[m.wm] = {'children': [home, np]}; m.top = np; m.call(170, gap=0); m.calls = []
assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == ['navigator_back_to_home']
passed()
# Other long-key paths remain byte-for-byte stock; exercise the inert keys and power gate.
# Home is not a local list, so a Play/Pause hold there stays stock too (queue menu below).
for key in (171, 172, 173, 222, 223, 218):
    m = long_machine(); m.page('home_page')
    if key == 218: m.byte(syms['g_poweroff_state'], 2)
    m.call(key, address=syms['on_wm_keylong_fun'], event_type=0x111)
    assert not destinations(m)
passed()

# Four complete compact rows resolve the same target for touch and centre at every row.
for index in range(4):
    m = Machine(); w, es = m.page_list(4, height=288, extent=288, name='folder_page')
    for i, e in enumerate(es):
        m.word(e+O['W_Y'], i*72); m.word(e+O['W_H'], 68)
    m.touch(); m.click(es[index])
    assert m.confirm() == 11 and m.dispatched()[0][1] == es[index]
passed()

# Recycle a small row pool: selection belongs to the logical index, never the widget.
m=Machine(); w,rows,entries=m.table_page(rebind=True)
m.paint(w); assert m.selected(w)==0
assert m.call()==11 and m.selected(w)==1
assert m.call()==11 and m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==60
assert m.moved()[-1][0]=='table_client_set_yoffset'
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# A wheel step ends its scroll as a touch scroll does, so stock lists load the covers in view.
m=Machine(); w,rows,entries=m.table_page(rebind=True); m.mock('widget_dispatch_simple_event')
m.paint(w); m.call(); m.call()
assert ('widget_dispatch_simple_event',w,O['EVT_SCROLL_END']) in [c[:3] for c in m.calls]; passed()
# A touch click in a rebound row immediately changes what centre opens.
m.touch(); m.click(entries[0]); assert m.selected(w)==1
assert m.confirm()==11 and m.dispatched()[0][1]==entries[0]; passed()
# Swipe out of the old pool, then centre: settle/re-resolve before dispatch.
m.touch(); m.word(w+O['TABLE_TOP'],480); m.bind(rows,480); m.word(w+O['TABLE_ANIMATOR'],0x9876)
assert m.confirm()==11 and m.selected(w)==10 and m.dispatched()[0][1]==entries[0]
assert m.get(w+O['TABLE_ANIMATOR'])==0; passed()
# Returning to a surviving menu keeps a valid selection; shrinking data repairs it.
oldtop=m.top; m.page('playing_page'); assert m.confirm()==0
m.top=oldtop; m.paint(w); assert m.selected(w)==10
m.word(w+O['TABLE_ROWS'],1); m.word(w+O['TABLE_TOP'],0); m.bind(rows); m.paint(w)
assert m.selected(w)==0; passed()
