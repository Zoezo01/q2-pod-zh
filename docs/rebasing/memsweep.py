"""Load/store instruction-word literals in tools/ and patch/ipod.json, unchanged from main, off a base that is not the
stack, the frame or gp, with an offset too large for a struct field (>= 0x200 or negative): the low half of a stock data
address that the rebase cannot see inside a replacement word. For hand review: memsweep.py REPO"""
import sys, re, pathlib, subprocess
repo = sys.argv[1]
OPS = {0x20: 'lb', 0x21: 'lh', 0x23: 'lw', 0x24: 'lbu', 0x25: 'lhu', 0x28: 'sb', 0x29: 'sh', 0x2b: 'sw', 0x31: 'lwc1', 0x35: 'ldc1', 0x39: 'swc1', 0x3d: 'sdc1'}
def s16(x): return x - 0x10000 if x & 0x8000 else x
for rel in subprocess.run(['git', '-C', repo, 'ls-files', 'tools', 'patch/ipod.json', 'patch'], capture_output=True, text=True).stdout.split():
    if not rel.endswith(('.py', '.json', '.c', '.h', '.inc', '.S')) or rel.endswith('rebase.py'): continue
    new = (pathlib.Path(repo)/rel).read_text().splitlines()
    old = subprocess.run(['git', '-C', repo, 'show', 'main:' + rel], capture_output=True, text=True).stdout.lower()
    for i, line in enumerate(new, 1):
        for m in re.finditer(r'0x([0-9a-f]{8})\b', line, re.I):
            w = int(m.group(1), 16); op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, s16(w & 0xffff)
            if op in OPS and rs not in (28, 29, 30, 0) and (imm < 0 or imm >= 0x200) and m.group(0).lower() in old:
                print(f'{rel}:{i}: {m.group(0)} {OPS[op]} ${rt},{imm:#x}(${rs})   | {line.strip()[:130]}')
