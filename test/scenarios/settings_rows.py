"""Settings rows: Swap L/R, Folder Root, Crossfade, Resume Songs, wheel sensitivity, Hold Return and Night Shift."""
from q2emu import *

# Swap L/R uses its own persistent flag, immediately below Mono Audio.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=MonoMachine)
assert texts()[3]=='Swap L/R'
assert click(3)==[(1,'Q2POD','SWAPLR')] and texts()[3]=='Swap L/R' and m.get(m.flag)==1
assert click(3)==[(0,'Q2POD','SWAPLR')] and texts()[3]=='Swap L/R' and m.get(m.flag)==0
m,rows,texts,*_=settings_page('audioset','scroll_view_playset',{'SWAPLR':'1'},stock_rows=13)
assert texts()[3]=='Swap L/R'; passed()
# Folder Root (#49): the row cycles the card's top-level folders, case aside, then the card, saving Q2POD
# FOLDERROOT each time. Not offered: files, dot-folders, names with an edge space or over 200 bytes (a
# config.ini line can't keep them). A saved folder gone moves on to the next name after it.
card=[('Zeta',4),('.Trash',4),('Música ñ',4),('alpha',4),('notes.txt',8),(' Edge',4),('B'*200,4),('C'*201,4),('Unknown',0)]
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',{'FOLDERROOT':'Deleted'},stock_rows=13,sub=1,cls=lambda:RootMachine(card=card))
assert texts()[3]=='Folder Root: Deleted'
saved=[]
for _ in range(6):
    assert click(3)==[] and [(m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='toolsWriteConfig']==[('Q2POD','FOLDERROOT')]
    saved.append((m.config['FOLDERROOT'],texts()[3]))
assert [v for v,_ in saved]==['Música ñ','Unknown','Zeta','','alpha','B'*200]
assert [t for _,t in saved]==['Folder Root: '+(v or 'Card') for v,_ in saved]; passed()
# Crossfade (crossfade.c): the row shows the saved length while on; an unreadable one is the default.
for config,text in (({'XFADE':'1','XFADESEC':'7'},'Crossfade: 7 s'),({'XFADE':'1','XFADESEC':'15'},'Crossfade: 15 s'),({'XFADE':'1','XFADESEC':'16'},'Crossfade: 5 s'),({'XFADESEC':'7'},'Crossfade: Off')):
    m,rows,texts,*_=settings_page('audioset','scroll_view_playset',config,stock_rows=13)
    assert texts()[0]==text and m.nodes[m.nodes[m.nodes[rows[0]]['children'][0]]['children'][1]].get('name')=='label_xfade'
passed()
# Resume Songs (crossfade.c resume_min): the row shows Q2POD RESUMEMIN, 30 min unless 0 or a five in 20-120.
for config,text in (({},'30 min'),({'RESUMEMIN':'0'},'Off'),({'RESUMEMIN':'120'},'120 min'),({'RESUMEMIN':'125'},'30 min'),
                    ({'RESUMEMIN':'22'},'30 min'),({'RESUMEMIN':'15'},'30 min'),({'RESUMEMIN':'x'},'30 min')):
    m,rows,texts,*_=settings_page('audioset','scroll_view_playset',config,stock_rows=13)
    assert texts()[1]=='Resume Songs: '+text and m.nodes[m.nodes[m.nodes[rows[1]]['children'][0]]['children'][1]].get('name')=='label_resumemin',config
passed()
# Wheel sensitivity (playback.c wheel_pct, test/playback.py's travel): Display → Controls' last row in both
# builds, Q2POD WHEELSENSITIVITY as 1.0.6 saved it, 50% unless a canonical ten in 10-200.
for config,pct in (({},50),({'WHEELSENSITIVITY':'130'},130),({'WHEELSENSITIVITY':'200'},200),({'WHEELSENSITIVITY':'40'},40),({'WHEELSENSITIVITY':'0'},50),
                   ({'WHEELSENSITIVITY':'135'},50),({'WHEELSENSITIVITY':'210'},50),({'WHEELSENSITIVITY':'x'},50)):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    label=m.nodes[m.nodes[rows[0]]['children'][0]]['children'][1]
    assert texts()[0]==f'Wheel Sensitivity: {pct}%' and icons[0]=='system_display' and m.nodes[label]['name']=='label_wheel'
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][0]==rows[0],config  # the list's start
passed()
# Hold Return (Q2POD HOLDRETURN; compact_now_playing): Controls' row after Wheel sensitivity,
# Recent unless set; Centre or tap toggles and saves it.
for config,text in (({},'Recent'),({'HOLDRETURN':'1'},'Home')):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    assert texts()[1]=='Hold Return: '+text and icons[1]=='playset_folderjump'
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][1]==rows[1],config
assert click(1)==[(0,'Q2POD','HOLDRETURN')] and texts()[1]=='Hold Return: Recent'
assert click(1)==[(1,'Q2POD','HOLDRETURN')] and texts()[1]=='Hold Return: Home'
passed()
# Wheel QS is gone: a saved Q2POD WHEELQS=1 is a key nobody reads, no row and nothing written.
m,rows,texts,icons,click=settings_page('display','scroll_view_display',{'WHEELQS':'1'},stock_rows=3,sub=3)
assert 'Wheel QS' not in texts() and texts()[:2]==['Wheel Sensitivity: 50%','Hold Return: Recent'] and click(1)==[(1,'Q2POD','HOLDRETURN')]; passed()
# Its row opens stock's Boot volume page re-dressed (crossfade.c): titled, no switch, the percent
# beside the slider, which runs 50-200 in tens. The wheel and +/- step 10%, live, the slider snaps to a ten, a drag
# is live, nothing is saved until the page closes, and Centre goes back (edit_key, as on Brightness).
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=3,sub=3)
display_page=m.top; m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(0)==[] and m.top!=display_page and m.nodes[m.top]['name']=='bootvol_page'
assert m.nodes[m.title]['text']=='Wheel Sensitivity' and m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='50%'
assert m.nodes[named(m,m.top,'view_bootvol')]['visible']==1 and m.nodes[named(m,m.top,'view_bootvol')]['enable']==1  # touch (#140)
def press(w,kind=O['EVT_CLICK']):
    m.calls=[]; f,ctx=m.handler(w,kind); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
