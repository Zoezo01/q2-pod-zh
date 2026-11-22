#!/usr/bin/env python3
"""Run test/patch.py with its asserts recorded instead of fatal.

pytest walks every case, so its run always finishes; patch.py is one long script whose first bad
assert hides everything after it (line 2065 today, the bar's clock box). This runner rewrites each
assert of the target into a check that still evaluates its expression — calls keep their side
effects — records a failure and runs on. Real exceptions still stop it, with the traceback and the
failures recorded so far.

    python test/keepgoing.py test/patch.py 构建产物-V189B

Exit status: 0 when nothing was recorded, 1 when something was.
"""
import ast
import os
import sys
import traceback


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = os.path.abspath(sys.argv[1])
    argv = sys.argv[1:]
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src, path)
    fails = []

    def no_arg_lambda(body):
        return ast.Lambda(
            args=ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                               kw_defaults=[], kwarg=None, defaults=[]),
            body=body)

    class Rewrite(ast.NodeTransformer):
        def visit_Assert(self, node):
            self.generic_visit(node)
            msg = node.msg if node.msg is not None else ast.Constant(value=None)
            call = ast.Call(func=ast.Name(id='__kg', ctx=ast.Load()),
                            args=[no_arg_lambda(node.test), no_arg_lambda(msg),
                                  ast.Constant(value=node.lineno)],
                            keywords=[])
            return ast.copy_location(ast.Expr(value=call), node)

    tree = ast.fix_missing_locations(Rewrite().visit(tree))
    code = compile(tree, path, 'exec')

    def __kg(test, msg, line):
        try:
            ok = test()
        except BaseException:
            fails.append((line, '<the check itself raised>'))
            print(f'KEPT GOING  line {line}: the check raised')
            traceback.print_exc()
            return
        if not ok:
            try:
                text = msg() if msg is not None else ''
            except BaseException:
                text = '<the message raised>'
            fails.append((line, text))
            print(f'KEPT GOING  line {line}: {text}')

    sys.path.insert(0, os.path.dirname(path))
    sys.argv = argv
    ns = {'__name__': '__main__', '__file__': path, '__builtins__': __builtins__,
          '__kg': __kg}
    status = 0
    try:
        exec(code, ns)
    except SystemExit:
        raise
    except BaseException:
        status = 1
        print(f'STOPPED at line {traceback.extract_tb(sys.exc_info()[2])[-1].lineno}:')
        traceback.print_exc()
    if fails:
        status = 1
        lines = sorted({line for line, _ in fails})
        print(f'\n{len(fails)} recorded, on {len(lines)} line(s): {lines}')
        for line in lines:
            text = next(text for at, text in fails if at == line)
            print(f'  line {line}: {text}')
    else:
        print('\nnothing recorded: every assert held')
    return status


if __name__ == '__main__':
    sys.exit(main())
