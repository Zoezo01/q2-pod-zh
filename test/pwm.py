#!/usr/bin/env python3
"""Execute exact stock/patched PWM MIPS instructions, with modeled kernel/MMIO boundaries.

Uses test/rotator.py's independent ELF relocator and branch-safe instruction
stepping. No device access. This does not model silicon DMA or real IRQ delivery;
an in-flight IRQ is explicitly drained through the actual stock IRQ instructions.
"""
from pathlib import Path
import argparse
import hashlib
import json
import importlib.util
import struct
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('q2_rotator_fixture', REPO/'test/rotator.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.mips_const import UC_CPU_MIPS32_24KF, UC_MIPS_REG_PC, UC_MIPS_REG_HI, UC_MIPS_REG_LO
reg, signed = fixture.reg, fixture.signed
STOCK = PATCHED = None
PWM = 0x134c0000
CLOCKS = ('mux_pwm','div_pwm','gate_pwm')


class Machine(fixture.Machine):
    def __init__(self, data=None, base=0x01001000):
        if data is None:data=PATCHED
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        for address,size in ((0x02000000,0x10000),(0x04000000,0x10000),
                             (0x05000000,0x10000),(PWM,0x1000)):
            self.uc.mem_map(address,size)
        self.halt=0x0500f000
        self.externals={}; self.handlers={}; self.events=[]; self.mmio=[]
        self.original_mmio=[]; self.guard_mmio=[]
        self.mutexes=set(); self.spinlocks=set(); self.clocks={}; self.clock_names={}
        self.irq_registered=False; self.irq_off=False; self.irq_drained=0
        self.fail=None; self.gpio_error=0; self.drain_irq=False; self.on_kernel=None
        self.depth=0; self.gpios={}; self.next_alloc=0x02006000
        module=fixture.Module(data)
        self.symbols,self.bss=module.load(self,base)
        self.guard_range=(0,0)
        if "q2_pwm_leave" in module.named:
            size=module.symbols[module.named["q2_pwm_leave"]][2]
            self.guard_range=(self.symbols["q2_pwm_leave"],self.symbols["q2_pwm_leave"]+size)
        self.uc.hook_add(UC_HOOK_CODE,self.code)
        self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE,self.memory)
        self.write(0x0500e000,0x1234abcd)
        self.pdev=0x02002000; self.cfg=0x02001000; self.label=0x02001100
        self.uc.mem_write(self.label,b'test_pwm\0')
        self.configure()

    def configure(self, frequency=10000, levels=100, polarity=1):
        self.uc.mem_write(self.cfg,struct.pack('<6I',polarity,0,0,frequency,levels,0))

    def cstring(self,address):
        return bytes(self.uc.mem_read(address,128)).split(b'\0',1)[0].decode()

    def clock_on(self):
        return all(self.clocks.get(n,[0,0])[1] > 0 for n in CLOCKS)

    def policy(self,name):
        return self.read(self.symbols['q2_pwm_'+name])

    def memory(self,u,access,address,size,value,unused):
        physical=address & 0x1fffffff
        if PWM <= physical < PWM+0x1000:
            assert self.clock_on(), ('MMIO with clock disabled',hex(u.reg_read(UC_MIPS_REG_PC)),hex(physical))
            off=physical-PWM
            record=(off,access==UC_MEM_WRITE,value if access==UC_MEM_WRITE else self.read(physical))
            self.mmio.append(record)
            pc=u.reg_read(UC_MIPS_REG_PC)
            if self.guard_range[0] <= pc < self.guard_range[1]:
                assert access!=UC_MEM_WRITE and off in (0x18,0x108,0x128)
                self.guard_mmio.append(record)
            else:
                self.original_mmio.append(record)
            if access==UC_MEM_WRITE and off==0x10:
                self.write(PWM+0x18,self.read(PWM+0x18)|value)
            if access==UC_MEM_WRITE and off==0x14:
                self.write(PWM+0x18,self.read(PWM+0x18)&~value)
            if access==UC_MEM_WRITE and off==0x134:
                self.write(PWM+0x130,self.read(PWM+0x130)&~value)

    def kernel_call(self,address):
        u=self.uc; name=self.handlers[address]
        args=[u.reg_read(reg(n)) for n in range(4,8)]
        self.events.append((name,*args))
        if self.on_kernel: self.on_kernel(name,args)
        result=0
        if name in ('__stack_chk_fail','__assert','warn_slowpath_fmt'):
            raise AssertionError(('kernel assertion',name,args))
        if name=='clk_get':
            cname=self.cstring(args[1])
            if cname not in self.clocks:
                self.clocks[cname]=[0,0]
                ptr=0x02003000+len(self.clocks)*0x100
                self.clock_names[ptr]=cname
            result=next(p for p,n in self.clock_names.items() if n==cname)
        elif name=='clk_get_rate': result=120000000
        elif name in ('clk_prepare','clk_enable','clk_disable','clk_unprepare','clk_put'):
            cname=self.clock_names[args[0]]; refs=self.clocks[cname]
            if self.fail and self.fail[:2]==(name,cname):
                result=self.fail[2]; self.fail=None
            elif name=='clk_prepare': refs[0]+=1
            elif name=='clk_enable':
                assert refs[0]>0,(name,cname,refs); refs[1]+=1
            elif name=='clk_disable':
                assert refs[1]>0,(name,cname,refs)
                assert not self.irq_registered or self.irq_off, 'clock down before IRQ drain'
                refs[1]-=1
            elif name=='clk_unprepare':
                assert refs[0]>0 and refs[1]==0,(name,cname,refs); refs[0]-=1
            else: assert refs==[0,0],(name,cname,refs)
        elif name=='mutex_lock':
            assert args[0] not in self.mutexes,('recursive mutex',hex(args[0]))
            self.mutexes.add(args[0])
        elif name=='mutex_unlock':
            assert args[0] in self.mutexes,('unlock unlocked',hex(args[0]))
            self.mutexes.remove(args[0])
        elif name=='_raw_spin_lock_irqsave':
            assert args[0] not in self.spinlocks
            self.spinlocks.add(args[0]); result=0xdeadbeef
        elif name=='_raw_spin_unlock_irqrestore':
            assert args[0] in self.spinlocks and args[1]==0xdeadbeef
            self.spinlocks.remove(args[0])
        elif name=='request_threaded_irq':
            assert args[0]==14 and args[1]==self.symbols['pwm_irq_handler']
            assert args[2:]==[0,0]
            self.irq_registered=True
        elif name=='disable_irq':
            assert args[0]==14 and self.irq_registered and not self.irq_off
            if self.drain_irq:
                self.drain_irq=False
                self.write(PWM+0x130,3)
                self.nested('pwm_irq_handler',14,0)
                self.irq_drained+=1
                assert self.read(PWM+0x130)==0
            self.irq_off=True
        elif name=='enable_irq':
            assert args[0]==14 and self.irq_registered and self.irq_off
            assert self.clock_on(), 'IRQ enabled before clocks'
            self.irq_off=False
        elif name=='free_irq':
            assert args[:2]==[14,0] and self.irq_registered
            self.irq_registered=False
        elif name=='gpio_request': result=self.gpio_error; self.gpio_error=0
        elif name=='gpio_set_func': self.gpios[args[0]]=args[1]
        elif name=='gpio_to_str': result=self.label
        elif name=='str_to_gpio': result=64
        elif name=='memset':
            self.uc.mem_write(args[0]&0x1fffffff,bytes([args[1]&255])*args[2]); result=args[0]
        elif name in ('__kmalloc','dma_alloc_attrs'):
            result=self.next_alloc; self.next_alloc+=0x400
            if name=='dma_alloc_attrs': self.write(args[2],result)
        elif name=='prepare_to_wait_event': result=0
        elif name=='schedule':
            raise AssertionError('unexpected unbounded DMA wait')
        elif name not in ('__mutex_init','__init_waitqueue_head','misc_register','misc_deregister',
                          'clk_set_parent','clk_set_rate','gpio_free','usleep_range','printk',
                          '__wake_up','kfree','dma_free_attrs','init_wait_entry','finish_wait',
                          'platform_device_register','platform_device_unregister',
                          '__platform_driver_register','platform_driver_unregister'):
            raise AssertionError(('unmodeled kernel call',name,args))
        u=self.uc
        for n in (2,3,4,5,6,7,8,9,10,11,12,13,14,15,24,25):
            u.reg_write(reg(n),0x5a000000+n)
        u.reg_write(reg(2),result&0xffffffff)
        return u.reg_read(reg(31))

    def run(self,name,*args):
        stack=0x0400c000-self.depth*0x2000
        self.uc.reg_write(reg(29),stack); self.uc.reg_write(reg(31),self.halt)
        preserved={n:0x60000000+n for n in (*range(16,24),28,30)}
        for n,value in preserved.items(): self.uc.reg_write(reg(n),value)
        for n,value in zip(range(4,8),(*args,0,0,0,0)[:4]):self.uc.reg_write(reg(n),value&0xffffffff)
        start=self.symbols[name]
        for _ in range(20000):
            if start==self.halt:break
            if start in self.handlers:
                start=self.kernel_call(start); self.fresh_cpu();continue
            word=self.read(start); opcode=word>>26
            transfer=opcode in (1,2,3,4,5,6,7,20,21,22,23) or opcode==0 and word&63 in (8,9)
            self.uc.emu_start(start,self.halt,count=1)
            start=self.uc.reg_read(UC_MIPS_REG_PC)
            if transfer:self.fresh_cpu()
        else:raise AssertionError(('instruction budget',name,hex(start)))
        assert self.uc.reg_read(reg(29))==stack,(name,'stack')
        assert all(self.uc.reg_read(reg(n))==v for n,v in preserved.items()),(name,'preserved')
        return signed(self.uc.reg_read(reg(2)))

    def nested(self,name,*args):
        regs=[reg(n) for n in range(1,32)]+[UC_MIPS_REG_HI,UC_MIPS_REG_LO]
        saved={r:self.uc.reg_read(r) for r in regs}
        self.depth+=1
        try:return self.run(name,*args)
        finally:
            self.depth-=1
            for r,v in saved.items():self.uc.reg_write(r,v)

    def probe(self,ready=True):
        assert self.run('ingenic_pwm_probe',self.pdev)==0
        assert not self.mutexes and not self.spinlocks
        if ready and 'q2_pwm_mcu_ready' in self.symbols:
            self.run('q2_pwm_mcu_ready')
        return self

    def setup(self,ch=0):
        assert self.run('pwm2_request',64+ch,self.label)==ch
        self.configure()
        assert self.run('pwm2_config',ch,self.cfg)==0
        return self

    def remove(self):
        assert self.run('ingenic_pwm_remove',self.pdev)==0
        assert not self.irq_registered
        assert all(v==[0,0] for v in self.clocks.values())


