#!/usr/bin/env python3
"""Run the real MIPS headset worker against bounded, simulated kernel I/O."""
import pathlib
source=pathlib.Path(__file__).with_name('patch.py').read_text()
exec(compile(source[:source.index('checks=0')], 'test/patch.py', 'exec'))

class KernelMachine(Machine):
    def __init__(self,stale=False):
        super().__init__(); self.stale=stale; self.hs=payload_syms['hs']; self.started=[]; self.sent=[]; self.closed=[]; self.reads=0; self.received=False; self.attempts=[]
        self.queue=[bytes([4,0,4,0,0x17,0,1,2,3]),bytes([4,0,4,0,6,0,0,2])]  # another report, then ears
        self.mock('strstr@GLIBC_2.0')
        self.pair=self.alloc(128); self.mac='AA:BB:CC:DD:EE:FF'
        self.u.mem_write(self.pair+4,self.mac.encode()+b'\0'); self.u.mem_write(self.pair+0x24,b'AirPods Pro\0')
        self.word(syms['pdeq_btpairlist'],self.alloc(4))
        for n in ('pthread_create@GLIBC_2.2','getBtLinkSize','getBtLinkItem','deque_size','deque_at',
                  'snprintf@GLIBC_2.0','socket@GLIBC_2.0','connect@GLIBC_2.0','select@GLIBC_2.0','getsockopt@GLIBC_2.0',
                  'fcntl@GLIBC_2.28','ioctl@GLIBC_2.0','open@GLIBC_2.0','read@GLIBC_2.0',
                  'recv@GLIBC_2.0','send@GLIBC_2.0','close@GLIBC_2.0','usleep@GLIBC_2.0'):
            self.handlers[syms[n]]='kernel:'+n.split('@')[0]
        self.byte(syms['g_bluetoothflag'],1); self.byte(syms['bt_linkstatus'],1)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('kernel:'): return super().hook(u,address,size,unused)
        name=name[7:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='snprintf':
            data=(self.text(c)%signed(d)).encode(); self.u.mem_write(a,data[:b-1]+b'\0'); ret=len(data)
        elif name=='pthread_create': self.started.append((c,d)); self.word(a,77)
        elif name=='getBtLinkSize': ret=1
        elif name=='getBtLinkItem': self.u.mem_write(c,self.mac.encode()+b'\0')
        elif name=='deque_size': ret=1
        elif name=='deque_at': ret=self.pair
        elif name=='socket': assert (a,b,c)==(31,5,0); ret=10
        elif name=='fcntl': assert (a,b,c)==(10,4,0x80)
        elif name=='connect':
            assert bytes(self.u.mem_read(b,c))[:10]==bytes([31,0,1,16,255,238,221,204,187,170]), bytes(self.u.mem_read(b,c)); ret=-1
        elif name=='getsockopt': assert (a,b,c)==(10,0xffff,0x1007); self.word(d,0)
        elif name=='send': self.sent.append(bytes(u.mem_read(b,c))); assert d==0x40; ret=c
        elif name=='open':
            assert b==0x80080
            self.attempts.append((self.now,self.text(a)))
            ret=11 if self.now>=12000 and self.text(a)=='/dev/input/event5' else -1
        elif name=='ioctl': assert b==0x40404506; self.u.mem_write(c,b'AirPods Pro (AVRCP)\0'); ret=19
        elif name=='select':  # fd_sets as words; the ready ones kept, as the kernel does
            self.now+=200
            if c: ret=1  # nonblocking connect completes
            else:
                asked=self.get(b); ready=asked&((1<<10 if not self.received else 0)|1<<11)
                self.word(b,ready); ret=bin(ready).count('1')
                if ready>>11&1 and self.reads: self.word(self.hs,self.get(self.hs)+1)  # disconnect after one input batch
                if not ready: self.now+=800  # the rest of a 1 s wait
        elif name=='recv':
            self.received=True
            if self.stale: self.word(self.hs,self.get(self.hs)+1)
            if self.queue: p=self.queue.pop(0); self.u.mem_write(b,p); ret=len(p)
            else: ret=-1  # EAGAIN: drained
        elif name=='read':
            if not self.reads:
                events=b''.join(struct.pack('<IIHHi',0,0,1,163,value) for value in (0,2,1))
                self.u.mem_write(b,events); ret=len(events)
            self.reads+=1
        elif name=='usleep': self.now+=a//1000
        elif name=='close': self.closed.append(a)
        self.returns(ret)
for stale in (False,True):
    m=KernelMachine(stale); m.call(address=payload_syms['headset_poll'],args=(0,0,0,0),gap=0)
    assert len(m.started)==1
    worker,generation=m.started[0]
    m.call(address=worker,args=(generation,0,0,0),gap=0,count=1_000_000)
    assert list(map(len,m.sent))==[16,14,10]
    assert m.get(m.hs+16)==(0 if stale else 5)  # one select drains to the ear report; generation checked after recv
    assert m.get(m.hs+4)==0 and 10 in m.closed
    if not stale:
        assert m.now>=12000 and 11 in m.closed
        assert m.get(m.hs+8)==1 and m.get(m.hs+20)==163  # only EV_KEY value 1 is queued
        assert any(now<12000 for now,path in m.attempts) and any(now>=12000 for now,path in m.attempts)
    # UI disconnect/reconnect invalidates the old queue and report before launching a worker.
    m.byte(syms['bt_linkstatus'],0); m.call(address=payload_syms['headset_poll'],args=(0,0,0,0),gap=200)
    m.byte(syms['bt_linkstatus'],1); m.call(address=payload_syms['headset_poll'],args=(0,0,0,0),gap=200)
    assert len(m.started)==2 and not m.get(m.hs+8) and not m.get(m.hs+16)
print('Headset worker: handshake, bounded nonblocking I/O, late discovery, release/repeat filtering, disconnect cleanup and stale reconnect reports passed.')
