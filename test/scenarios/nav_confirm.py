"""Table recall on recreation; Centre's single and double press, confirmed at DOUBLE_CLICK_MS."""
from q2emu import *

# Virtual music tables recall a logical row and scroll to it on recreation.
m=Machine(); w,_,entries=m.table_page()
m.paint(w)
for _ in range(2): assert m.call()==11
assert m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==60
m.rebind=None
w2,_,entries2=m.table_page()
m.paint(w2); assert m.selected(w2)==2 and m.get(w2+O['TABLE_TOP'])==60
assert m.confirm()==11 and m.dispatched()[0][1]==entries2[2]; passed()

# Table recall survives interruption even when the target is outside the recycled row pool.
for wanted in (2,12):
    m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
    for _ in range(wanted): m.call()
    w2,rs2,es2=m.table_page(); m.glide=False
    m.touch(); m.paint(w2)
    assert m.selected(w2)==wanted and m.moved()[-1][2]==(wanted-1)*48+12
    # Rebind the pool as the restarted glide completes.
    top=(wanted-1)*48+12
    m.word(w2+O['TABLE_TOP'],top); m.word(w2+O['TABLE_ANIMATOR'],0)
    m.bind(rs2,top)
    m.paint(w2); assert m.selected(w2)==wanted
    m.confirm(); assert m.dispatched()[0][1]==es2[1]
    passed()
# A wheel during recall advances the logical target; an explicit tap instead replaces it.
m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
for _ in range(12): m.call()
w2,rs2,es2=m.table_page(rebind=True); m.glide=False
m.touch(); m.call(); assert m.selected(w2)==13
m.click(es2[0]); assert m.get(w2+O['TABLE_ANIMATOR'])==0
m.paint(w2)
assert m.selected(w2)==12
m.confirm(); assert m.dispatched()[0][1]==es2[0]; passed()

# A synchronous restore rebind must discard the pre-scroll pool before centre dispatch.
m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
for _ in range(12): m.call()
w2,rs2,es2=m.table_page(rebind=True)
m.paint(w2); assert m.selected(w2)==12
m.confirm(); assert m.dispatched()[0][1]==es2[1]; passed()

# Home keeps its native carousel presentation and value (including touch changes).
m=Machine(); w=m.page('home_page','slide_menu'); m.word(w+O['SLIDE_INDEX'],1)
child=[m.entry(w),m.entry(w)]; m.nodes[w]['children']=child
assert m.paint(w)==0 and not m.drawn()
assert any(c[0]=='stock_paint' for c in m.calls)
assert m.confirm()==11 and m.dispatched()[0][1]==child[1]
m.word(w+O['SLIDE_INDEX'],0)
assert m.confirm()==11 and m.dispatched()[0][1]==child[0]; passed()
# Long-press/boot release must reach stock cleanup, never activate a menu item.
for addr in [syms['g_power_longkey'],syms['g_ingore_bootkey_flag'],O['BOOT_KEY_GUARD']]:
    m.byte(addr,1); assert m.confirm()==0 and not m.dispatched(); m.byte(addr,0); passed()
# A single release confirms exactly once at DOUBLE_CLICK_MS, never a millisecond before.
m=Machine(); w,es=m.page_list(3)
assert m.release()==11 and not m.clicks
m.advance(DC-1); assert not m.clicks
m.advance(1); assert m.clicks==[es[0]] and not m.timers and not m.screens
m.advance(1000); assert m.clicks==[es[0]]; passed()
# Stock's wheel lockout after the centre key ends when the press opens its row: the next tick moves.
m=Machine(); w,es=m.page_list(3)
assert m.release()==11 and m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\x08'
m.advance(DC); assert m.clicks==[es[0]] and m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\0'
assert m.call(gap=0,debounce=True)==11 and m.selected(w)==1; passed()
# Before the deadline, the second release cancels the click and executes stock screen-off.
for gap in (0,100,DC-1):
    m=Machine(); w,es=m.page_list(3)
    assert m.release()==11 and m.release(gap)==0
    assert m.screens==[0] and not m.u.mem_read(syms['g_backlight_status'],1)[0]
    m.advance(1000); assert not m.clicks and not m.timers
    # A release while off follows the real stock wake path, with no menu activation.
    assert m.release()==0 and m.screens==[0,1]
    assert m.u.mem_read(syms['g_backlight_status'],1)[0]==1 and not m.clicks; passed()
# A page still sliding in takes Centre, but a double press there still turns the screen off; a slow
# second press does not, and the first press alone never does.
m=Machine(); w,es=m.page_list(3); m.animating=1
assert m.release()==11 and not m.screens and m.release(DC-1)==11 and m.screens==[0]
m=Machine(); w,es=m.page_list(3); m.animating=1
assert m.release()==11 and m.release(DC)==11 and not m.screens; passed()
# At/after expiry, the first single has dispatched; the next release starts a new single.
for gap in (DC,DC+1):
    m=Machine(); w,es=m.page_list(3)
    assert m.release()==11 and m.release(gap)==11 and m.clicks==[es[0]]
    m.advance(300); assert m.clicks==[es[0],es[0]] and not m.screens; passed()
