#!/usr/bin/env python3
"""Portable host checks for the shared duration service; needs a C compiler, no firmware ZIP."""
import os
import pathlib
import shlex
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='q2-durations-') as tmp:
    exe = pathlib.Path(tmp)/('durations.exe' if os.name == 'nt' else 'durations')
    subprocess.run([*shlex.split(os.environ.get('CC', 'cc')), '-std=c11', '-D_GNU_SOURCE', '-O2',
                    '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(ROOT/'test/durations.c'),
                    '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
