"""Selection recall by scope (folders, queries, detail pages); search and T9 dialogs, prompts, sliders and Quick Settings."""
from q2emu import *

# Run the actual stock label text accessor, including value_wstr and the label vtable.
# U+0100 and U+0200 share a low zero byte; titles/subtitles must use complete code points.
m=Machine(); w,es=m.page_list(4,extent=1000)
del m.handlers[syms['widget_get_text']]
m.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
def stock_text(e,text):
    m.word(e+0x74,syms['g_label_vtable'])
    m.word(e+0x38,len(text)); m.word(e+0x40,m.wide_string(text))
for i,e in enumerate(es):
    stock_text(e,'\u0100 Intro')
    sub=m.node('label'); stock_text(sub,['\u0100','\u0200','\U0001f600','音楽'][i])
    m.nodes[e]['children']=[sub]
# The narrow accessor really returns NULL, even though widget_get_text sees the label.
del m.handlers[syms['widget_get_prop_str']]
assert m.call(address=syms['widget_get_prop_str'],args=(es[0],m.string('text'),0,0))==0
m.mock('widget_get_prop_str')
m.paint(w); m.call(); assert m.selected(w)==1
w2,es2=m.page_list(4,extent=1000)
for i,e in enumerate(es2):
    stock_text(e,'\u0100 Intro')
    sub=m.node('label'); stock_text(sub,['\u0100','\U0001f600','音楽','\u0200'][i])
    m.nodes[e]['children']=[sub]
assert m.paint(w2)==0 and m.selected(w2)==3
assert m.confirm()==11 and m.dispatched()[0][1]==es2[3]; passed()

# Folder memory belongs to the full path, even when every visible row title is identical.
m=Machine(); m.folder('/sd/Folder A')
w,es=m.page_list(8,name='folder_page'); m.label(es,['Intro']*8)
m.paint(w)
for _ in range(5): m.call()
w2,es2=m.page_list(8,name='folder_page'); m.label(es2,['Intro']*8)
m.paint(w2); assert m.selected(w2)==5  # same path still recalls
m.folder('/sd/Folder B')
w3,es3=m.page_list(8,name='folder_page'); m.label(es3,['Intro']*8)
m.paint(w3); assert m.selected(w3)==0
# A surviving surface rebound to another folder also resets, even at the same row count.
for _ in range(3): m.call()
m.folder('/sd/Folder C')
m.word(w3+O['SCROLL_Y'],0); m.paint(w3); assert m.selected(w3)==0; passed()
# Nested folder returns restore each level on recreated or rebound surfaces.
for reuse in (False,True):
    m=Machine(); w,es=m.page_list(10,name='folder_page')
    for path,wanted in (('/sd',6),('/sd/child',4),('/sd/child/grandchild',2)):
        m.folder(path)
        if not reuse: w,es=m.page_list(10,name='folder_page')
        m.word(w+O['SCROLL_Y'],0); m.paint(w); assert m.selected(w)==0
        for _ in range(wanted): m.call()
    for path,wanted in (('/sd/child',4),('/sd',6),('/sd/child/grandchild',2)):
        m.folder(path)
        if not reuse: w,es=m.page_list(10,name='folder_page')
        m.word(w+O['SCROLL_Y'],0); m.paint(w)
        assert m.selected(w)==wanted and m.get(w+O['SCROLL_Y'])==(wanted-1)*48+12
    passed()

# Exactly 64 scopes fit. Selection promotes; restoration alone does not change recency.
for promote in (False,True):
    m=Machine(); w,es=m.page_list(4,name='folder_page')
    for i in range(64):
        m.folder(f'/sd/{i}',w); m.call(); m.call()
    m.folder('/sd/0',w); assert m.selected(w)==2
    if promote: m.call()  # update and protect the oldest entry
    m.folder('/sd/64',w); m.call()
    for i,wanted in ((0,3),(2,2)) if promote else ((1,2),(63,2)):
        m.folder(f'/sd/{i}',w); assert m.selected(w)==wanted
    evicted=1 if promote else 0
    m.folder(f'/sd/{evicted}',w); assert m.selected(w)==0
    passed()

