"""targets.py: which functions the weekly run mutates, and mutmut's names for them."""
import targets

SOURCE = '''
def top():
    def nested(): pass

class Thing:
    def method(self): pass
    @property
    def value(self): pass

constant = 1
'''


def test_functions_are_mutmuts_units():
    assert set(targets.functions(SOURCE)) == {'top', 'Thing.method', 'Thing.value'}


def test_functions_compare_by_code_not_layout():
    assert targets.functions('def f():\n    return 1\n')['f'] == targets.functions('# note\n\ndef f(): return 1\n')['f']
    assert targets.functions('def f():\n    return 1\n')['f'] != targets.functions('def f():\n    return 2\n')['f']


def test_mutant_glob():
    assert targets.mutant_glob('build', 'jal') == 'tools.build.x_jal__mutmut_*'
    assert targets.mutant_glob('rotator', 'Elf.pack') == 'tools.rotator.xǁElfǁpack__mutmut_*'
