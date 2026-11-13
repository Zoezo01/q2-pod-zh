# Post-script for demo: make a function at every PIC prologue (lui gp / addiu gp / addu gp,gp,t9) that analysis left
# without one. A handler demo only passes by address (widget_on, timer_add, pthread_create) is found as code at best.
# @category q2
import struct
fm = currentProgram.getFunctionManager()
mem = currentProgram.getMemory()
text = mem.getBlock('.text')
start, size = text.getStart().getOffset(), text.getSize()
import jarray
buf = jarray.zeros(size, 'b')
text.getBytes(text.getStart(), buf)
raw = buf.tostring()
made = inside = failed = 0
for o in range(0, size - 12, 4):
    a, b, c = struct.unpack_from('<III', raw, o)
    if a >> 16 != 0x3c1c or b >> 16 != 0x279c or c != 0x0399e021:
        continue
    addr = toAddr(start + o)
    if fm.getFunctionAt(addr) is not None:
        continue
    if fm.getFunctionContaining(addr) is not None:
        inside += 1
        continue
    if getInstructionAt(addr) is None:
        disassemble(addr)
    if createFunction(addr, None) is None:
        failed += 1
    else:
        made += 1
print('FUNCTIONS made %d, inside another %d, failed %d' % (made, inside, failed))