# A full history must not add work to steady painting or turns in the current scope.
costs=[]
for occupancy in (1,64):
    m=Machine(); w,es=m.page_list(20,height=192,extent=960,name='folder_page')
    for i in range(occupancy):
        m.folder(f'/sd/{i:02}',w); m.call()
    m.paint(w)  # the first paint of a selection arms its marquee (marq_poll)
    instructions=[0]
    def count_payload(*args): instructions[0]+=1
    hook=m.u.hook_add(UC_HOOK_CODE,count_payload,begin=BASE,end=SCRATCH-1)
    m.paint(w); paint_cost=instructions[0]; instructions[0]=0
    m.call(); costs.append((paint_cost,instructions[0]))
    m.u.hook_del(hook)
assert costs[0]==costs[1],costs
cost_line('folder list paint',costs[0][0]); cost_line('folder list poll',costs[0][1])
passed()

# Returning after another scope still applies text matching and stale-index rejection.
for shortened in (False,True):
    m=Machine(); m.folder('/sd/parent')
    w,es=m.page_list(8,name='folder_page'); m.label(es,[f'track {i}' for i in range(8)]); m.paint(w)
    for _ in range(6): m.call()
    m.folder('/sd/child')
    w,es=m.page_list(8,name='folder_page'); m.paint(w); m.call()
    m.folder('/sd/parent')
    w,es=m.page_list(3 if shortened else 8,name='folder_page'); m.label(es,[f'track {i}' for i in range(8)])
    if not shortened: m.nodes[es[3]]['text']='track 6'; m.nodes[es[6]]['text']='other'
    m.paint(w); assert m.selected(w)==(0 if shortened else 3)
    passed()

# An unavailable or unterminated path cannot supply a content identity.
for path in (b'\0',b'x'*1024):
    m=Machine(); m.u.mem_write(syms['g_folder_path'],path)
    w,es=m.page_list(8,name='folder_page'); m.paint(w); m.call(); m.call()
    w2,es2=m.page_list(8,name='folder_page'); m.paint(w2)
    assert m.selected(w2)==0; passed()

# Root collections keep selection after a detail page overwrites the shared query.
for cls in (O['CLASS_ALBUMS'],0xf004,0xf005,0xf006):
    for reuse in (False,True):
        m=Machine(); m.word(syms['g_class_type'],cls)
        w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True)
        m.word(w+O['W_H'],288); m.paint(w)
        for _ in range(7): m.call()
        before=(m.selected(w),m.get(w+O['TABLE_TOP'])); assert before==(7,108)
        page=m.top; m.page('artistinfo_page')
        m.u.mem_write(syms['g_local_classinfo_save']+0x10d,b'Selected Artist\0')
        m.top=page
        if not reuse: w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True)
        m.word(w+O['W_H'],288); m.paint(w)
        assert (m.selected(w),m.get(w+O['TABLE_TOP']))==before
        # Switching Artist / Album Artist still opens a separate collection.
        m.word(syms['g_artist_type'],1)
        w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True); m.paint(w)
        assert m.selected(w)==0
        passed()

# Same-sized local lists must not inherit selection across query or browsing-mode changes.
for changed in ('g_class_type','g_local_classinfo_save','g_artist_type','album_modetype'):
    m=Machine(); w,rs,es=m.table_page(); m.paint(w); m.call(); m.call()
    w2,rs2,es2=m.table_page(); m.paint(w2); assert m.selected(w2)==2
    m.word(syms[changed],m.get(syms[changed])+1)
    w3,rs3,es3=m.table_page(); m.paint(w3); assert m.selected(w3)==0
    m.call(); m.call()
    m.word(syms[changed],m.get(syms[changed])+1)
    m.word(w3+O['TABLE_TOP'],0); m.paint(w3); assert m.selected(w3)==0
    passed()
