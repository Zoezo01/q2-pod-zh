"""Relocate MIPS modules at kernel-like addresses before low-address emulation.

The semantic emulators intentionally use convenient synthetic addresses. This
separate independent-reader check prevents those addresses from hiding R_MIPS_26
overflow between the vmalloc/module and kernel text regions.
"""
import struct

MODULE_BASES=(0xc0001000,0xc1007800,0xc200f800)
INIT_BASES=(0xd0001000,0xd1007800,0xd200f800)
KERNEL_BASE=0x80001000

class Memory:
    def __init__(self):self.regions=[];self.writes={}
    def mem_map(self,address,size):
        assert size>0 and address+size<=0x100000000
        self.regions.append((address,address+size))
    def mem_write(self,address,data):
        assert any(a<=address and address+len(data)<=b for a,b in self.regions)
        self.writes[address]=bytes(data)

class Resolver:
    def __init__(self,external_base=KERNEL_BASE):
        self.uc=Memory();self.names={};self.external_base=external_base
    def external(self,name):
        if name not in self.names:self.names[name]=self.external_base+len(self.names)*0x100
        return self.names[name]

def witness_elf(text_name=".text"):
    """Tiny real ELF with one external JAL relocation; no production code edits."""
    section_names=['',text_name,'.bss','.rel'+text_name,'.symtab','.strtab','.shstrtab']
    names=b'\0'.join(n.encode() for n in section_names)+b'\0'
    contents=[b'',struct.pack('<II',0x0c000000,0),b'',struct.pack('<II',0,1<<8|4),
              bytes(16)+struct.pack('<IIIBBH',1,0,0,0x10,0,0),b'\0external\0',names]
    blob=bytearray(52);sections=[]
    for i,content in enumerate(contents):
        blob.extend(bytes((-len(blob))%4));offset=len(blob);blob.extend(content)
        kind=[0,1,8,9,2,3,3][i];flags=6 if i==1 else 3 if i==2 else 0
        size=4 if i==2 else len(content)
        sections.append((names.index(section_names[i].encode()+b'\0'),kind,flags,0,offset,size,
                         4 if i==3 else 5 if i==4 else 0,
                         1 if i in (3,4) else 0,4 if i in (1,2,3,4) else 1,
                         8 if i==3 else 16 if i==4 else 0))
    blob.extend(bytes((-len(blob))%4));shoff=len(blob)
    blob.extend(b''.join(struct.pack('<10I',*s) for s in sections))
    struct.pack_into('<16sHHIIIIIHHHHHH',blob,0,b'\x7fELF\x01\x01\x01'+bytes(9),
                     1,8,1,0,0,shoff,0,52,0,0,40,len(sections),6)
    return bytes(blob)

def negative_control(reader):
    data=witness_elf()
    try:reader.Module(data).load(Resolver(),MODULE_BASES[0])
    except AssertionError as exc:
        assert 'R_MIPS_26' in str(exc),str(exc)
    else:raise AssertionError('Out-of-region module-to-kernel JAL was accepted')
    # This same instruction/relocation is encodable when its target shares
    # the module's 256 MiB region; the control must not reject every R26.
    same=Resolver(0xc0101000)
    reader.Module(data).load(same,MODULE_BASES[0])
    word=struct.unpack_from('<I',same.uc.writes[MODULE_BASES[0]])[0]
    assert ((MODULE_BASES[0]+4)&0xf0000000)|((word&0x3ffffff)<<2)==0xc0101000
    init=witness_elf('.init.text')
    try:reader.Module(init).load(Resolver(0xc0101000),MODULE_BASES[0],init_base=INIT_BASES[0])
    except AssertionError as exc:assert 'R_MIPS_26' in str(exc),str(exc)
    else:raise AssertionError('Out-of-region init-to-core JAL was accepted')
    reader.Module(init).load(Resolver(0xd0101000),MODULE_BASES[0],init_base=INIT_BASES[0])
    return dict(out_of_region='REJECTED',same_region='PASS',split_init_to_core='REJECTED')


def validate(reader,data):
    control=negative_control(reader)
    for base,init in zip(MODULE_BASES,INIT_BASES):reader.Module(data).load(Resolver(),base,init_base=init)
    module=reader.Module(data);external=[];cross_section=[];r26=0
    for section,contents in zip(module.sections,module.bytes):
        if section[1]!=9:continue
        for offset,info in struct.iter_unpack('<II',contents):
            if info&255!=4:continue
            r26+=1
            symbol=module.symbols[info>>8]
            if symbol[4]==0:external.append((symbol[0],offset))
            elif symbol[4]!=section[7]:cross_section.append((symbol[0],offset))
    assert not external,('External R_MIPS_26 imports require long calls',external)
    assert not cross_section,('R_MIPS_26 must stay within its source section',cross_section)
    return dict(module_bases=list(MODULE_BASES),init_bases=list(INIT_BASES),kernel_import_base=KERNEL_BASE,
                external_r26=0,cross_section_r26=0,local_r26=r26,control=control)
