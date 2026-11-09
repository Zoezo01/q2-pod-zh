"""targets.py: which functions mutmut covers, and mutmut's names for them."""
import os

import pytest

import targets

SOURCE = '''
import functools

def top():
    def nested():
        return 1
    return nested

class Thing:
    def method(self):
        return self.x + 1

    @classmethod
    def make(cls):
        return cls(2)

    @property
    def value(self):
        return self.x * 2

    @functools.cache
    def cached(self):
        return self.x * 3

constant = 1
'''


def test_functions_are_mutmuts_units():
    """A nested function goes with its parent. mutmut leaves a decorated one whole, but for a lone
    @staticmethod or @classmethod."""
    assert set(targets.functions(SOURCE)) == {'top', 'Thing.method', 'Thing.make'}


def test_functions_compare_by_code_not_layout():
    assert targets.functions('def f():\n    return 1\n')['f'] == targets.functions('# note\n\ndef f(): return 1\n')['f']
    assert targets.functions('def f():\n    return 1\n')['f'] != targets.functions('def f():\n    return 2\n')['f']


def test_functions_leave_out_what_mutmut_skips():
    """The pragma on a def line, and a body with nothing to mutate."""
    source = '''
def kept():
    x = 1  # pragma: no mutate block
    return x + 2

def stock(data):  # pragma: no mutate block
    return data + 1

def unchanged(data):
    return data

class Thing:
    def method(self):  # pragma: no mutate block
        return 1

    def value(self):
        return self.x

    def nothing(self):
        pass
'''
    assert set(targets.functions(source)) == {'kept'}


TOOLS = {'build.py': '''
def jal(x):
    return x + 1

def build(zip_path):  # pragma: no mutate block
    return zip_path + 1
''', 'aic.py': '''
def validate_stock(elf):  # pragma: no mutate block
    return elf + 1
''', 'rotator.py': '''
class Elf:
    def pack(self):
        return 4
''', 'format.sh': 'true'}


def test_targets_are_all_of_tools_but_the_skipped(tmp_path, monkeypatch):
    """Each module in tools/, its functions and methods but those mutmut skips; no other file."""
    (tmp_path/'tools').mkdir()
    for name, text in TOOLS.items():
        (tmp_path/'tools'/name).write_text(text)
    monkeypatch.setattr(targets, 'ROOT', tmp_path)
    assert targets.targets() == {'aic': [], 'build': ['jal'], 'rotator': ['Elf.pack']}


# Under mutmut, ROOT is mutants/, whose copy of tools/ holds every mutant: too big to read in a test.
@pytest.mark.skipif('MUTANT_UNDER_TEST' in os.environ, reason="reads mutmut's copy of tools/ under mutmut")
def test_targets_skip_the_stock_bound_and_import_only_functions():
    """A few seconds, most of them on build.py and ipod.py. Without build()'s pragma, mutmut writes a
    96 MB file that never finishes. Only the skipped are named, so a rename elsewhere doesn't fail here."""
    aic, art, build, ipod, peq, pwm, rotator = (targets.functions((targets.ROOT/f'tools/{m}.py').read_text())
                                                for m in ('aic', 'art', 'build', 'ipod', 'peq', 'pwm', 'rotator'))
    assert all((aic, art, build, ipod, peq, pwm, rotator))  # each still has functions to mutate
    assert not {'build', 'compile_payload'} & set(build)
    assert 'patch_asset' not in ipod and 'patch_code' in ipod  # patch_code pins no stock hash: test_patch_code
    assert not {'compile_common', 'patch_player'} & set(peq)
    assert 'validate_stock' not in aic and 'patch_module' not in aic and 'patch_module' not in rotator
    assert not {'link', 'patch_pwm', 'patch_mcu', 'patch_bundle'} & set(pwm)
    assert 'wave' not in art


def test_mutant_glob():
    assert targets.mutant_glob('build', 'jal') == 'tools.build.x_jal__mutmut_*'
    assert targets.mutant_glob('rotator', 'Elf.pack') == 'tools.rotator.xǁElfǁpack__mutmut_*'
