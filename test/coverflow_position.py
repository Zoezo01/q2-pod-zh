#!/usr/bin/env python3
"""Portable checks of the real Cover Flow position painter; no firmware ZIP needed."""
import os
import pathlib
import shlex
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='q2-cf-position-') as tmp:
    exe = pathlib.Path(tmp)/('position.exe' if os.name == 'nt' else 'position')
    subprocess.run([*shlex.split(os.environ.get('CC', 'cc')), '-std=c11', '-O2', '-Wall', '-Wextra',
                    '-Werror', '-Wno-unused-parameter', str(ROOT/'test/coverflow_position.c'),
                    '-o', str(exe), '-lm'], check=True)
    for args in ([], ['--repaint']): subprocess.run([str(exe), *args], check=True)
