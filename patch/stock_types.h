#ifndef Q2_STOCK_TYPES_H
#define Q2_STOCK_TYPES_H
/* Stock's objects, under the names AWTK gives them: the ones stock makes and owns, which the
 * payload only ever holds by address. Each is an incomplete type, so nothing here says what is
 * inside one: a handle can't be dereferenced, sized or copied, and the compiler refuses one kind
 * where another is asked for (-Wincompatible-pointer-types, an error in the build and in
 * tools/tidy.sh). An event can't be passed as a widget, nor a canvas as a widget, nor a buffer of
 * the payload's own as either. tools/build.py's FUNCTIONS types stock's functions with these.
 *
 * What they don't check. void * still becomes any handle, and any handle void *, without a word,
 * as C has it: so what P() reads out of stock's memory, a handler's ctx and an explicit cast are as
 * unchecked as they were. A widget_t * is any widget: which class it is, and so which of
 * offsets.inc's class offsets hold for it, is still its caller's to know. Nor does a type say the
 * object is still alive. And what the payload lays out itself and hands to stock stays as it was:
 * a rectangle (rect_t or int[4]), a point, the 64 words a bitmap_t is loaded into, an event built
 * in words of its own. docs/internals.md#stock-handles has the whole of it. */
typedef struct widget widget_t; /* a window, the window manager, a page's list, a row, a label */
typedef struct event event_t;   /* what a handler is given: a key, a touch, a window's open */
typedef struct canvas canvas_t; /* what a paint draws on: the LCD's, or an offline one */
typedef struct vgcanvas vgcanvas_t;               /* a canvas's vector half (canvas_get_vgcanvas) */
typedef struct widget_animator widget_animator_t; /* a glide or a fade in progress */
typedef struct widget_factory widget_factory_t;   /* widget_factory()'s */
typedef struct image_manager image_manager_t;     /* image_manager()'s */
typedef struct input_method input_method_t;       /* input_method()'s */
#endif