# Each query can be revisited, including on a surface reused for other queries.
for reuse in (False,True):
    for changed in ('g_class_type','g_local_classinfo_save','g_artist_type','album_modetype'):
        m=Machine(); w,rs,es=m.table_page(n=20)
        for query,wanted in ((10,7),(20,4),(30,2)):
            m.word(syms[changed],query)
            if not reuse: w,rs,es=m.table_page(n=20)
            m.word(w+O['TABLE_TOP'],0); m.paint(w); assert m.selected(w)==0
            for _ in range(wanted): m.call()
        for query,wanted in ((10,7),(20,4),(30,2)):
            m.word(syms[changed],query)
            if not reuse: w,rs,es=m.table_page(n=20)
            m.word(w+O['TABLE_TOP'],0); m.paint(w)
            assert m.selected(w)==wanted and m.get(w+O['TABLE_TOP'])==(wanted-1)*48+12
        passed()
# Without an audited content identity, a recreated detail, dialog or network page starts fresh.
for name in ('netdiskfolder_page','tidal_albuminfo_page','playerqueue_page',
             'search_dialog','tidal_search_dialog'):
    m=Machine(); w,es=m.page_list(8,name=name); m.paint(w)
    for _ in range(3): m.call()
    m.paint(w); assert m.selected(w)==3
    w2,es2=m.page_list(8,name=name); m.paint(w2); assert m.selected(w2)==0
    passed()

# Search result dialogs navigate their list and centre opens the highlighted result.
for name in ('search_dialog','tidal_search_dialog'):
    m=Machine(); w,es=m.page_list(8,name=name)
    m.paint(w)
    for _ in range(2): assert m.call()==11
    assert m.selected(w)==2
    assert m.confirm()==11 and m.dispatched()[0][1]==es[2]
    passed()
# The search input dialogs have no navigable pane: they stay off the allowlist so the wheel
# keeps changing volume instead of being consumed by a window that cannot scroll.
for name in ('searchbox_dialog','tidal_searchbox_dialog'):
    m=Machine(); m.top=m.node('window',name,[m.node('view')])
    assert m.call()==0 and not m.moved(); passed()

for name in ('search_dialog','tidal_search_dialog','searchbox_dialog','tidal_searchbox_dialog'):
    for keyboard in (False,True):
        m=SearchMachine(name,keyboard)
        assert m.call(O['KEY_RETURN'],gap=0)==11 and m.nodes[m.edit]['focused']==0
        assert m.top==m.dialog and m.get(m.im+O['IM_WIDGET'])==0
        assert len([c for c in m.calls if c[0]=='search:close'])==1
        assert m.call(O['KEY_RETURN'],gap=0)==0 and not [c for c in m.calls if c[0]=='search:close']
        passed()
# Our T9 edits (peq_ui.c edit_create, EDIT_NAME): Return empties the box before the blur, so its
# EVT_VALUE_CHANGED is a cancel, then detaches the keyboard; stock's and the playlist search keep their text.
for name,text in (('q2_edit',''),('search_edit','query')):
    m=SearchMachine(); m.nodes[m.edit]['name']=name; m.nodes[m.edit]['text']='query'
    assert m.call(O['KEY_RETURN'],gap=0)==11 and m.nodes[m.edit]['focused']==0 and m.nodes[m.edit]['text']==text
    assert m.get(m.im+O['IM_WIDGET'])==0 and len([c for c in m.calls if c[0]=='search:close'])==1; passed()
# An idle input method, a different editor and unrelated keys leave the keyboard alone.
for case in ('idle','other','key','missing'):
    m=SearchMachine()
    if case=='idle': m.word(m.im+O['IM_WIDGET'],0)
    elif case=='other': m.nodes[m.edit]['name']='password'
    elif case=='missing': m.im=0
    key=O['KEY_NEXT'] if case=='key' else O['KEY_RETURN']
    assert m.call(key,gap=0)==0 and m.nodes[m.edit]['focused']==1
    assert not [c for c in m.calls if c[0]=='search:close']; passed()

