#!/usr/bin/env python3
"""The hill-climb's scoreboard: each metric as `metric<TAB>value<TAB>work<TAB>errors`, one command, no judgment.

work is what a number stands on (tests passed, mutants tested, scenarios run, inputs fed), so a number that
moved because less ran shows as work that shrank. --check BASE.tsv exits 1 naming each metric that got worse,
each error, each work count that shrank and each metric that went missing.

  T1  C coverage of patch/, line and branch percent: test/fw and the host suites through test/coverage/cc    up
      (worse only if the count not covered, _missed, rose as well: covered code taken out lowers the percent)
  T2  C mutation score of the wired files --t2 names (test/cmut, in a bubblewrap sandbox); T2.wired, how many up
  R1  known bugs: xfail marks in test/, however they are written                                              down
  R2  parsers of PARSERS with a passing test_fuzz_<name> in test/fw, under ASan and UBSan                      up
  P1  instructions on each path test/patch.py counts (its COST lines), against --build; not run without one,
      as with test/patch.py and what a build is made from unchanged the counts can't have moved               down
  P2  peak bytes the firmware allocates opening a big library's screens (test/fw/peak.py)                     down
  P3  payload and rootfs bytes of --build; a gate: more than GATE over base is worse                          gate
  S1  shell-outs in patch/ fed outside input, in a function no passing @pytest.mark.hostile test names        down
  S2  refusals of REFUSALS a passing @pytest.mark.refusal test names                                          up

A number here is this machine's: the emulator's instructions and a 64-bit host's bytes, not the Q2's.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
WORK = Path.home()/'.cache/q2-hillclimb/score'/ROOT.name  # main() adds a folder for the run
WAY = {'T1': 'up', 'T2': 'up', 'R1': 'down', 'R2': 'up', 'P1': 'down', 'P2': 'down', 'P3': 'gate', 'S1': 'down', 'S2': 'up'}
GATE = 0.005
FLAKY = {'T1.branch_missed': 1}  # one branch of playlist_art.c is taken on some runs and not on others
HOST_SUITES = 'library_scope mp3 peq coverflow coverflow_position chapters mp3_gapless remote build rotator_build'.split()  # firmware.yml's
PARSERS = ('id3', 'mp4_chapters', 'lrc', 'cue', 'm3u', 'flac_frame', 'mp3_gapless', 'ogg_tags', 'config_ini', 'ratings_tsv',
    'durations_cache', 'lyrics_response', 'scrobble_response', 'radio_response', 'release_list')
FUZZ_SECONDS = 10
SINKS = r'\b(?:system|popen|execl|execlp|execle|execv|execvp|execve|posix_spawn|posix_spawnp)\s*\('
# patch/'s shell-outs by function, each read once (S1). These, and this many in each, take no outside value, so no
# hostile one can be fed; remote.c's is q2remote's shell, a command by design once the card's key is given
# (test/remote.py). Any other shell-out is fed a file name, tag, URL or response and wants its test: one more in
# a function here, or one in a function this table doesn't name, counts as fed, so a new one can't arrive unread.
CONST = {'netupdate.c:install': 3, 'netupdate.c:sh_worker': 2, 'addon.c:game_kill': 1, 'spotify.c:spot_launch': 1,
    'spotify.c:spot_stop': 1, 'spotify.c:spot_poll': 1, 'navigation.c:rockbox_open': 1, 'radio.c:radio_halt': 1,
    'system.c:reboot_record': 1, 'system.c:logo_sync': 1, 'remote.c:serve': 1}
# Stock's own system() calls whose command a hook here builds: no sink in the source to find.
HOOKS = ('folder_delete.c:ringnav_folder_rm', 'onlinematch.c:ringnav_lyric_save', 'onlinematch.c:ringnav_lyric_drop',
    'onlinematch.c:ringnav_cover_copy')
# Each install path's refusals (S2): Network Update, and the Apps list's install (both netupdate.c). update:traversal
# is left out: update.tar goes to one fixed path and only stock's updater unpacks it.
REFUSALS = ('update:hash', 'update:truncated', 'update:oversize', 'update:changed',
    'addon:hash', 'addon:truncated', 'addon:oversize', 'addon:traversal', 'addon:changed')


def run(name, cmd, env=None, private_tmp=False):
    """cmd's exit code, its output in WORK/<name>.log. private_tmp: a /tmp of its own, for suites that build in fixed folders there."""
    if private_tmp and shutil.which('bwrap'):
        cmd = ['bwrap', '--bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--die-with-parent', *cmd]
    with open(WORK/f'{name}.log', 'w') as log:
        return subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT).returncode


