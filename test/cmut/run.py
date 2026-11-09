#!/usr/bin/env python3
"""Judge a directory of C mutants, each a whole copy of one source file, against that file's tests.

Runs inside sandbox.sh's container. Results go to stdout as JSON lines, so nothing outside /tmp needs to be
writable, and ids already in --done are skipped, as a job can be stopped halfway. First every mutant is compiled
as its tests build it: one the compiler rejects is invalid, and one that compiles to the original's object or
to an earlier mutant's can't behave differently, so it isn't run. None of those count towards the score.

A host suite (--test test/x.py) builds the file itself, with --cflags. With --fw, the test is a test/fw file, which
pytest runs against test/fw's library: test/fw/host.py builds it once, and each mutant's object is linked in place
of the original's (conftest.py's Q2_FW_LIBRARY), so a mutant compiles one file, not the firmware. Only the tests that
run the mutant's lines run, as gcov counts them for the original (Reach), and they stop at the first failure.

A compile that fails without the compiler naming a line of the source, or for want of room or memory, is the
sandbox's failure, not the mutant's: the run stops there, unanswered, rather than count it as rejected.
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import resource
import select
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time

CANARY_EVERY = 64  # mutants between runs of the original, which has to keep passing
GIB = 1 << 30
BASE = Path('/tmp/base')  # --fw: host.py's build of the firmware, shared by the workers
OBJECTS = Path('/tmp/o')  # --fw: each mutant's object, until it has been tested
SPARE = 256 << 20  # --fw: room on /tmp under which a mutant's object is compiled again for its test, not kept
COV = Path('/tmp/cov')  # --fw: the original built with gcov's counters, and each test's counts
# --fw: each run of pytest forks from a process that has already imported it and test/fw's host.py with tools/,
# which would otherwise take a third of a second a mutant. Answers each job's pid, then its exit status.
FORK_SERVER = r'''
import importlib.metadata, json, os, resource, sys, tempfile
sys.path.insert(0, sys.argv[1])
import pytest, host
importlib.metadata.entry_points(group='pytest11')
answer, null = os.fdopen(os.dup(1), 'w'), os.open(os.devnull, os.O_RDONLY)
for line in sys.stdin:
    job = json.loads(line)
    pid = os.fork()
    if not pid:
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (job['cpu'], job['cpu'] + 5))
        log = os.open(job['log'], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.dup2(null, 0), os.dup2(log, 1), os.dup2(log, 2)
        os.chdir(job['cwd'])
        os.environ.clear()
        os.environ.update(job['env'])
        tempfile.tempdir = None
        code = pytest.main(job['args'])
        sys.stdout.flush(), sys.stderr.flush()
        os._exit(int(code))
    print(pid, file=answer, flush=True)
    status = os.waitpid(pid, 0)[1]
    print(-os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status), file=answer, flush=True)
'''

out_lock = threading.Lock()
def emit(**record):
    with out_lock:
        os.write(1, (json.dumps(record, ensure_ascii=False) + '\n').encode())

def numbered(path):
    return [int(s) if s.isdigit() else s for s in re.split(r'(\d+)', path.name)]

def kill_group(pid):
    try: os.killpg(pid, signal.SIGKILL)  # this mutant's own process group, in this container
    except ProcessLookupError: pass

def cpu_seconds():
    """The container's CPU time so far: other work on the machine stretches the clock's time, not this."""
    try:
        with open('/sys/fs/cgroup/cpu.stat') as stat:
            return next(round(int(l.split()[1]) / 1e6, 1) for l in stat if l.startswith('usage_usec '))
    except (OSError, StopIteration):
        return None

class Broken(Exception):
    """The sandbox failed, not the mutant: the run stops."""

def environmental(output):
    return re.search(r'No space left on device|Cannot allocate memory|Disk quota exceeded|signal terminated program|'
        r'\bKilled\b', output)

def rejected(stderr):
    """Why the compiler refused a mutant: its first error at a line of the source."""
    error = re.search(r'^[^\s:][^:\n]*:\d+:\d+: (?:fatal )?error: .*', stderr, re.M)
    if not error or environmental(stderr): raise Broken(f'cc failed, not for the mutant: {stderr.strip()[-300:]}')
    return error[0][:200]

def free_space():
    tmp = os.statvfs('/tmp')
    return tmp.f_bavail * tmp.f_frsize

class Worker:
    def __init__(self, index, args):
        self.args, self.index = args, index
        self.root = Path(f'/tmp/w{index}')
        shutil.copytree(args.tree, self.root)
        self.source = self.root/args.source
        self.source.chmod(0o644)
        self.since_canary = 0

    def compile(self, mutant):
        shutil.copyfile(mutant, self.source)
        # A relative path, the same for every worker, as -fsanitize=undefined writes it into the object.
        result = subprocess.run(['cc', '-c', *self.args.cflags, '-I', str(Path(self.args.source).parent),
            self.args.source, '-o', 'm.o'], cwd=self.root, capture_output=True, text=True, errors='replace')
        if result.returncode: return None, rejected(result.stderr)
        return hashlib.sha256((self.root/'m.o').read_bytes()).hexdigest(), ''

    def command(self, source):
        """The test's command line and what it adds to the environment, source in place; None and why if the
        mutant can't be put together after all."""
        return ['python3', str(self.root/self.args.test)], {}

    def verdict(self, code, lines):
        """A failed run's status, and what to show for it."""
        reason = lines[-1][:200] if lines else ''
        if code < 0: return 'crash', reason
        # The test's own build of the file refusing the mutant is the compiler's verdict, not the test's: its wrapper
        # can use what the file alone leaves unused, as mp3_gapless.py's calls mp3_trim. A disk the run filled isn't.
        if (lines and re.match(r"subprocess\.CalledProcessError: Command '[\[(]'cc'", lines[-1])
                and not environmental('\n'.join(lines))): return 'invalid', reason
        return ('assert' if re.match(r'\w*AssertionError', reason) else 'error'), reason

    def passed(self, lines):
        """What a passing run ends with, to compare with the original's."""
        return lines[-1][:200] if lines else ''

    def agrees(self, reason, passed):
        """Whether a passing run ended as the original's did. A mutant can swap fork's parent and child: the test
        goes on in the child, and the exit status is ffmpeg's."""
        return reason == passed

    def run(self, command, env, cwd, timeout):
        """The test's exit status, None if it ran out of time, and its output."""
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            output, _ = process.communicate(timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            kill_group(process.pid)
            output, _ = process.communicate()
            code = None
        kill_group(process.pid)  # a failed test can leave a child behind, like an ffmpeg waiting on a FIFO
        return code, output

    def test(self, source, timeout):
        shutil.copyfile(source, self.source)
        command, extra = self.command(source)
        if command is None: return 'invalid', None, 0, extra
        tmp = self.root/'tmp'
        tmp.mkdir()
        env = {'PATH': os.environ['PATH'], 'TMPDIR': str(tmp), 'HOME': str(tmp), 'LC_ALL': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', **extra}
        start = time.monotonic()
        code, output = self.run(command, env, tmp, timeout)
        seconds = time.monotonic() - start
        subprocess.run(['chmod', '-R', 'u+rwx', str(tmp)], stderr=subprocess.DEVNULL)  # a crash can leave a folder locked
        shutil.rmtree(tmp)
        lines = output.decode(errors='replace').splitlines()
        if code is None: status, reason = 'timeout', lines[-1][:200] if lines else ''
        elif code == 0: status, reason = 'survived', self.passed(lines)
        else: status, reason = self.verdict(code, lines)
        return status, code, round(seconds, 3), reason

class FwWorker(Worker):
    """A test/fw file, run by pytest against host.py's library with the mutant's object linked in."""
    def __init__(self, index, args):
        super().__init__(index, args)
        self.stem = self.source.stem
        self.lib = self.root/'lib'
        self.lib.mkdir()
        # The headers, the other objects and stubs.json as host.py left them, for a test that reads them.
        for path in BASE.iterdir():
            if path.name not in ('fw.so', 'sizes.json'): (self.lib/path.name).symlink_to(path)
        self.serve()  # while the tree is the original: tools/ reads patch/ at import

    def serve(self):
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'LC_ALL': 'C.UTF-8', **self.environment()}
        self.server = subprocess.Popen([sys.executable, '-c', FORK_SERVER, str(self.root/'test/fw')], cwd=self.root,
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)

    def run(self, command, env, cwd, timeout):
        """timeout is in seconds of CPU, which other work on the machine can't eat into: a loop runs out of it.
        Something that waits without using any meets six times as many seconds of the clock."""
        if self.server.poll() is not None: self.serve()
        log = cwd/'pytest.log'
        job = {'args': command, 'env': env, 'cwd': str(cwd), 'log': str(log), 'cpu': math.ceil(timeout)}
        self.server.stdin.write(json.dumps(job).encode() + b'\n')
        pid = int(self.server.stdout.readline() or sys.exit('the fork server stopped'))
        if select.select([self.server.stdout], [], [], 6 * timeout)[0]: code = int(self.server.stdout.readline())
        else:
            kill_group(pid)
            self.server.stdout.readline()  # the server reaps it
            code = None
        kill_group(pid)
        return None if code == -signal.SIGXCPU else code, log.read_bytes() if log.exists() else b''

    def build(self, mutant):
        """The mutant's object in OBJECTS, its statics global as host.py makes them; None and the compiler's
        complaint if it refuses the mutant."""
        shutil.copyfile(mutant, self.source)
        out = OBJECTS/Path(mutant).stem
        out.mkdir(parents=True, exist_ok=True)
        obj = out/(self.stem + '.o')
        result = subprocess.run(['cc', *host.FLAGS, '-I', str(BASE), '-I', 'patch', '-c', self.args.source,
            '-o', str(obj)], cwd=self.root, capture_output=True, text=True, errors='replace')
        if result.returncode:
            shutil.rmtree(out)
            return None, rejected(result.stderr)
        globalize(obj)
        return obj, ''

    def compile(self, mutant):
        """The mutant's object hashed without its debug information, which holds each worker's own folder. It waits
        in OBJECTS for the mutant's test while /tmp has room to spare, and is compiled again then otherwise, so a
        large file's mutants can't fill the tmpfs."""
        obj, why = self.build(mutant)
        if not obj: return None, why
        stripped = obj.with_name('stripped.o')
        subprocess.run(['objcopy', '--strip-debug', str(obj), str(stripped)], check=True)
        digest = hashlib.sha256(stripped.read_bytes()).hexdigest()
        stripped.unlink()
        if obj.parent.name != 'original' and free_space() < SPARE: shutil.rmtree(obj.parent)
        return digest, ''

    def link(self, obj):
        """fw.so and sizes.json in self.lib, as host.build writes them, with obj in place of the original's object."""
        (self.lib/obj.name).unlink()
        (self.lib/obj.name).symlink_to(obj)
        objects = [obj if o.name == obj.name else o for o in base_objects()]
        result = subprocess.run(['cc', '-shared', *map(str, objects), str(BASE/'fw_stubs.o'), '-lm', '-o',
            str(self.lib/'fw.so')], capture_output=True, text=True, errors='replace')
        if result.returncode:
            if environmental(result.stderr): raise Broken(f'the link failed, not the mutant: {result.stderr[-300:]}')
            return result.stderr.strip()[-200:]
        sizes = {r[-1]: int(r[1], 16) for r in host.nm(self.lib/'fw.so', '-S', '--defined-only') if len(r) == 4}
        (self.lib/'sizes.json').write_text(json.dumps(sizes))
        return ''

    def environment(self):
        # The tree is this worker's own, so pytest's rewritten test and tools/ compile once, not for every mutant.
        return {'Q2_FW_LIBRARY': str(self.lib/'fw.so'), 'LD_LIBRARY_PATH': os.environ.get('LD_LIBRARY_PATH', ''),
            'PYTHONDONTWRITEBYTECODE': ''}

    def command(self, source):
        self.expected = None
        obj = OBJECTS/Path(source).stem/(self.stem + '.o')
        if not obj.exists():
            obj, why = self.build(source)
            if not obj: raise Broken(f'{Path(source).name} compiled once, and not again: {why}')
        why = self.link(obj)
        if why: return None, why
        # -x: one failing test is enough. pytest finds the tree's pyproject.toml from the test's path, and runs the
        # tests named in the order given: those that run the mutant's lines, if the original's gcov run says which.
        names = order.now()
        chosen = reach.tests(Path(source).name) if reach else None
        if chosen: names = [name for name in names if name in chosen]
        self.expected = len(names)
        test = self.root/self.args.test
        return ['-x', '-q', '-p', 'no:cacheprovider', *(f'{test}::{name}' for name in names)], self.environment()

    def agrees(self, reason, passed):
        return counted(reason) == self.expected

    def collect(self):
        """The test file's tests, in its own order."""
        result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider',
            str(self.root/self.args.test)], cwd=self.root, capture_output=True, text=True, check=True,
            env={'PATH': os.environ['PATH'], 'HOME': '/tmp', **self.environment()})
        return [line.partition('::')[2] for line in result.stdout.splitlines() if '::' in line]

    def lines_run(self, name):
        """The source's lines that have code, and those the test runs, from COV's build: (None, why) if it fails alone."""
        tmp, counts = self.root/'tmp', COV/f'counts{self.index}'
        tmp.mkdir()
        env = {'PATH': os.environ['PATH'], 'TMPDIR': str(tmp), 'HOME': str(tmp), 'LC_ALL': 'C.UTF-8',
            **self.environment(), 'Q2_FW_LIBRARY': str(COV/'lib/fw.so'), 'GCOV_PREFIX': str(counts)}
        code, output = self.run(['-q', '-p', 'no:cacheprovider', f'{self.root/self.args.test}::{name}'], env, tmp, 600)
        shutil.rmtree(tmp)
        if code != 0: return None, f'{name} alone: {output.decode(errors="replace").splitlines()[-1:]}'
        # libgcov writes the counts under GCOV_PREFIX at the object's own path, beside which gcov wants the notes.
        where = counts/COV.relative_to('/')
        if not where.exists(): return set(), set()  # the test never loaded the library
        (where/(self.stem + '.gcno')).symlink_to(COV/(self.stem + '.gcno'))
        result = subprocess.run(['gcov', '--json-format', '--stdout', '--object-directory', str(where), self.args.source],
            cwd=self.root, capture_output=True, text=True, check=True)
        shutil.rmtree(counts)
        lines = [l for f in json.loads(result.stdout)['files'] if f['file'] == self.args.source for l in f['lines']]
        return {l['line_number'] for l in lines}, {l['line_number'] for l in lines if l['count']}

    def verdict(self, code, lines):
        # The first test that failed, or pytest's own last word: exit status 1 is a failed test, the rest pytest's.
        # A crash names the test it was in from faulthandler's traceback, innermost first.
        test = Path(self.args.test).name
        failed = next((l for l in lines if l.startswith(('FAILED ', 'ERROR '))), lines[-1] if lines else '')
        if code < 0:
            where = next((m[1] for l in lines if (m := re.search(rf'/{re.escape(test)}", line \d+ in (test_\w+)', l))), None)
            if where: failed = f'CRASHED {self.args.test}::{where}'
        return 'crash' if code < 0 else 'assert' if code == 1 else 'error', failed.replace(' ../test/', ' test/', 1)[:200]

    def passed(self, lines):
        return re.sub(r' in [\d.]+s\b.*', '', lines[-1].strip('= '))[:200] if lines else ''

