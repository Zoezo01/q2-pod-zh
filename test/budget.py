#!/usr/bin/env python3
"""Where one test/patch.py case spends its instruction budget, by function: test/patch.py allows 100,000 a call,
so a split that makes a hot path dearer shows here as the function that grew.

  python3 test/budget.py BUILD CASE   # CASE: N of a trace's `== N` line, the Nth passed() check

Runs test/patch.py BUILD up to case CASE and counts each instruction the emulator runs on the way to it, from the
check before (case CASE-1), then stops. For each call it prints what test/patch.py's limit counts against that
limit (Machine.budget, 100,000 unless a machine raises it): Unicorn starts its count again at every emu_start,
so a call with a nested run (a mock calling back into guest code) is counted from the last nested start. Then
it prints the largest call's instructions per function, most first, over the whole call, nested runs too."""
import argparse
import bisect
import collections
import os
import pathlib
import runpy
import subprocess
import sys
import traceback

import unicorn

PATCH = str(pathlib.Path(__file__).resolve().with_name('patch.py'))


def functions(*elves):
    """Sorted [(address, name)] of every FUNC symbol in elves."""
    out = {}
    for elf in elves:
        for s in subprocess.run(['readelf', '-Ws', elf], check=True, capture_output=True, text=True).stdout.splitlines():
            s = s.split()
            if len(s) >= 8 and s[3] == 'FUNC' and int(s[1], 16): out[int(s[1], 16)] = s[7]
    return sorted(out.items())


def by_function(counts, table):
    """{name: instructions} from {address: instructions}; an address below every symbol is named in hex."""
    starts = [a for a, _ in table]
    out = collections.Counter()
    for a, n in counts.items():
        i = bisect.bisect_right(starts, a) - 1
        out[table[i][1] if i >= 0 and a < 0x1000000 else 'mocks' if a >= 0x1000000 else '%08x' % a] += n
    return out


assert by_function({0x10: 2, 0x14: 1, 0x24: 5, 0x4: 1, 0x1000004: 3}, [(0x10, 'f'), (0x20, 'g')]) == {'f': 3, 'g': 5, '00000004': 1, 'mocks': 3}


def window(checks, case):
    """What an emu_start does at checks passed: case CASE's calls run while CASE-1 have passed."""
    return 'stop' if checks >= case else 'count' if checks == case - 1 else 'run'


assert [window(n, 5) for n in (3, 4, 5, 6)] == ['run', 'count', 'stop', 'stop']


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('build'); p.add_argument('case', type=int)
    a = p.parse_args()
    build = pathlib.Path(a.build)
    sys.path.insert(0, str(pathlib.Path(PATCH).parents[1] / 'tools'))
    from build import PRIVATE_FUNCTIONS  # stock functions the payload names that stock's symbols don't
    table = sorted({**dict(functions(build / 'stock-demo', build / 'patch.elf')), **{v: k for k, v in PRIVATE_FUNCTIONS.items()}}.items())
    calls = []  # per top-level emu_start: its line in patch.py, the limit, {address: instructions}, total, mark
    depth = [0]
    start = unicorn.Uc.emu_start

    def report():
        for c in calls: print(f"call\tline {c['line']}\t{c['n'] - c['mark']} of {c['limit']}\t({c['n']} with nested runs)")
        if not calls: print(f'case {a.case}: no emulator calls')
        else:
            c = max(calls, key=lambda c: c['n'] - c['mark'])
            print(f"largest\tline {c['line']}\t{c['n'] - c['mark']} of {c['limit']}; by function, all {c['n']}:")
            for name, n in by_function(c['counts'], table).most_common(30): print(f'{n}\t{name}')
        sys.stdout.flush()
        os._exit(0)

    def emu_start(u, begin, until, timeout=0, count=0):
        frame = sys._getframe(1)
        while frame and not (frame.f_code.co_filename == PATCH and frame.f_code.co_name == '<module>'): frame = frame.f_back
        if depth[0]:  # a nested run: Unicorn's count, and so the limit, starts again here
            calls[-1]['mark'], calls[-1]['limit'] = calls[-1]['n'], count
            return start(u, begin, until, timeout, count)
        to = window(frame.f_globals.get('checks', 0) if frame else 0, a.case)
        if to == 'stop': report()
        if to == 'run': return start(u, begin, until, timeout, count)
        c = {'line': frame.f_lineno if frame else 0, 'limit': count, 'counts': collections.defaultdict(int), 'n': 0, 'mark': 0}
        counts = c['counts']

        def tally(_u, address, _size, _d): counts[address] += 1; c['n'] += 1
        calls.append(c)
        hook = u.hook_add(unicorn.UC_HOOK_CODE, tally)
        depth[0] += 1
        try: return start(u, begin, until, timeout, count)
        finally: depth[0] -= 1; u.hook_del(hook)

    unicorn.Uc.emu_start = emu_start
    sys.argv = [PATCH, str(build)]
    try: runpy.run_path(PATCH, run_name='__main__')
    except BaseException: traceback.print_exc()  # an instruction limit reached: the profile still prints
    report()


if __name__ == '__main__':
    main()
