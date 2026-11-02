"""The stock-free test/*.py scripts that mutants may safely reach. Not test/remote.py: it runs real
commands through a real shell, and a mutant rewrites a command before it runs."""
import pathlib
import runpy
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_release_script():
    try:
        runpy.run_path(str(ROOT/'test/release.py'))
    finally:
        mock.patch.stopall()  # it leaves release.ROOT patched
