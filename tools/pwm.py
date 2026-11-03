#!/usr/bin/env python3
"""Pinned Q2 PWM/MCU cooperative clock-gating experiment; never a generic module loader."""
import argparse, hashlib, pathlib, struct, subprocess, tempfile
from rotator import Elf, require

PWM_SHA256='8e389adc1a92e420c726bf6e2ad2860ed2cde09659a06914c51d8725da9515cd'
ROOT=pathlib.Path(__file__).resolve().parent
CFLAGS=['--target=mipsel-linux-gnu','-march=mips32r5','-mabi=32','-msoft-float',
        '-mno-abicalls','-fno-pic','-mlong-calls','-G0','-ffreestanding','-fno-stack-protector',
        '-fno-builtin','-Os','-Wall','-Wextra','-Werror','-Wno-unused-function']

def symbol(elf,name,value=0,size=0,section=0,info=0x10):
    strings=elf.by_name['.strtab']['data']; n=len(strings)
    strings.extend(name.encode()+b'\0')
    idx=len(elf.symbols)
    elf.by_name['.symtab']['data'].extend(struct.pack('<IIIBBH',n,value,size,info,0,section))
    elf.symbols.append(dict(name=name,value=value,size=size,info=info,other=0,section=section))
    return idx

def alias(elf,old,new):
    s=next(s for s in elf.symbols if s['name']==old)
    return symbol(elf,new,s['value'],s['size'],s['section'],0x10|(s['info']&15))

def wrap(elf,name,target):
    """Replay the exact displaced prologue in its original section, then return.
    Keep paired HI16/LO16 relocation ordering, including init-section prologues.
    """
    s=next(s for s in elf.symbols if s['name']==name)
    section=elf.sections[s['section']]; data=section['data']; off=s['value']
    rel=elf.by_name['.rel'+section['name']]
    tramp=len(data); displaced=bytes(data[off:off+8])
    # This patch family only displaces non-control instructions.
    for word, in struct.iter_unpack('<I',displaced):
        require(word>>26 not in (1,2,3,4,5,6,7,20,21,22,23), 'Branch in prologue')
    data.extend(displaced+struct.pack('<II',0x08000000|((off+8)>>2),0))
    rels=[(tramp+r-off if off<=r<off+8 else r,info)
          for r,info in struct.iter_unpack('<II',rel['data'])]
    dest=symbol(elf,target); section_index=elf.symbol_index(section['name'])
    if section['name'].startswith('.init.'):
        # Init and core memory are allocated separately and may occupy different
        # 256 MiB regions. Keep the entry J local, then use a full address.
        veneer=len(data)
        data.extend(struct.pack('<IIII',0x3c190000,0x27390000,0x03200008,0))
        rels += [(off,section_index<<8|4),(tramp+8,section_index<<8|4),
                 (veneer,dest<<8|5),
                 (veneer+4,dest<<8|6)]
        entry=0x08000000|(veneer>>2)
    else:
        rels += [(off,dest<<8|4),(tramp+8,section_index<<8|4)]
        entry=0x08000000
    struct.pack_into('<II',data,off,entry,0)
    rel['data']=bytearray(b''.join(struct.pack('<II',*r) for r in rels))
    symbol(elf,'q2_stock_'+name,tramp,16,s['section'],0x12)

def rebind(elf,section_name,first,last,old,new):
    oldidx=elf.symbol_index(old); newidx=symbol(elf,new)
    rel=elf.by_name['.rel'+section_name]; count=0; items=[]
    for off,info in struct.iter_unpack('<II',rel['data']):
        if first<=off<last and info>>8==oldidx:
            info=newidx<<8|(info&255); count+=1
        items.append((off,info))
    require(count>0,'No call relocations for '+old)
    rel['data']=bytearray(b''.join(struct.pack('<II',*r) for r in items))

def permanent(elf):
    # Exact modules' this_module cleanup relocation identifies the exit field.
    # Ordinary unload cannot safely quiesce autonomous MCU DMA after a fault.
    rel=elf.by_name['.rel.gnu.linkonce.this_module']; target=elf.by_name['.gnu.linkonce.this_module']['data']
    cleanup=elf.symbol_index('cleanup_module'); kept=[]; removed=0
    for off,info in struct.iter_unpack('<II',rel['data']):
        if info>>8==cleanup:
            require(info&255==2,'Unexpected module exit relocation')
            struct.pack_into('<I',target,off,0); removed+=1
        else: kept.append((off,info))
    require(removed==1,'Expected one module exit pointer')
    rel['data']=bytearray(b''.join(struct.pack('<II',*r) for r in kept))

