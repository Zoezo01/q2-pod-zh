"""Compile the production fallback-weight helper against a native widget boundary."""
import os
import pathlib
import shlex
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='q2-font-weight-') as tmp:
    exe = pathlib.Path(tmp)/('weight.exe' if os.name == 'nt' else 'weight')
    subprocess.run([*shlex.split(os.environ.get('CC', 'cc')), '-std=c11', '-O2', '-Wall', '-Wextra',
                    '-Werror', '-Wno-unused-parameter', str(ROOT/'test/font_weight.c'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