def turn(key=O['KEY_NEXT']):
    assert m.call(key)==11; return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
W='Q2POD','WHEELSENSITIVITY'
assert turn()==[] and m.nodes[m.vol]['text']=='60%' and texts()[0]=='Wheel Sensitivity: 60%'
assert turn(O['KEY_PREV'])==[] and m.nodes[m.vol]['text']=='50%' and turn(O['KEY_PREV'])==[] and m.nodes[m.vol]['text']=='40%' and turn()==[]  # 50% is the default, not the bottom
assert press(m.add)==[] and m.nodes[m.vol]['text']=='60%' and texts()[0]=='Wheel Sensitivity: 60%'
assert press(m.dec)==[] and texts()[0]=='Wheel Sensitivity: 50%'
m.nodes[m.slider]['value']=3; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and m.nodes[m.vol]['text']=='10%' and press(m.dec)==[] and turn(O['KEY_PREV'])==[] and m.nodes[m.vol]['text']=='10%'  # 10% is the bottom
m.nodes[m.slider]['value']=127; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and m.nodes[m.vol]['text']=='130%'
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='200%'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and press(m.add)==[] and turn()==[] and m.nodes[m.vol]['text']=='200%'
m.nodes[m.slider]['value']=163; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and press(m.slider,O['EVT_VALUE_CHANGED'])==[] and m.nodes[m.vol]['text']=='160%'
m.echoes=0; m.nodes[m.slider]['value']=157; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and m.echoes==1  # its snap to 160 echoes
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and press(m.add)==[]
assert texts()[0]=='Wheel Sensitivity: 200%' and m.nodes[m.switch]['visible']==0
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1
assert press(m.top,O['EVT_DESTROY'])==[(200,*W)]; passed()  # the page goes: the one change, once
# Resume Songs' row opens the same page: no switch, Off below 20, then 20-120 min in fives, live, saved
# as the page closes; Centre goes back.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(1)==[] and m.nodes[m.top]['name']=='bootvol_page' and m.nodes[m.title]['text']=='Resume Songs'
assert m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='30 min'
R='Q2POD','RESUMEMIN'
assert turn()==[] and m.nodes[m.vol]['text']=='35 min' and texts()[1]=='Resume Songs: 35 min'
assert press(m.dec)==[] and turn(O['KEY_PREV'])==[] and turn(O['KEY_PREV'])==[] and m.nodes[m.vol]['text']=='20 min'
assert turn(O['KEY_PREV'])==[] and m.nodes[m.vol]['text']=='Off' and texts()[1]=='Resume Songs: Off'
assert turn(O['KEY_PREV'])==[] and press(m.add)==[] and m.nodes[m.vol]['text']=='20 min'  # Off is the bottom; up from it is 20
m.nodes[m.slider]['value']=63; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and m.nodes[m.vol]['text']=='65 min'
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='120 min'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and turn()==[]
m.nodes[m.slider]['value']=15; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and texts()[1]=='Resume Songs: Off'
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1
assert press(m.top,O['EVT_DESTROY'])==[(0,*R)]  # the page goes: 30 min to Off, once
# A page closed mid-drag saves the dragged value once, as it closes.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(1)==[]
m.nodes[m.slider]['value']=53; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and texts()[1]=='Resume Songs: 55 min'
assert press(m.top,O['EVT_DESTROY'])==[(55,*R)] and texts()[1]=='Resume Songs: 55 min'; passed()
# Crossfade's row opens it with its switch: Off or XF_MIN..XF_MAX s. Every change is live; the close
# saves the keys that changed.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(0)==[] and m.nodes[m.title]['text']=='Crossfade' and m.nodes[m.vol]['text']=='Off'
X=lambda on,sec: [(on,'Q2POD','XFADE'),(sec,'Q2POD','XFADESEC')]
# Leaving Off turns stock's Gapless on, as its row does: saved and set through mclSetGaplessMode (a stub
# here, so the byte is set as it would set it), and later turns on save nothing more.
m.byte(O['MCL_GAPLESS'],0)
m.nodes[m.slider]['value']=7; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[(1,'PLAYSET','GAPLESS')] and m.nodes[m.vol]['text']=='7 s'
assert [c[1] for c in m.calls if c[0]=='mclSetGaplessMode']==[1]; m.byte(O['MCL_GAPLESS'],1)
m.nodes[m.slider]['value']=9; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and press(m.slider,O['EVT_VALUE_CHANGED'])==[]
assert texts()[0]=='Crossfade: 9 s' and press(m.switch)==[] and m.nodes[m.vol]['text']=='Off'
assert press(m.add)==[] and turn()==[] and m.nodes[m.vol]['text']=='2 s'  # up from Off is XF_MIN, 1 s
m.nodes[m.slider]['value']=0; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='Off'
assert press(m.top,O['EVT_DESTROY'])==[(2,'Q2POD','XFADESEC')]  # Off as it opened: only the length is new
passed()
# Night Shift (nightshift.c): the main Display page's last row, after Appearance,
# Coverflow and Controls; Q2POD NIGHTSHIFT and NIGHTK, 3400K unless a hundred in 2000-5000.
for config,text in (({},'Off'),({'NIGHTSHIFT':'1'},'3400K'),({'NIGHTSHIFT':'1','NIGHTK':'2700'},'2700K'),({'NIGHTSHIFT':'1','NIGHTK':'5000'},'5000K'),
                    ({'NIGHTSHIFT':'1','NIGHTK':'2750'},'3400K'),({'NIGHTSHIFT':'1','NIGHTK':'6500'},'3400K'),({'NIGHTK':'2000'},'Off')):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3)
    label=m.nodes[m.nodes[rows[1]]['children'][0]]['children'][1]
    assert texts()[1]=='Night Shift: '+text and icons[1]=='display_backlight' and m.nodes[label]['name']=='label_night',config
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][3]==rows[1],config  # after Smart Awake
passed()
# Its row opens the same page with its switch, Off. The switch turns it on and off; the wheel, +/- and
# the slider step 100K, warmer to the right, a move turning it on; each change repaints the whole screen
# at once, a drag's every step too, and nothing is saved until the page closes.
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=3)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(1)==[] and m.nodes[m.top]['name']=='bootvol_page' and m.nodes[m.title]['text']=='Night Shift'
assert m.nodes[m.switch]['visible']!=0 and m.nodes[m.vol]['text']=='Off'
N,K=('Q2POD','NIGHTSHIFT'),('Q2POD','NIGHTK')
def repaints(): return [c[1:3] for c in m.calls if c[0]=='widget_invalidate_force']==[(m.wm,0)]
assert press(m.switch)==[] and repaints() and m.nodes[m.vol]['text']=='3400K' and texts()[1]=='Night Shift: 3400K'
assert m.nodes[m.switch]['image']=='switch_on'
assert turn()==[] and repaints() and m.nodes[m.vol]['text']=='3300K'
assert turn(O['KEY_PREV'])==[] and press(m.add)==[] and press(m.dec)==[] and m.nodes[m.vol]['text']=='3400K'
m.nodes[m.slider]['value']=30; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and turn()==[] and press(m.add)==[] and m.nodes[m.vol]['text']=='2000K'
m.nodes[m.slider]['value']=10; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and repaints() and texts()[1]=='Night Shift: 4000K'
m.nodes[m.slider]['value']=0; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and repaints() and texts()[1]=='Night Shift: 5000K'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[] and turn(O['KEY_PREV'])==[]
# The switch cycles Off, On and Auto; a move in Auto keeps Auto.
assert texts()[1]=='Night Shift: 5000K' and press(m.switch)==[] and repaints() and m.nodes[m.vol]['text']=='Auto'
assert texts()[1]=='Night Shift: Auto' and m.nodes[m.switch]['image']=='switch_on'
assert turn()==[] and texts()[1]=='Night Shift: Auto' and turn(O['KEY_PREV'])==[] and m.nodes[m.vol]['text']=='Auto'
assert press(m.switch)==[] and repaints() and m.nodes[m.vol]['text']=='Off'
assert texts()[1]=='Night Shift: Off' and m.nodes[m.switch]['image']=='switch_off'
assert turn(O['KEY_PREV'])==[] and texts()[1]=='Night Shift: 5000K'  # at an end too
assert press(m.switch)==[] and press(m.switch)==[]
assert turn()==[] and texts()[1]=='Night Shift: 4900K'  # a move turns it on
assert press(m.top,O['EVT_DESTROY'])==[(1,*N),(4900,*K)]; passed()  # the page goes: what changed, once
# The tint (nightshift.c night_end, stock lcd_end_frame calling it): on, as boot read it, the first
# normal frame's paint gives the screen's lcd night_end, reading no setting. Each frame warms the
# pixels drawn in its rect (alpha not 0xf7): red kept, green and blue scaled, alpha 0xf7; one
# already warmed, and any outside the rect, stay. Rows not w pixels long, a layout stock's lcd never
# has, are left as drawn. Off gives stock's end_frame back.

