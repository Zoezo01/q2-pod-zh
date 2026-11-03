#!/usr/bin/env python3
"""Execute linked Linux MCU ownership helpers; kernel services and MCU ACK modeled.

The separate test/mcu_park.py executes the MCU's actual idle helper. This suite
checks host error/lease/lifecycle behavior, not physical bus timing or silicon.
"""
import argparse, hashlib, importlib.util, json, pathlib, struct, sys
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE, UC_TLB_VIRTUAL
from unicorn.mips_const import UC_CPU_MIPS32_24KF, UC_MIPS_REG_0, UC_MIPS_REG_PC, UC_MIPS_REG_HI, UC_MIPS_REG_LO
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('q2_rotator_test',ROOT/'test/rotator.py')
reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
sys.path.insert(0,str(ROOT/'tools'))
import pwm,mcu_park
reg=lambda n:UC_MIPS_REG_0+n
REQ,ACK=0xb3429f00,0xb3429f04
INTEGER_REGS=[reg(n) for n in range(1,32)]+[UC_MIPS_REG_HI,UC_MIPS_REG_LO]

def clear_pending_branch(u,clean):
    # Restore a pristine integer CPU context after a completed transfer.
    # Memory is untouched; preserve every integer architectural register.
    saved=[u.reg_read(r) for r in INTEGER_REGS]
    u.context_restore(clean)
    for r,v in zip(INTEGER_REGS,saved):u.reg_write(r,v)

def branch_control():
    # Independent valid MIPS sequence matching init's failure: a taken branch
    # with a store delay slot lands directly on JAL. No branch is in a delay slot.
    # Unicorn 2.1.4 continuous execution raises exception 20 at that JAL.
    words={0x01001000:0x14200004,0x01001004:0xac627440,
           0x01001014:0x0d400940,0x01001018:0}
    def fresh():
        u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
        u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF);u.ctl_set_tlb_mode(UC_TLB_VIRTUAL)
        u.mem_map(0x01000000,0x10000);u.mem_map(0x05000000,0x10000)
        clean=u.context_save()
        for a,w in words.items():u.mem_write(a,struct.pack('<I',w))
        for n,v in ((1,1),(2,0x1234),(3,0x01000000)):u.reg_write(reg(n),v)
        return u,clean
    u,_=fresh()
    try:
        u.emu_start(0x01001000,0x05002500,count=10)
        continuous='PASS'
    except UcError as exc:continuous=str(exc)
    u,clean=fresh()
    u.emu_start(0x01001000,0x05002500,count=1)
    pc=u.reg_read(UC_MIPS_REG_PC);assert pc==0x01001014
    clear_pending_branch(u,clean)
    u.emu_start(pc,0x05002500,count=1)
    assert u.reg_read(UC_MIPS_REG_PC)==0x05002500
    assert u.reg_read(reg(31))==0x0100101c
    assert bytes(u.mem_read(0x01007440,4))==struct.pack('<I',0x1234)
    for a,w in words.items():assert bytes(u.mem_read(a,4))==struct.pack('<I',w)
    return dict(continuous=continuous,branch_safe='PASS',instructions_unchanged=True)


