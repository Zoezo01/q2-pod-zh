"""Bluetooth: LDAC quality, reconnects, auto-link adoption and the headset volume read-back."""
from q2emu import *

# LDAC: stock switchBtPriority sends quality 0x16 for both HQ and Standard; patched HQ sends 0x15.
for patched,want in ((True,(0x15,0x16,0x18)),(False,(0x16,0x16,0x18))):
    bt=Machine(patched); bt.mock('btctl_set_codec_priority','btctl_set_ldac_quality','btctl_set_sbc_quality')
    for row,quality in enumerate(want):
        assert bt.call(address=syms['switchBtPriority'],args=(row,0,0,0),gap=0)==1
        assert [c[1] for c in bt.calls if c[0]=='btctl_set_ldac_quality']==[quality], (patched,row)
    passed()
# A boot reconnect can arrive as the control thread starts, before openBluetooth's GPIO write.
from build import BT_INIT, BT_PARAMS
assert struct.unpack_from('<I',demo,fileoff(demo,BT_INIT[0]))[0]==jal(payload_syms['headset_bt_init'])
assert struct.unpack_from('<I',demo,fileoff(demo,BT_PARAMS[0]))[0]==0
# The old radio-power hook is gone: startup owns registration, and stock retains its toggle path.
assert struct.unpack_from('<I',demo,fileoff(demo,0x4fa3dc))[0]==0x0320f809

for result in (0,-7):
    bt_init=BtInitMachine(); bt_init.result=result; thread=bt_init.alloc(4)
    assert bt_init.call(address=payload_syms['headset_bt_init'],args=(thread,0,0,0),gap=0)==result
    assert bt_init.calls[0][0]=='initBluetoothParam'
    assert [c[:3] for c in bt_init.calls[1:]]==[
        ('btctl_set_prompt_callback',0x4f9d20,0),
        ('btctl_device_status_callback',payload_syms['headset_bt_status'],0),
        ('btctl_set_a2dp_callback',syms['btrecvA2dpFunc'],0x01020304),
        ('btctl_create_daemon_thread',thread,0)]
    assert bt_init.get(thread)==0x12345678
    passed()
# An incoming link while a link thread waits for the saved device (BT_STATUS) is adopted; auto-link
# skips while a listed one is connected (BT_AUTOLINK).
from build import BT_STATUS, BT_AUTOLINK
assert struct.unpack_from('<I',demo,fileoff(demo,BT_STATUS[0]))[0]==jal(payload_syms['headset_status_register'])
assert struct.unpack_from('<I',demo,fileoff(demo,BT_AUTOLINK[0]))[0]==jal(payload_syms['headset_bt_autolink'])

link=BtLinkMachine([]); link.call(address=payload_syms['headset_status_register'],args=(0,0,0,0),gap=0)
assert [c[:3] for c in link.calls]==[('btctl_device_status_callback',payload_syms['headset_bt_status'],0)]
link=BtLinkMachine([(a_mac,5)])  # 5 while the thread's Connect runs
link.call(address=payload_syms['headset_bt_status'],args=(3,link.string(b_mac),0,0),gap=0)
assert link.links==[[a_mac,4],[b_mac,3]] and 'device_status' not in [c[0] for c in link.calls]; passed()
# Otherwise stock's callback gets the event as libbtctl sent it: not connecting, thread ended or judged,
# already listed, unpaired, empty list, target connected or not yet taken by the thread (1), a thread
# for another device, other events.
for event,links,paired,connecting,saved in ((3,[(a_mac,5)],True,0,a_mac),(3,[(a_mac,5)],True,2,a_mac),
        (3,[(a_mac,5)],True,3,a_mac),(3,[(a_mac,5),(b_mac,1)],True,1,a_mac),(3,[(a_mac,5)],False,1,a_mac),
        (3,[],True,1,''),(3,[(a_mac,3)],True,1,a_mac),(3,[(a_mac,1)],True,1,a_mac),(3,[(a_mac,5)],True,1,'CC:CC:CC:CC:CC:CC'),
        (4,[(a_mac,5)],True,1,a_mac)):
    link=BtLinkMachine(links,paired,connecting,saved); addr=link.string(b_mac)
    link.call(address=payload_syms['headset_bt_status'],args=(event,addr,0x1234,0x5678),gap=0)
    assert link.links==[list(x) for x in links] and link.calls[-1]==('device_status',event,addr,0x1234,0x5678)
    passed()
