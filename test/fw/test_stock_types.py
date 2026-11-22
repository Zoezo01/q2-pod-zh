"""What stock_types.h's handles refuse, and what they still let through (docs/internals.md#stock-handles).

Each case is a function compiled, not run: against the headers test/fw builds with, a pointer of the wrong kind
a hard error, as the Q2's build and tools/tidy.sh have it (-Werror). A refusal must be all its case's errors are,
so a case that stopped compiling for another reason fails here instead of passing."""
import subprocess

import pytest

import host

ARGS = 'widget_t *w, event_t *e, canvas_t *c, vgcanvas_t *vg, widget_animator_t *a, void *p'
FLAGS = [f for f in host.FLAGS if f != '-Wno-incompatible-pointer-types'] + ['-Werror=incompatible-pointer-types']

# What the compiler stops: {case: its statements, or (statements, a function they use)}
REFUSED = {
    'an event as a widget': 'widget_set_visible(e, 1, 0);',
    'a widget as an event': 'widget_dispatch_event_to_target_recursive(w, w);',
    'a paint with its widget and canvas the wrong way round': 'widget_paint(c, w);',
    'a canvas as its own vector half': 'vgcanvas_fill(c);',
    'an animator as the widget it moves': 'widget_destroy(a);',
    "a buffer of the payload's own as a widget": 'char name[8] = ""; widget_set_text_utf8(name, name);',
    "a widget where the payload's own function draws on a canvas": 'fill_box(w, 0, 0, 0);',
    "a canvas where the payload's own function wants a list": 'list_row(c, 0, 0, 0);',
    'a handler that takes a widget for its event': (
        'widget_on(w, EVT_CLICK, wrong, 0);', 'static int wrong(void *ctx, widget_t *target) { return ctx && target; }'),
    # one position each that test/unit's name rule can't reach: what the most used finders and makers hand back, and
    # a paint's canvas
    'a widget found by name kept as a canvas': 'c = widget_lookup(w, "name", 1); (void)c;',
    "a widget's child given as the event": 'widget_dispatch_event_to_target_recursive(w, widget_get_child(w, 0));',
    'the top window kept as a canvas': 'c = window_manager_get_top_window(window_manager()); (void)c;',
    "a factory's widget kept as a canvas": 'c = widget_factory_create_widget(widget_factory(), "x", w, 0, 0, 1, 1); (void)c;',
    'an event made for a widget given as one': 'widget_destroy(pointer_event_init(p, 0, w, 0, 0));',
    'a widget where a paint wants its canvas': 'widget_paint(w, w);',
}
KINDS = ['widget_t', 'event_t', 'canvas_t', 'vgcanvas_t', 'widget_animator_t', 'widget_factory_t', 'image_manager_t',
         'input_method_t']
# What it can't: C lets void * be any of them, and P() and I() take any pointer
ACCEPTED = {
    'each kind where it belongs': (
        'widget_paint(w, c); vgcanvas_fill(vg); vgcanvas_fill(canvas_get_vgcanvas(c)); widget_animator_start(a); '
        'widget_dispatch_event_to_target_recursive(w, e); widget_on(w, EVT_CLICK, right, w);',
        'static int right(void *ctx, event_t *event) { return ctx && event; }'),
    'void * as any handle': 'widget_set_visible(p, 1, 0); widget_paint(p, p);',
    'a handle through void * as another kind': 'void *any = e; widget_set_visible(any, 1, 0);',
    'what P() reads as whatever it is assigned to': 'canvas_t *wrong = P(w, W_PARENT); widget_paint(w, wrong);',
    "a handler's ctx as a widget, whatever was registered": (
        'widget_on(w, EVT_CLICK, as_widget, e);',
        'static int as_widget(void *ctx, event_t *event) { return widget_set_visible(ctx, 1, 0) + !event; }'),
    'a cast': 'widget_set_visible((widget_t *)e, 1, 0);',
    "one class's offset read from a widget of any class": 'return I(w, ROW_INDEX) + I(w, SLIDE_INDEX);',
}


def compiled(library, tmp_path, case):
    body, helper = case if isinstance(case, tuple) else (case, '')
    source = tmp_path/'case.c'
    source.write_text(f'#include "navigation.h"\n{helper}\nint check({ARGS});\nint check({ARGS}) {{\n    {body}\n    return 0;\n}}\n')
    return subprocess.run(['cc', *FLAGS, '-fsyntax-only', '-I', str(library.parent), '-I', str(host.ROOT/'patch'), str(source)],
                          capture_output=True, text=True)


@pytest.mark.parametrize('name', REFUSED)
def test_a_pointer_of_the_wrong_kind_is_refused(library, tmp_path, name):
    result = compiled(library, tmp_path, REFUSED[name])
    assert result.returncode, name
    errors = [line for line in result.stderr.splitlines() if 'error:' in line]
    assert errors and all('incompatible' in e and 'pointer type' in e for e in errors), result.stderr


@pytest.mark.parametrize('kind', KINDS)
def test_each_kind_refuses_every_other(library, tmp_path, kind):
    # stock_types.h's whole point: no two are the same type, so a typedef copied and left naming another's struct
    # fails here, whichever two
    others = [k for k in KINDS if k != kind]
    result = compiled(library, tmp_path, ' '.join(f'{{ {kind} *x = ({other} *)p; (void)x; }}' for other in others))
    errors = [line for line in result.stderr.splitlines() if 'error:' in line]
    assert len(errors) == len(others) and all('incompatible' in e and 'pointer type' in e for e in errors), result.stderr


@pytest.mark.parametrize('name', ACCEPTED)
def test_what_the_types_cannot_see_still_compiles(library, tmp_path, name):
    result = compiled(library, tmp_path, ACCEPTED[name])
    assert not result.returncode and 'error:' not in result.stderr, result.stderr


@pytest.mark.parametrize('body', ['return (int)sizeof *w;', 'widget_t copy = *w; (void)copy;', 'return *(int *)e + e->type;'])
def test_a_handle_has_no_inside(library, tmp_path, body):
    # nothing here lays stock's objects out: one can't be sized, copied or reached into
    result = compiled(library, tmp_path, body)
    assert result.returncode and 'incomplete' in result.stderr, result.stderr