class Machine:
    def __init__(self,data,image,base=0x01001000,init_base=None):
        self.uc=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        self.uc.ctl_set_tlb_mode(UC_TLB_VIRTUAL)
        self.clean_cpu=self.uc.context_save()
        for addr,size in [(0x02000000,0x10000),(0x04000000,0x10000),(0x05000000,0x10000),
                          (0xb3420000,0x10000),(0xb34c0000,0x1000)]: self.uc.mem_map(addr,size)
        self.handlers={};self.externals={};self.events=[];self.locks=set()
        self.clocks={0x02003000+i*16:[0,0,0] for i in range(3)} # get/prepare/enable
        self.fail=None;self.ack_mode='success';self.pending_ack=None;self.init_error=0
        self.ready_count=0;self.stock_init_completed=False
        self.symbols,_=reader.Module(data).load(self,base,init_base=init_base)
        self.halt=0x0500f000
        for name in ['mcu_init','mcu_bootup','mcu_reset','mcu_shutdown','mcu_write_data','mcu_ioctl']:
            self.handlers[self.symbols['q2_stock_'+name]]='stock_'+name
        self.uc.mem_write(0xb3422000,image)
        self.write(0xb3421030,1)
        self.uc.hook_add(UC_HOOK_CODE,self.code)
        self.uc.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,self.memory)
        self.steps=0
    def external(self,name):
        if name not in self.externals:
            if name=='__stack_chk_guard': addr=0x0500e000
            else:
                addr=0x05000000+len(self.externals)*0x100
                self.handlers[addr]=name
            self.externals[name]=addr
        return self.externals[name]
    def read(self,p):return struct.unpack('<I',self.uc.mem_read(p,4))[0]
    def write(self,p,v):self.uc.mem_write(p,struct.pack('<I',v&0xffffffff))
    def value(self,name):return self.read(self.symbols[name])
    def text(self,p):return bytes(self.uc.mem_read(p,80)).split(b'\0',1)[0].decode()
    def code(self,u,addr,size,_):
        self.steps+=1
        assert addr not in self.handlers,'stepped past modeled service boundary'
    def memory(self,u,access,addr,size,value,_):
        if 0xb34c0000<=addr<0xb34c1000:
            assert all(v[2] for v in self.clocks.values()),'PWM access without clocks'
        if access==UC_MEM_WRITE and addr in (REQ,ACK):
            self.events.append(('shared',addr,value))
            if addr==REQ:
                if value==0:assert all(v[2] for v in self.clocks.values()),'wake without clocks'
                self.pending_ack=value
    def call(self,addr):
        name=self.handlers[addr];u=self.uc;a=[u.reg_read(reg(i)) for i in range(4,8)]
        self.events.append((name,*a));r=0
        if name=='__mutex_init':pass
        elif name=='mutex_lock':assert a[0] not in self.locks;self.locks.add(a[0])
        elif name=='mutex_unlock':assert a[0] in self.locks,(name,hex(a[0]),[hex(x) for x in self.locks],self.events[-8:]);self.locks.remove(a[0])
        elif name=='clk_get':
            i=['mux_pwm','div_pwm','gate_pwm'].index(self.text(a[1]));r=0x02003000+i*16
            if self.fail==(name,i):r=-5;self.fail=None
            else:self.clocks[r][0]+=1
        elif name in ('clk_prepare','clk_enable','clk_disable','clk_unprepare','clk_put'):
            i=(a[0]-0x02003000)//16;state=self.clocks[a[0]]
            if self.fail==(name,i):r=-5;self.fail=None
            elif name=='clk_prepare':assert state[0] and not state[1];state[1]+=1
            elif name=='clk_enable':assert state[1] and not state[2];state[2]+=1
            elif name=='clk_disable':
                assert state[2];state[2]-=1
                # Successful gating requires an actual park acknowledgement.
                if self.value('q2_mcu_running'):assert self.read(ACK)==1
            elif name=='clk_unprepare':assert state[1] and not state[2];state[1]-=1
            elif name=='clk_put':assert state[0] and not any(state[1:]);state[0]-=1
        elif name=='usleep_range':
            if self.ack_mode=='success' and self.pending_ack is not None:
                self.write(ACK,self.pending_ack);self.pending_ack=None
        elif name=='q2_pwm_mcu_ready':
            assert all(v==[1,1,1] for v in self.clocks.values()),'ready before all clock references owned'
            assert self.stock_init_completed and self.init_error==0,'ready before successful stock init'
            self.ready_count+=1
            assert self.symbols['q2_mcu_lock'] in self.locks,'ready outside init serialization'
        elif name=='stock_mcu_init':r=self.init_error;self.stock_init_completed=(r==0)
        elif name=='stock_mcu_shutdown':self.write(0xb3421030,1)
        elif name in ('stock_mcu_bootup','stock_mcu_reset'):
            assert self.read(0xb3421030)&1,'not reset before handshake initialization'
            assert self.read(ACK)==0,'stale ACK survived reset'
            assert all(v[2] for v in self.clocks.values()),'boot without clocks'
            self.write(0xb3421030,0);self.pending_ack=self.read(REQ)
        elif name=='stock_mcu_write_data':
            assert all(v[2] for v in self.clocks.values())
            assert not self.value('q2_mcu_known') or self.value('q2_mcu_fault') or self.read(ACK)==0
            r=a[1]
        elif name=='stock_mcu_ioctl':
            assert self.read(0xb3421030)&1,'firmware copied while executing'
            assert self.value('q2_mcu_known')==0
        else:raise AssertionError(('Unexpected service',name))
        ra=u.reg_read(reg(31))
        for n in (2,3,4,5,6,7,8,9,10,11,12,13,14,15,24,25):u.reg_write(reg(n),0x5a000000+n)
        u.reg_write(reg(2),r&0xffffffff)
        return ra
    def run(self,name,*args):
        u=self.uc;u.reg_write(reg(29),0x04008000);u.reg_write(reg(31),self.halt)
        preserved={i:0x60000000+i for i in (*range(16,24),28,30)}
        for i,v in preserved.items():u.reg_write(reg(i),v)
        for i in range(4):u.reg_write(reg(4+i),args[i] if i<len(args) else 0)
        pc=self.symbols[name]
        for _ in range(500000):
            if pc==self.halt:break
            if pc in self.handlers:
                pc=self.call(pc);clear_pending_branch(u,self.clean_cpu);continue
            word=self.read(pc);opcode=word>>26
            transfer=opcode in (1,2,3,4,5,6,7,20,21,22,23) or opcode==0 and word&63 in (8,9)
            # A single branch step executes its original delay instruction.
            u.emu_start(pc,self.halt,count=1)
            pc=u.reg_read(UC_MIPS_REG_PC)
            if transfer:clear_pending_branch(u,self.clean_cpu)
        else:raise AssertionError('execution budget exhausted')
        assert u.reg_read(reg(29))==0x04008000
        assert all(u.reg_read(reg(i))==v for i,v in preserved.items()),name
        assert not self.locks
        v=u.reg_read(reg(2));return v if v<0x80000000 else v-0x100000000
    def init(self):assert self.run('q2_mcu_init')==0;assert self.ready_count==1
    def boot(self):assert self.run('q2_mcu_bootup')==0
    def off(self):assert all(v[1:]==[0,0] for v in self.clocks.values())
    def on(self):assert all(v[1:]==[1,1] for v in self.clocks.values())

