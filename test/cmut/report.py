#!/usr/bin/env python3
"""The score of one run.py results file, how each mutant was caught, and every survivor as a diff."""
import argparse
import collections
import difflib
import json
from pathlib import Path
import shutil

CAUGHT = ('assert', 'crash', 'timeout', 'error')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('mutants', type=Path)
    parser.add_argument('results', type=Path)
    parser.add_argument('--survivors', type=Path, help='write each survivor as a diff here')
    parser.add_argument('--copy', type=Path, help='copy each survivor into this folder, to run them alone again')
    parser.add_argument('--floor', type=float, help='exit 1 under this score, in percent, or if the run stopped short')
    args = parser.parse_args()
    records = {}
    meta = []
    for line in open(args.results, encoding='utf-8'):
        record = json.loads(line)
        if record['id'].startswith('_'): meta.append(record)
        else: records[record['id']] = record
    generated = len(list(args.mutants.glob('*.c')))
    counts = collections.Counter(r['status'] for r in records.values())
    caught = sum(counts[s] for s in CAUGHT)
    tested = caught + counts['survived']
    score = 100 * caught / tested if tested else 0
    seconds = sum(r.get('seconds', 0) for r in records.values())
    print(f'generated {generated}, answered {len(records)}: {counts["invalid"]} rejected by the compiler, '
        f'{counts["same"]} same object as the original, {counts["duplicate"]} same object as another mutant')
    print(f'tested {tested}: caught {caught} ({counts["assert"]} assertion, {counts["crash"]} crash, '
        f'{counts["timeout"]} timeout, {counts["error"]} other error), survived {counts["survived"]}')
    print(f'score {score:.1f}%; test time {seconds:.0f} s, {seconds / tested if tested else 0:.2f} s a mutant')
    for record in meta:
        if record['id'] != '_baseline': print(record)
    if args.survivors:
        original = args.source.read_text(encoding='utf-8').splitlines()
        with open(args.survivors, 'w', encoding='utf-8') as out:
            for name, record in sorted(records.items()):
                if record['status'] != 'survived': continue
                mutant = (args.mutants/name).read_text(encoding='utf-8').splitlines()
                out.write(f'=== {name}\n')
                for line in list(difflib.unified_diff(original, mutant, lineterm='', n=0))[2:]:
                    out.write(line + '\n')
    if args.copy:
        # With their lines of mutate.py's manifest, by which run.py picks the tests that reach each one.
        args.copy.mkdir(parents=True, exist_ok=True)
        left = {name for name, record in records.items() if record['status'] == 'survived'}
        for name in left: shutil.copyfile(args.mutants/name, args.copy/name)
        manifest = args.mutants/'manifest.jsonl'
        if manifest.exists():
            with open(args.copy/'manifest.jsonl', 'w', encoding='utf-8') as out:
                out.writelines(line for line in open(manifest, encoding='utf-8') if json.loads(line)['id'] in left)
    # A stopped run, or one whose original stopped passing, can't vouch for its score.
    short = len(records) < generated or any(r['id'] == '_canary_failed' for r in meta)
    if short: print(f'stopped short: {generated - len(records)} mutants unanswered')
    if args.floor is not None and (score < args.floor or short): raise SystemExit(1)

if __name__ == '__main__':
    main()
