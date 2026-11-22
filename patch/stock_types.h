#ifndef Q2_STOCK_TYPES_H
#define Q2_STOCK_TYPES_H
/* Stock's objects, which the payload only ever holds by address: each an incomplete type, so one
 * can't be dereferenced, sized or copied, and one kind is refused where another is asked for.
 * docs/internals.md#stock-handles has what they check and what they don't. */
typedef struct widget widget_t; /* a window, the window manager, a page's list, a row, a label */
typedef struct event event_t;   /* what a handler is given: a key, a touch, a window's open */
typedef struct canvas canvas_t; /* what a paint draws on: the LCD's, or an offline one */
typedef struct vgcanvas vgcanvas_t;               /* a canvas's vector half (canvas_get_vgcanvas) */
typedef struct widget_animator widget_animator_t; /* a glide or a fade in progress */
typedef struct widget_factory widget_factory_t;   /* widget_factory()'s */
typedef struct image_manager image_manager_t;     /* image_manager()'s */
typedef struct input_method input_method_t;       /* input_method()'s */
#endif