def check(module,image,base):
    count=0
    def new():return Machine(module,image,base)
    m=new();m.init();m.on();m.boot();m.off();assert m.read(ACK)==1;count+=1
    for _ in range(5):
        assert m.run('q2_mcu_write_data',0x02001000,7,100,0)==7;m.off();assert m.read(ACK)==1;count+=1
    m.run('q2_mcu_reset');m.off();count+=1
    m.run('q2_mcu_shutdown');m.off();assert not m.value('q2_mcu_running');count+=1
    m.boot();m.off();count+=1
    for name in ('clk_get','clk_prepare','clk_enable'):
        for i in range(3):
            m=new();m.fail=(name,i);assert m.run('q2_mcu_init')==-5
            assert all(v==[0,0,0] for v in m.clocks.values());assert m.ready_count==0;count+=1
    m=new();m.init_error=-12;assert m.run('q2_mcu_init')==-12
    assert all(v==[0,0,0] for v in m.clocks.values());assert m.ready_count==0;count+=1
    for name in ('clk_prepare','clk_enable'):
        for i in range(3):
            m=new();m.init();m.boot();m.fail=(name,i)
            assert m.run('q2_mcu_write_data',0,7,100,0)==-5;m.off()
            assert m.run('q2_mcu_write_data',0,7,100,0)==7;m.off();count+=1
    m=new();m.init();m.ack_mode='timeout';m.boot();m.on();assert m.value('q2_mcu_fault')==1
    assert m.run('q2_mcu_write_data',0,7,100,0)==7;m.on();count+=1
    m=new();m.init();m.boot();m.ack_mode='timeout'
    assert m.run('q2_mcu_write_data',0,7,100,0)==-110;m.on();assert m.value('q2_mcu_fault')==1
    assert m.run('q2_mcu_write_data',0,7,100,0)==7;m.on();count+=1
    m=new();m.init();m.uc.mem_write(0xb3422100,b'\xff');m.boot();m.on();assert not m.value('q2_mcu_known');count+=1
    m=new();m.write(0xb3421030,0);m.init();m.on();assert m.value('q2_mcu_fault');m.boot();m.on();count+=1
    m=new();m.init();m.boot();assert m.run('q2_mcu_ioctl',0,0x80044d73,0,0)==0
    m.on();assert not m.value('q2_mcu_known');assert not m.value('q2_mcu_running');m.boot();m.off();count+=1
    # An active Linux backlight on channel0 does not block the MCU lease release.
    m=new();m.init();m.write(0xb34c0018,1);m.boot();m.off();count+=1
    # Residual MCU-channel activity vetoes release even after ACK.
    for addr in (0xb34c0018,0xb34c0108,0xb34c0128):
        m=new();m.init();m.write(addr,2);m.boot();m.on();count+=1
    # Stale park ACK must be cleared under reset before the MCU starts.
    m=new();m.init();m.write(ACK,1);m.ack_mode='timeout';m.boot();m.on();assert m.value('q2_mcu_fault');count+=1
    # Initial mutable data must be pristine before trust can be established.
    m=new();m.init();m.uc.mem_write(0xb3422000+0x3cc8,b'\xff');m.boot();m.on()
    assert not m.value('q2_mcu_known');count+=1
    # Lifecycle ioctl returns clock failure, unlike stock's discarded void return.
    m=new();m.init();m.boot();m.fail=('clk_enable',2)
    assert m.run('q2_mcu_ioctl',0,0x20004d72,0,0)==-5;count+=1
    return count