def link(elf,source,extra_include=None,sleep=False):
    with tempfile.TemporaryDirectory(prefix='q2-pwm-') as directory:
        p=pathlib.Path(directory); (p/'stock.ko').write_bytes(elf.pack())
        includes=['-I',str(ROOT)]+(['-DQ2_MCU_SLEEP=1'] if sleep else [])
        if extra_include is not None:
            (p/'mcu_park_expected.h').write_text(extra_include)
            includes += ['-I',str(p)]
        subprocess.run(['clang',*CFLAGS,*includes,'-c',str(ROOT/source),'-o',str(p/'helper.o')],check=True)
        subprocess.run(['ld.lld','-r','--build-id=sha1',str(p/'stock.ko'),str(p/'helper.o'),'-o',str(p/'linked.ko')],check=True)
        result=Elf((p/'linked.ko').read_bytes())
        result.by_name['.modinfo']['data'].extend(b'q2_clock_policy=cooperative-pwm-v1\0')
        if sleep: result.by_name['.modinfo']['data'].extend(b'q2_mcu_sleep=irq-epc-recheck-v1\0')
        note=result.by_name['.note.gnu.build-id']['data']
        require(len(note)==36 and note[12:16]==b'GNU\0','Unexpected build ID')
        note[16:36]=bytes(20); note[16:36]=hashlib.sha256(result.pack()).digest()[:20]
        return result.pack()

def patch_pwm(data):
    require(hashlib.sha256(data).hexdigest()==PWM_SHA256,'Unsupported or already patched PWM module')
    elf=Elf(data)
    require(len(elf.by_name['.text']['data'])==0x1fa0 and elf.by_name['.bss']['size']==0x7e0,'PWM layout changed')
    for name in ['pwm_mux_clk','pwm_div_clk','pwm_gate_clk']:
        alias(elf,name,'q2_stock_'+name)
    for name in ['pwm2_request','pwm2_config','pwm2_set_level','pwm2_release',
                 'pwm2_dma_init','pwm2_dma_update','pwm2_dma_disable_loop','ingenic_pwm_probe']:
        wrap(elf,name,'q2_'+name)
    for old,new in [('clk_prepare','q2_pwm_probe_prepare'),('clk_enable','q2_pwm_probe_enable'),
                    ('clk_unprepare','q2_pwm_probe_unprepare')]:
        rebind(elf,'.text',0x181c,0x1b04,old,new)
    rebind(elf,'.text',0xa8c,0xb6c,'clk_disable','q2_pwm_remove_disable')
    rebind(elf,'.text',0xa8c,0xb6c,'clk_unprepare','q2_pwm_remove_unprepare')
    permanent(elf)
    return link(elf,'pwm_clock.c')

MCU_SHA256='4faffb1695e528acf533b1eefd94369d841391d33d4c990f5f41a662c21a3acb'

def expected_header(image,helper_size):
    ranges=[(0,0x3cc8),(0x6c00,0x6c00+helper_size),(0x7f08,0x7f10),
            (0x3cc8,0x3f88)]
    lines=['struct q2_expected { u32 offset,length; const unsigned char *bytes; };']
    for i,(start,end) in enumerate(ranges):
        values=','.join(str(x) for x in image[start:end])
        lines.append(f'static const unsigned char q2_expected_{i}[]={{'+values+'};')
    lines += ['#define Q2_MCU_RANGES 3','#define Q2_MCU_PRISTINE_RANGES 4','static const struct q2_expected q2_mcu_ranges[] = {']
    lines += [f'{{{start},{end-start},q2_expected_{i}}},' for i,(start,end) in enumerate(ranges)]
    return '\n'.join(lines+['};',''])

def patch_mcu(data,image,helper_size,sleep=False):
    require(hashlib.sha256(data).hexdigest()==MCU_SHA256,'Unsupported or already patched MCU module')
    elf=Elf(data)
    for name in ['mcu_init','mcu_bootup','mcu_reset','mcu_shutdown','mcu_write_data','mcu_ioctl']:
        wrap(elf,name,'q2_'+name)
    permanent(elf)
    info=elf.by_name['.modinfo']['data']
    require(info.count(b'depends=utils\0')==1, 'Unexpected MCU dependencies')
    info[:]=info.replace(b'depends=utils\0',b'depends=utils,soc_pwm\0')
    return link(elf,'mcu_pwm_clock.c',expected_header(image,helper_size),sleep=sleep)

def patch_bundle(cat,sleep=False):
    import mcu_park
    helper,symbols=mcu_park.compile_helper(sleep=sleep)
    old_image=cat('usr/data/libmcu-bare.bin')
    image=mcu_park.patch_image(old_image,helper,sleep=sleep,symbols=symbols)
    originals={path:cat(path) for path in ('module_driver/soc_pwm.ko','module_driver/soc_mcu.ko')}
    files={'module_driver/soc_pwm.ko':patch_pwm(originals['module_driver/soc_pwm.ko']),
           'module_driver/soc_mcu.ko':patch_mcu(originals['module_driver/soc_mcu.ko'],image,len(helper),sleep=sleep),
           'usr/data/libmcu-bare.bin':image}
    originals['usr/data/libmcu-bare.bin']=old_image
    manifest=dict(policy='cooperative-pwm-v1', normal_module_unload=False,
        mcu=mcu_park.make_manifest(old_image,image,helper,symbols,sleep=sleep),
        files={path:dict(original_sha256=hashlib.sha256(originals[path]).hexdigest(),
                        sha256=hashlib.sha256(data).hexdigest()) for path,data in files.items()})
    # Source paths are not part of reproducible build metadata.
    manifest['mcu']['assembly']['path']='tools/mcu_park.S'
    return files,manifest

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stock',type=pathlib.Path); ap.add_argument('out',type=pathlib.Path)
    args=ap.parse_args(); args.out.write_bytes(patch_pwm(args.stock.read_bytes()))