for links,paired,want in (([],True,1),([],False,0),([(b_mac,3)],True,0),([(b_mac,4)],True,1),
                          ([(b_mac,5)],True,1),([(b_mac,3),(a_mac,5)],True,0)):
    link=BtLinkMachine(links,paired)
    assert link.call(address=payload_syms['headset_bt_autolink'],args=(link.string(a_mac),0,0,0),gap=0)==want
    passed()

ov=OutputVolumeMachine(); assert ov.output(1)==20  # first connection inherits the current level
ov.save(65); assert ov.config=={'VOLUME':'20','BT_VOLUME':'65'}
assert ov.output(0)==20; ov.save(13)
assert ov.output(1)==65 and ov.output(1)==65 and ov.output(2)==13
assert ov.output(0)==13 and ov.output(1)==65; passed()
# A fresh boot uses wired VOLUME; reconnect restores Bluetooth, including muted and capped levels.
for value,limit,want in (('0',100,0),('100',40,40),('37',100,37)):
    ov=OutputVolumeMachine({'VOLUME':'13','BT_VOLUME':value}); ov.byte(syms['g_volume'],13)
    ov.byte(syms['g_maxvolume'],limit)
    assert ov.output(1)==want and ov.output(0)==13; passed()
for value in ('','-1','101','999999999999999999','20junk',' 20'):
    ov=OutputVolumeMachine({'BT_VOLUME':value}); assert ov.output(1)==20; passed()
ov=OutputVolumeMachine(); ov.output(1); ov.save(7,'OTHER','VOLUME'); ov.save(8,'PLAYSET','OTHER')
assert ov.config=={'VOLUME':'7','OTHER':'8'}; passed()  # other writes keep their section/key

