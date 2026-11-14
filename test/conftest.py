"""Load the Chinese-expectation adapter before any test module is collected.

Upstream's test/unit and test/fw assert the English UI; this fork's is fixed
Simplified Chinese. Rather than editing their assertions (and resolving them on
every upstream merge), test/zh_expect.py rewrites the literals from the central
table in test/i18n_zh.py. Set Q2_ZH_EXPECT=0 to see upstream's own assertions.
"""
import os
import pathlib
import sys

_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

if os.environ.get('Q2_ZH_EXPECT', '1') != '0':
    import zh_expect
    zh_expect.install()
