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
    # patch/stock_types.h: a FUNCTIONS entry added the old way, or put back to void * by a merge, fails here
    objects = ('widget_', 'canvas_', 'vgcanvas_', 'window_', 'dialog_', 'slider_', 'slide_menu_', 'scroll_',
               'table_client_', 'text_selector_', 'hscroll_label_', 'label_', 'list_view_', 'list_item_', 'image_',
               'button_', 'edit_', 'pages_', 'input_method_')
    for name, (ret, args) in build.FUNCTIONS.items():
        assert 'int (*)(void *, void *)' not in args, name  # a handler is given its event
        if name.startswith(objects) and name != 'slide_menu_on_scroll_done':  # that one is stock's (ctx, event)
            assert not args.startswith('void *'), name
        if name.endswith('_create') or name in ('window_manager', 'widget_factory', 'image_manager', 'input_method'):
            assert ret != 'void *', name
