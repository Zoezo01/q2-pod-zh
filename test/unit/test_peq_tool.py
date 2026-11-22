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
    # patch/stock_types.h (docs/internals.md#stock-handles): AWTK names a function for what it works on, so a pointer
    # first parameter is one of those; what makes one, and stock's one of each, hand that kind back; a handler is
    # given its event. An entry added the old way, or put back to void * when a merge goes wrong, fails here.
    classes = ('window_', 'dialog_', 'slider_', 'slide_menu_', 'scroll_view_', 'scroll_bar_', 'table_client_',
               'text_selector_', 'hscroll_label_', 'label_', 'list_view_', 'list_item_', 'image_', 'button_', 'edit_',
               'pages_')  # a widget class's own functions take its widget
    kinds = {'widget_animator_': 'widget_animator_t *', 'widget_factory_': 'widget_factory_t *',
             'vgcanvas_': 'vgcanvas_t *', 'canvas_': 'canvas_t *', 'image_manager_': 'image_manager_t *',
             'input_method_': 'input_method_t *', 'widget_': 'widget_t *', **dict.fromkeys(classes, 'widget_t *')}
    makes = {'widget_animator_prop_create', 'widget_animator_scroll_create'}  # their first argument is what moves
    handlers = {'slide_menu_on_scroll_done', 'stock_search', 'album_covermode'}  # stock's own, called by the payload
    one_of_each = {'window_manager': 'widget_t *', 'widget_factory': 'widget_factory_t *',
                   'image_manager': 'image_manager_t *', 'input_method': 'input_method_t *'}
    handles = {*one_of_each.values(), 'event_t *', 'canvas_t *', 'vgcanvas_t *', 'widget_animator_t *'}
    held = 0
    for name, (ret, args) in build.FUNCTIONS.items():
        kind = next((k for prefix, k in kinds.items() if name.startswith(prefix)), None)
        first = args.split(',')[0]
        if name in handlers or (name.startswith('on_') and len(args.split(',')) == 2):  # an on_ timer takes one
            assert args == 'void *, event_t *', name
        elif kind and name not in makes and (first == 'void *' or first in handles):
            assert first == kind, name  # a string, a number or a timer's info first is not the object
            held += 1
        if kind and name.endswith('_create') or name in one_of_each:
            assert ret == one_of_each.get(name, kind), name
            held += 1
        if name.endswith('_on'):  # widget_on, widget_animator_on
            assert 'int (*)(void *, event_t *)' in args, name
        assert 'int (*)(void *, void *)' not in args, name
    assert held > 130  # the rule still finds them
