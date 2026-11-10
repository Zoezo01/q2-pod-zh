"""Instruction-word literals in tools/ and patch/ipod.json whose value also exists in main's copy of the file and that are
position-dependent: addiu off a non-stack base, branches, bal, j/jal. For hand review: wordsweep.py REPO"""
import sys, re, pathlib, subprocess
repo = sys.argv[1]
R = ['zero','at','v0','v1','a0','a1','a2','a3','t0','t1','t2','t3','t4','t5','t6','t7','s0','s1','s2','s3','s4','s5','s6','s7','t8','t9','k0','k1','gp','sp','fp','ra']
def s16(x): return x - 0x10000 if x & 0x8000 else x
def kind(w):
    op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xffff
    if op == 9 and rs not in (0, 28, 29) and (rs != rt or rs == 25): return f'addiu ${R[rt]},${R[rs]},{s16(imm):#x}'
    if op in (4, 5, 6, 7): return f'b{["eq","ne","lez","gtz"][op-4]} ${R[rs]},${R[rt]},{s16(imm)*4+4:+#x}'
    if op == 1 and rt in (0, 1, 0x10, 0x11): return f'{["bltz","bgez","","","","","","","","","","","","","","","bltzal","bal/bgezal"][rt]} ${R[rs]},{s16(imm)*4+4:+#x}'
    if op in (2, 3): return f'{"j" if op == 2 else "jal"} {(w & 0x3ffffff) << 2:#x}'
    if op == 0xf: return f'lui ${R[rt]},{imm:#x}'
    return None
for rel in subprocess.run(['git', '-C', repo, 'ls-files', 'tools', 'patch/ipod.json'], capture_output=True, text=True).stdout.split():
    if not rel.endswith(('.py', '.json')) or rel.endswith('rebase.py'): continue
    new = (pathlib.Path(repo)/rel).read_text().splitlines()
    old = subprocess.run(['git', '-C', repo, 'show', 'main:' + rel], capture_output=True, text=True).stdout.lower()
    for i, line in enumerate(new, 1):
        for m in re.finditer(r'0x([0-9a-f]{8})\b', line, re.I):
            w = int(m.group(1), 16); k = kind(w)
            if k and m.group(0).lower() in old: print(f'{rel}:{i}: {m.group(0)} {k}   | {line.strip()[:120]}')