class Order:
    """A test/fw file's tests, moving the last to kill a mutant to the front if they may: mutants come in the
    file's order, neighbours tend to fall to the same test, and pytest -x stops at it."""
    def __init__(self, names, moving=False):
        self.names, self.moving, self.lock = list(names), moving, threading.Lock()

    def now(self):
        with self.lock: return list(self.names)

    def killed(self, reason):
        """The test a verdict names goes to the front."""
        test = reason.partition('::')[2]
        with self.lock:
            for i, name in enumerate(self.names):
                if self.moving and (test == name or test.startswith(name + ' ') or name.startswith(test + '[')):
                    self.names.insert(0, self.names.pop(i))
                    return

class Reach:
    """Which tests run each line of the source, from the original built with gcov's counters, a test at a time. A
    test that never runs a mutant's lines sees what the original does, so only those that do need to run. A line with
    no code (data, a #define's body) or that no test runs falls back to them all, as does a case label's line: the
    switch jumps there, so a changed label moves tests that ran other lines."""
    def __init__(self, runs, manifest, source):
        self.code = set().union(*(code for code, _ in runs.values()))
        self.by_line = {}
        for name, (_, ran) in runs.items():
            for line in ran: self.by_line.setdefault(line, set()).add(name)
        self.spans = {r['id']: range(r['line'], r['line'] + r['old'].count('\n') + 1) for r in manifest}
        code = [re.sub(r'/\*.*?\*/|//.*', '', line) for line in source.splitlines()]
        self.labels = {i + 1 for i, line in enumerate(code) if re.search(r'\bcase\b', line)}

    def tests(self, mutant):
        """The tests that run any of the mutant's lines, or None for all of them."""
        span = self.spans.get(mutant)
        if not span or any(line not in self.code or line not in self.by_line or line in self.labels for line in span):
            return None
        return set().union(*(self.by_line[line] for line in span))