for left,right,want in ((0,0,0),(0,20,25),(20,0,25),(1,1,1),(20,20,25),(40,30,51),(50,50,64),(100,100,127),
                        (-5,-1,0),(101,200,127)):
    gain=(max(0,left)*100//max(left,right),max(0,right)*100//max(left,right)) if want else (left,right)  # AVRCP 0 isn't silent on every headset: keep stock's mute
    b=BtVolumeMachine(); assert b.volume(left,right)==[gain] and b.writes()==[want]
    assert b.volume(left,right)==[gain] and not b.writes(); passed()
# Control Device Volume off (the default): the Q2's volume is stock's gain alone, the device's own
# volume is never read or written, and the poll leaves it be.
b=BtVolumeMachine(link=False); assert b.volume(20,20)==[(20,20)] and b.volume(40,30)==[(40,30)]
b.poll(); b.poll(); assert not [c for c in b.calls if c[0].startswith('btctl_')]; passed()
# Volume 0 and back up: the headset gets 0 then 1, stock gain 0 then full.
b=BtVolumeMachine(); assert b.volume(0,0)==[(0,0)] and b.writes()==[0]
assert b.volume(1,1)==[(100,100)] and b.writes()==[1]; passed()
b=BtVolumeMachine(); b.byte(syms['g_volume'],0); b.poll()
assert b.writes()==[0] and [c[1:3] for c in b.calls if c[0]=='stock_btvol']==[(0,0)]; passed()
for cur in (-1,128,255):
    b=BtVolumeMachine(); b.absolute=cur
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
b=BtVolumeMachine(); b.write_result=-1
assert b.volume(20,30)==[(20,30)] and b.writes()==[38]; passed()
for name,value in (('g_bluetoothflag',0),('bt_linkstatus',0),('bt__recv_pageflag',1),('bt_showcoding',0)):
    b=BtVolumeMachine()
    (b.word if name=='bt_showcoding' else b.byte)(syms[name],value)
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
for way in (0,2):
    b=BtVolumeMachine(); b.way=way
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
b=BtVolumeMachine(); b.absolute=-1; b.poll()
assert not b.writes()
b.absolute=10; b.poll(O['BT_VOLUME_SLOW_MS']-1); assert not b.writes()  # no absolute volume yet: the slow pace
b.poll(1); assert b.writes()==[25]
b.absolute=5; b.poll(); assert not b.writes()  # exact rounded remote value
b.byte(syms['bt_linkstatus'],0); b.poll(); assert not b.writes()
b.byte(syms['bt_linkstatus'],1); b.word(syms['bt_showcoding'],0); b.poll(); assert not b.writes()
b.word(syms['bt_showcoding'],1); b.poll(); assert not b.writes(); passed()
b=BtVolumeMachine(); b.write_result=-1; b.poll()
assert b.writes()==[25] and [c[1:3] for c in b.calls if c[0]=='stock_btvol']==[(20,20)]
b.write_result=0; b.poll(); assert not b.writes()  # a refusal retries at the slow pace
b.poll(O['BT_VOLUME_SLOW_MS']-400); assert b.writes()==[25]; passed()
# Dark, the read-back slows down.
b=BtVolumeMachine(); b.poll(); b.calls=[]; b.byte(syms['g_backlight_status'],0); b.absolute=50
b.poll(); assert not any(c[0]=='btctl_transport_get_volume' for c in b.calls)
b.poll(O['BT_VOLUME_SLOW_MS']-400); assert any(c[0]=='btctl_transport_get_volume' for c in b.calls); passed()
# Dark with a volume limit, the headset's own keys are still held to it, every BT_VOLUME_LIMIT_MS.
b=BtVolumeMachine(); b.byte(syms['g_maxvolume'],60); b.poll(); b.byte(syms['g_backlight_status'],0); b.absolute=127
b.poll(); assert not b.writes()
b.poll(O['BT_VOLUME_LIMIT_MS']-400); assert b.writes()==[76] and b.u.mem_read(syms['g_volume'],1)[0]==60; passed()
# A headset that can't report absolute volume is asked again at the slow pace, lit too, not every 400 ms.
b=BtVolumeMachine(); b.absolute=-1; asks=[]
for _ in range(2*O['BT_VOLUME_SLOW_MS']//400): b.poll(); asks.append(sum(c[0]=='stock_btvol' for c in b.calls))
assert asks==[1,0,0,0,0]*2; passed()
# Native device_set_volume calls LocalVol and BtVol for the DAC/Bluetooth, USB's own
# setter for USB. g_output_way=0 avoids stock's fixed line-out branch; notify is covered too.
for way,soft in ((0,0),(1,0),(2,0),(2,1)):
    b=BtVolumeMachine(); b.handlers.pop(syms['device_set_volume'],None)
    b.byte(syms['g_output_way'],0); b.way=way; b.byte(syms['g_usbvol_mode'],soft)
    b.call(address=syms['device_set_volume'],args=(20,1,0,0),gap=0)
    gains=[c[1:3] for c in b.calls if c[0]=='stock_btvol']
    local=[c[1:3] for c in b.calls if c[0]=='mclSetLocalVol']
    usb=[c[1:3] for c in b.calls if c[0]=='mclUsbAudioSetVol']
    assert gains==([(100,100)] if way==1 else [(20,20)] if way==0 else [])
    assert local==([(20,20)] if way in (0,1) else [])
    assert usb==([(20,20)] if soft else [(100,100)]) if way==2 else not usb
    assert b.writes()==([25] if way==1 else [])
    assert any(c[0]=='notifyVolume' for c in b.calls); passed()

# Screen off, the UI loop idles SCREEN_OFF_SLEEP_MS before stock's own pacing; screen on, stock alone.

for light,want in ((1,[]),(0,[O['SCREEN_OFF_SLEEP_MS']])):
    s=Machine(); s.handlers[sleep_hook+12]='stock_sleep'; s.byte(syms['g_backlight_status'],light)
    assert s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
    assert [c[1] for c in s.calls if c[0]=='sleep_ms']==want and s.calls[-1][:2]==('stock_sleep',0x1234); passed()
    # The next pass with the backlight on repaints the whole screen once; staying on, nothing more.
    s.byte(syms['g_backlight_status'],1)
    for wake in (not light,False):
        s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)
        assert [c[1] for c in s.calls if c[0]=='widget_invalidate_force']==([s.wm] if wake else [])
    passed()
