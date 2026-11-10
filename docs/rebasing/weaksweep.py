"""Literals the tool gave to one binary on weak evidence although another binary's code holds the same value: each is
a rewrite that may have used the wrong binary's map. weaksweep.py REPO COMMIT OLD_ROOT NEW_ROOT (python3 -I).
COMMIT is the tree before `rebase.py apply` (the old layout)."""
import sys, pathlib, subprocess
repo = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(repo/'tools'))
import rebase
commit, old, new = sys.argv[2], pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
class Tree:
    def __init__(self, root, version): self.root, self.version = root, version
    def cat(self, path): return (self.root/path).read_bytes()
maps = rebase.open_maps(Tree(old, 'V1.32'), Tree(new, 'V1.34'))
views = {b: rebase.view(mp.a) for b, mp in maps.items()}
git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], capture_output=True, text=True, check=True).stdout
files = [f for f in git('ls-tree', '-r', '--name-only', commit, *rebase.SCANNED).split('\n')
         if f and f not in rebase.SKIPPED and f != 'tools/stock_map.tsv' and not f.endswith(('.png', '.bin', '.ttf', '.zip', '.jpg', '.gif', '.wav', '.mp3', '.flac', '.ogg'))]
texts = {}
for f in files:
    try: texts[f] = git('show', f'{commit}:{f}')
    except Exception: pass
judged = rebase.judge_tree(texts, views)
for rel, (lits, verdict) in judged.items():
    rows = texts[rel].split('\n')
    for n, (b, how) in sorted(verdict.items()):
        lit = lits[n]
        if how in rebase.SOLID or lit.kind != 'hex' or b == '?': continue
        x = lit.value
        others = [o for o in views if o != b and not x & 3 and views[o].function(x) is not None and maps[o].a.d != maps[o].b.d]
        if not others and maps[b].a.d != maps[b].b.d: continue
        if not others: continue
        y = maps[b].lookup(x)[0]
        alt = {o: maps[o].lookup(x)[0] for o in others}
        i = rows[lit.line].find(f'{x:#x}')
        print(f"{rel}:{lit.line + 1}: {x:#x} given to {b} ({how}) -> {y and hex(y)}; also {', '.join(f'{o} -> {v and hex(v)}' for o, v in alt.items())} | {rows[lit.line][max(0, i - 70):i + 50].strip()}")