# iPod: a confirm dialog's buttons are its rows (contexts.inc BUTTONS). The wheel moves between the
# side-by-side buttons without scrolling anything, the bar is the button's own tile, the ends are
# hard and Centre clicks the selected button. The Stock build leaves the dialog stock: the wheel is volume.
m=Machine(); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm)
m.word(d+O['W_W'],375); m.word(d+O['W_H'],320); m.clip=(0,0,375,320); m.top=d
buttons=[m.entry(d,220) for _ in range(2)]; m.nodes[d]['children']=buttons
pair=(53,242)  # a pair of tiles side by side, as Tidal's confirm keeps
for b,x in zip(buttons,pair): m.word(b+O['W_X'],x); m.word(b+O['W_W'],80); m.word(b+O['W_H'],80)
m.paint(d); assert m.selected(d)==0 and m.sel()==(53,220,80,80)
# The focused tile is framed in constant white (no accent lookup), over the bar; stroke color restored.
frame=[r for r in m.rounded if r['kind']=='stroke']
assert not m.strokes and [(r['rect'],r['radius'],r['color']) for r in frame]==[((53,220,80,80),O['SEL_RADIUS'],0xffffffff),((54,221,78,78),O['SEL_RADIUS']-1,0xffffffff)]
assert m.lcd_colors()==LCD_COLORS
assert m.call()==11 and m.selected(d)==1 and not m.moved()
m.paint(d); assert m.sel()==(242,220,80,80) and m.clip==(0,0,375,320)
assert not m.strokes and [r['rect'] for r in m.rounded if r['kind']=='stroke']==[(242,220,80,80),(243,221,78,78)]
assert m.call()==11 and m.selected(d)==1 and not m.moved()
assert m.call(O['KEY_PREV'])==11 and m.selected(d)==0
assert m.confirm()==11 and m.dispatched()[0][1]==buttons[0]; passed()
# Modern frames it over its accent bar too (Classic's band has its own edge: classic_light cases).
k=Machine(); k.config['THEME']='2'; kd=k.node('dialog','confirminfo_dialog'); k.word(kd+O['W_PARENT'],k.wm)
k.word(kd+O['W_W'],375); k.word(kd+O['W_H'],320); k.clip=(0,0,375,320); k.top=kd
kb=[k.entry(kd,220) for _ in range(2)]; k.nodes[kd]['children']=kb
for b,x in zip(kb,pair): k.word(b+O['W_X'],x); k.word(b+O['W_W'],80); k.word(b+O['W_H'],80)
k.paint(kd); assert k.bands and [r['rect'] for r in k.rounded if r['kind']=='stroke']==[(53,220,80,80),(54,221,78,78)]; passed()
# A wide button (autoshutdown's Cancel) gets the full-width bar.
m.nodes[d]['name']='autoshutdown_dialog'; m.nodes[d]['children']=[buttons[0]]; m.word(buttons[0]+O['W_W'],287)
m.paint(d); assert m.sel()==(0,220,375,80) and not m.strokes; passed()
# iPod's confirminfo_dialog.bin: Cancel and OK are full-width rows, so the bar is a list's, unframed.
m=Machine(); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm)
m.word(d+O['W_W'],375); m.word(d+O['W_H'],320); m.clip=(0,0,375,320); m.top=d
rows=[m.entry(d,208+48*i) for i in range(2)]; m.nodes[d]['children']=rows
for r in rows: m.word(r+O['W_W'],375)
m.paint(d); assert m.selected(d)==0 and m.sel()==(0,208,375,48) and not m.strokes
assert m.call()==11 and m.selected(d)==1; m.paint(d); assert m.sel()==(0,256,375,48)
assert m.confirm()==11 and m.clicks==[rows[1]]; passed()
# A runtime prompt is styled once: stock's style setters invalidate even equal values.
prompt=m.node('label','prompt'); m.word(prompt+O['W_H'],40)
m.nodes[d]['children'].append(prompt)
m.paint(d)
assert tuple(m.get(prompt+O[k]) for k in ('W_X','W_Y','W_W','W_H'))==(0,0,375,O['CONFIRM_TOP'])
assert m.nodes[prompt]['style:normal:font_size']==O['CONFIRM_PROMPT_PX']
assert m.nodes[prompt]['style:normal:text_align_h']=='center' and m.nodes[prompt]['style:normal:text_align_v']=='middle'
m.paint(d)
assert not [c for c in m.calls if c[0].startswith('widget_set_prop') and c[1]==prompt]; passed()
# Delete File's "Also delete source file" keeps its stock place; the prompt centres above it.
m.nodes[d]['children'].remove(prompt); prompt=m.node('label','prompt'); check=m.node('check_button','msg_delcheckbutton')
for w,(x,y) in ((prompt,(10,79)),(check,(10,124))):
    for k,v in zip(('W_X','W_Y','W_W','W_H'),(x,y,355,24)): m.word(w+O[k],v)
