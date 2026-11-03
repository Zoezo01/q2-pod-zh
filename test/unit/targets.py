#!/usr/bin/env python3
"""What mutmut covers: every tools/ function, or with --since REV those added or changed since REV,
as mutant name globs. A pull request's run mutates what it changes; the nightly run mutates it all.

mutmut's own generator picks them, so each one has mutants. It skips a decorated function (but for a
lone @staticmethod or @classmethod), one with nothing to mutate, such as `return self.x`, and one with
`# pragma: no mutate block` on its def line. build() has the pragma as too big to mutate, art's wave()
as it runs only at import, before mutmut can switch a mutant on, and the others as they pin the SHA-256
of a Shanling file or link against one, so no stock-free test can reach them: test/<name>.py runs them
against the real thing, given a build.
mutmut mutates Python only, so patch/*.c is out of scope here (docs/building.md#mutation-tests)."""
import argparse
import pathlib
import subprocess

# mutmut is pinned in test/unit/requirements.txt; the hashes are those mutate_file_contents gives mutants/.
from mutmut.mutation.file_mutation import _compute_mutated_function_hashes, create_mutations
from mutmut.utils.file_utils import change_cwd

ROOT = pathlib.Path(__file__).resolve().parents[2]
SETTINGS = ROOT  # mutmut reads pyproject.toml from the working directory; the tests move ROOT alone


def functions(source):
    """{name: hash} of the top-level functions and methods mutmut makes mutants for; it mutates a
    nested function with its parent. The hash is mutmut's, of the function's code, not its layout."""
    with change_cwd(SETTINGS):
        module, mutations, _, _ = create_mutations('tools', source)
    hashes = _compute_mutated_function_hashes(source, module, mutations)
    return {key[2:].replace('ǁ', '.'): h for key, h in hashes.items()}  # x_function, xǁClassǁmethod


def changed(path, base):
    """Names in path, a file in tools/, added or changed since base."""
    text = path.read_text()
    shown = subprocess.run(['git', '-C', ROOT, 'show', f'{base}:tools/{path.name}'], capture_output=True, text=True)
    if shown.stdout == text:
        return []  # unchanged: skip mutmut's parse, a second for a big file
    old = functions(shown.stdout) if shown.returncode == 0 else {}
    return [name for name, h in functions(text).items() if old.get(name) != h]


def mutant_glob(module, name):
    """mutmut's name for name's mutants: x_function, or xǁClassǁmethod."""
    mangled = f'xǁ{name.replace(".", "ǁ")}' if '.' in name else f'x_{name}'
    return f'tools.{module}.{mangled}__mutmut_*'


def targets(since=None):
    """{module: [function]}: all of each tools/ module's, or those changed since a revision."""
    if since:
        # Without the revision, every function would look new.
        subprocess.run(['git', '-C', ROOT, 'rev-parse', '--verify', f'{since}^{{commit}}'], check=True, capture_output=True)
    return {path.stem: changed(path, since) if since else list(functions(path.read_text()))
            for path in sorted((ROOT/'tools').glob('*.py'))}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--since', metavar='REV', help='only the functions added or changed since REV')
    ap.add_argument('--names', action='store_true', help='print module.function, not mutant globs')
    args = ap.parse_args()
    for module, names in targets(args.since).items():
        for name in names:
            print(f'{module}.{name}' if args.names else mutant_glob(module, name))
