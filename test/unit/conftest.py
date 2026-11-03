"""Import tools/ as the tools package, under the plain names its scripts use for each other, and keep
every test away from real processes, sockets and devices.

mutmut only switches on a mutant whose module is tools.<name>, and tools/ imports `build`, not
tools.build. The alias makes both one module, so a test can `import build` as test/*.py do. This
folder has its own rootdir entry on sys.path, so test/build.py can't shadow tools/build.py here."""
import importlib
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# build first: the others import it, or each other in this order, while they load.
for name in ('build', 'module_elf', 'rotator', 'ipod', 'peq', 'aic', 'flac', 'mcu_park', 'pwm', 'q2remote', 'release'):
    sys.modules[name] = importlib.import_module('tools.' + name)

# The compilers a test may run. A mutant changes these tools' commands before they run, and
# q2remote's are shell scripts for a live Q2, reboot among them, so nothing else may execute.
COMPILERS = {'clang', 'ld.lld', 'llvm-objcopy', 'llvm-nm', 'readelf'}
REAL_POPEN = subprocess.Popen


class Guarded(REAL_POPEN):
    def __init__(self, args, *rest, shell=False, **kwargs):
        program = None if shell or isinstance(args, (str, bytes)) else pathlib.PurePath(str(args[0])).name
        if program not in COMPILERS:
            raise AssertionError(f'a test tried to run {args!r}')
        super().__init__(args, *rest, **kwargs)


def refuse(what):
    def refused(*args, **kwargs):
        raise AssertionError(f'a test tried {what}{args!r}')
    return refused


@pytest.fixture(autouse=True)
def contained(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', Guarded)
    for name in ('system', 'popen', 'kill', 'killpg', 'execv', 'execve', 'execvp', 'execvpe', 'spawnv', 'spawnvp'):
        monkeypatch.setattr(os, name, refuse(f'os.{name}'))
    monkeypatch.setattr(socket.socket, 'connect', refuse('socket.connect'))
    monkeypatch.setattr(socket.socket, 'connect_ex', refuse('socket.connect_ex'))
    monkeypatch.setattr(socket, 'create_connection', refuse('socket.create_connection'))
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    for name in ('Q2_HOST', 'Q2_KEY', 'Q2_KEY_FILE', 'Q2_BUILD', 'Q2_PROF_BIN'):
        monkeypatch.delenv(name, raising=False)