m.nodes[d]['children']+=[prompt,check]
m.paint(d)
assert tuple(m.get(prompt+O[k]) for k in ('W_X','W_Y','W_W','W_H'))==(0,0,375,124)
assert m.nodes[prompt]['style:normal:font_size']==O['CONFIRM_PROMPT_PX']
assert tuple(m.get(check+O[k]) for k in ('W_X','W_Y','W_W','W_H'))==(10,124,355,24)
assert not [c for c in m.calls if c[0].startswith('widget_set_prop') and c[1]==check]; passed()

# USB mode's exit asks whether to scan before it leaves the mode: with the cable out, the prompt
# takes the wheel (iPod: its buttons); with the cable in, input stays stock's.
for cable in (0,1):
    m=Machine(); d=m.node('dialog','confirminfo_dialog',[]); m.word(d+O['W_PARENT'],m.wm); m.top=d
    m.nodes[d]['children']=[m.entry(d,220) for _ in range(2)]
    m.byte(syms['g_usblink_status'],2); m.byte(syms['g_usbdet_value'],cable)
    assert m.call()==(11 if not cable else 0); passed()

# The real input entry handles USB confirmation before native USB/volume routing.
m=Machine(); d=m.node('dialog','confirminfo_dialog',[]); m.word(d+O['W_PARENT'],m.wm); m.top=d
m.nodes[d]['children']=[m.entry(d,220) for _ in range(2)]
m.byte(syms['g_usblink_status'],2); m.byte(syms['g_usbdet_value'],0)
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
assert m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)==0
assert m.selected(d)==1 and not any(c[0]=='stock_input' for c in m.calls)
assert any(c[0]=='reset_poweroptions_timer' for c in m.calls)
m.call(O['KEY_NEXT'],address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=O['EVT_POINTER_DOWN'],gap=0)
assert any(c[0]=='stock_input' for c in m.calls)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=1000)
assert m.selected(d)==1
m.byte(syms['g_usbdet_value'],1)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert any(c[0]=='stock_input' for c in m.calls); passed()
# Transition-time volume applies once, without a queued overlay; fresh input stays native.
m=Machine(); m.top=m.node('window','playing_page'); m.animating=1
m.byte(syms['g_volume'],20); m.byte(syms['g_maxvolume'],21)
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
for key,want in ((O['KEY_NEXT'],21),(O['KEY_NEXT'],21),(O['KEY_PREV'],20)):
    m.call(key,address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
    assert m.u.mem_read(syms['g_volume'],1)[0]==want
    assert not any(c[0] in ('stock_input','navigator_to') for c in m.calls)
m.byte(syms['g_volume'],100); m.byte(syms['g_maxvolume'],200)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert m.u.mem_read(syms['g_volume'],1)[0]==100
m.animating=0
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert any(c[0]=='stock_input' for c in m.calls) and not any(c[0]=='device_set_volume' for c in m.calls); passed()

# Slider settings: the wheel presses the page's own +/-, Centre goes back once the double-press
# window has passed, and a double Centre is stock's screen toggle instead.
for name in ('backlight_page','maxvol_page','bootvol_page','balance_page'):
    m=Machine(); add,dec=m.node('image','img_add'),m.node('image','img_dec')
    m.top=m.node('window',name,[m.node('view','view_body',[dec,add])]); m.mock('on_wm_keyup_fun')
    assert m.call()==11 and m.clicks==[add]
    assert m.call(O['KEY_PREV'])==11 and m.clicks==[add,dec]
    m.byte(syms['bt__recv_pageflag'],2)
    assert m.call()==11 and m.call(O['KEY_PREV'])==11 and m.clicks==[add,dec,add,dec]
    m.byte(syms['bt__recv_pageflag'],0)
    assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1
    assert m.call(O['KEY_CENTER'])==11 and m.call(O['KEY_CENTER'],gap=50)==11
    m.advance(300,clear=False); names=[c[0] for c in m.calls]
    assert 'on_wm_keyup_fun' in names and 'navigator_back' not in names; passed()
# Quick Settings: the wheel sets its brightness slider within its range; Centre and Return close it,
# as its swipe up does (dialog_quit), not stock's screen off and Home.
m=Machine(); bright=m.node('slider','slider_backlight',value=99)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])]); m.mock('dialog_quit')
assert m.call()==11 and m.nodes[bright]['value']==100
assert m.call()==11 and m.nodes[bright]['value']==100
assert m.call(O['KEY_PREV'])==11 and m.nodes[bright]['value']==99
for key in ('KEY_CENTER','KEY_RETURN'):
    assert m.call(O[key])==11 and [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(m.top,0)]
