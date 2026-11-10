#!/usr/bin/env python3
"""A small source-level mutator for C: one token-sized change per mutant, each a whole copy of the file.

It works on tokens, not regular expressions, so comments stay alone and strings change once, whole. It needn't
be careful about what compiles: run.py drops whatever the compiler rejects, along with mutants that compile to
the same object as the original or as each other.
"""
import argparse
import json
from pathlib import Path
import re

TOKEN = re.compile(r'''
    (?P<comment>/\*.*?\*/|//[^\n]*)
  | (?P<string>"(?:\\.|[^"\\\n])*")
  | (?P<char>'(?:\\.|[^'\\\n])+')
  | (?P<number>(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*)
  | (?P<name>[A-Za-z_]\w*)
  | (?P<op><<=|>>=|\.\.\.|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%&|^~!<>=?:;,.(){}\[\]\#])
  | (?P<space>\s+|\\\n)
''', re.X | re.S)
DIRECTIVE = re.compile(r'^[ \t]*#(?:.*\\\n)*.*$', re.M)
DEFINE_BODY = re.compile(r'[ \t]*#[ \t]*define[ \t]+\w+(?:\([^)]*\))?')

# Each token's stand-ins: the two neighbours of a comparison, its opposite, and the usual swaps.
SWAPS = {
    '==': ['!='], '!=': ['=='],
    '<': ['<=', '>='], '<=': ['<', '>'], '>': ['>=', '<='], '>=': ['>', '<'],
    '&&': ['||'], '||': ['&&'],
    '<<': ['>>'], '>>': ['<<'], '/': ['*'], '%': ['*'], '^': ['&'],
    '+=': ['-=', '='], '-=': ['+=', '='], '*=': ['='], '/=': ['='], '|=': ['&=', '='], '&=': ['|=', '='],
    '++': ['--'], '--': ['++'],
}
BINARY_SWAPS = {'+': ['-'], '-': ['+'], '*': ['/'], '&': ['|'], '|': ['&']}  # these are unary as well
TYPES = {'char', 'int', 'unsigned', 'long', 'short', 'void', 'const', 'struct', 'static', 'scope_rule', 'sizeof',
    'return'}

def tokenize(text):
    """(kind, start, end) of the code, skipping comments and directives; a #define's body counts as code."""
    directives = [(m.start(), m.end()) for m in DIRECTIVE.finditer(text)]
    tokens, position = [], 0
    def scan(start, end, keep):
        at = start
        while at < end:
            match = TOKEN.match(text, at, end)
            if not match: raise SyntaxError(f'line {text.count(chr(10), 0, at) + 1}: {text[at:at + 20]!r}')
            if match.lastgroup in keep:
                tokens.append((match.lastgroup, match.start(), match.end(), start < position))
            at = match.end()
    every = {'string', 'char', 'number', 'name', 'op'}
    for start, end in directives:
        scan(position, start, every)
        position = end
        body = DEFINE_BODY.match(text, start, end)
        if body: scan(body.end(), end, {'string', 'number'})
    scan(position, len(text), every)
    return tokens

def closing(tokens, text, opening):
    depth = 0
    for i in range(opening, len(tokens)):
        word = text[tokens[i][1]:tokens[i][2]]
        depth += word == '('
        depth -= word == ')'
        if not depth: return i
    raise SyntaxError('unbalanced parentheses')

