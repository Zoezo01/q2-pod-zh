#!/usr/bin/env python3
"""Verify the development guard, paired packaging, pinned inputs and module ABI."""
import argparse, hashlib, json, pathlib, struct, subprocess, sys, tempfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import build
from pwm import patch_bundle, PWM_SHA256, MCU_SHA256
from rotator import Elf

with tempfile.TemporaryDirectory() as directory:
    root=pathlib.Path(directory)
    try: build(root/'missing.zip',root/'output',root/'missing.jpg',pwm_idle_clock=True)
    except ValueError as exc: assert str(exc)=='--pwm-idle-clock requires --dev'
    else: raise AssertionError('PWM experiment accepted without --dev')
    assert not (root/'output').exists()
    for flags in ({}, {'dev':True}, {'pwm_idle_clock':True}):
        try: build(root/'missing.zip',root/'output',root/'missing.jpg',mcu_idle_sleep=True,**flags)
        except ValueError as exc: assert 'requires --dev' in str(exc)
        else: raise AssertionError('MCU sleep experiment accepted without paired dev PWM option')
        assert not (root/'output').exists()

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('stockfs',type=pathlib.Path)
ap.add_argument('--sleep',action='store_true')
ap.add_argument('builds',nargs='*',type=pathlib.Path)
a=ap.parse_args()
cat=lambda path:(a.stockfs/path).read_bytes()
files,manifest=patch_bundle(cat,sleep=a.sleep)
repeat,second=patch_bundle(cat,sleep=a.sleep)
assert files==repeat and manifest==second, 'Paired patch is not reproducible'

def info(data):return dict(x.split(b'=',1) for x in bytes(Elf(data).by_name['.modinfo']['data']).split(b'\0') if b'=' in x)
for path in ('module_driver/soc_pwm.ko','module_driver/soc_mcu.ko'):
    old,new=Elf(cat(path)),Elf(files[path])
    assert old.header[7]==new.header[7]
    # Modules live in c0000000 while kernel services live in 80000000. A JAL's
    # 26-bit target cannot cross a 256 MiB region; kernel calls must use JALR.
    for section in new.sections:
        if section['type']==9:
            for off,rel in struct.iter_unpack('<II',section['data']):
                assert not (rel&255==4 and new.symbols[rel>>8]['section']==0), ('external R_MIPS_26',path,section['name'],off)
    assert old.by_name['.MIPS.abiflags']['data']==new.by_name['.MIPS.abiflags']['data']
    for key in (b'vermagic',b'license',b'name'): assert info(cat(path))[key]==info(files[path])[key]
    assert new.by_name['.gnu.linkonce.this_module']['size']==old.by_name['.gnu.linkonce.this_module']['size']
    # Verify the exact module's unload field is null and unrelocated.
    assert struct.unpack_from('<I',new.by_name['.gnu.linkonce.this_module']['data'],0x16c)==(0,)
    assert all(off!=0x16c for off,_ in struct.iter_unpack('<II',new.by_name['.rel.gnu.linkonce.this_module']['data']))
    # Existing export table entries stay intact (symbol ordering can change at link).
    def exports(e):
        table='__ksymtab_gpl' if '__ksymtab_gpl' in e.by_name else '__ksymtab'
        d=e.by_name[table]['data'];r=e.by_name['.rel'+table]['data'];result={}
        names=e.by_name['__ksymtab_strings']['data']
        for off,idx in struct.iter_unpack('<II',r):
            if off%12==0:
                nameoff=struct.unpack_from('<I',d,off+4)[0]
                name=bytes(names[nameoff:]).split(b'\0',1)[0].decode()
                result[name]=e.symbols[idx>>8]['name']
        return result
    before,after=exports(old),exports(new)
    assert all(after[k]==v for k,v in before.items())
    if path.endswith('soc_pwm.ko'): assert set(after)-set(before)=={'q2_pwm_mcu_ready'}
    else: assert after==before and info(files[path])[b'depends']==b'utils,soc_pwm'
    print(path,'ABI, exports and permanent-module checks passed')
for directory in a.builds:
    m=json.loads((directory/'manifest.json').read_text())
    for path,expected in files.items():
        actual=subprocess.check_output(['unsquashfs','-cat',str(directory/'rootfs.squashfs'),path])
        if 'pwm_clock' in m: assert actual==expected==(directory/pathlib.Path(path).name).read_bytes()
        else: assert actual==cat(path), 'Default changed '+path
    if 'pwm_clock' in m: assert m['dev'] and m['pwm_clock']==manifest
    assert (directory/'rootfs.squashfs').stat().st_size<=(directory/'stock.squashfs').stat().st_size
    print(directory.name,'paired/default packaging passed')
print('PASS: guard, deterministic paired outputs, exact ABI and requested packages')
