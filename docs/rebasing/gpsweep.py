"""gp-relative load literals (lw REG, OFF($gp)) in our sources that the rebase left as they were in main, and whose GOT
slot names a different thing in the new stock: gpsweep.py REPO OLD_ROOT NEW_ROOT"""
import sys, re, pathlib, subprocess
repo, ra, rb = sys.argv[1:4]
sys.path.insert(0, repo + '/tools')
import rebase
A = rebase.Elf((pathlib.Path(ra)/'release/bin/demo').read_bytes())
B = rebase.Elf((pathlib.Path(rb)/'release/bin/demo').read_bytes())
def slot(e, w):
    off = w & 0xffff; off = off - 0x10000 if off >= 0x8000 else off
    a = e.gp + off
    lo, size = e.sec['.got'][0], e.sec['.got'][2]
    if not lo <= a < lo + size: return None
    return e.got.get(a) or e.where(e.u32(a))
files = subprocess.run(['git', '-C', repo, 'ls-files', 'tools', 'patch', 'test', 'docs'], capture_output=True, text=True).stdout.split()
for rel in files:
    if not rel.endswith(('.py', '.json', '.c', '.h', '.inc', '.S', '.md')): continue
    new = (pathlib.Path(repo)/rel).read_text(errors='replace').splitlines()
    old = subprocess.run(['git', '-C', repo, 'show', 'main:' + rel], capture_output=True, text=True).stdout.splitlines()
    oldset = set(old); oldtext = '\n'.join(old).lower()
    for i, line in enumerate(new, 1):
        for m in re.finditer(r'0x(8f[89][0-9a-f]{5})\b', line, re.I):
            w = int(m.group(1), 16)
            if (w >> 21) & 31 != 28: continue
            sa, sb = slot(A, w), slot(B, w)
            mark = 'UNCHANGED-LINE' if line in oldset else 'IN-MAIN-TOO' if m.group(0).lower() in oldtext else 'new-value'
            if sa != sb or sb is None:
                print(f'{rel}:{i}: {m.group(0)} {mark}  old slot={sa}  new slot={sb}')
