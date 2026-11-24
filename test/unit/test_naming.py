"""The repository's own words: the Lock Screen's second layout is At a Glance, and no text file names it after the
design it came from."""
import os
import re

from conftest import ROOT

# Folders git leaves out (.gitignore) and its own: built, cached or vendor files, none of them the repository's text.
SKIP = {'.git', '__pycache__', '.mypy_cache', '.pytest_cache', 'mutants', 'c-coverage', 'graphify-out', 'stock', 'work',
        'build', 'dist', 'node_modules'}
# The name, spelled out of pieces so this file does not hold it: any case, a space, hyphen or underscore or none between.
NAME = re.compile('q2' + r'[ _-]?' + 'core', re.I)


def text_files():
    """Every text file under ROOT outside SKIP: its path from ROOT and its text."""
    for top, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('build-')]
        for name in files:
            path = os.path.join(top, name)
            if os.path.islink(path):
                continue
            try:
                with open(path, encoding='utf-8') as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue  # a picture, a font, a PDF
            yield os.path.relpath(path, ROOT), text


def test_no_text_file_names_the_old_design():
    found = [(path, text.count('\n', 0, m.start()) + 1) for path, text in text_files() for m in NAME.finditer(text)]
    assert found == []


def test_the_search_finds_the_name_in_any_case_and_spelling():
    for sep in (' ', '-', '_', ''):
        for case in (str.lower, str.upper, str.title):
            assert NAME.search(f'a {case("q2" + sep + "core")} layout')
    assert not NAME.search('Q2 Pod core files') and not NAME.search('q2 c' + 'ore'[1:])