order = None  # --fw: an Order
reach = None  # --fw: a Reach, unless the test file forks, as a child's gcov counts end with it unwritten

def counted(summary):
    """The tests a pytest summary line says passed, failed as they were meant to, or skipped themselves (one of
    test_games.py's does where the process can't take stock's address)."""
    return sum(int(n) for n, _ in re.findall(r'(\d+) (passed|xfailed|skipped)', summary))

def coverage_build(worker):
    """The original compiled with gcov's counters, linked as a mutant is, in COV/lib."""
    stem = worker.stem
    shutil.copyfile(Path(worker.args.tree)/worker.args.source, worker.source)
    (COV/'lib').mkdir(parents=True)
    for path in BASE.iterdir():
        if path.name not in ('fw.so', 'sizes.json'): (COV/'lib'/path.name).symlink_to(path)
    obj = COV/(stem + '.o')
    subprocess.run(['cc', *host.FLAGS, '--coverage', '-I', str(BASE), '-I', 'patch', '-c', worker.args.source, '-o',
        str(obj)], cwd=worker.root, check=True)
    globalize(obj)
    (COV/'lib'/obj.name).unlink()
    (COV/'lib'/obj.name).symlink_to(obj)
    objects = [obj if o.name == obj.name else o for o in base_objects()]
    subprocess.run(['cc', '-shared', '--coverage', *map(str, objects), str(BASE/'fw_stubs.o'), '-lm', '-o',
        str(COV/'lib/fw.so')], check=True)
    sizes = {r[-1]: int(r[1], 16) for r in host.nm(COV/'lib/fw.so', '-S', '--defined-only') if len(r) == 4}
    (COV/'lib/sizes.json').write_text(json.dumps(sizes))