passed()
# A swipe down whose release the pull-down never got (stock state 1 or 2, no touch, no slide timer)
# leaves it on top off screen, the page under it stopped (#189): the next UI pass hands it the
# release, which stock's handler slides; an open panel, a live drag and a running slide are left.
for state,pressed,timer,stuck in ((1,0,None,1),(2,0,-1,1),(3,0,None,0),(0,0,None,0),(1,1,None,0),(1,0,7,0)):
    m=Machine(); m.top=m.node('dialog','statusbar_dialog',**({} if timer is None else {'timer_idex':timer}))
    m.word(syms['g_dropdown_menu_state'],state); m.pressed=pressed
    m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
    m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=O['EVT_POINTER_FIRST'],gap=0)  # a touch just now: a finger
    m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
    m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0)
    assert m.ups==[m.top]*stuck,(state,pressed,timer)
passed()
# A held Play/Pause there switches its wheel to stock's volume and back, kept as Q2POD QSVOLUME.
m=Machine(); bright=m.node('slider','slider_backlight',value=50)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])])
hold=lambda: m.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)
saved=lambda: [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
assert hold()==11 and saved()==[(1,'Q2POD','QSVOLUME')]
assert m.call()!=11 and m.nodes[bright]['value']==50  # stock's volume takes the tick
assert hold()==11 and saved()==[(0,'Q2POD','QSVOLUME')]
assert m.call()==11 and m.nodes[bright]['value']==51
m=Machine(); m.config['QSVOLUME']='1'; bright=m.node('slider','slider_backlight',value=50)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])])
assert m.call()!=11 and m.nodes[bright]['value']==50; passed()
# While the pull-down slides (stock state 1 or 2, or its timer_idex slide timer running) the wheel
# does nothing there, brightness and volume mode alike: only the rested, open panel takes a tick.
for state,timer,volume in ((1,7,'0'),(2,7,'0'),(3,7,'0'),(3,7,'1'),(2,7,'1')):
    m=Machine(); m.config['QSVOLUME']=volume; bright=m.node('slider','slider_backlight',value=50)
    m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],timer_idex=timer)
    m.word(syms['g_dropdown_menu_state'],state)
    assert m.call()==11 and m.nodes[bright]['value']==50, (state,timer,volume)
for state,timer,want in ((1,-1,50),(2,-1,50),(3,-1,51)):  # the state alone gates it, and at rest it ticks
    m=Machine(); bright=m.node('slider','slider_backlight',value=50)
    m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],timer_idex=timer)
    m.word(syms['g_dropdown_menu_state'],state)
    assert m.call()==11 and m.nodes[bright]['value']==want, (state,timer)
passed()
# A swipe up that closes it may start on the brightness slider: the slider gets its press only once the
# touch goes 10 px more sideways than up or down, or with a tap's release; a touch that goes up first,
# or any while stock slides the pull-down (g_dropdown_menu_state 1, 2), never reaches it, and a press
# it has is aborted. Withheld events return 0, not STOP, so the window manager still sees the touch.

