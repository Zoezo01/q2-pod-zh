#!/usr/bin/env python3
"""Execute MCU instructions; WAIT/IRQ arrival and dispatch are modeled boundaries."""
import argparse, importlib.util, json, os, struct, sys, unittest
from pathlib import Path
from unicorn import UC_MEM_WRITE
from unicorn import mips_const as c
REPO = Path(os.environ.get('Q2_REPO', Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('park_fixture', REPO/'test/mcu_park.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
p = f.patch
BASE = p.MCU_BASE
REQ, ACK, FLAG, WRITER, READER, MASK = f.REQ, f.ACK, f.FLAG, f.WRITER, f.READER, f.MASK
WAKE, SOFT_MASK, SFTINT = BASE+0x7f10, 0x10001044, 0x100000bc
STOCK = HELPER = PATCHED = None
SYMBOLS = {}
COUNTS = {'race_boundaries': 0, 'negative_controls': 0}

class Machine(f.Machine):
    def __init__(self, data=None, mask=0, status=1, soft_mask=0):
        super().__init__(PATCHED if data is None else data, mask, status)
        self.wait_seen = False
        self.write(SOFT_MASK, soft_mask)
        self.write(BASE + 0x3f90, BASE + 0xe88)

    def memory(self, uc, access, address, size, value, user):
        self.accesses.append((access, address, size, value))
        assert BASE <= address < BASE + 0x10000 or address in (MASK, SOFT_MASK, SFTINT), hex(address)
        if self.on_memory:
            self.on_memory(access, address, size, value)

    def code(self, uc, address, size, user):
        super().code(uc, address, size, user)
        if self.read(address) == 0x42000020:
            self.wait_seen = True
            self.wait_status = uc.reg_read(c.UC_MIPS_REG_CP0_STATUS)
            uc.emu_stop()

    def clone(self):
        m = Machine()
        for start, size in ((BASE, 0x10000), (0x10000000, 0x2000)):
            m.uc.mem_write(start, bytes(self.uc.mem_read(start, size)))
        for r in [f.reg(n) for n in range(1, 32)] + [c.UC_MIPS_REG_HI, c.UC_MIPS_REG_LO, c.UC_MIPS_REG_CP0_STATUS]:
            m.uc.reg_write(r, self.uc.reg_read(r))
        m.pc = self.pc
        return m

    def soft_irq(self):
        self.uc.reg_write(c.UC_MIPS_REG_RA, BASE + 0x9010)
        self.pc = BASE + 0x11e0
        self.run(until=BASE + 0x9010)
        return self

    def interrupt(self, epc, bd=False, wake=True, fix=True):
        m = self.clone()
        assert not bd or epc == SYMBOLS['q2_pwm_sleep_check'] + 4
        if wake:
            m.write(WAKE, 1)
            m.write(REQ, 0)
        m.write(SFTINT, 1)
        status = m.uc.reg_read(c.UC_MIPS_REG_CP0_STATUS)
        assert status & 7 == 1
        m.write(BASE + 0x9000, 0x409b7000)
        m.write(BASE + 0x9004, 0x000000c0)
        m.uc.reg_write(f.reg(27), epc)
        m.pc = BASE + 0x9000
        m.run(until=BASE + 0x9008)
        m.uc.reg_write(c.UC_MIPS_REG_CP0_STATUS, status | 2)
        m.pc = BASE + 0x1010
        m.run(until=BASE + 0x10a4)
        frame = m.uc.reg_read(c.UC_MIPS_REG_SP)
        assert m.read(frame + 136) == epc
        m.soft_irq()
        if not fix:
            m.uc.mem_write(BASE + 0x1138, STOCK[0x1138:0x1140])
        m.pc = BASE + 0x10ac
        target = SYMBOLS['q2_pwm_sleep_check'] if fix and SYMBOLS['q2_pwm_sleep_check'] <= epc < SYMBOLS['q2_pwm_sleep_end'] else epc
        m.run(until=target)
        assert m.uc.reg_read(c.UC_MIPS_REG_CP0_STATUS) == status
        return m

def registers(m):
    return [m.uc.reg_read(f.reg(n)) for n in range(1,32) if n not in (26,27)] + [m.uc.reg_read(r) for r in (c.UC_MIPS_REG_HI,c.UC_MIPS_REG_LO,c.UC_MIPS_REG_CP0_STATUS)]

class ImageChecks(unittest.TestCase):
    def test_deterministic_pins_default_and_manifest(self):
        h,s=p.compile_helper(sleep=True)
        self.assertEqual((h,s),(HELPER,SYMBOLS))
        self.assertEqual(PATCHED,p.patch_image(STOCK,h,sleep=True,symbols=s))
        dh,_=p.compile_helper(); default=p.patch_image(STOCK,dh)
        self.assertEqual(default[0x1138:0x1140],STOCK[0x1138:0x1140])
        self.assertEqual(default[0x11e0:0x11e8],STOCK[0x11e0:0x11e8])
        self.assertNotIn(struct.pack('<I',0x42000020),dh)
        self.assertEqual((len(default),len(PATCHED)),(0x7f10,0x7f14))
        self.assertEqual(struct.unpack_from('<IIIII',PATCHED,0x7f00),(0,0,p.MAGIC,2,0))
        changed=set(range(0xf98,0xfa0))|set(range(0x1138,0x1140))|set(range(0x11e0,0x11e8))
        self.assertTrue(all(PATCHED[i]==STOCK[i] for i in range(len(STOCK)) if i not in changed))
        manifest=p.make_manifest(STOCK,PATCHED,HELPER,SYMBOLS,sleep=True)
        self.assertEqual(manifest['protocol']['mcu_wake'],WAKE)
        self.assertEqual(manifest['sleep_policy'],'irq-epc-recheck-v1')
        for a in manifest['immutable_ranges']:
            self.assertEqual(a['sha256'],p.sha256(PATCHED[a['start']:a['end_exclusive']]))
        self.assertEqual(SYMBOLS['q2_pwm_sleep_end']-SYMBOLS['q2_pwm_sleep_check'],20)
        self.assertEqual(struct.unpack_from('<I',PATCHED,SYMBOLS['q2_pwm_sleep_end']-BASE-4)[0],0x42000020)

    def test_reject_bad_inputs(self):
        b=bytearray(STOCK); b[0]^=1
        for bad in (bytes(b),STOCK[:-1],STOCK+b'\0',PATCHED):
            with self.subTest(size=len(bad)),self.assertRaises(ValueError):
                p.patch_image(bad,HELPER,sleep=True,symbols=SYMBOLS)

class InstructionChecks(unittest.TestCase):
    def test_enabled_irq_empty_ring_waits(self):
        for flag in (0,1):
            m=Machine().park(); m.write(FLAG,flag); m.run(300)
            self.assertTrue(m.wait_seen); self.assertEqual(m.wait_status&7,1)
            self.assertEqual(m.read(ACK),1); self.assertEqual(m.read(MASK)&0x40,0x40)
            self.assertNotIn(BASE+0xfa0,m.trace)

    def test_irq_disabled_exception_and_mask_poll(self):
        for status in (0,1,2,3,4,5,6,7,0x20401005):
            for mask in (0,2):
                if status==1 and mask==0: continue
                with self.subTest(status=status,mask=mask):
                    m=Machine(status=status,soft_mask=mask).park(); m.run(300)
                    self.assertFalse(m.wait_seen); self.assertEqual(m.read(ACK),1)
                    m.write(REQ,0); m.run(until=SYMBOLS['q2_pwm_idle_wait'])
                    self.assertEqual(m.read(ACK),0)
                    self.assertEqual(m.uc.reg_read(c.UC_MIPS_REG_CP0_STATUS),status)

    def test_partial_ring_and_stale_flags_require_commit(self):
        for parked in (False,True):
            for timing in ('before_ack','after_ack'):
                with self.subTest(parked=parked,timing=timing):
                    m=Machine()
                    if parked:
                        m.park()
                        if timing=='before_ack': m.write(FLAG,1)
                        m.write(REQ,0); m.run(until=SYMBOLS['q2_pwm_idle_wait'])
                    m.write(FLAG,1); m.write(WRITER,3); m.write(REQ,0); m.run(300)
                    self.assertNotIn(BASE+0xfa0,m.trace); self.assertEqual(m.read(READER),0)
                    self.assertFalse(m.wait_seen)
                    m.write(WRITER,7); m.write(REQ,1); m.run(until=BASE+0xfa4)
                    self.assertEqual(m.read(ACK),0)

    def test_wake_only_clear_before_token_and_no_flag(self):
        m=Machine(); m.write(WAKE,1); m.write(SFTINT,1); m.write(FLAG,0x1234)
        before={n:m.uc.reg_read(f.reg(n)) for n in range(16,24)}
        writes=[]
        m.on_memory=lambda a,addr,size,val: writes.append((addr,val)) if a==UC_MEM_WRITE else None
        m.soft_irq()
        self.assertEqual(m.read(FLAG),0x1234)
        self.assertEqual(writes,[(SFTINT,0),(WAKE,0)])
        self.assertEqual(m.uc.reg_read(c.UC_MIPS_REG_SP),f.SP)
        for n,value in before.items(): self.assertEqual(m.uc.reg_read(f.reg(n)),value)

    def test_normal_callback_and_null_path_stock_parity(self):
        for callback in (0,BASE+0xe88):
            with self.subTest(callback=callback):
                old,new=Machine(STOCK),Machine()
                for m in (old,new):
                    m.write(BASE+0x3f90,callback); m.write(SFTINT,1); m.soft_irq()
                self.assertEqual(new.read(FLAG),old.read(FLAG))
                self.assertEqual(new.read(FLAG),1 if callback else 0)
                self.assertEqual(new.read(SFTINT),0)
                for n in list(range(16,24))+[28,29,30,31]:
                    self.assertEqual(new.uc.reg_read(f.reg(n)),old.uc.reg_read(f.reg(n)))

    def test_all_sleep_window_interrupt_boundaries(self):
        start,end=SYMBOLS['q2_pwm_sleep_check'],SYMBOLS['q2_pwm_sleep_end']
        for boundary in range(start,end+4,4):
            with self.subTest(boundary=hex(boundary)):
                m=Machine(mask=0x8040).park(); m.run(until=min(boundary,end-4))
                bd=boundary==start+8; epc=start+4 if bd else boundary
                before=registers(m); r=m.interrupt(epc,bd=bd)
                self.assertEqual(registers(r),before)
                self.assertEqual((r.read(WAKE),r.read(SFTINT),r.read(FLAG)),(0,0,0))
                r.wait_seen=False; r.run(until=SYMBOLS['q2_pwm_idle_wait'])
                self.assertFalse(r.wait_seen); self.assertEqual(r.read(ACK),0)
                self.assertEqual(r.read(MASK),0x8040); COUNTS['race_boundaries']+=1

    def test_missing_fixup_negative_controls_lose_wake(self):
        start,end=SYMBOLS['q2_pwm_sleep_check'],SYMBOLS['q2_pwm_sleep_end']
        for boundary in (start+4,start+8,start+12,end-4):
            with self.subTest(boundary=hex(boundary)):
                m=Machine().park(); m.run(until=boundary)
                bd=boundary==start+8; epc=start+4 if bd else boundary
                r=m.interrupt(epc,bd=bd,fix=False); r.wait_seen=False; r.run(100)
                self.assertTrue(r.wait_seen)
                self.assertEqual((r.read(REQ),r.read(WAKE),r.read(SFTINT),r.read(ACK)),(0,0,0,1))
                COUNTS['negative_controls']+=1

    def test_epc_outside_window_is_unchanged(self):
        start,end=SYMBOLS['q2_pwm_sleep_check'],SYMBOLS['q2_pwm_sleep_end']
        for epc in (BASE+0xf98,BASE+0x1008,start-4,end,end+4):
            with self.subTest(epc=hex(epc)):
                m=Machine().park(); before=registers(m); r=m.interrupt(epc)
                self.assertEqual(r.pc,epc); self.assertEqual(registers(r),before)

    def test_normal_irq_then_wake_with_stale_flag(self):
        start,end=SYMBOLS['q2_pwm_sleep_check'],SYMBOLS['q2_pwm_sleep_end']
        m=Machine().park(); m.run(until=start+4)
        first=m.interrupt(start+4,wake=False)
        self.assertEqual((first.read(FLAG),first.read(REQ)),(1,1))
        first.run(100); self.assertTrue(first.wait_seen)
        second=first.interrupt(end); self.assertEqual(second.read(FLAG),1)
        second.wait_seen=False; second.run(until=SYMBOLS['q2_pwm_idle_wait'])
        second.write(WRITER,3); second.run(200); self.assertNotIn(BASE+0xfa0,second.trace)
        second.write(REQ,1); second.run(until=BASE+0xfa4)

def main():
    global STOCK,HELPER,PATCHED,SYMBOLS
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stock',type=Path); ap.add_argument('--json',type=Path)
    args=ap.parse_args()
    STOCK=args.stock.read_bytes(); HELPER,SYMBOLS=p.compile_helper(sleep=True)
    PATCHED=p.patch_image(STOCK,HELPER,sleep=True,symbols=SYMBOLS)
    f.STOCK,f.HELPER,f.PATCHED,f.SYMBOLS=STOCK,HELPER,PATCHED,SYMBOLS
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    report={'status':'PASS' if result.wasSuccessful() else 'FAIL',
      'test_methods':result.testsRun, **COUNTS,
      'stock_sha256':p.sha256(STOCK),'patched_sha256':p.sha256(PATCHED),
      'helper_bytes':len(HELPER),
      'limitations':['WAIT and IRQ arrival are modeled hardware boundaries',
        'BD injections supply branch EPC; Cause.BD is not exposed by Unicorn',
        'Stock save/restore and patched IRQ/park instructions execute',
        'No physical sleep, bus-posting, host-handshake, or energy proof']}
    if args.json: args.json.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() else 1)
if __name__=='__main__': main()