def check_init_entry(data,image):
    import module_regions
    for core,init in zip(module_regions.MODULE_BASES,module_regions.INIT_BASES):
        m=Machine(data,image,core,init)
        # Execute original exported module-init entry and the new veneer;
        # only stock MCU transport remains an explicit service mock.
        assert m.symbols['init_module']>>28==init>>28
        assert m.symbols['q2_mcu_init']>>28==core>>28
        assert m.run('init_module',0x11,0x22,0x33,0x44)==0
        assert m.ready_count==1
        stock=[e for e in m.events if e[0]=='stock_mcu_init']
        assert stock==[('stock_mcu_init',0x11,0x22,0x33,0x44)],stock
        m.on()
    return len(module_regions.MODULE_BASES)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('stockfs',type=pathlib.Path);ap.add_argument('--json',type=pathlib.Path);a=ap.parse_args()
    h,_=mcu_park.compile_helper();image=mcu_park.patch_image((a.stockfs/'usr/data/libmcu-bare.bin').read_bytes(),h)
    data=pwm.patch_mcu((a.stockfs/'module_driver/soc_mcu.ko').read_bytes(),image,len(h))
    import module_regions
    regions=module_regions.validate(reader,data)
    bases=[0x01001000,0x01007800,0x0100f800]
    control=branch_control()
    cases=sum(check(data,image,b) for b in bases)
    init_entries=check_init_entry(data,image)
    out=dict(status='PASS',cases=cases,split_init_entries=init_entries,load_bases=bases,relocation_regions=regions,branch_control=control,sha256=hashlib.sha256(data).hexdigest(),limitations=['Kernel service calls, stock MCU transport, and MCU ACK delivery modeled','Actual linked helper instructions unchanged; integer CPU context reset after completed transfers to avoid independently reproduced Unicorn chaining defect','Physical bus/clock/wake behavior requires device validation'])
    if a.json:a.json.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out))
if __name__=='__main__':main()