D,M,U,A=O['EVT_POINTER_DOWN']-1,O['EVT_POINTER_MOVE_BEFORE']-1,O['EVT_POINTER_UP_BEFORE']-1,O['EVT_POINTER_ABORT']
assert slider_touch([('down',100,300),('move',103,290),('move',104,270),('move',140,250),('up',140,200)])==[(0,[])]*5  # a swipe up
assert slider_touch([('down',100,300),('move',105,301),('move',120,302),('up',150,302)])==[(0,[]),(0,[]),(0,[D,M]),(0,[U])]  # a drag
assert slider_touch([('down',100,300),('up',101,301)])==[(0,[]),(0,[D,U])]  # a tap
assert slider_touch([('down',100,300),('move',120,300),('up',120,250)],state=1)==[(0,[]),(0,[D,M]),(0,[A])]  # then stock slides it
assert slider_touch([('down',100,300),('up',100,300)],state=2)==[(0,[]),(0,[])]
assert slider_touch([('down',100,300),('move',103,280)],dialog='backlight_page')==[(0,[D]),(0,[M])]  # Settings' slider is stock's
passed()
# Run stock's slider event handler with the packaged hit rectangle. Position arithmetic is
# stock FP64 (unsupported by Unicorn); this checks capture and delivery, including outside drags.

node=next(n for n in ui_walk(decode((B/'ui/playing_page.bin').read_bytes())) if n[2].get('name')=='slider_play')
for x in (22,187,353):
  for y in (234,255,276):
   for end in (U,A):
    m=Machine(); slider=m.node('slider','slider_play'); parent=m.node('window','playing_page',[slider])
    m.word(slider+O['W_PARENT'],parent)
    for k,v in zip(('W_X','W_Y','W_W','W_H'),node[1]): m.word(slider+O[k],v)
    assert node[1][0]<=x<node[1][0]+node[1][2] and node[1][1]<=y<node[1][1]+node[1][3]
    m.word(slider+0x98,int(node[2]['bar_size'])); m.byte(slider+0xa6,node[2]['slide_with_bar']=='true')
    m.handlers[0x62d284]='stock_slider_preview'
    m.mock('widget_find_animator','widget_set_state','widget_grab','widget_ungrab')
    def pointer(typ,x,y):
        m.byte(m.event+0x24,1); m.word(m.event+O['EVENT_Y'],y)
        return m.call(x,address=0x62d7c4,args=(slider,m.event,0,0),event_type=typ,gap=0)
    assert pointer(D,x,y)==11 and bytes(m.u.mem_read(slider+0xac,1))==b'\x01'
    assert any(c[0]=='widget_grab' and c[1:3]==(parent,slider) for c in m.calls)
    pointer(M,400,210)  # drift well above and beyond the bar without losing the drag
    assert bytes(m.u.mem_read(slider+0xad,1))==b'\x01'
    assert any(c[0]=='stock_slider_preview' for c in m.calls)
    pointer(end,400,210)
    assert bytes(m.u.mem_read(slider+0xac,2))==b'\x00\x00'
    assert any(c[0]=='widget_ungrab' and c[1:3]==(parent,slider) for c in m.calls)
    passed()

# Set date and time: the wheel turns the outlined field, Centre moves on (to the time page after
# the day) and then to OK, which Centre presses; a turn on OK goes back to the fields.

