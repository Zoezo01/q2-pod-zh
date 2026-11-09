#!/usr/bin/env python3
"""What the last mutmut run found in targets.py's functions: a score, a table and each survivor's diff.

The score is what the tests caught of what they could: killed and timed out, over all but skipped.
A survivor or a mutant no test reaches counts against it. Exits 1 under --floor or with mutants unrun."""
import argparse
import ast
import collections
import difflib
import functools
import json
import pathlib
import re
import sys

from mutmut.stats import status_by_exit_code

import targets

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAUGHT = {'killed', 'timeout', 'segfault'}
SKIPPED = {'skipped', 'typecheck'}


def results(since=None):
    """{(module, function): {mutant name: status}} for the targets, function as targets.py names it."""
    found = {}
    for module, names in targets.targets(since).items():
        meta = ROOT/f'mutants/tools/{module}.py.meta'
        codes = json.loads(meta.read_text())['exit_code_by_key'] if meta.exists() else {}
        for name in names:
            prefix = targets.mutant_glob(module, name).removesuffix('*')
            found[module, name] = {key: 'not run' if code is None else status_by_exit_code[code]
                                   for key, code in codes.items() if key.startswith(prefix)}
    return found


@functools.cache
def generated(module):
    """mutmut's copy of module, every mutant in it: its lines and each function's span, parsed once."""
    source = (ROOT/f'mutants/tools/{module}.py').read_text()
    return source.splitlines(), {n.name: (n.lineno, n.end_lineno) for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}


def diffs(module, keys):
    """Each mutant's changed lines, from mutmut's generated copies; none of them runs."""
    lines, spans = generated(module)

    def body(name):
        first, last = spans[name]
        return [re.sub(r'\w+__mutmut_\w+', '', line) for line in lines[first:last]]
    out = {}
    for key in keys:
        mangled = key.rsplit('.', 1)[1]
        original = mangled.partition('__mutmut_')[0] + '__mutmut_orig'
        out[key] = [l for l in difflib.unified_diff(body(original), body(mangled), lineterm='', n=0)
                    if l[:1] in '+-' and l[:3] not in ('+++', '---')]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--floor', type=float, help='fail under this score, percent')
    ap.add_argument('--survivors', type=pathlib.Path, help='write each survivor and its diff here')
    ap.add_argument('--pending', action='store_true', help='print only the mutants not run yet, for mutmut run')
    ap.add_argument('--since', metavar='REV', help='only the functions added or changed since REV, as targets.py')
    args = ap.parse_args()
    found = results(args.since)
    if args.pending:
        print('\n'.join(k for mutants in found.values() for k, status in mutants.items() if status == 'not run'))
        return 0
    rows = []
    totals = collections.Counter()
    for (module, name), mutants in found.items():
        count = collections.Counter(mutants.values())
        totals.update(count)
        caught, counted = sum(count[s] for s in CAUGHT), len(mutants) - sum(count[s] for s in SKIPPED)
        rows.append((module, name, counted, caught, count['survived'], count['no tests'], count['not run']))
    caught = sum(r[3] for r in rows)
    counted = sum(r[2] for r in rows)
    score = 100 * caught / counted if counted else 0
    where = f'changed since {args.since}' if args.since else 'of tools/'
    if not rows:
        print(f'No functions {where} to mutate.')
        return 0
    print(f'**Mutation score {score:.1f}%**: {caught} of {counted} mutants caught in {len(rows)} functions {where}; '
          f'{totals["survived"]} survived, {totals["no tests"]} reached by no test, '
          f'{totals["timeout"]} timed out, {totals["not run"]} not run.\n')
    print('| Function | Mutants | Caught | Survived | No test | Score |\n|---|---:|---:|---:|---:|---:|')
    for module, name, n, ok, survived, untested, _ in sorted(rows, key=lambda r: (r[3] - r[2], r[0], r[1])):
        if ok < n:
            print(f'| `{module}.{name}` | {n} | {ok} | {survived} | {untested} | {100 * ok / n:.0f}% |')
    print(f'\n{sum(r[2] == r[3] for r in rows)} more functions have every mutant caught.')
    if args.survivors:
        with args.survivors.open('w') as f:
            for (module, name), mutants in found.items():
                lost = [k for k, status in mutants.items() if status in ('survived', 'no tests')]
                for key, lines in (diffs(module, lost) if lost else {}).items():
                    f.write(f'{key} ({mutants[key]})\n' + ''.join(f'    {line}\n' for line in lines))
    if totals['not run']:
        print(f'{totals["not run"]} mutants did not run', file=sys.stderr)
        return 1
    if args.floor is not None and score < args.floor:
        print(f'Mutation score {score:.1f}% is under the {args.floor:g}% floor', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
