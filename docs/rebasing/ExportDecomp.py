# Decompile the functions holding the listed addresses into one file each.
# Args: LIST (one hex address a line, '#' comments) OUTDIR [NAMES.tsv: address<TAB>name, labels applied first]
# @category q2
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.symbol import SourceType
import os

args = getScriptArgs()
listing, outdir = args[0], args[1]
if not os.path.isdir(outdir):
    os.makedirs(outdir)
fm = currentProgram.getFunctionManager()
if len(args) > 2 and os.path.exists(args[2]):
    for line in open(args[2]):
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 2 or not parts[0].startswith('0x'):
            continue
        addr = toAddr(int(parts[0], 16))
        f = fm.getFunctionAt(addr)
        if f is None:
            f = createFunction(addr, parts[1])
        if f is not None:
            try:
                f.setName(parts[1], SourceType.USER_DEFINED)
            except Exception as e:
                print('name %s: %s' % (parts[1], e))
ifc = DecompInterface()
ifc.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()
done = set()
index = open(os.path.join(outdir, 'INDEX.tsv'), 'w')
for line in open(listing):
    cols = line.split('#')[0].split()
    if not cols:
        continue
    want = int(cols[0], 16)
    addr = toAddr(want)
    how = ''
    f = fm.getFunctionContaining(addr)
    if f is None and len(cols) > 1 and int(cols[1], 16) == want:
        # our own walk of the code has a function start exactly here (a leaf only ever passed by address)
        if getInstructionAt(addr) is None:
            disassemble(addr)
        f = createFunction(addr, None)
        how = 'made'
    if f is None:
        # in a gap of a function's body (code after a trap, a jump table's case): the function before it
        f = getFunctionBefore(addr)
        how = 'gap'
    if f is None:
        index.write('%#x\t-\t-\tno function\t\n' % want)
        continue
    entry = f.getEntryPoint().getOffset()
    extent = f.getBody().getMaxAddress().getOffset() + 1 - entry
    index.write('%#x\t%#x\t%d\t%s\t%s\n' % (want, entry, extent, f.getName(), how))
    if entry in done:
        continue
    done.add(entry)
    res = ifc.decompileFunction(f, 180, monitor)
    callers = sorted(set(c.getName() + '@' + c.getEntryPoint().toString() for c in f.getCallingFunctions(monitor)))
    callees = sorted(set(c.getName() for c in f.getCalledFunctions(monitor)))
    users = set()
    for ref in getReferencesTo(f.getEntryPoint()):
        if not ref.getReferenceType().isCall():
            g = fm.getFunctionContaining(ref.getFromAddress())
            users.add((g.getName() if g is not None else 'data') + '@' + ref.getFromAddress().toString())
    strings = []
    it = currentProgram.getListing().getInstructions(f.getBody(), True)
    while it.hasNext():
        ins = it.next()
        for ref in ins.getReferencesFrom():
            d = getDataAt(ref.getToAddress())
            if d is not None and d.hasStringValue():
                s = str(d.getValue())
                if s not in strings:
                    strings.append(s)
    out = open(os.path.join(outdir, '%x.c' % entry), 'w')
    out.write('// %s  entry 0x%x  size %d\n' % (f.getName(), entry, extent))
    out.write('// callers: %s\n' % ', '.join(callers[:40]))
    out.write('// address taken by: %s\n' % ', '.join(sorted(users)[:40]))
    out.write('// callees: %s\n' % ', '.join(callees[:80]))
    out.write('// strings: %s\n\n' % ' | '.join(repr(s)[:80] for s in strings[:60]))
    if res is not None and res.decompileCompleted():
        out.write(res.getDecompiledFunction().getC())
    else:
        out.write('// decompile failed: %s\n' % (res.getErrorMessage() if res is not None else 'no result'))
    out.close()
index.close()