def base_objects():
    return [BASE/(source.stem + '.o') for source in host.SOURCES]

def globalize(obj):
    """host.build's renames: each static a global <file>__<name>, so a test reaches it."""
    local = [r[-1] for r in host.nm(obj) if len(r) == 3 and r[1] in 'tdbr' and re.fullmatch(r'[A-Za-z_]\w*', r[-1])]
    renames = [f for name in local for f in ('--redefine-sym', f'{name}={obj.stem}__{name}', '--globalize-symbol', f'{obj.stem}__{name}')]
    subprocess.run(['objcopy', *renames, str(obj)], check=True)

def symbols(path):
    """Each defined symbol's name, type and size, which move with any compiler flag that changes the code."""
    return sorted((r[-1], r[2], r[1]) for r in host.nm(path, '-S', '--defined-only') if len(r) == 4)

def build_firmware(args):
    """test/fw's library, built by host.py itself; then checks that the link here makes the same library, and that
    a worker compiles the original into the same symbols, so a mutant runs in the build the tests know."""
    global host
    sys.path.insert(0, str(Path(args.tree)/'test/fw'))
    import host
    # Its compilers, and fontTools' warnings, would land among the results on stdout.
    subprocess.run([sys.executable, '-c', 'import pathlib, sys; sys.path.insert(0, sys.argv[1]); import host; '
        'host.build(pathlib.Path(sys.argv[2]))', str(Path(args.tree)/'test/fw'), str(BASE)], stdout=2, check=True)
    if BASE/(Path(args.source).stem + '.o') not in base_objects(): sys.exit(f'{args.source} is not in test/fw\'s library')
    copy = Path('/tmp/relinked.so')
    subprocess.run(['cc', '-shared', *map(str, base_objects()), str(BASE/'fw_stubs.o'), '-lm', '-o', str(copy)], check=True)
    if copy.read_bytes() != (BASE/'fw.so').read_bytes(): sys.exit('run.py links test/fw\'s library unlike host.build')
    copy.unlink()

