/* The widgets a card theme's rules reach (card_rules.c; docs/internals.md, Card themes), Q2 Theme
 * Studio's catalogue on Q2 Pod: one row per widget, CARD_WIDGET(page, name, actions, font, x0, y0,
 * x1, y1): its page, its name there, the rules it takes, the Studio's default text size (0: no
 * font rule), and the box a move or size keeps it in, in its parent's coordinates: the parent's
 * laid-out size on the ipod geometry set, or less on purpose; 0s where it takes neither. The art
 * comes first (card_rules.c's ART). Left out on purpose: the heart's hide (its rating badge would
 * stay), icon sizes (Modern's marks draw at their own), the times' and the bar's moves (np_beside
 * reads their rows), the dots' (np_full and the visualizer lay them out), the lock clock's size and
 * font (big_clock lays it out), img_more (Tidal's only way to More), tint, the status bar, Home. */
CARD_WIDGET(CARD_PLAYING, "img_cover", CR_HIDE | CR_MOVE | CR_SIZE | CR_SQUARE, 0, 0, 0, 375, 186)
CARD_WIDGET(CARD_PLAYING, "scrlabel_title", CR_HIDE | CR_MOVE | CR_SIZE | CR_FONT, 22, 0, 0, 375, 186)
CARD_WIDGET(CARD_PLAYING, "scrlabel_artist", CR_HIDE | CR_MOVE | CR_SIZE | CR_FONT, 18, 0, 0, 375, 186)
/* its default Minimal's capitals (NP_CAPS_PX), as the Studio previews it */
CARD_WIDGET(CARD_PLAYING, "label_ipod_album", CR_HIDE | CR_MOVE | CR_SIZE | CR_FONT, 15, 0, 0, 375, 186)
CARD_WIDGET(CARD_PLAYING, "img_playmode", CR_HIDE | CR_MOVE, 0, 0, 0, 375, 186)
CARD_WIDGET(CARD_PLAYING, "img_fav", CR_MOVE, 0, 0, 0, 375, 186)
/* ends at More's x, so it can't cover More's tap */
CARD_WIDGET(CARD_PLAYING, "label_ipod_pos", CR_HIDE | CR_MOVE | CR_SIZE | CR_FONT, 16, 0, 0, 303, 40)
CARD_WIDGET(CARD_PLAYING, "label_playtime", CR_HIDE | CR_FONT, 14, 0, 0, 0, 0)
CARD_WIDGET(CARD_PLAYING, "label_ipod_remain", CR_HIDE | CR_FONT, 14, 0, 0, 0, 0)
CARD_WIDGET(CARD_PLAYING, "slider_play", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_PLAYING, "slide_indicator1", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "view_timeinfo", CR_HIDE | CR_MOVE, 0, 0, 0, 375, 320)
CARD_WIDGET(CARD_LOCK, "clock_time", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "clock_date", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "label_battery", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "img_battery", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "view_playinfo", CR_HIDE | CR_MOVE, 0, 0, 0, 375, 320)
CARD_WIDGET(CARD_LOCK, "scrlabel_title", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "scrlabel_artist", CR_HIDE, 0, 0, 0, 0, 0)
CARD_WIDGET(CARD_LOCK, "img_slide", CR_HIDE | CR_MOVE, 0, 0, 0, 375, 320)
