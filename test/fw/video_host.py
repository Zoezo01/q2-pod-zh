"""q2video (patch/video.c) for this machine, beside host.py's fw.so: on the Q2 it is a program of its own
(tools/build.py compile_helper), so here it is a library of its own, built as host.py builds fw.so.

video.c compiles as it does for the Q2, without PEQ_HOST: it declares what it calls itself, glibc's
and alsa-lib's. Each of those calls but host.py's pure libc (and sscanf, which video.c declares under
its own name) is a stub named fw_<name> (fw_fork, fw_snd_pcm_writei) that records the call and returns
0 or what a test's mock returns, so nothing reaches a process, file, socket or sound card here. main
is video_main and a static <name> is video__<name>, as in fw.so. Each stub's return type is video.c's
own declaration (gcc -aux-info), for conftest's strict mode.

A small harness runs it. fw_video_run(argc, argv) returns main's status, or the one _exit was given:
_exit ends the run where it is called, as it ends the process (fw__exit's stub sees it first).
fw_video_writer() runs the sound thread's body the same way. Once a test sets fw_video_stop, the next
poll or usleep ends the run with -1, so a run that would never end (a key lost, a mutant) fails the
test instead of hanging it.

There is no second process. When fork's stub answers a pid, the harness first runs the child's half
here: ffmpeg() again with ffmpeg()'s own arguments, read off its frame at the offsets its debug info
gives (-O0 keeps each parameter there), its fork answering 0, so the child's moves and its execv reach
the stubs; its _exit comes back to the parent's fork, which then answers the pid. A fork the stub
answers 0 makes the caller itself the child, as ever. Finding that frame takes x86-64's layout (its
CFA two words above the frame pointer), so the harness refuses to compile for anything else."""
import json
import os
import re
import subprocess

import host

SOURCE = host.ROOT/'patch'/'video.c'
PURE = host.PURE | {'sscanf'}
# The calls the harness answers first: each goes on to its stub, fw_<name>.
HARNESS = {'_exit': 'fw_video_exit', 'poll': 'fw_video_poll', 'usleep': 'fw_video_usleep', 'fork': 'fw_video_fork'}
# video.c's ffmpeg(), whose child's half the harness runs: its parameters and their types, in order.
CHILD = ('ffmpeg', [('args', 'const char **'), ('a', 'int'), ('at_a', 'int'), ('b', 'int'), ('at_b', 'int')])
HARNESS_SOURCE = r'''
#ifndef __x86_64__
#error "fw_video_fork finds ffmpeg() arguments by x86-64 frame layout (CFA = frame pointer + 16): port it first"
#endif
#include <setjmp.h>
#include <stdlib.h>
int video_main(int, char **);
void *video__writer(void *);
int video__ffmpeg(const char **, int, int, int, int);
static jmp_buf end_at, child_done;
static int running, status, child;
volatile int fw_video_stop;

static void end(int s) {
    status = s;
    longjmp(end_at, 1);
}
void fw_video_exit(int s) {
    if (child) longjmp(child_done, 1); /* the child's half is over: back to its parent's fork */
    fw__exit(s, 0, 0, 0, 0, 0);
    if (!running) abort(); /* no run to end: a test reached an exit outside fw_video_run */
    end(s);
}
int fw_video_fork(void) {
    if (child) return 0;
    int pid = (int)fw_fork(0, 0, 0, 0, 0, 0);
    if (pid > 0) {
        char *cfa = (char *)__builtin_frame_address(1) + 2 * sizeof(void *); /* ffmpeg()'s */
        child = 1;
        if (!setjmp(child_done)) video__ffmpeg(CHILD_ARGS);
        child = 0;
    }
    return pid;
}
int fw_video_poll(void *fds, unsigned n, int ms) {
    if (fw_video_stop && running) end(-1);
    return (int)fw_poll((long)fds, n, ms, 0, 0, 0);
}
int fw_video_usleep(unsigned us) {
    if (fw_video_stop && running) end(-1);
    return (int)fw_usleep(us, 0, 0, 0, 0, 0);
}
int fw_video_run(int argc, char **argv) {
    running = 1;
    if (setjmp(end_at)) {
        running = 0;
        return status;
    }
    int s = video_main(argc, argv);
    running = 0;
    return s;
}
int fw_video_writer(void) {
    running = 1;
    if (setjmp(end_at)) {
        running = 0;
        return status;
    }
    video__writer(0);
    running = 0;
    return 0;
}
'''


