"""Independent ELF32 MIPS relocator used by binary-module execution tests.

Deliberately does not import the patcher ELF writer.
"""
import struct

def cstring(data, start):
    return data[start:].split(b'\0', 1)[0].decode()


class Module:
    """Independent ELF reader/relocator, deliberately separate from patcher code."""
    def __init__(self, data):
        h = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
        assert h[0][:7] == b'\x7fELF\x01\x01\x01' and h[1:3] == (1, 8)
        self.sections = [struct.unpack_from('<10I', data, h[6] + n * h[11])
                         for n in range(h[12])]
        self.bytes = [bytearray(s[5]) if s[1] == 8 else bytearray(data[s[4]:s[4]+s[5]])
                      for s in self.sections]
        names = self.bytes[h[13]]
        self.names = [cstring(names, s[0]) for s in self.sections]
        sym = self.names.index('.symtab')
        strings = self.bytes[self.sections[sym][6]]
        self.symbols = []
        for off in range(0, len(self.bytes[sym]), 16):
            n, value, size, info, other, section = struct.unpack_from('<IIIBBH', self.bytes[sym], off)
            self.symbols.append((cstring(strings, n), value, size, info, section))
        self.named = {s[0]: i for i, s in enumerate(self.symbols) if s[0]}

    def load(self, machine, base, *, init_base=None):
        cursors = dict(core=base)
        if init_base is not None:cursors["init"]=init_base
        addresses = {}
        for i, s in enumerate(self.sections):
            if s[2] & 2:
                group="init" if init_base is not None and self.names[i].startswith(".init") else "core"
                align = max(s[8], 4)
                cursor = (cursors[group] + align - 1) & -align
                addresses[i] = cursor
                cursors[group] = cursor + max(s[5], 4) + 0x40
        for group,cursor in cursors.items():
            start=init_base if group=="init" else base
            if cursor>start:
                machine.uc.mem_map(start & ~0xfff, ((cursor+0xfff)&~0xfff)-(start&~0xfff))
        values = []
        for name, value, size, info, section in self.symbols:
            if section == 0:
                values.append(machine.external(name) if name else 0)
            elif section == 0xfff1:
                values.append(value)
            elif section in addresses:
                values.append(addresses[section] + value)
            else:
                values.append(0)
        for i, s in enumerate(self.sections):
            if s[1] != 9 or s[7] not in addresses:
                continue
            target = self.bytes[s[7]]
            pending = []
            for off, info in struct.iter_unpack('<II', self.bytes[i]):
                symbol, kind = info >> 8, info & 255
                value = values[symbol]
                word = struct.unpack_from('<I', target, off)[0]
                if kind == 2:  # R_MIPS_32
                    patched = (word + value) & 0xffffffff
                elif kind == 4:  # R_MIPS_26
                    address = ((word & 0x3ffffff) << 2) + value
                    site=addresses[s[7]]+off
                    assert 0<=address<=0xffffffff and address%4==0, ('Invalid R_MIPS_26 target',hex(address))
                    assert (address & 0xf0000000) == ((site+4) & 0xf0000000), (
                        'R_MIPS_26 region overflow',self.symbols[symbol][0],hex(site),hex(address))
                    patched = (word & 0xfc000000) | ((address >> 2) & 0x3ffffff)
                elif kind == 5:  # R_MIPS_HI16: final low addend comes later.
                    pending.append((off, word, value))
                    continue
                elif kind == 6:  # R_MIPS_LO16
                    low = struct.unpack('<h', struct.pack('<H', word & 65535))[0]
                    remaining = []
                    for highoff, highword, highvalue in pending:
                        if highvalue != value:
                            remaining.append((highoff, highword, highvalue))
                            continue
                        full = ((highword & 65535) << 16) + low + value
                        struct.pack_into('<I', target, highoff,
                                         (highword & 0xffff0000) | (((full + 0x8000) >> 16) & 65535))
                    pending = remaining
                    patched = (word & 0xffff0000) | ((value + low) & 65535)
                else:
                    raise AssertionError(('unsupported relocation', kind))
                struct.pack_into('<I', target, off, patched)
            assert not pending, pending
        for i, address in addresses.items():
            machine.uc.mem_write(address, bytes(self.bytes[i]))
        return {name: values[i] for name, i in self.named.items()}, addresses[self.names.index('.bss')]


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

def relocation_control():
    data=witness_elf()
    try:Module(data).load(Resolver(),MODULE_BASES[0])
    except AssertionError as exc:
        assert 'R_MIPS_26' in str(exc),str(exc)
    else:raise AssertionError('Out-of-region module-to-kernel JAL was accepted')
    # This same instruction/relocation is encodable when its target shares
    # the module's 256 MiB region; the control must not reject every R26.
    same=Resolver(0xc0101000)
    Module(data).load(same,MODULE_BASES[0])
    word=struct.unpack_from('<I',same.uc.writes[MODULE_BASES[0]])[0]
    assert ((MODULE_BASES[0]+4)&0xf0000000)|((word&0x3ffffff)<<2)==0xc0101000
    init=witness_elf('.init.text')
    try:Module(init).load(Resolver(0xc0101000),MODULE_BASES[0],init_base=INIT_BASES[0])
    except AssertionError as exc:assert 'R_MIPS_26' in str(exc),str(exc)
    else:raise AssertionError('Out-of-region init-to-core JAL was accepted')
    Module(init).load(Resolver(0xd0101000),MODULE_BASES[0],init_base=INIT_BASES[0])
    return dict(out_of_region='REJECTED',same_region='PASS',split_init_to_core='REJECTED')
