"""tools/peq.py's stock-free parts; compile_common and patch_player link against stock (# pragma: no mutate block).
Not test_peq.py: test/fw has one, and neither folder is a package, so pytest can't collect both."""
import build
import peq


def test_function_macros_call_each_stock_function_at_its_address():
    got = peq.function_macros({'_create_vector': '0x401000u', 'vector_init': '0x402000u', 'not_stock': '0x403000u'})
    # _create_vector is void *(const char *), vector_init void (void *); a name stock lacks gets no macro
    assert got == ['#define _create_vector ((void * (*)(const char *))0x401000u)',
                   '#define vector_init ((void (*)(void *))0x402000u)']
    assert len(peq.function_macros(dict.fromkeys(build.FUNCTIONS, '0'))) == len(build.FUNCTIONS)


def test_stock_functions_take_stock_types():
    # patch/stock_types.h (docs/internals.md#stock-handles): a widget's, a canvas's, a vector canvas's and an
    # animator's own functions take one, not void *, and a handler is given its event. An entry added the old way,
    # all void *, fails here.
    kinds = {'widget_animator_': 'widget_animator_t *', 'vgcanvas_': 'vgcanvas_t *', 'canvas_': 'canvas_t *',
             'widget_': 'widget_t *'}
    make_one = {'widget_factory', 'widget_factory_create_widget', 'canvas_offline_create',
                'widget_animator_prop_create', 'widget_animator_scroll_create'}  # their first argument is not one
    held = 0
    for name, (_, args) in build.FUNCTIONS.items():
        kind = next((k for prefix, k in kinds.items() if name.startswith(prefix)), None)
        if kind and name not in make_one:
            assert args.split(',')[0] == kind, name
            held += 1
        assert 'int (*)(void *, void *)' not in args, name
    assert held > 80  # the rule still finds them