def flags():
    """conftest.py's extra cc flags for fw.so, from the same variables, so both libraries build alike. A copy of
    conftest's library fixture, which builds them inline: a change to one goes in both."""
    extra = []
    if os.environ.get('Q2_FW_SANITIZE'):
        extra += ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-sanitize=alignment']
    if os.environ.get('Q2_FW_COVERAGE'):
        extra += ['--coverage']
    return extra


def declared(aux):
    """{name: C return type} for each function gcc's -aux-info lists as declared."""
    return {m[2]: m[1].strip() for m in re.finditer(r'extern (.*?)\b(\w+) \((?!\*)', aux)}  # signal's: void (*signal (


def parameters(obj, function):
    """{name: offset from the frame's CFA} for each of function's parameters, from obj's debug info."""
    dump = subprocess.run(['readelf', '--debug-dump=info', str(obj)], capture_output=True, text=True, check=True).stdout
    name = r'DW_AT_name\s*:\s*(?:\([^)]*\): )?'
    for entry in dump.split('\n <1>')[1:]:
        if '(DW_TAG_subprogram)' in entry.split('\n', 1)[0] and re.search(rf'{name}{function}\n', entry.split('\n <2>')[0]):
            host.tools_build.check('DW_OP_call_frame_cfa' in entry, f'{function}: its frame base is not the CFA')
            found = [re.search(rf'{name}(\w+)\n(?:.*\n)*?.*DW_OP_fbreg: (-?\d+)', chunk) for chunk in entry.split('\n <2>')[1:]
                     if '(DW_TAG_formal_parameter)' in chunk.split('\n', 1)[0]]
            return {m[1]: int(m[2]) for m in found if m}
    raise ValueError(f'No {function} in {obj}')


def build(out, extra=()):
    """out/q2video.so, with the stubs.json and sizes.json host.Fw reads beside it."""
    out.mkdir(parents=True, exist_ok=True)
    obj, aux = out/'video.o', out/'video.aux'
    subprocess.run(['cc', *host.FLAGS, *extra, '-aux-info', str(aux), '-c', str(SOURCE), '-o', str(obj)], check=True)
    rows = host.nm(obj)
    undefined = {r[-1] for r in rows if len(r) == 2 and r[0] == 'U'}
    local = [r[-1] for r in rows if len(r) == 3 and r[1] in 'tdbr' and re.fullmatch(r'[A-Za-z_]\w*', r[-1])]
    system = {'__stack_chk_fail', '_GLOBAL_OFFSET_TABLE_', *PURE}
    system |= {n for n in undefined if n.startswith(('__gcov', '__asan', '__ubsan', '__lsan'))}
    if [n for n in system if 'fork' in n or 'exec' in n]:  # gcov's __gcov_fork would start a real process
        raise RuntimeError(f'{SOURCE.name}: a process call reaches no stub: {sorted(system)}')
    calls = sorted(undefined - system)
    function, args = CHILD
    offsets = parameters(obj, function)
    child = ', '.join(f'*({kind} *)(cfa + {offsets[arg]})' for arg, kind in args)
    renames = [f for name in local for f in ('--redefine-sym', f'{name}=video__{name}', '--globalize-symbol', f'video__{name}')]
    renames += [f for name in calls for f in ('--redefine-sym', f'{name}={HARNESS.get(name, "fw_" + name)}')]
    subprocess.run(['objcopy', *renames, '--redefine-sym', 'main=video_main', str(obj)], check=True)
    stubs = [f'fw_{name}' for name in calls]
    (out/'fw_stubs.c').write_text(host.stubs_source(stubs, {}) + HARNESS_SOURCE.replace('CHILD_ARGS', child))
    # A frame pointer, so fw_video_fork finds its caller's frame.
    subprocess.run(['cc', '-O1', '-fPIC', '-fno-omit-frame-pointer', '-w', '-c', str(out/'fw_stubs.c'), '-o', str(out/'fw_stubs.o')],
                   check=True)
    library = out/'q2video.so'
    # -Bsymbolic: video.c's own names (scale, swap, overlay) bind to themselves, never to a namesake loaded first.
    subprocess.run(['cc', '-shared', '-Wl,-Bsymbolic', *extra, str(obj), str(out/'fw_stubs.o'), '-o', str(library)], check=True)
    types = declared(aux.read_text())
    (out/'stubs.json').write_text(json.dumps({f'fw_{name}': types.get(name) for name in calls}))
    sizes = {r[-1]: int(r[1], 16) for r in host.nm(library, '-S', '--defined-only') if len(r) == 4}
    (out/'sizes.json').write_text(json.dumps(sizes))
    return library