def pixels(m,fb,n): return [m.get(fb+4*i) for i in range(n)]
m,fb,end=tint_machine({'NIGHTSHIFT':'1'}); before=pixels(m,fb,32)
assert ('Q2POD','NIGHTSHIFT') in [r[1:3] for r in m.config_reads]; reads=len(m.config_reads)
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==payload_syms['night_end'] and len(m.config_reads)==reads and tint_frame(m)==[m.lcd]
inside={y*8+x for y in (1,2) for x in (2,3,4)}-{1*8+3}
assert pixels(m,fb,32)==[warmed(c) if i in inside else c for i,c in enumerate(before)]
once=pixels(m,fb,32); assert tint_frame(m)==[m.lcd] and pixels(m,fb,32)==once  # nothing warmed twice
m.word(fb+4*(2*8+2),0xff808080); assert tint_frame(m)==[m.lcd] and m.get(fb+4*(2*8+2))==warmed(0xff808080); passed()  # drawn again
# Stock's own image pixel (0x6c8520 from an RGBA image, 0x6c5140 a BGRA one: dst, src, alpha, premultiplied) drawn
# over a warmed row, a source pixel of each alpha: every one stock touches comes out warmed, once. It copies a
# source above 0xf8 alpha and all, so the mark is below that: at 0xfe the icons' rims (alpha 0xfe) stayed bright.
for pixel in (0x6c8520,0x6c5140):
    m,fb,end=tint_machine({'NIGHTSHIFT':'1'},w=256,h=1)
    for off,v in ((0xf8,0),(0xfc,0),(0x100,256),(0x104,1)): m.word(m.lcd+off,v)
    m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0); tint_frame(m); under=pixels(m,fb,256)
    src=m.alloc(4)
    for a in range(256): m.word(src,a<<24|0x30a0ff); m.call(address=pixel,args=(fb+4*a,src,0xff,0),gap=0)
    drawn=pixels(m,fb,256); assert drawn[0]==under[0] and all(d!=u for d,u in zip(drawn[1:],under[1:]))
    tint_frame(m); assert pixels(m,fb,256)==drawn[:1]+[warmed(c) for c in drawn[1:]],pixel
    once=pixels(m,fb,256); tint_frame(m); assert pixels(m,fb,256)==once