# Touch and either wheel direction cancel pending confirmation, including invalid navigation.
for action in ('touch','next','prev'):
    m=Machine(); w,es=m.page_list(3); m.release()
    if action=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],gap=100)
    else: m.call(O['KEY_NEXT'] if action=='next' else O['KEY_PREV'],gap=100)
    m.advance(1000); assert not m.clicks and not m.timers
    assert m.release()==11
    m.advance(300); assert len(m.clicks)==1 and not m.screens; passed()
# Changed, destroyed, or address-reused targets cannot receive a delayed click.
for change in ('top','surface','scope','selection','count','text','hidden','disabled',
               'animating','pressed','screen_off','locked','long','boot','reused_top','reused_surface'):
    m=Machine(); w,es=m.page_list(3,name='folder_page')
    m.folder('/first')
    m.nodes[es[0]]['text']='First'
    m.release()
    if change=='top': m.page_list(3,name='display_page')
    elif change=='surface': m.nodes[m.top]['children']=[m.node()]
    elif change=='scope': m.folder('/other')
    elif change=='selection': m.nodes[w]['_ringnav_index']=1
    elif change=='count': m.nodes[w]['children']=es[:2]
    elif change=='text': m.nodes[es[0]]['text']='Rebound'
    elif change=='hidden': m.nodes[w]['visible']=0
    elif change=='disabled': m.nodes[es[0]]['enable']=0
    elif change=='animating': m.animating=1
    elif change=='pressed': m.pressed=1
    elif change=='screen_off': m.byte(syms['g_backlight_status'],0)
    elif change=='locked': m.byte(syms['g_lockscreen_pageflag'],1)
    elif change=='long': m.byte(syms['g_power_longkey'],1)
    elif change=='boot': m.byte(O['BOOT_KEY_GUARD'],1)
    else: m.nodes[m.top if change=='reused_top' else w].pop('_ringnav_confirm')
    m.advance(300); assert not m.clicks and not m.timers,change; passed()
# Non-virtual rows cannot inherit confirmation merely by sharing an index and text.
for kind in ('scroll_view','slide_menu'):
    for text in ('','Same title'):
        for reused in (False,True):
            m=Machine(); w,es=m.page_list(3)
            m.nodes[w]['type']=kind
            if kind=='slide_menu': m.word(w+O['SLIDE_INDEX'],0)
            m.nodes[es[0]]['text']=text
            m.release()
            if reused:
                m.nodes[es[0]].pop('_ringnav_confirm',None)
            else:
                replacement=m.entry(w)
                m.nodes[replacement]['text']=text
                m.nodes[w]['children'][0]=replacement
            m.advance(300)
            assert not m.clicks and not m.timers,(kind,text,reused)
            passed()

# A recycled table pool resolves the original logical index from the live row mapping.
m=Machine(); w,rs,es=m.table_page(); m.release()
m.word(rs[0]+O['ROW_INDEX'],1); m.word(rs[1]+O['ROW_INDEX'],0)
m.advance(300); assert m.clicks==[es[1]]; passed()
m=Machine(); w,rs,es=m.table_page(); m.release(); m.bind(rs,480)
m.advance(300); assert not m.clicks; passed()
# Invalid state observed before expiry stays cancelled even if the same page returns.
m=Machine(); w,es=m.page_list(3); m.release(); m.animating=1
m.call(gap=100); m.animating=0; m.advance(300)
assert not m.clicks and not m.timers; passed()
# Timer allocation failure consumes both releases without activation; the double press still turns
# the screen off, as on any page that keeps Centre.
m=Machine(); w,es=m.page_list(3); m.timer_fail=True
assert m.release()==11 and m.release(100)==11
m.advance(1000); assert not m.clicks and m.screens==[0] and not m.timers; passed()
# Empty menus never arm confirmation; a double press there still turns the screen off.
m=Machine(); m.page()
assert m.release()==11 and m.release(100)==11
assert not m.timers and not m.clicks and m.screens==[0]; passed()
# Audit the stock creation path independently: live offset, duration, easing,
# completion address and deselection agree with the fields used by the patch.
m=Machine(); w=m.page('home_page','slide_menu')
m.call(address=0x606290,args=(w,-240,0,0),gap=0)
a=m.get(w+O['SLIDE_ANIMATOR'])
assert m.get(a+O['ANIM_DURATION'])==150 and signed(m.get(a+O['ANIM_X_TO']))==-240
assert m.slide_callbacks[a]==(syms['slide_menu_on_scroll_done'],w)
assert m.nodes[m.nodes[w]['children'][0]]['focused']==0
m.advance(150); assert m.get(w+O['SLIDE_INDEX'])==1 and not m.slides; passed()
