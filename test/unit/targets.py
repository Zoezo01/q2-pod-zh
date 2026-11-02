#!/usr/bin/env python3
"""What the weekly mutmut run covers: tools/ functions changed since BASE, as mutant name globs.

mutmut mutates Python only, so patch/*.c is out of scope here (docs/building.md#mutation-tests)."""
import ast
import pathlib
import subprocess
import sys

BASE = '1.2.0'
ROOT = pathlib.Path(__file__).resolve().parents[2]
# Changed since BASE, but no stock-free test can reach them: each pins the SHA-256 of a Shanling
# file, or links against one. test/<name>.py runs them against the real thing, given a build.
NEEDS_STOCK = {
    'aic.py': {'validate_stock', 'patch_module'},
    'build.py': {'build', 'compile_payload'},
    'ipod.py': {'patch_asset'},
    'peq.py': {'compile_common', 'patch_player'},
    'pwm.py': {'link', 'patch_pwm', 'patch_mcu', 'patch_bundle'},
    'rotator.py': {'patch_module'},
}


def functions(source):
    """{name: AST} of top-level functions and methods; mutmut mutates a nested one with its parent."""
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            found[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef):
            found.update((f'{node.name}.{m.name}', ast.dump(m)) for m in node.body if isinstance(m, ast.FunctionDef))
    return found


def changed(path, base=BASE):
    """Names in path, a file in tools/, added or changed since base."""
    now = functions(path.read_text())
    shown = subprocess.run(['git', '-C', ROOT, 'show', f'{base}:tools/{path.name}'], capture_output=True, text=True)
    old = functions(shown.stdout) if shown.returncode == 0 else {}
    return [name for name, tree in now.items() if old.get(name) != tree]


def mutant_glob(module, name):
    """mutmut's name for name's mutants: x_function, or xǁClassǁmethod."""
    mangled = f'xǁ{name.replace(".", "ǁ")}' if '.' in name else f'x_{name}'
    return f'tools.{module}.{mangled}__mutmut_*'


def targets():
    # Without the tag, every function would look new.
    subprocess.run(['git', '-C', ROOT, 'rev-parse', '--verify', f'{BASE}^{{commit}}'], check=True, capture_output=True)
    found = {}
    for path in sorted((ROOT/'tools').glob('*.py')):
        names = changed(path)
        excluded = NEEDS_STOCK.get(path.name, set())
        if stale := excluded - set(functions(path.read_text())):
            raise SystemExit(f'targets.py: {path.name} no longer has {sorted(stale)}')
        found[path.stem] = [name for name in names if name not in excluded]
    return found


if __name__ == '__main__':
    for module, names in targets().items():
        for name in names:
            print(mutant_glob(module, name) if '--names' not in sys.argv else f'{module}.{name}')