passed()
m.calls=[]; m.call(address=payload_syms['night_set'],args=(0,3400,0,0),gap=0)
assert m.get(m.lcd+0x7c)==end and [c[1:3] for c in m.calls if c[0]=='widget_invalidate_force']==[(m.wm,0)]
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==end; passed()  # off: the next paint leaves it
for row in (4,9):  # rows of h (rotated), padded rows
    m,fb,end=tint_machine({'NIGHTSHIFT':'1'},row=row); before=pixels(m,fb,32)
    m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
    assert tint_frame(m)==[m.lcd] and pixels(m,fb,32)==before,row
passed()
for config,kw in (({},{}),({'NIGHTSHIFT':'1'},{'mode':4}),({'NIGHTSHIFT':'1'},{'fmt':5})):  # off; an offline canvas; not BGRA
    m,fb,end=tint_machine(config,**kw); m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
    assert m.get(m.lcd+0x7c)==end,(config,kw)
passed()
# Auto (NIGHTSHIFT 2) warms the screen in Light Mode's dark hours, 19:00 to 7:00. Booted by day (the mock's
# clock is 18:14) a paint leaves the lcd alone; the crossing (clock_show's night_clock) repaints the screen and
# the next paint takes it, warmed as On's; the same hour again changes nothing, and day gives stock's
# end_frame back. Booted in the dark hours it starts warm; off while the clock can't be read.
m,fb,end=tint_machine({'NIGHTSHIFT':'2'}); before=pixels(m,fb,32)
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0); assert m.get(m.lcd+0x7c)==end
m.calls=[]; m.call(address=payload_syms['night_clock'],args=(1,0,0,0),gap=0)
assert [c[1:3] for c in m.calls if c[0]=='widget_invalidate_force']==[(m.wm,0)]
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==payload_syms['night_end'] and tint_frame(m)==[m.lcd]
assert pixels(m,fb,32)==[warmed(c) if i in inside else c for i,c in enumerate(before)]
m.calls=[]; m.call(address=payload_syms['night_clock'],args=(1,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==payload_syms['night_end'] and not m.calls
m.call(address=payload_syms['night_clock'],args=(0,0,0,0),gap=0); assert m.get(m.lcd+0x7c)==end; passed()
for clock,hooked in (((22,0),True),((6,59),True),((7,0),False),('localtime',False)):
    m,fb,end=tint_machine({'NIGHTSHIFT':'2'},clock=clock); m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
    assert (m.get(m.lcd+0x7c)==payload_syms['night_end'])==hooked,clock
passed()
# Bluetooth's list (btmenu_rows, rebuilt on every search) gains Resume on connect, Control Device Volume and
# Receive Audio with Bluetooth on, right after its fourth row, Bluetooth Headset Volume Control, as stock's rows:
# no icon, 78 high. The first two off unless Q2POD RESUME / BTVOLLINK is 1 (docs/internals.md#resume-on-connect,
# #headset-controls-and-volume); off, the Q2's volume is its own gain and the device keeps its own volume.
# Receive Audio on unless BTRECEIVE is 0 (docs/internals.md#bluetooth-receive-audio).
m,rows,texts,icons,click=settings_page('btrows','scroll_view_bluetooth',stock_rows=7)  # 5 rows, 2 devices
names=['Resume on Connect','Control Device Volume','Receive Audio']
assert len(rows)==3 and icons==[None]*3 and texts()==names
for k,row in enumerate(rows):
    kids=m.nodes[m.nodes[row]['children'][0]]['children']  # the label and its switch
    assert len(kids)==2 and m.get(kids[0]+O['W_X'])==10 and m.get(row+O['W_H'])==78
    assert switch_image(m,row)==('switch_on' if k==2 else 'switch_off')
    assert m.nodes[named(m,m.top,'scroll_view_bluetooth')]['children'].index(row)==4+k
assert click(0)==[(1,'Q2POD','RESUME')] and switch_image(m,rows[0])=='switch_on'
assert click(0)==[(0,'Q2POD','RESUME')] and switch_image(m,rows[0])=='switch_off'
assert click(1)==[(1,'Q2POD','BTVOLLINK')] and switch_image(m,rows[1])=='switch_on'
assert click(1)==[(0,'Q2POD','BTVOLLINK')] and switch_image(m,rows[1])=='switch_off'
assert click(2)==[(0,'Q2POD','BTRECEIVE')] and switch_image(m,rows[2])=='switch_off'
assert m.toasts[-1][3]=='Turn Bluetooth off and on to apply'  # bluealsa's A2DP Sink changes at its next start
assert click(2)==[(1,'Q2POD','BTRECEIVE')] and switch_image(m,rows[2])=='switch_on'
assert texts()==names; passed()
m,rows,texts,*_=settings_page('btrows','scroll_view_bluetooth',{'RESUME':'1','BTVOLLINK':'1','BTRECEIVE':'0'},stock_rows=7)
assert [switch_image(m,r) for r in rows]==['switch_on','switch_on','switch_off']; passed()
m,rows,*_=settings_page('btrows','scroll_view_bluetooth',stock_rows=1,bt=0)  # just the switch
assert rows==[]; passed()
