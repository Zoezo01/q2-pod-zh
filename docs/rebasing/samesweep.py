"""Hex literals in the stock executables' address range that a file has on main and on the branch alike:
samesweep.py REPO [BASE=main]. The rebase tool rewrites only what it judges an address; this lists what it left."""
import sys, re, subprocess, pathlib
repo = pathlib.Path(sys.argv[1]); base = sys.argv[2] if len(sys.argv) > 2 else 'main'
HEX = re.compile(r'(?<![0-9A-Za-z_])0[xX]([0-9a-fA-F]{6,7})(?=(?:[uU][lL]{0,2})?(?![0-9A-Za-z_]))')
files = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '--', 'patch', 'tools', 'test', 'docs'], text=True).split()
for rel in files:
    try: now = (repo/rel).read_text()
    except (UnicodeDecodeError, FileNotFoundError): continue
    old = subprocess.run(['git', '-C', str(repo), 'show', f'{base}:{rel}'], capture_output=True, text=True).stdout
    olds = {int(m[1], 16) for m in HEX.finditer(old)}
    for n, line in enumerate(now.split('\n'), 1):
        for m in HEX.finditer(line):
            v = int(m[1], 16)
            if 0x400000 <= v < 0xb30000 and v in olds:
                print(f'{rel}:{n}: {m[0]} | {line.strip()[:150]}')
