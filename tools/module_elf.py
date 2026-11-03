"""Minimal pinned-module ELF32 writer; not a general ELF linker."""
import struct

HEADER = '<16sHHIIIIIHHHHHH'
SECTION = '<10I'
KEYS = ('name_offset', 'type', 'flags', 'addr', 'offset', 'size', 'link', 'info', 'align', 'entsize')


def require(ok, message):
    if not ok:
        raise ValueError(message)


class Elf:
    """Small ELF32 reader/writer; preserve section/symbol indices and ELF flags."""
    def __init__(self, data):
        self.header = list(struct.unpack_from(HEADER, data))
        h = self.header
        require(h[0][:7] == b'\x7fELF\x01\x01\x01' and h[1:3] == [1, 8], 'Expected MIPS ELF32 relocatable')
        require(h[5] == 0 and h[8] == 52 and h[11] == 40, 'Unexpected ELF headers')
        self.sections = []
        for i in range(h[12]):
            s = dict(zip(KEYS, struct.unpack_from(SECTION, data, h[6] + i * 40)))
            s['data'] = bytearray() if s['type'] == 8 else bytearray(data[s['offset']:s['offset'] + s['size']])
            self.sections.append(s)
        names = self.sections[h[13]]['data']
        for s in self.sections:
            s['name'] = bytes(names[s['name_offset']:]).split(b'\0', 1)[0].decode()
        self.by_name = {s['name']: s for s in self.sections}
        self.symbols = []
        strings = self.by_name['.strtab']['data']
        for off in range(0, len(self.by_name['.symtab']['data']), 16):
            n, value, size, info, other, section = struct.unpack_from('<IIIBBH', self.by_name['.symtab']['data'], off)
            name = bytes(strings[n:]).split(b'\0', 1)[0].decode()
            self.symbols.append(dict(name=name, value=value, size=size, info=info, other=other, section=section))

    def symbol_index(self, name):
        if name in self.by_name:
            section = self.sections.index(self.by_name[name])
            return next(i for i, s in enumerate(self.symbols) if s['info'] & 15 == 3 and s['section'] == section)
        return next(i for i, s in enumerate(self.symbols) if s['name'] == name)

    def add_function(self, name, value, size):
        strings = self.by_name['.strtab']['data']
        n = len(strings)
        strings.extend(name.encode() + b'\0')
        # Global, non-exported: appending leaves the existing local/global split intact.
        section = self.sections.index(self.by_name['.text'])
        self.by_name['.symtab']['data'].extend(struct.pack('<IIIBBH', n, value, size, 0x12, 0, section))
        self.symbols.append(dict(name=name, value=value, size=size, info=0x12, other=0, section=section))

    def pack(self):
        data = bytearray(52)
        for i, s in enumerate(self.sections):
            if not i:
                continue
            data.extend(bytes((-len(data)) % max(s['align'], 1)))
            s['offset'] = len(data)
            if s['type'] != 8:
                s['size'] = len(s['data'])
                data.extend(s['data'])
        data.extend(bytes((-len(data)) % 4))
        self.header[6] = len(data)
        for s in self.sections:
            data.extend(struct.pack(SECTION, *(s[k] for k in KEYS)))
        struct.pack_into(HEADER, data, 0, *self.header)
        return bytes(data)
