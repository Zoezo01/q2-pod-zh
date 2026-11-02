"""conftest.py's fence: a mutated tool may not reach a process, a socket or a real Q2."""
import os
import socket
import subprocess

import pytest


@pytest.mark.parametrize('args', [['true'], ['/bin/sh', '-c', 'true'], 'true', ['reboot'], ['pkill', 'x']])
def test_processes_are_refused(args):
    with pytest.raises(AssertionError, match='a test tried to run'):
        subprocess.run(args)


def test_a_shell_is_refused_even_for_a_compiler():
    with pytest.raises(AssertionError):
        subprocess.run(['clang'], shell=True)


@pytest.mark.parametrize('name,args', [('system', ('true',)), ('popen', ('true',)), ('kill', (1, 15)), ('killpg', (1, 15)),
                                       ('execv', ('/bin/true', ['true'])), ('spawnv', (os.P_WAIT, '/bin/true', ['true']))])
def test_os_escapes_are_refused(name, args):
    with pytest.raises(AssertionError, match=f'os.{name}'):
        getattr(os, name)(*args)


def test_sockets_are_refused():
    with pytest.raises(AssertionError):
        socket.create_connection(('127.0.0.1', 1))
    with socket.socket() as s, pytest.raises(AssertionError):
        s.connect(('127.0.0.1', 1))


def test_a_set_q2_host_never_reaches_a_test():
    assert 'Q2_HOST' not in os.environ and 'Q2_KEY' not in os.environ
