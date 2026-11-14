#!/usr/bin/env python3
"""Give upstream's English-UI tests this fork's Chinese expectations at runtime.

Upstream asserts strings like b'Search' or 'Library'. The Chinese build says
搜索 and 音乐. test/i18n_zh.py holds the pairs; this module rewrites the literal
constants of test modules as they are imported, so no upstream assertion has to
be edited and a merge from upstream never conflicts over translations.

Two entry points:
  pytest        test/conftest.py calls install() (automatic, for test/unit and
                test/fw alike).
  a test script python3 test/zh_expect.py patch.py /tmp/q2-build
                (same arguments as running test/patch.py directly).

Set Q2_ZH_EXPECT=0 to run the suites with upstream's untouched assertions.
Only files under test/ are rewritten; tools/ and the shipped payload are never
touched. Pytest's assertion rewriting is re-applied to the rewritten AST, so
failing tests keep their usual diffs.
"""
from __future__ import annotations

import ast
import importlib.abc
import importlib.util
import os
import pathlib
import sys

TEST_DIR = pathlib.Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from i18n_zh import ZH, ZH_BYTES, ZH_SKIP_MODULES, ZH_SKIP_TESTS  # noqa: E402

# Our own infrastructure, plus the Chinese-only suite: rewriting them would
# change the mechanism itself and the one test that asserts Chinese directly.
INFRASTRUCTURE = {'i18n_zh', 'zh_expect', 'gen_zh_expect', 'chinese', 'conftest'}


def enabled() -> bool:
    return os.environ.get('Q2_ZH_EXPECT', '1') != '0'


def _constants(node, module):
    """Walk a module, skipping whole functions on the exclusion list."""
    for child in ast.iter_child_nodes(node):
        if (isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (module, child.name) in ZH_SKIP_TESTS):
            continue
        if isinstance(child, ast.Constant):
            yield child
        yield from _constants(child, module)


def rewrite_tree(tree: ast.Module, module: str | None = None) -> ast.Module:
    """English literal -> the Chinese the build actually says (str and bytes)."""
    if not enabled() or module in ZH_SKIP_MODULES:
        return tree
    for node in _constants(tree, module):
        value = node.value
        if isinstance(value, str):
            new = ZH.get(value)
        elif isinstance(value, bytes):
            new = ZH_BYTES.get(value)
        else:
            continue
        if new is not None:
            node.value = new
    return tree


def compile_source(source: str, filename: str, module: str | None = None,
                   pytest_rewrite: bool = False):
    tree = rewrite_tree(ast.parse(source, filename=filename), module)
    if pytest_rewrite:
        # Keep upstream's rich assertion diffs. Our finder takes the place of
        # pytest's rewriting loader, so ask its transformer to run on our AST.
        try:
            from _pytest.assertion import rewrite
            rewrite.rewrite_asserts(tree, source.encode('utf-8'), filename, None)
        except (ImportError, AttributeError, TypeError):  # a pytest without this API
            pass
    return compile(tree, filename, 'exec', dont_inherit=True)


class _Loader(importlib.abc.Loader):
    def __init__(self, origin: str):
        self.origin = origin
        self.module = pathlib.Path(origin).stem

    def create_module(self, spec):
        return None

    def get_source(self, fullname):
        return pathlib.Path(self.origin).read_text(encoding='utf-8')

    def get_code(self, fullname):
        # Pytest injects '@pytest_ar' as an AST import alias, not a real module,
        # so the way to tell a pytest session is that pytest itself is loaded.
        # A standalone test script must not get rewritten asserts: there is no
        # assertion hook there to serve them.
        return compile_source(self.get_source(fullname), self.origin, self.module,
                              pytest_rewrite='pytest' in sys.modules)

    def exec_module(self, module):
        exec(self.get_code(module.__name__), module.__dict__)


class _Finder(importlib.abc.MetaPathFinder):
    """Take over imports of test/ modules, before pytest's own rewrite hook."""

    def find_spec(self, fullname, path=None, target=None):
        if not enabled() or fullname in _resolving:
            return None
        _resolving.add(fullname)
        try:
            spec = None
            for finder in sys.meta_path:
                if finder is self or isinstance(finder, _Finder):
                    continue
                # Pytest rewrites asserts in its own loader; ours does both.
                if finder.__class__.__name__ == 'AssertionRewritingHook':
                    continue
                find = getattr(finder, 'find_spec', None)
                if find is None:
                    continue
                try:
                    spec = find(fullname, path, target)
                except Exception:
                    continue
                if spec is not None:
                    break
        finally:
            _resolving.discard(fullname)
        if spec is None or not spec.origin or not spec.origin.endswith('.py'):
            return None
        origin = pathlib.Path(spec.origin).resolve()
        if TEST_DIR not in origin.parents or origin.stem in INFRASTRUCTURE:
            return None
        return importlib.util.spec_from_file_location(
            fullname, str(origin), loader=_Loader(str(origin)),
            submodule_search_locations=list(spec.submodule_search_locations or []))


_resolving: set[str] = set()


def install() -> None:
    if enabled() and not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[7].strip(), file=sys.stderr)
        return 2
    install()
    script = TEST_DIR / argv[0]
    if not script.is_file():
        print(f'no such test script: {script}', file=sys.stderr)
        return 2
    sys.argv = [str(script), *argv[1:]]
    scope = {'__name__': '__main__', '__file__': str(script), '__package__': None,
             '__cached__': None, '__doc__': None, '__spec__': None}
    exec(compile_source(script.read_text(encoding='utf-8'), str(script),
                        module=script.stem), scope)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