def pytest(name, args, env=None):
    """(exit code, {(module, function): (passed, seconds, cases passed)}) of a pytest run; a parametrized function passes
    when all its cases do."""
    xml = WORK/f'{name}.xml'
    code = run(name, [PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', str(WORK/f'tmp-{name}'),
        f'--junitxml={xml}', *args], env)
    cases: dict = {}
    if xml.exists():
        for case in ET.parse(xml).iter('testcase'):
            key = (case.get('classname', '').rsplit('.', 1)[-1], case.get('name', '').split('[')[0])
            ok = not any(case.find(x) is not None for x in ('failure', 'error', 'skipped'))
            was, seconds, count = cases.get(key, (True, 0.0, 0))
            cases[key] = (was and ok, seconds + float(case.get('time', 0)), count + ok)
    return code, cases


def t1():
    cov = WORK/'cov'
    cov.mkdir()
    env = {**os.environ, 'Q2_COV': str(cov), 'PATH': f'{ROOT}/test/coverage:{os.environ["PATH"]}'}
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        fw = pool.submit(pytest, 't1-fw', ['test/fw'], env)
        suites = list(pool.map(lambda s: run(f't1-{s}', [PY, f'test/{s}.py'], env, private_tmp=True), HOST_SUITES))
    code, cases = fw.result()
    work = sum(count for *_, count in cases.values()) + suites.count(0)  # each case, so a list of them cut short shows
    errors = bool(code) + len(suites) - suites.count(0)
    summary = WORK/'t1.json'
    errors += bool(run('t1-gcovr', [PY, '-m', 'gcovr', '--root', '.', '--filter', 'patch/',
        '--gcov-ignore-errors=source_not_found', '--json-summary', str(summary), str(cov)]))
    s = json.loads(summary.read_text()) if summary.exists() else {}
    return [row for kind in ('line', 'branch') for row in (
        (f'T1.{kind}', round(100*s.get(f'{kind}_covered', 0)/max(s.get(f'{kind}_total', 0), 1), 3), work, errors),
        (f'T1.{kind}_missed', s.get(f'{kind}_total', 0) - s.get(f'{kind}_covered', 0), work, errors))]


def wired(source):
    """sandbox.sh's own table for a source: (test, cflags or None for a test/fw file), or None if it isn't wired."""
    text = (ROOT/'test/cmut/sandbox.sh').read_text()
    table = text[text.index('\ncase $source in'):text.index('\nesac\n') + 6]
    result = subprocess.run(['sh', '-c', f'fw=; cflags=; source={source}{table}printf "%s\\n%s\\n%s" "$test" "$fw" "$cflags"'],
        capture_output=True, text=True)
    if result.returncode: return None
    test, fw, cflags = result.stdout.split('\n', 2)
    return test, None if fw else ' '.join(cflags.split())