m,sels,ok,slide=time_page('manualtime_page')
assert m.call()==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==6
m.paint(sels[0]); assert not m.strokes and m.lcd_colors()==LCD_COLORS
assert [(r['kind'],r['rect'],r['radius'],r['color']) for r in m.rounded]==[('stroke',(0,0,80,170),O['SEL_RADIUS'],0xffffffff),('stroke',(1,1,78,168),O['SEL_RADIUS']-1,0xffffffff)]
m.rounded_fail=True; m.paint(sels[0]); assert [s[:4] for s in m.strokes]==[(0,0,80,170),(1,1,78,168)] and m.lcd_colors()==LCD_COLORS
m.rounded_fail=None; m.paint(sels[1]); assert not m.strokes and not m.rounded
for f in range(1,6): assert m.confirm()==11 and m.nodes[m.top]['_edit_field']==f
assert m.nodes[slide]['value']==1 and not m.clicks
# OK, when next, is a selected row: the off-white card with its label dark, not a frame.
m.word(ok+O['W_W'],287); m.word(ok+O['W_H'],40); m.nodes[ok]['text']='OK'; m.paint(ok)
assert not m.strokes and [(r['rect'],r['radius']) for r in m.rounded]==[((0,0,287,40),O['SEL_RADIUS'])]
assert [l['text'] for l in m.letters]==['OK'] and m.lcd_colors()==LCD_COLORS
backs=lambda: [c[0] for c in m.calls].count('navigator_back')
assert m.confirm()==11 and m.clicks==[ok] and backs()==1  # OK sets the time, then back to Time
assert m.call(O['KEY_PREV'])==11 and m.nodes[m.top]['_edit_field']==4
for _ in range(9): m.call()
assert m.get(sels[4]+O['SELECTOR_INDEX'])==11
m.nodes[slide]['value']=0; assert m.call(O['KEY_PREV'])==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==5; passed()
# The side keys move between the fields and OK, stopping at the ends; a tap on OK goes back too.
m,sels,ok,slide=time_page('manualtime_page'); field=lambda: m.nodes[m.top].get('_edit_field',0)
assert m.call(O['KEY_BACK_BTN'])==11 and field()==0
for f in (1,2,3,4,5,5): assert m.call(O['KEY_FWD_BTN'])==11 and field()==f
assert m.nodes[slide]['value']==1 and not m.clicks
assert m.call(O['KEY_BACK_BTN'])==11 and field()==4 and m.call(O['KEY_BACK_BTN'])==11 and field()==3
assert not any(c[0] in ('media_key','stock_input') for c in m.calls)
m.call(address=HOOKS['widget_dispatch'][0],args=(ok,m.event,0,0),event_type=O['EVT_CLICK'],gap=0)
assert m.clicks==[ok] and [c[0] for c in m.calls].count('navigator_back')==1; passed()
m,sels,ok,slide=time_page('sleepshutdown_page')
assert m.call()==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==6
assert m.call(O['KEY_FWD_BTN'])==11 and m.nodes[m.top]['_edit_field']==1 and m.call(O['KEY_BACK_BTN'])==11
assert m.confirm()==11 and m.confirm()==11 and m.confirm()==11 and m.clicks==[ok]
assert 'navigator_back' not in [c[0] for c in m.calls]; passed()  # the sleep timer's OK stays stock's

# A picker opens on its checked option, not the row last chosen there.
m=Machine(); w,es=m.page_list(4,name='playmode_page')
m.nodes[es[2]]['children']=[m.node('image',image='select')]
m.paint(w); assert m.selected(w)==2; passed()
# iPod Minimal: the selected row's white radio is drawn again dark on the off-white card: a ring,
# and its dot when checked. Other rows keep stock's.
for row,image,fills in ((2,'select',3),(0,'unselect',0),(2,'more',4),(0,'more',0)):
    m=Machine(); w,es=m.page_list(4,name='playmode_page')
    radio=m.node('image',image=image); m.nodes[es[row]]['children']=[radio]
    if image=='more':
        m.nodes[radio]['name']='img_more0'
        if row==2: m.nodes[es[row]]['children'].append(m.node('image',image='select'))
    m.word(radio+O['W_PARENT'],es[row]); m.word(radio+O['W_W'],50 if image=='more' else 26); m.word(radio+O['W_H'],70)
    if row==0: m.nodes[es[2]]['children']=[m.node('image',image='select')]
    m.paint(w); m.paint(radio)
    expected=[(12,31,26,8),(14,33,4,4),(21,33,4,4),(28,33,4,4)] if image=='more' else [(0,22,26,26),(2,24,22,22),(7,29,12,12)]
    assert [r['rect'] for r in m.rounded]==expected[:fills] and m.lcd_colors()==LCD_COLORS, (image,m.rounded,m.lcd_colors())
    passed()

# iPod: a playlist's More page (Rename, Delete) is a BUTTONS page: the wheel moves between them.
m=Machine(); top=m.node('window','playlistsmore_page'); m.word(top+O['W_PARENT'],m.wm); m.top=top
m.word(top+O['W_W'],375); m.word(top+O['W_H'],290)
rows=[m.node('list_item',children=[m.entry(0)]) for _ in range(2)]
for i,r in enumerate(rows): m.word(r+O['W_Y'],10+i*78); m.word(m.nodes[r]['children'][0]+O['W_PARENT'],r); m.word(r+O['W_PARENT'],top)
m.nodes[top]['children']=rows
assert m.call()==11 and m.selected(top)==1 and m.call()==11 and m.selected(top)==1
assert m.confirm()==11 and m.clicks==[m.nodes[rows[1]]['children'][0]]; passed()