def main():
    global order, reach
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', default='/tree')
    parser.add_argument('--mutants', default='/mutants')
    parser.add_argument('--done', default='/done.jsonl')
    parser.add_argument('--source', required=True)
    parser.add_argument('--test', required=True)
    parser.add_argument('--cflags', help='as the test builds the source, in one string')
    parser.add_argument('--fw', action='store_true', help='--test is a test/fw file: pytest runs it against host.py\'s library')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=float, help="seconds for each mutant; by default 20 times the original's, at "
        "least 5. With --fw, seconds of CPU, by default 5 times the original's run, at least 10: pytest stops at the "
        'first failure, so a mutant is no slower than the original')
    args = parser.parse_args()
    if not args.fw and args.cflags is None: parser.error('--cflags: a host suite builds the file itself')
    args.cflags = shlex.split(args.cflags or '')

    # Inherited by every mutant: a runaway hits these, not the host. Core files would only fill the tmpfs.
    resource.setrlimit(resource.RLIMIT_AS, (4 * GIB, 4 * GIB))
    resource.setrlimit(resource.RLIMIT_FSIZE, (GIB // 8, GIB // 8))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    done = set()
    if os.path.exists(args.done):
        for line in open(args.done, encoding='utf-8'):
            done.add(json.loads(line).get('id'))
    original = Path('/tmp/original.c')
    shutil.copyfile(Path(args.tree)/args.source, original)
    mutants = sorted(Path(args.mutants).glob('*.c'), key=numbered)
    start = time.monotonic()
    if args.fw: build_firmware(args)
    kind = FwWorker if args.fw else Worker
    pool = queue.Queue()
    workers = [kind(i, args) for i in range(args.workers)]
    for worker in workers:
        pool.put(worker)

    def using_worker(job):
        def wrapped(*a):
            worker = pool.get()
            try: return job(worker, *a)
            finally: pool.put(worker)
        return wrapped

    with concurrent.futures.ThreadPoolExecutor(args.workers) as executor:
        base, why = using_worker(kind.compile)(original)
        if not base: sys.exit(f'the original does not compile: {why}')
        stem = Path(args.source).stem
        if args.fw and symbols(OBJECTS/'original'/(stem + '.o')) != symbols(BASE/(stem + '.o')):
            sys.exit(f'run.py compiles {args.source} unlike host.build')
        if args.fw: order = Order(using_worker(FwWorker.collect)())
        status, code, seconds, passed = using_worker(kind.test)(original, args.timeout or 600)
        if status != 'survived' or args.fw and counted(passed) != len(order.names):
            sys.exit(f'the original fails its test: {status} {code} {passed}')
        timeout = args.timeout or (max(10, 5 * seconds) if args.fw else max(5, 20 * seconds))
        fw = {}
        if args.fw:
            # A killer moves to the front only if the original passes its tests backwards too: none leans on another.
            names = order.now()
            order = Order(names[::-1])
            status, code, _, backwards = using_worker(kind.test)(original, timeout)
            order = Order(names, moving=status == 'survived' and counted(backwards) == len(names))
            # Each test alone, counted by gcov, to say which tests run a mutant's lines: not for a file that forks.
            manifest = Path(args.mutants)/'manifest.jsonl'
            if re.search(r'\bin_fork\b|\bfork\(', (Path(args.tree)/args.test).read_text()): fw['selected'] = 'it forks'
            elif not manifest.exists(): fw['selected'] = f'no {manifest.name} beside the mutants'
            else:
                coverage_build(workers[0])
                runs = dict(zip(names, executor.map(using_worker(FwWorker.lines_run), names)))
                alone = [why for lines, why in runs.values() if lines is None]
                if alone: fw['selected'] = alone[0]
                else:
                    reach = Reach(runs, [json.loads(line) for line in open(manifest, encoding='utf-8')],
                        original.read_text(encoding='utf-8'))
                    fw['selected'] = True
            fw['reordered'] = order.moving
        emit(id='_baseline', seconds=seconds, timeout=timeout, mutants=len(mutants), workers=args.workers,
            resumed=len(done), passed=passed, **fw)

        def every(job, items):
            """job for each item on the workers; a broken sandbox cancels the rest and stops the run unanswered."""
            try: return list(executor.map(using_worker(job), items))
            except Broken as stop:
                executor.shutdown(cancel_futures=True)
                sys.exit(f'run.py stops: {stop}')

        objects = every(kind.compile, mutants)
        compiled = time.monotonic() - start
        seen = {base: 'original'}
        to_test = []
        for mutant, (digest, why) in zip(mutants, objects):
            if digest is None: verdict = {'status': 'invalid', 'reason': why}
            elif digest == base: verdict = {'status': 'same'}
            elif digest in seen: verdict = {'status': 'duplicate', 'of': seen[digest]}
            else:
                seen[digest] = mutant.name
                verdict = None
            if verdict is None and mutant.name not in done:
                to_test.append((mutant, digest))
                continue
            if args.fw: shutil.rmtree(OBJECTS/mutant.stem, ignore_errors=True)
            if verdict and mutant.name not in done: emit(id=mutant.name, object=digest, **verdict)

        failed = threading.Event()
        def judge(worker, item):
            if failed.is_set(): return
            mutant, digest = item
            status, code, seconds, reason = worker.test(mutant, timeout)
            if status == 'survived' and not worker.agrees(reason, passed): status = 'error'
            extra = {}
            if args.fw:
                shutil.rmtree(OBJECTS/mutant.stem, ignore_errors=True)
                extra['tests'] = worker.expected
                if status in ('assert', 'crash'): order.killed(reason)
            emit(id=mutant.name, object=digest, status=status, code=code, seconds=seconds, reason=reason, **extra)
            worker.since_canary += 1
            if worker.since_canary >= CANARY_EVERY:
                worker.since_canary = 0
                status, code, seconds, reason = worker.test(original, timeout)
                if status != 'survived' or not worker.agrees(reason, passed):
                    failed.set()
                    emit(id='_canary_failed', status=status, code=code, seconds=seconds, reason=reason)
        every(judge, to_test)
    emit(id='_end', compile_seconds=round(compiled, 1), total_seconds=round(time.monotonic() - start, 1),
        cpu_seconds=cpu_seconds(), tested=len(to_test), canary_failed=failed.is_set())

if __name__ == '__main__':
    main()