def mutations(text):
    """(start, end, replacement, operator) for each change to make."""
    tokens = tokenize(text)
    word = lambda i: text[tokens[i][1]:tokens[i][2]]
    attribute = set()
    for i, (kind, *_) in enumerate(tokens):
        if kind == 'name' and word(i) == '__attribute__':
            attribute.update(range(i, closing(tokens, text, i + 1) + 1))
    for i, (kind, start, end, directive) in enumerate(tokens):
        if i in attribute: continue
        token = text[start:end]
        if kind == 'number':
            digits = token.rstrip('uUlL')
            if digits in ('0', '1'):  # usually a flag, where 2 or -1 means the same
                yield start, end, '10'[int(digits)], 'constant'
                continue
            yield start, end, f'({token}+1)', 'constant'
            yield start, end, f'({token}-1)', 'constant'
            yield start, end, '0', 'constant'
        elif kind in ('string', 'char') and len(token) > 2:
            quote = token[0]
            first = 3 if token[1] == '\\' else 2  # keeps the length, whatever is left of an escape
            yield start, end, quote + ('X' if token[1] != 'X' else 'Y') + token[first:], 'literal'
            if kind == 'string': yield start, end, '""', 'literal'
        elif kind == 'op':
            for swap in SWAPS.get(token, ()):
                yield start, end, swap, 'operator'
            if token in BINARY_SWAPS and i and (tokens[i - 1][0] in ('name', 'number', 'string', 'char')
                    and word(i - 1) not in TYPES or word(i - 1) in (')', ']')):
                for swap in BINARY_SWAPS[token]:
                    yield start, end, swap, 'operator'
            if token == '!': yield start, end, '', 'negation'
        elif kind == 'name' and word(i) in ('if', 'while'):
            stop = closing(tokens, text, i + 1)
            for value in '10':
                yield tokens[i + 1][1], tokens[stop][2], f'({value})', 'condition'
        elif kind == 'name' and word(i) == 'for':
            stop = closing(tokens, text, i + 1)
            semicolons = [j for j in range(i + 2, stop) if word(j) == ';']
            if len(semicolons) == 2 and semicolons[1] > semicolons[0] + 1:
                for value in '10':
                    yield tokens[semicolons[0] + 1][1], tokens[semicolons[1] - 1][2], value, 'condition'
    yield from clauses(tokens, text)
    yield from statements(tokens, text)

def clauses(tokens, text):
    """Each side of && and || held at 0 and at 1, as the whole condition's mutants can't test one side alone."""
    word = lambda i: text[tokens[i][1]:tokens[i][2]]
    def edge(at, step, opening, closes, stops):
        depth = 0
        while True:
            token = word(at)
            if not depth and token in stops: return at - step
            depth += (token == opening) - (token == closes)
            at += step
    for i in range(len(tokens)):
        if word(i) not in ('&&', '||'): continue
        first = edge(i - 1, -1, ')', '(', {'(', '&&', '||', '=', ',', '?', ';', '{', 'return'})
        last = edge(i + 1, 1, '(', ')', {')', '&&', '||', ',', '?', ':', ';'})
        for begin, end in ((first, i - 1), (i + 1, last)):
            for value in '01':
                yield tokens[begin][1], tokens[end][2], value, 'clause'

def statements(tokens, text):
    """Each simple statement inside a function as a lone semicolon, and each return of a value as a return of 0."""
    word = lambda i: text[tokens[i][1]:tokens[i][2]]
    braces = parens = 0
    begin = i = 0
    while i < len(tokens):
        if tokens[i][3]:  # a #define's body
            i += 1
            begin = i
            continue
        token = word(i)
        if i == begin and token in ('if', 'while', 'for', 'switch'):
            i = closing(tokens, text, i + 1) + 1
            begin = i
            continue
        if i == begin and token in ('else', 'do'):
            i += 1
            begin = i
            continue
        parens += token == '('
        parens -= token == ')'
        if token in '{}' and not parens:
            braces += 1 if token == '{' else -1
            begin = i + 1
        elif token == ';' and not parens:
            if braces and i > begin:
                start, end = tokens[begin][1], tokens[i][2]
                yield start, end, ';', 'statement'
                if word(begin) == 'return' and i > begin + 1:
                    yield start, end, 'return 0;', 'return'
            begin = i + 1
        i += 1

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    text = args.source.read_text(encoding='utf-8')
    args.directory.mkdir(parents=True, exist_ok=True)
    seen = {text}
    with open(args.directory/'manifest.jsonl', 'w', encoding='utf-8') as manifest:
        for start, end, new, operator in mutations(text):
            mutant = text[:start] + new + text[end:]
            if mutant in seen: continue
            seen.add(mutant)
            name = f'{len(seen) - 1:05}.c'
            (args.directory/name).write_text(mutant, encoding='utf-8')
            manifest.write(json.dumps({'id': name, 'line': text.count('\n', 0, start) + 1, 'operator': operator,
                'old': text[start:end], 'new': new}, ensure_ascii=False) + '\n')
    print(f'{len(seen) - 1} mutants of {args.source} in {args.directory}')

if __name__ == '__main__':
    main()