class Checks(unittest.TestCase):
    def test_stock_positive_control_and_patched_mmio_parity(self):
        old=Machine(STOCK).probe().setup()
        new=Machine().probe().setup()
        for level in (0,20,50,100,50,0):
            self.assertEqual(old.run('pwm2_set_level',0,level),new.run('pwm2_set_level',0,level))
            self.assertEqual(old.gpios,new.gpios)
            self.assertEqual(old.original_mmio,new.original_mmio)
            self.assertTrue(new.guard_mmio)
        self.assertFalse(new.clock_on())
        self.assertTrue(new.irq_off)
        old.remove();new.remove()

    def test_bootstrap_retains_clock_until_mcu_owns_references(self):
        m=Machine().probe(ready=False).setup()
        self.assertTrue(m.clock_on());self.assertFalse(m.irq_off)
        self.assertEqual(m.policy('mcu_owned'),0)
        m.run('pwm2_set_level',0,0)
        self.assertTrue(m.clock_on())
        m.run('q2_pwm_mcu_ready')
        self.assertEqual(m.policy('mcu_owned'),1)
        m.run('pwm2_set_level',0,0)
        self.assertFalse(m.clock_on())
        m.remove()

    def test_request_and_config_gate_inactive_clock(self):
        m=Machine().probe()
        self.assertTrue(m.clock_on())
        m.setup()
        self.assertFalse(m.clock_on());self.assertTrue(m.irq_off)
        self.assertEqual(m.policy('calls'),0)
        self.assertEqual(m.run('pwm2_config',0,m.cfg),0)
        self.assertFalse(m.clock_on())
        m.remove()

    def test_partial_level_holds_and_endpoints_release(self):
        m=Machine().probe().setup()
        for level,on in ((20,True),(20,True),(100,False),(50,True),(0,False),(0,False)):
            self.assertEqual(m.run('pwm2_set_level',0,level),0)
            self.assertEqual(m.clock_on(),on)
            self.assertEqual(m.irq_off,not on)
            self.assertEqual(m.policy('calls'),0)
        m.remove()

    def test_two_channels_keep_other_active(self):
        m=Machine().probe().setup(0).setup(2)
        for ch,level,on in ((0,20,True),(2,40,True),(0,0,True),(2,0,False)):
            self.assertEqual(m.run('pwm2_set_level',ch,level),0)
            self.assertEqual(m.clock_on(),on)
        m.remove()

    def test_validation_errors_release(self):
        m=Machine().probe()
        self.assertEqual(m.run('pwm2_request',1000,m.label),-1)
        m.setup()
        for name,args in [('pwm2_set_level',(0,101)),('pwm2_set_level',(16,2)),
                          ('pwm2_release',(16,))]:
            self.assertEqual(m.run(name,*args),-1)
            self.assertFalse(m.clock_on())
            self.assertEqual(m.policy('calls'),0)
        m.configure(frequency=0)
        self.assertEqual(m.run('pwm2_config',0,m.cfg),-1)
        m.remove()

    def test_stock_gpio_request_failure_negative_control(self):
        # Both images retain this stock defect: failed gpio_request unlocks
        # pwmdata-52 instead of the channel lock. Do not silently model success.
        for data in (STOCK,PATCHED):
            with self.subTest(patched=data is PATCHED):
                m=Machine(data).probe();m.gpio_error=-16
                with self.assertRaisesRegex(AssertionError,'unlock unlocked'):
                    m.run('pwm2_request',64,m.label)
                self.assertEqual(m.events[-1][:2],
                    ('mutex_unlock',m.symbols['pwmdata']-52))
                self.assertIn(m.symbols['pwmdata']+72,m.mutexes)

    def test_untracked_hardware_activity_retains_clock_refs(self):
        for offset in (0x18,0x108,0x128):
            with self.subTest(offset=hex(offset)):
                m=Machine().probe().setup()
                # Model activity from another master, absent Linux pwmdata.
                m.write(PWM+offset,2)
                m.run('pwm2_config',0,m.cfg)
                self.assertTrue(m.clock_on());self.assertTrue(m.irq_off)
                self.assertEqual(m.policy('calls'),0)
                m.write(PWM+offset,0)
                m.run('pwm2_config',0,m.cfg)
                self.assertFalse(m.clock_on())
                m.remove()

    def test_mmio_guard_negative_control(self):
        m=Machine().probe().setup()
        # Direct stock handler invocation while gated must trip the oracle.
        with self.assertRaisesRegex(AssertionError,'MMIO with clock disabled'):
            m.run('pwm_irq_handler',14,0)

    def test_each_wake_clock_failure_rolls_back_and_retries(self):
        for operation in ('clk_prepare','clk_enable'):
            for clock in CLOCKS:
                with self.subTest(operation=operation,clock=clock):
                    m=Machine().probe().setup()
                    m.fail=(operation,clock,-5)
                    before=len(m.mmio)
                    self.assertEqual(m.run('pwm2_set_level',0,20),-5)
                    self.assertEqual(len(m.mmio),before)
                    self.assertFalse(m.clock_on());self.assertTrue(m.irq_off)
                    self.assertTrue(all(m.clocks[n]==[0,0] for n in CLOCKS))
                    self.assertEqual(m.policy('calls'),0)
                    self.assertEqual(m.policy('errors'),1)
                    self.assertEqual(m.run('pwm2_set_level',0,20),0)
                    self.assertTrue(m.clock_on())
                    self.assertEqual(m.run('pwm2_set_level',0,0),0)
                    m.remove()

    def test_probe_ignored_clock_error_is_recorded_and_repaired(self):
        for operation in ('clk_prepare','clk_enable'):
            for clock in CLOCKS:
                with self.subTest(operation=operation,clock=clock):
                    m=Machine();m.fail=(operation,clock,-5);m.probe()
                    m.setup()
                    self.assertTrue(all(m.clocks[n]==[0,0] for n in CLOCKS))
                    m.remove()

    def test_linux_dma_entry_pins_even_if_invalid(self):
        for name,args in [('pwm2_dma_init',(16,0)),('pwm2_dma_update',(16,0,0,0)),
                          ('pwm2_dma_disable_loop',(16,))]:
            with self.subTest(name=name):
                m=Machine().probe().setup()
                self.assertEqual(m.run(name,*args),-1)
                self.assertEqual(m.policy('dma_pinned'),1)
                self.assertTrue(m.clock_on());self.assertFalse(m.irq_off)
                m.run('pwm2_set_level',0,0)
                self.assertTrue(m.clock_on())
                m.remove()

    def test_inflight_real_irq_drains_before_clock_down(self):
        m=Machine().probe().setup()
        m.run('pwm2_set_level',0,20)
        m.drain_irq=True
        begin=len(m.events)
        self.assertEqual(m.run('pwm2_set_level',0,0),0)
        self.assertEqual(m.irq_drained,1)
        events=[e[0] for e in m.events[begin:]]
        self.assertLess(events.index('disable_irq'),events.index('__wake_up'))
        self.assertLess(events.index('__wake_up'),events.index('clk_disable'))
        self.assertEqual(m.original_mmio[-1][:2],(0x134,True))
        self.assertEqual([x[0] for x in m.guard_mmio[-3:]],[0x18,0x108,0x128])
        self.assertFalse(m.clock_on())
        m.remove()

    def test_nested_other_channel_cannot_clear_outer_lease(self):
        m=Machine().probe().setup(0).setup(2);fired=False
        def interleave(name,args):
            nonlocal fired
            if not fired and name=='gpio_set_func' and args[0]==64:
                fired=True
                self.assertEqual(m.policy('calls'),1)
                m.nested('pwm2_set_level',2,0)
                self.assertEqual(m.policy('calls'),1)
                self.assertTrue(m.clock_on())
        m.on_kernel=interleave
        self.assertEqual(m.run('pwm2_set_level',0,20),0)
        self.assertTrue(fired);self.assertTrue(m.clock_on())
        self.assertEqual(m.policy('calls'),0)
        m.on_kernel=None
        m.run('pwm2_set_level',0,0);m.remove()

    def test_waveform_timeout_keeps_active_clock(self):
        m=Machine().probe().setup()
        m.run('pwm2_set_level',0,20)
        m.write(PWM+0x24,1)
        self.assertEqual(m.run('pwm2_set_level',0,40),-1)
        self.assertTrue(m.clock_on())
        self.assertEqual(m.policy('calls'),0)
        m.write(PWM+0x24,0)
        m.run('pwm2_set_level',0,0);m.remove()

    def test_alternate_relocation_base(self):
        m=Machine(base=0x01201000).probe().setup()
        m.run('pwm2_set_level',0,50)
        self.assertTrue(m.clock_on())
        m.run('pwm2_release',0)
        self.assertFalse(m.clock_on())
        m.remove()


def main():
    global STOCK,PATCHED
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stockfs',type=Path)
    ap.add_argument('--json',type=Path)
    a=ap.parse_args()
    import pwm as patcher
    STOCK=(a.stockfs/'module_driver/soc_pwm.ko').read_bytes()
    PATCHED=patcher.patch_pwm(STOCK)
    import module_regions
    regions=module_regions.validate(fixture,PATCHED)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    out=dict(status='PASS' if result.wasSuccessful() else 'FAIL',cases=result.testsRun,relocation_regions=regions,
             sha256=hashlib.sha256(PATCHED).hexdigest(),
             limitations=['Kernel calls and hardware register effects modeled',
                          'Integer CPU context recreated after completed transfers to avoid Unicorn chaining artifacts',
                          'Actual stock and wrapper instructions executed; physical DMA and IRQ delivery require device validation',
                          'Known stock GPIO allocation failure wrong-mutex unlock retained as a negative control'])
    if a.json:a.json.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out))
    if not result.wasSuccessful():raise SystemExit(1)

if __name__=='__main__':
    main()