def t2(names):
    sources = sorted(p for p in (ROOT/'patch').glob('*.c') if wired(f'patch/{p.name}'))
    rows: list[tuple] = [('T2.wired', len(sources), len(sources), 0)]
    for name in ([p.stem for p in sources] if names == ['all'] else names):
        source = f'patch/{name}.c'
        table = wired(source)
        if not table:
            rows.append((f'T2.{name}', 0, 0, 1))
            continue
        test, cflags = table
        mutants, results = WORK/f'cmut-{name}', WORK/f'cmut-{name}.jsonl'
        errors = bool(run(f't2-{name}-mutate', [PY, 'test/cmut/mutate.py', source, str(mutants)]))
        # sandbox.sh's fence without Docker: no network, a read-only root, only a tmpfs to write; results leave on stdout
        cmd = ['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--unshare-net',
            '--unshare-pid', '--unshare-ipc', '--die-with-parent', '--new-session', PY, str(ROOT/'test/cmut/run.py'),
            '--tree', str(ROOT), '--mutants', str(mutants), '--done', '/dev/null', '--source', source, '--test', test,
            '--workers', str(max(4, (os.cpu_count() or 8)//2)), *(['--fw'] if cflags is None else [f'--cflags={cflags}'])]
        with open(results, 'w') as out, open(WORK/f't2-{name}.log', 'w') as log:
            errors += bool(subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=log).returncode)
        records = {}
        for line in results.read_text().splitlines():
            try: records[json.loads(line)['id']] = json.loads(line)
            except ValueError: errors += 1
        mutants = [r for id, r in records.items() if not id.startswith('_')]  # as report.py: _end and the canary aside
        caught = sum(r.get('status') in ('assert', 'crash', 'timeout', 'error') for r in mutants)
        tested = caught + sum(r.get('status') == 'survived' for r in mutants)
        errors += '_end' not in records or bool(records['_end'].get('canary_failed'))  # stopped short, or tests that catch nothing
        rows.append((f'T2.{name}', round(100*caught/max(tested, 1), 2), tested, errors))
    return rows


def test_files():
    return sorted(p for p in (ROOT/'test').rglob('*.py') if p != Path(__file__).resolve())


def r1():
    texts = [p.read_text(errors='replace') for p in test_files()]
    bugs = sum(len(re.findall(r'\bxfail\(', t)) for t in texts)  # all of them, so rewording one takes none off
    return [('R1', bugs, sum(len(re.findall(r'^\s*def test_\w+', t, re.M)) for t in texts), 0)]


def r2():
    log = WORK/'fuzz.log'
    env = {**os.environ, 'Q2_FW_SANITIZE': '1', 'ASAN_OPTIONS': 'detect_leaks=0:allocator_may_return_null=1', 'Q2_FUZZ_LOG': str(log),
        'LD_PRELOAD': subprocess.run(['cc', '-print-file-name=libasan.so'], capture_output=True, text=True).stdout.strip()}
    results, names = {}, []
    for path in sorted((ROOT/'test/fw').glob('test_*.py')):
        found = re.findall(r'^def test_fuzz_(\w+)\(', path.read_text(), re.M)
        if not found: continue
        names += found
        # a file a process: a crash under ASan ends the run it is in
        _, cases = pytest(f'r2-{path.stem}', ['-o', 'junit_duration_report=call',  # not the library's build
            *(f'{path.relative_to(ROOT)}::test_fuzz_{n}' for n in found)], env)
        results.update({function[len('test_fuzz_'):]: result for (_, function), result in cases.items()})
    inputs: dict = {}
    for line in (log.read_text().splitlines() if log.exists() else []):
        name, count = line.split('\t')
        inputs[name] = inputs.get(name, 0) + int(count)
    good = [n for n in set(names) if n in PARSERS and results.get(n, (False, 0))[0] and results[n][1] <= FUZZ_SECONDS and inputs.get(n)]
    return [('R2', len(good), sum(inputs[n] for n in good), len(names) - len(good))]


def sites():
    """Each shell-out in patch/*.c, as file:function, comments aside, and each of HOOKS still there."""
    found = []
    for path in sorted((ROOT/'patch').glob('*.c')):
        # one pass, so a /* inside a string opens no comment and a quote inside a comment no string
        text = re.sub(r'''"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|/\*.*?\*/|//[^\n]*''',
            lambda m: '""' if m[0][0] in '"\'' else '\n'*m[0].count('\n'), path.read_text(errors='replace'), flags=re.S)
        function = None
        for line in text.splitlines():
            definition = re.match(r'[A-Za-z_][\w\s*]*?\b(\w+)\s*\(', line)  # at column 0
            if definition and ('{' in line or not line.rstrip().endswith(';')): function = definition[1]
            if not line[:1].isspace() and '{' not in line: continue  # a prototype, not a call
            found += [f'{path.name}:{function}']*len(re.findall(SINKS, line))
        found += [h for h in HOOKS if h.startswith(path.name + ':') and re.search(rf'\b{h.split(":")[1]}\s*\(', text)]
    return found


def marked(kind):
    """{name: [(file, function)]} for each @pytest.mark.<kind>('name', ...) on a test/fw function."""
    found: dict = {}
    for path in sorted((ROOT/'test/fw').glob('test_*.py')):
        pending: list = []
        for line in path.read_text().splitlines():
            mark = re.match(rf'@pytest\.mark\.{kind}\((.*)\)', line)
            test = re.match(r'def (test_\w+)\(', line)
            if mark: pending += re.findall(r"'([^']+)'", mark[1])
            elif test:
                for name in pending: found.setdefault(name, []).append((path, test[1]))
                pending = []
            elif not line.startswith('@'): pending = []
    return found


def security():
    hostile, refusal = marked('hostile'), marked('refusal')
    tests = sorted({f'{path.relative_to(ROOT)}::{function}' for found in (hostile, refusal) for group in found.values()
        for path, function in group})
    cases = pytest('security', tests)[1] if tests else {}
    def passing(found, name): return [t for t in found.get(name, []) if cases.get((t[0].stem, t[1]), (False, 0))[0]]
    def counts(found): return (sum(bool(passing(found, n)) for n in found), sum(not passing(found, n) for n in found))
    found = sites()
    open_sites = [s for i, s in enumerate(found) if found[:i].count(s) >= CONST.get(s, 0) and not passing(hostile, s)]
    for site in sorted(set(open_sites)): print(f'S1 open: {site} x{open_sites.count(site)}', file=sys.stderr)
    done = [r for r in REFUSALS if passing(refusal, r)]
    for case in sorted(set(REFUSALS) - set(done)): print(f'S2 open: {case}', file=sys.stderr)
    unlisted = len(set(refusal) - set(REFUSALS))
    return [('S1', len(open_sites), counts(hostile)[0], counts(hostile)[1]),
        ('S2', len(done), counts(refusal)[0], counts(refusal)[1] + unlisted)]


def p1(build):
    lock = Path.home()/'.cache/q2-build.lock'  # one build or MIPS suite at a time on this machine
    python = os.environ.get('Q2_PATCH_PYTHON') or shutil.which('python3') or PY  # the one that built: docs/building.md
    code = run('p1', [*(['flock', str(lock)] if lock.exists() else []), python, 'test/patch.py', str(build)])
    text = (WORK/'p1.log').read_text(errors='replace')
    scenarios = re.search(r'^(\d+) MIPS execution scenarios passed', text, re.M)
    work, errors = int(scenarios[1]) if scenarios else 0, int(bool(code) or not scenarios)
    rows: list[tuple] = []
    seen: dict = {}
    for name, count in re.findall(r'^COST\t(.+)\t(\d+)$', text, re.M):
        seen[name] = seen.get(name, 0) + 1
        rows.append((f'P1.{name.replace(" ", "_")}' + (f'#{seen[name]}' if seen[name] > 1 else ''), int(count), work, errors))
    return rows or [('P1', 0, work, 1)]


def p2():
    script = ROOT/'test/fw/peak.py'
    if not script.exists(): return []
    code = run('p2', [PY, str(script)])
    rows = [(f'P2.{name}', int(peak), int(calls), int(bool(code)))
        for name, peak, calls in re.findall(r'^(\w+)\t(\d+)\t(\d+)$', (WORK/'p2.log').read_text(), re.M)]
    return rows or [('P2', 0, 0, 1)]


def p3(build):
    manifest = build/'manifest.json'
    if not manifest.exists(): return [('P3', 0, 0, 1)]
    return [('P3.payload', json.loads(manifest.read_text())['patch_bytes'], 1, 0),
        ('P3.rootfs', (build/'rootfs.squashfs').stat().st_size, 1, 0)]


def check(rows, base, skipped):
    was = {name: (float(value), int(work)) for name, value, work, _ in (line.split('\t') for line in base.read_text().splitlines())}
    bad = []
    now = {name: value for name, value, *_ in rows}
    for name, value, work, errors in rows:
        if errors: bad.append(f'{name}: {errors} errors')
        if name not in was: continue
        before, before_work = was[name]
        way = WAY[name[:2]]
        if name[:2] == 'T1':  # the percent and the count go together
            missed = name if name.endswith('_missed') else name + '_missed'
            if name == missed or now.get(missed, 0) <= was.get(missed, (0, 0))[0] + FLAKY.get(missed, 0): way = 'either'
        if (way == 'up' and value < before or way == 'down' and value > before
                or way == 'gate' and value > before*(1 + GATE)): bad.append(f'{name}: worse, {before:g} to {value:g}')
        # a wired file's mutants are as many as its source makes: fewer is no less work
        if work < before_work and name[:2] != 'T2': bad.append(f'{name}: work shrank, {before_work} to {work}')
    ran = {name for name, *_ in rows}
    bad += [f'{name}: missing' for name in was if name not in ran and name[:2] not in skipped]
    for line in bad: print(line, file=sys.stderr)
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', help='one metric: T1, T2, R1, R2, P1, P2, P3, S1 or S2 (S1 and S2 run together); T2:NAME for one file')
    parser.add_argument('--t2', default='', help="wired files to mutate, as lockscreen,photos, or all; a full run leaves T2's scores out without it")
    parser.add_argument('--build', type=Path, help='a build of this tree (tools/build.py --out), for P1 and P3')
    parser.add_argument('--check', type=Path, help='a saved run: exit 1 if anything is worse than it')
    args = parser.parse_args()
    only, _, file = (args.only or '').partition(':')
    names = [file] if file else [n for n in args.t2.split(',') if n]
    jobs = {'T1': t1, 'T2': lambda: t2(names), 'R1': r1, 'R2': r2, 'P2': p2, 'S1': security,
        **({'P1': lambda: p1(args.build), 'P3': lambda: p3(args.build)} if args.build else {})}
    if only: jobs = {k: v for k, v in jobs.items() if k == {'S2': 'S1'}.get(only, only)}
    if not jobs: parser.error(f'{only}: not a metric, or one that needs --build')
    global WORK
    WORK = WORK/(args.only or 'all').replace(':', '-')  # so one metric's run doesn't clear another's
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    with concurrent.futures.ThreadPoolExecutor(len(jobs)) as pool:
        rows = [row for group in pool.map(lambda job: job(), jobs.values()) for row in group]
    for row in sorted(rows): print(*row, sep='\t')
    if not args.build and not only: print('P1 and P3 need --build: not run', file=sys.stderr)
    # What this run was not asked for is not missing: another metric under --only, P1 and P3 with no build, and
    # the T2 scores --t2 didn't name.
    skipped = set(WAY) - set(jobs) - ({'S2'} if 'S1' in jobs else set()) | {'T2'}
    sys.exit(check(rows, args.check, skipped) if args.check else 0)


if __name__ == '__main__': main()
