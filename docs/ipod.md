# iPod UI

The build carries the compact layout payload helpers and build-time edits in `tools/ipod.py`.
`patch/ipod.json` records the original
asset hashes and full MIPS instructions. The builder also pins the complete stock
ZIP and executable, rejects mismatches, and records every changed asset/site.

AWTK binary UI files contain a four-byte magic, recursive widgets with a 32-byte
type and four signed geometry fields, NUL-separated properties and child/end
markers. Decode/encode must round-trip exactly before editing. Only the assets
pinned in `ipod.json` are accepted: nine local browsing pages, the settings
and streaming pages, Home, the status bar, Now Playing, the quick settings
pull-down, the confirm and volume pop-ups, the equalizer page (its [transition](#transitions) only) and the
theme (`styles/default.bin`). The primary `view_navbar` stays allocated but invisible
and disabled, including dynamically recreated children. Separate action bars are
moved into its space.

## Rows

`PITCH = 72` is shared by all build-time row geometry edits, native row-height
resets and artwork offset divisors. Four rows fit the 290-pixel client area below
the status bar, so `BOTTOM = 290` replaces the stock lists' 260-pixel content
bottom. Row bodies are `PITCH - 4` pixels, and the stock 52-pixel artwork is
drawn at natural size (`ART = 52`) with `ART_INSET = 8` on all four sides: the
artwork never rescales, so glyphs and covers stay as sharp as stock, and the row
layout's eight-pixel left margin seats the artwork exactly. ART_INSET also
positions the playing overlay. Font definitions are untouched. Full-height
text/icon containers are shortened with their button. Titles and metadata keep
their stock centring, one pixel higher for the two-pixel-shorter body.

Native local row-pool constructors are at 0x533bc4 (folder), 0x4ac588 (songs),
0x4b39f4 (local categories) and 0x4a6910 (album list/grid). The album grid branch
is unchanged. Album detail, artist track and playlist constructors have their own
explicit sites in the audit. Folder reset at 0x532000, return offset division at
0x532308 and scrolling cover division at 0x533288 all use the same compact pitch.
The category cover callback originally divides by 120 despite using 78-pixel
rows; iPod corrects its audited divisor at 0x4b2a48 to 72. Rebinding and delayed
cover callbacks keep the same widget geometry and saved cover preferences.
Artists rows hide their head picture (`CLASS_ICON`), so the title takes its width.

Seven audited row-constructor calls install a per-instance children layouter for
folder, song, album-list, category, album-track, artist-track and playlist rows.
Before the stock horizontal layout runs, it gives the title (and its containing
view, where present) the row width minus the existing side margins, visible sibling
widths and gaps. Hidden artwork and controls reserve no space. The stock layouter
still positions the children, preserving the title's left edge, artwork, row height
and padding. Its clone/destruction and parameter functions remain native; neither
the shared widget implementation nor album grids are hooked. The folder rebind's
140/190-pixel resize call is disabled so recycled titles retain their computed
width. Title styles and ellipsis settings are untouched; the title scrolls only while focused
(`hscroll_label_set_only_focus`), which no row is, so a long title keeps its ellipsis instead of
every row's scrolling once as the page opens. The selected row's scrolls, as an iPod's does: once
the selection settles the payload runs stock's scroller on it (`hscroll_label_start`) as a looping
label, with every label's holds and pace (docs/internals.md, `ringnav_hscroll_start`): its start
held `MARQUEE_HOLD_MS`, a scroll at Now Playing's pace, its end held as long, then back to its start.
The lull at each pass's end counts it, and after `MARQUEE_PASSES` (3) the title rests on its start
until the selection moves; each pass repaints every 16 ms while it moves. `only_focus` is off while
it runs, since stock's timer (`0x5fb7ac`) stops an unfocused `only_focus` label on its first tick;
once running, stock's paint (`0x5fbff0`) drops the ellipsis, so the selected title shows its start
without one as it holds. A paint that moves the selection, to another widget or to another row in the
same pooled widget, stops it from a timer. A title that fits costs one timer per selection.
ponytail: the first `hscroll_label` of the row is taken as its title, and a dark screen, a covered
page or a touch's hidden selection stops it only at the end of its pass, not at once.

## Flat rows and selection

The iPod theme uses black surfaces, white titles and subdued secondary text with the stock
multilingual font. The existing 72-pixel browsing pitch, 52-pixel covers and 68-pixel settings
rows remain calibrated to the glass. Settings icons are resized as before and keep their colours;
**Theme: Minimal** turns every icon's coloured disc the same neutral grey (`MINIMAL_CHEVRON`, as
stock's folder and song discs), keeping the white glyph and blending its edges by saturation, and
inks `list_into`'s glyph that grey too, as the image manager loads them (`ringnav_image_add`), so
it is visible on both row surfaces. Built-in library discs use the same mapping; navigation,
streaming and control icons become monochrome while retaining their light/dark glyphs and active
states. Alpha stays intact. Artwork, photos, external images and battery warning colours stay
unchanged; Classic keeps the icons' colours.
The pinned style edits in `patch/ipod.json` remove list fills and corners and make pressed
feedback neutral grey. Playing titles keep the separate playing glyph.

In Minimal, wheel selection under its own accent (**Accent: Ivory**) is off-white (`#EEEEEC`): a card, under **Rounded Corners** on inset
`SEL_INSET` (6) pixels from each side of the row, a pill (corners half its height, less on a row whose cover the round end would cut, such as Search's results: `pill_radius` shrinks them until the cover's corners are inside, checking only the row's own visible left-hand, non-centred images, so Folder's hidden multi-select check doesn't square it; grid tiles `SEL_RADIUS`, 6), and under **Rounded Corners** off a full-width square bar with square tiles,
drawn with the anti-aliased rounded fill (square without a vgcanvas). Any other accent fills the
card with its colour and leaves the rows' own (white) ink, as Classic's band does. Minimal's unselected
secondary text, stock's `#AAAAAA`, is the quieter `SUDO_MUTED` (`#8C8C8C`) everywhere. **Theme:
Classic** draws its accent's gloss band instead (`SEL_STOPS`), square and full width under white text, and a grid tile a square card in the accent's colour; **Theme: Modern** the accent's
flat bar (`SEL_BAR`), full width, or an inset pill under Rounded Corners ([Theme](#theme)). The background
hook resolves the selected row; the color hook maps its title to `#171717` and metadata to
`#484848`, preserving alpha. Paint ancestry determines the colors, so no style overrides can
remain on recycled rows or stale scrolling labels. Selected backgrounds stay transparent over
the selection. Touch hides wheel selection until wheel/centre input; the native pressed state
remains visible. Under **Rounded Corners** on a pressed list row (`s_btn_listitem`) is that grey as the
selection's inset pill instead (`pressed_pill`), and a pressed tile under half its row's width (the album
grid's, the playlist page's pair) is the tile's selection card, `TILE_INSET` inside it with `SEL_RADIUS`
corners (square under Square); other pressed widgets keep stock's box. The same hooks cover settings, streaming, dialogs, PEQ rows and media browser
chrome. Photos, video, book content, EQ plots and visualizers keep their own drawing.

## Home

Home splits the screen: the menu on the left, the current local track's art in the right panel.
In Minimal the selection is a small white dot and bright text; other destinations use `#AAAAAA`.
There is no selection rectangle or chevron. **Theme: Classic** gives the menu its lists' look
instead: the gloss band and the white chevron on the selected row (below, and [Theme](#theme)).
**Theme: Modern** does the same with its accent bar, from the top of the screen, its text at
`HOME_MODERN_TEXT_X` and Full's rows `HOME_MODERN_ROW` wide, with the chevron on the bar.

The seven destinations remain Now Playing, Library, Cover Flow, Folder, Rockbox, Streaming and
Settings. Display → Appearance's **Hide Streaming** and **Hide Folder** (`Q2POD HIDESTREAM`, `HIDEFOLDER`, off; every theme's last two rows; "… on Home" until 1.8.4, too long for a switch row's label) hide those rows of Home in Split and Full as a missing Rockbox is hidden: restacked last and insensitive (`coverflow_home_layout`). Carousel keeps their cards, as it keeps the Shortcut's. Rockbox is visible only when its binary is on the card, unless Display → Appearance's **Shortcut**
(`IPOD/SHORTCUT` 0-11, read as a decimal by `config_value`) turns that row into Artists, Albums, Favorites, Rated Albums, Shuffle, Games, Playlists, Podcasts, Audiobooks or Groovebox (`home_shortcut_open`; Groovebox (10, `SHORTCUT_GROOVEBOX`) is the App Store's Groovebox card game, started by id through `addon_game_open` as its Games row would start it (the tap toasts when no card game has the id), shown as a row only while `/mnt/mmc/.groovebox/q2-groovebox` is a regular file (`groovebox_available`, cached as `st.home_groovebox`; `bar_sync` relays out Home when that changes, as for Rockbox), and offered by the Centre cycle only then, while Rockbox is always offered; Rated Albums is Ratings → Albums, `rated_albums`; Shuffle is the Library's Shuffle page, `shuffle_menu`; Playlists is the Library's list, opened as stock's Home opens it; Podcasts and Audiobooks are the card's folders, opened as their Local Music rows open them, `media_click`, which toasts "No Podcasts folder on the card" without one; Split's pane shows Folder's icon for them); None (11, `SHORTCUT_NONE`) takes the row off Home's list, as a missing Rockbox does (`ipod_home_rockbox` returns 0), in Split, Full and every theme; the list's count changes, so its selection starts over, and it wraps over the rows left; with no key, or a value this build does not know (12 and up, and 11 on a build before 1.8.2, which reads it as no choice and shows the row), it is Rockbox when the card has it, else Favorites. Centre cycles Rockbox, Favorites, Shuffle, Rated Albums, Playlists, Artists, Albums, Games, Podcasts, Audiobooks, Groovebox (when installed), None. Settings opens Playback and
System; Return restores the menu's previous selection, and a short Return on the main menu selects
Now Playing; a short Return with Now Playing selected opens it without changing playback. Return there goes back to Home. The wheel bumps at either end of both lists and wraps after a pause, as ring lists do ([internals.md](internals.md#wheel-movement)).
Folder opens directly at the SD root when a card is in and no OTG drive is connected; Back at that root returns
to Home. With OTG connected or no card in on entry, Folder keeps the storage selector. Nested folders and
Library’s Podcasts/Audiobooks shortcuts keep their usual Back navigation.
The native stock handlers, translation keys, full-row tap targets and submenu animation remain intact.

Minimal's rows are `HOME_ROW=39` high from `HOME_TOP=8`; Classic's are its set's `home_row` (32),
both Home menus directly below the status bar, matching Library. Minimal's 6-pixel dot sits at x 25, clear of the
bottom row's corner, and its labels start 8 pixels after it (x 39) and end 33 pixels before the row's
edge. Both menus are 375 pixels wide in the asset; the payload lays them out (below). Long
translated labels ellipsize and scroll, in the stock font under Minimal.
The background view paints before the transparent menus, and both menus are clipped to the
window while sliding.

Display settings offers **Home: Split / Full / Carousel**, stored as `IPOD/HOME` 0, 1 or 2; invalid or absent
values mean Split. `coverflow_home_layout` moves the asset's widgets at runtime, in every theme:
Split narrows both menus to `HOME_SPLIT_W` (187, half the screen) and shows the cover itself in the 290-pixel-high panel right of them
(`img_homeart` is an `image` for this, fitted to the cover's proportions and clipped to the panel,
the menus clipped left of it; `default_album_home` without a cover); Full hides the art and
widens the menus to the screen, their rows to `HOME_FULL_ROW` (375; Classic's `full_row`, the same). Minimal's labels keep x 39 and end 33 pixels from the screen edge in Full, `HOME_SPLIT_LABEL_END` (8) before the art in Split, where no corner needs clearing (140 pixels wide). Classic's
start at x 20 and end 20 pixels before the art in Split, 33 from the screen edge in Full, in its own face at 27 pixels (`theme_font`); where that face lacks a character of the text, Liberation Sans Bold stands in: the build ships it as `fonts/bold.ttf` (`assets/fonts`, OFL), cut to its Latin, Greek, Cyrillic and punctuation, given the same play mark as Classic's face, and renamed Q2 Pod Sans (the OFL keeps the Liberation name for the unmodified font), named in a label's style while the font has every character of its text (`BOLD_RANGES`, generated from the same cmap), else stock's font, which has CJK. Now Playing's title goes through it too, as its text changes. Classic's chevron is its own white glyph on the selected row only, at
`home.chevron.x` in Split and its lists' `list.chevron.x` in Full.

The art is the player's cover once `g_lastcover_url` matches the current queue path, else the
current album's Cover Flow thumbnail, else the placeholder; the previous track's player file is
never used as a fallback. It reloads only when its key (the queue path, usable player-cover type
and parsed album tags) changes, and not at all in Full. The reload runs inside a paint, and AWTK
drops what a paint invalidates when the frame ends, so a 0 ms timer then repaints the whole screen;
otherwise the new art and its fit would reach only the regions repainted later. `g_playcover_finishflag` remains
stock-owned. Now Playing and Spotify's page keep their black background behind the art.

**Carousel** replaces the lists and the art with stock's home carousel. It has eight cards: Now Playing, Library, Cover Flow, Folder, the Rockbox shortcut, Streaming, Playback Settings and System Settings. The Rockbox shortcut card is the Shortcut row: it opens what the Shortcut setting chooses, and stays when that row is hidden (no Rockbox, or no Groovebox, on the card), where tapping it shows a message. With Shortcut: None it stays too, and shows and opens what no choice would (Rockbox when the card has it, else Favorites): None applies to Home's list. The cards keep their own colours, as on the stock Home, whatever the Theme, accent or Light Mode. The wheel moves through the carousel ([internals.md](internals.md#home-carousel)), Centre opens the card and touch drags and taps as in stock; Return keeps stock's behaviour there. Leaving Carousel for Split or Full shows the main list.

The player writes every track's cover to the same `/tmp` file, so before Split loads it `coverflow_home_art` drops AWTK's cached bitmap of that name, as the full-screen art does; otherwise the image manager hands back the last track's cover.

In Split a cover pans slowly, as an iPod classic's menu art did: `home_tex` box filters it once
(at most 4 × 4 samples a texel) to its fit zoomed `DRIFT_ZOOM_PCT` (110), and each axis runs its
own linear ping-pong across that margin alone, from the old crop (`DRIFT_X_MS` 20 s, `DRIFT_Y_MS`
26 s): about 1.5 pixels a second on a square cover, as iPod.js's recreation moves, and the cover's
edges never show. The art widget then spans the panel with an empty image, and `home_paint` (from
`coverflow_home_clip`, inside the panel's clip) draws one 188 × 290 frame `home_render` filters from
the cover at the pan's position in 1/256 px, a cubic B-spline over 4 × 4 texels (each cover row
across once, then down). Whole-pixel steps at that speed read as a jerk every two thirds of a
second, one axis at a time; the filter moves it by fractions of a pixel, as the iPod's own
rendering does. Bilinear would keep a whole-pixel frame's texels but mix a half-pixel one's 50/50,
so the cover's sharpness would pulse with the pan's phase, most visibly in lettering. The
B-spline blurs every phase about alike but is soft, so `home_tex` sharpens the filtered cover
once, `[-1,6,-1]/4` across and down (`home_sharpen`), and keeps it inside a border of its edges (a
texel left and above, two right and below) for the filter's reads past the panel. The art is about
twice as sharp as a `[1,2,1]/4` softening with bilinear, and its sharpness changes with the phase by
about a tenth on a photo and a fifth on lettering. A frame costs about 4.8 million instructions,
2.3 times the bilinear's, five a second while Home shows; the sharpening adds about 13 million
once a cover. A cover whose zoomed fit passes `DRIFT_TEXELS` (four panels: wider
than about 2:1, or taller than 5:1), a decode that is not 32-bit, the placeholder and Full stay still and unzoomed,
drawn by the stock fill. Nothing is stored: the pan's phase counts only time shown, so leaving
Home, a new cover or a Theme change resumes it where it was. One `DRIFT_TICK_MS` (200 ms, about 0.3 px a step; a late tick steps at most `DRIFT_MAX_STEP_MS`, 500 ms) timer
runs it, armed from Home's paint only while Home is the top window and not sliding, the screen is
lit and the lock screen is off; its next step after any of those changes removes it, and
`coverflow_home_layout` (Home, Theme, a new Home window) removes it at once. A step is capped at
`DRIFT_MAX_STEP_MS`, so a stalled loop never jumps. A step invalidates only the panel, and a paint
renders the frame again only once the pan has moved. It pans while paused too. Not done: the
iPod's random slideshow of library covers and its crossfades (the library's thumbnails are too
small for a 290 px panel, so each change would decode a full cover), and Light Mode's paper behind
a transparent cover follows only the next cover change. The timer trusts `home.win` as Home's
identity, as the art's paint always has: Home is only replaced through `coverflow_home`, which
stops the timer first.

## Status bar and clock

**Status Bar** (`STATUSBAR`, an own option every theme declares, under Theme) swaps the look of the bar alone. `bar_theme()` names the theme whose fill, battery and play state are painted, the theme's own at **Theme**, and `bar_scheme()` that theme's row at its own stored accent, in the half this theme's Light Mode picks. The row itself is still the booted set's: its height, its widgets' places and the title's face and case stay the theme's, so a choice applies at the next paint with no restart (a look that moved them would need a geometry set per bar). What a look says of its own group goes with it onto any row: the battery's size (`bar_geom_val`) and the rows Bluetooth and Wi-Fi sit off the layout's (`bar.icons`, Classic's a row down and a row up). Either battery centres on the row's height (`batt_top`), as the icons beside it do, not on its slot's, which is the bar's: Classic's row is 22 px of its bar's 26, sized so the body lands in the middle of the rows above the bar's two lines, and on Minimal's 30 px row the same rule keeps Classic's body level with its icons and the clock, which that row centres on the bar's whole height: 2 px below the middle of the rows above the lines. What the row has room for is the layout's, so `bar_sync` counts Classic's right-hand group by `theme()`. On Classic's dark pages a flat look turns `dark_bar_rows` plain black, and those pages keep their own title and neutral battery. Choosing a theme presets the row to **Theme**.

`systembar_showface` (`0x5417d0`, run by `system_bar_init` and then a 1 s widget
timer) finds each status bar widget with a recursive `widget_lookup` from the bar,
so parents and order are free to change. Every tick it re-shows the volume,
EQ, Bluetooth, SyncLink and Wi-Fi widgets and sets their images and text, but
never their geometry. Minimal's `system_bar.bin` (iPod) therefore keeps the play state
and EQ in `view_left`, as stock does, and puts Bluetooth/codec, Wi-Fi and the
battery in `view_right`; Classic's bar, its set's own copy, has them all on the right
([Theme](#theme)), and the layout below is Minimal's. EQ uses an explicit 16 px font and a 28 px cell: its text is 24 px wide in the shipped font, so stock's 20 px cell clips it. A 16 px `Q2` label at the left inset appears only when both playback state and EQ are hidden. Stock's tick also hides `img_state` whenever its own player is stopped, as it is under Spotify, radio or a video; `ringnav_showface` runs `bar_state_sync`, the play state and the `Q2` label alone, straight after the tick, so the payload's play state is back before any paint, dark or not (no file reads; a relayout waits for the paint); the codec badge, accent and Rockbox check wait for paint (`bar_sync`). A new battery level or charge state repaints the bar from the tick while lit ([Status bar](internals.md#status-bar-ipod)). Correcting it at paint time instead relaid the row twice a second, and EQ was drawn for a frame in the play icon's place. When `bar_sync` changes `img_state` while another window paints (Now Playing paints every 250 ms, so it usually sees a Play/Pause first), it arms a 0 ms timer that runs `widget_invalidate_force` on the bar after the frame: an invalidate made mid-paint is dropped with that frame's dirty rects, and `img_state` keeps its dirty flag (`+0x24` bit `0x200`), which makes stock `widget_invalidate` return early, so the 1 s tick's own invalidates skipped it until something else repainted the bar (#116). `bar_sync` updates its visibility without moving the centred clock. The volume icon and number and SyncLink move to `x = -200`, where they
draw off-screen. Both groups sit 54 pixels
from the edges (`STATUS_MARGIN`): the 50 where the 16-pixel icons clear the top corners
([Rounded corners](#rounded-corners)) plus 2 (`STATUS_PAD`) so they don't crowd the glass. A new
`label_clock` (`s_scrlabel_white20c`, overridden to 16 px) is centred on the screen. It has the width left in the
narrowest case, 109 pixels at x 133, clear of either group with every icon shown (left 94
pixels, right 133); the build fails if that would be under `CLOCK_MIN` (105). The payload paints
the bar and writes the time into `label_clock` (see
[internals.md](internals.md#status-bar-ipod)); the widest text, `12:59 PM`, is 69 pixels in the
stock font at 16 pixels.

**Time in Title.** Display → Appearance's **Time in Title** switch (`IPOD TIMETITLE`, on unless chosen; Minimal's and Classic's presets turn it on, Modern's off) is the iPod's own. Under Minimal and Modern, off, `label_clock` names the page in front instead of the time, as an iPod's title bar does (`bar_title`): "Now Playing" or "Cover Flow", Home by `coverflow_home_title` as Classic's bar names it ("Q2 Pod", or nothing with Show Wordmark off: Home has no clock either way), else stock's title in the page's hidden navbar (`scrlabel_title`, or a settings page's unnamed first child). A page showing its navbar and a page with no title keep the clock, and a pop-up or keyboard leaves the bar as it is. A title past the clock's width ends in an ellipsis. Classic's bar names the page in a title of its own, and the switch decides only its clock ([Theme](#theme)). ponytail: the payload's other pages (Games, Photos, Books) have no navbar, so the bar stays on the clock there, most with a heading on the page; naming them means reading each page's caption.

**Codec.** The Bluetooth images are 42-pixel canvases with their ink at the right: the glyph takes
10 pixels, while stock's codec badges take up to 40 (aptX HD). A new badge shows for a second
(`CODEC_MS`), then fades out and the white Bluetooth glyph (`bar_btcon`) fades in at the same place,
150 ms each way; the glyph then stays until the codec changes or Bluetooth goes off.

**Battery.** The Battery setting (see [Display settings](#display-settings)) offers four modes at the right end; the layout skips hidden widgets:

- Icon: a clean 16×11 charge-level outline and a 2-pixel nub, drawn in `view_battery`.
- Percent: the 16 px percentage alone, drawn in `view_battery`.
- Icon + Percent: the same charge-level icon with a 16 px percentage beside it.

The percentage's slot is as wide as its digits (measured once in the stock font, which has tabular
digits), so every mode ends on the icons' margin with the same 4-pixel gap to Wi-Fi; `BATT_PCT_W`
(42, "100%") stands in until the first paint has measured them.

- Percent Inside (`BATTERY=3`): a compact 28×15 solid battery with slightly rounded corners and a 2×5 terminal, the regular icon's. Centred 13 px dark digits show the percentage without a percent sign. The body turns green while charging and red when low.

The playback, Bluetooth and Wi-Fi glyphs share smooth edges, rounded ends and a consistent visual weight. Four-pixel spacing keeps the enlarged numbers clear of connectivity icons. Wi-Fi's arcs are concentric round its dot and stand the battery's 11 pixels tall; it retains all five signal states.

In Icon and Icon + Percent, charging fills the icon solid green (`BATT_CHARGE_RGB`); low battery always uses
`BATT_LOW_RGB` (`#FF1448`) under every accent. Saved Battery values keep their existing meanings.
Minimal's bar is black, or Light Mode's paper; Modern's black, or `LIGHT_BAR`'s grey; Classic's is its gloss, with a battery of its own ([Theme](#theme)). Every mode fits with ordinary Bluetooth and Wi-Fi, with at least
4 pixels (`CLOCK_GAP`) before the widest clock text (`BATT_ROOM`, 94 pixels from the margin).
Wide codec badges temporarily show the icon alone until they fade. The check (`bar_sync`) takes the percentage at the width its face draws 100% (`pct_width`; `BATT_PCT_W`, 42, until the first paint has measured it, and that paint asks for the bar again): 36 px in the built-in bar's face and 37 in Classic's at the bar's 21 px, so Classic's Percent fits beside Bluetooth, Wi-Fi and the play state (91 px). The room is the built-in bar's and the widest clock's whatever the bar. Under Minimal or Modern with **Status Bar: Classic**, Icon + Percent beside both Bluetooth and Wi-Fi needs 99 px (Classic's 27 px battery), so it is drawn as Percent Inside there, the icon's own width, which keeps the number; with one of the two it fits and stays beside the icon.

The navbar is hidden, as on the local pages, on the settings pages
(`systemset/*`, `playset/*`), `audiosetting_page` and `stream_page`, listed in
`navbar_only` in `ipod.json` with their pinned hashes. Their native
inits destroy the navbar's children and create an unnamed title `hscroll_label`,
back, Home and Now Playing buttons; none has a control the keys lack, except the on/off switches
of the sleep timer, Startup volume (`img_bootswitch`, which `playset_bootvol_page_init` puts in
the navbar; Crossfade reuses the page) and Lock screen (`img_switch`), whose fields hide while off:
those three pages (`TOOLBAR_SWITCHES`) keep it. There the switch takes the toolbar's right-hand
slot and the title runs up to it (225 pixels); the Home and Now Playing buttons are 0 wide, as the
keys do what they did (`immediates` in `ipod.json`). A set with `toolbar` 0 (Classic) hides those toolbars
too, Startup volume's aside: `navigation.c`'s `switch_row` makes the switch a row whose click is
`img_switch`'s, hidden with its toolbar, a loop pass later. The set's `stock_row`, `stock_body`,
`stock_title_y` and `stock_title_h` re-pitch stock's own pages (`stock_rows` in `ipod.json`), which
other sets get back byte for byte: Lock Screen, Update Library and More as settings rows, Queue, Go to and
Special Folders through their row constructors' immediates. More's Speed row keeps 78 pixels: its
four 42 x 61 buttons don't fit (`stock_more_speed`). Lists hold
settings rows (see [Settings](#settings)); other panels move up 50 pixels and keep
their size. The settings inits never move or resize
these widgets. Left out: `wifitransport_page` (its image starts above the
navbar's bottom edge), `fwdownload_page` (no navbar), the PEQ page (it replaces
all children) and every Tidal page, whose navbars hold the search and sort
buttons. A page with a visible navbar keeps its own title. The artist page shows its stock
navbar (multi-select, the Album Artist switch) again at run time when Artist View is Grid, its
tabs 50 pixels lower ([internals.md](internals.md#artist-albums-in-coverflow)).

## Settings

Settings, Playback and Streaming lists use 68-pixel controls
(`SET_ROW`, `SET_ROWS`) under Minimal, starting 8 pixels (`SET_TOP`) below the status bar. The
list stays 272 pixels high, preserving scrolling and full-row tap targets; Classic's rows come
from its set (below). The `navbar_only` assets set their
`list_view`'s `default_item_height` from 78 to 68, which no stock asset uses, and nothing else.
Constants are `SET_*` in `patch/offsets.inc`.
Under a geometry set (Classic's: 32-pixel rows from the top, no icons) the row height, top, row
count and icon size come from the set (`set_row`, `set_top`, `set_rows`, `set_icon`); the payload
reads `set_row` and `set_icon` from the set `theme_boot` wrote, hides the icon at 0 and starts the
text where a row without an icon starts. Streaming's own row height is the set's `stream_row`, matched
by the view's name (`scroll_view_streamsset`) at layout time; where it differs from `set_row` (52 under Classic
Light's Row Art on) its rows keep their `SET_ICON` icons, on the art rows' columns. A payload picker's `select` or `unselect` (Theme, Key Lock)
is not an icon: at 0 it stays, in the column of stock's own marks, and the Theme picker, whose page
is no `CHOICE` context, opens on its mark under a `set_text` theme as a `CHOICE` picker does, and
under such a theme every picker does so when a tap opened it too. Another list whose `default_item_height` equals the set's
`set_row` carries an explicit `item_height`, so only the settings pages are told apart by it.

System Settings puts Wireless and Network first, then Display, Controls and Power, then Date & Time and device
maintenance, with About last; neither list has headings. Play Mode, EQ, Gapless and Crossfade come first; specialist output controls come next, and Library & Folders last, one reverse detent from the top on Playback's ring. Added binary settings
use Shanling's `switch_on` / `switch_off` images with the whole row as the tap target. Settings
that cycle between named choices retain their value labels. Crossfade, Resume Songs, Wheel
Sensitivity and App Store show a chevron, as do the added Library browsing rows.

Every row is built natively, about 60 builders in all, one pattern throughout: a
`list_item_create(view, 0, 0, 0, 0)` (some pass 78 as the height) holding a `button_create(item,
20, 0, 335, 70)`, and in the button an optional 52-pixel icon at x 10, a 24-pixel `hscroll_label`
at x 72 (x 10 without an icon) and a 50-pixel trailing image (chevron, tick or switch) at x 276
or 282. Two-line rows put a 20-pixel label at y 25; USB volume shows a value label at x 176. The
builders never move, resize or scroll their widgets afterwards (the stock equalizer page, which
the PEQ editor replaces, is the only settings code that calls `widget_move`), so one layout hook
owns the geometry:

- iPod points the `list_view` children layouter's vtable layout slot (`0x93a4c4`, vtable
  `0x93a4bc` + 8, stock `0x5fcd44`) at `ipod_list_layout`. Every list lays out through it; only a
  list with no `item_height` and a `default_item_height` of 68 gets settings rows, so Home (an
  `item_height` of 39), the local lists (72) and every stock list are untouched.
- Before the stock layout it sets list items that stock made 78 high to 68: the stock layout keeps
  an item's own height over `default_item_height` (`0x5fd454` onward). The stock layout then
  stacks the rows and sizes the scroll view, so scrolling, the scroll bar and the payload's
  selection all see 68-pixel rows.
- After it, each stock button (x 20, 335 wide) in a visible row spans its row, 375 by 68, so the
  selection bar and the tap target are the whole row. The stock layout skips hidden rows
  (`widget_get_children_for_layout` without `keep_invisible`, `0x5fd074`) and leaves them unsized,
  so a hidden row is mapped once it is shown and laid out. Its icon box shrinks to 40 pixels (`SET_ICON`, drawn
  `scale_down`, value 5 in the stock draw type table at `0x93ae54`, which draws a bitmap that
  already fits 1:1; see [Settings icons](#settings-icons)), centred on the row at x 28
  (`SET_ICON_X`). Text starts 12 pixels (`SET_GAP`) after the icon, at x 80, or at x 20
  (`SET_TEXT_X`) without one.
  Trailing images end 24 pixels (`SET_EDGE`) from the right, at x 351; value labels and the text's
  right edge move with them, never nearer the edge than 20. Full-height children fill the row and
  shorter ones keep their centre. A mapped button no longer matches the stock geometry, so a later
  layout leaves it alone.

With these values the text, icons and trailing images of the first and the last visible row clear
the rounded glass (`test/patch.py` runs the Language, Bluetooth quality, System Settings and
Wi-Fi builders and the Display rows, then checks both positions).

### Settings icons

Scaling the 52-pixel artwork down on the device left jagged edges, so the build pre-sizes it
instead. `settings_icons` in `ipod.json` pins the 39 settings icons by hash: `system_*`,
`playset_*`, `display_*`, `wifiset_*`, `netservice_*`, `usb_chargeswitch` and `bt_adjvol`, 52-pixel
RGBA PNGs that only native settings code names (top level and nested pages such as Display,
Wi-Fi, Bluetooth and Network services); no UI asset or other screen uses them. For each one the
build runs ImageMagick (`magick`, else `convert`) with an alpha-weighted Lanczos resize to
40x40, strips metadata and date chunks so the bytes are reproducible, and replaces the file in
place, keeping its inode metadata. The build fails if an input's hash or format differs, or if an
output is not 40-pixel 8-bit RGBA with the same transparency; the manifest records both hashes
and the ImageMagick version under `changed_assets` and `tools`. `test/build.py` checks the
packaged bytes, the sizes, and that each icon's average colour on black and on the Graphite
selection grey matches the stock icon's, and that Minimal's runtime grey is neutral on black. Other 52-pixel images that land in settings rows, such as
Streaming's Tidal logo (`list_tidal`, which the folder root may also use) and the Spotify row's
build-added icon ([internals.md](internals.md#spotify)), keep their bytes and still scale down. The
Spotify row's Now Playing page uses Now Playing's layout (`NP_*`), rounded art included. The recolouring of accent-red artwork
(`ringnav_image_add`) works on the decoded bitmap, so it applies at either size.

Brightness, Channel Balance, Max Volume and Default Volume use eight-pixel rounded, theme-aware bars
with their original touch areas. Crossfade, Wheel Sensitivity and Resume Songs reuse Default Volume's
page and inherit its styling. All keep their ranges and wheel/touch controls, but lose their endpoint icons (`slider_plain`, tools/ipod.py):
the images stay, named, with no image and no size, since stock looks them up. The quick
settings slider and custom volume overlay retain their layouts.

## Quick settings

The pull-down (`dialog/statusbar_dialog.bin`) covers the whole screen when open. Its eight 60-pixel
controls keep their four columns in two rows from y 20. Every label is the stock 16-pixel white
style (`s_label_white16c`), 80 pixels wide and 40 high (two lines), top-aligned so one- and
two-line labels start on the same line, 6 pixels under its icon and 12 above the next row. The
brightness slider becomes a 6-pixel track in Now Playing's colours (`TRACK_COLOR`, filled with the accent's light tone as the progress bar, `quick_fill`) in a 48-pixel-high slider that
still takes a tap or drag anywhere on it (`slide_with_bar`), from x 68 to 307. The stock dim and
bright suns stay at its ends, in line with the first and last icon columns. Sizes are `QS_*` in
`tools/ipod.py`. `dialog_statusbar_dialog_init` (`0x4a2338`) finds every widget by name and
never moves or resizes one. A finger held on Wi-Fi or Bluetooth opens its settings, and on Playback the EQ page
([internals.md](internals.md#quick-settings-holds)); a tap stays stock's.

Under Classic (`classic_pages()`) the page is flat, in final colours (`quick_page`, `quick_scrub`): its ground is Home's pane in the half (`#63717B` light, `#1C1C1C` dark), with no gradient, gloss or shadow. `quick_open` moves `view_menu` to y 0, puts the icons at x 31, 115, 199, 283 (24 apart) and y 20, 138, and leaves them at opacity 0, where they keep stock's image name, taps and holds and paint nothing. `quick_page` paints each one's 60-pixel tile with corners of 12 (`qs_tile`): a 1-pixel border round its fill. Off is a two-stop vertical fill in light, lighter at the top, silver (`#E9EBEE` to `#D3D6DC`, border `#9BA2AA`), and a flat `#333333` in dark, the dark bar's hairline, with no ring; on, when stock's image is one that is on (`QS_LIT`, from `tools/art.py`), is flat in the accent's red tone, as Minimal's lit discs, with Classic Blue's own `#207CDE` (border `#125CB0` light, none in dark; another accent's light border is its fill a quarter darker) ([internals](internals.md#quick-settings-blue)). Fill and border are a sixth darker while pressed. Over it goes the glyph, `classic_light_<stock image>`, in the tile's ink: white on (`_l` on a tone brighter than `GLYPH_LIGHT_MAX`), `_l` (`#262626`) off in light, `_d` (`#E4E6E9`) off in dark; a disabled one at 45%. USB's three modes keep stock's three marks: a USB stick for storage, off as stock's grey disc, a USB-C port for DAC and the USB trident for audio, both on. Each label is white in the theme's face, its box 18 high, or 32 when its text wraps. Stock's brightness bar goes clear and its suns are hidden, and `quick_scrub` draws the suns, track, fill and knob over the slider, which keeps its rect and touch, the same in both halves.

The controls' stock images are 60-pixel discs: `#444444` with a white glyph when off, stock red
(`#FF1448`) with a white glyph when on (`drop_wifiopen`, `drop_btopen`, `drop_keylockopen`,
`drop_highgain`, `drop_lo`, `drop_usbaudio`, `drop_usbdac`), grey glyphs when disabled. An active
disc takes the accent's red tone, as everything else red does, so it stands clearly apart from the
grey inactive discs. On a tone brighter than `GLYPH_LIGHT_MAX` (perceived brightness 160 of 255),
which is Graphite's silver, the white glyph and its anti-aliased edges turn `CONFIRM_SURFACE`
(`#2B2B2B`) so the glyph stays legible; Crimson, Tidal, Champagne and Cobalt keep the white glyph (3:1 or
more). Only an image that holds the red disc gets the dark glyph, so inactive discs keep their white
glyphs; off and disabled discs contain no red and keep their stock look, and alpha is untouched.
The brightness suns (`drop_lighleft`, `drop_lightright`) sit on black, not a disc, and keep the
accent's red tone (`DROPDOWN_SUN`).

## Cover Flow

The covers are drawn with depth ([internals](internals.md#coverflow-depth)): the
selected cover at its native 160 pixels, its neighbours a side turned edge-on to it and, while they move, the ones coming in at the
frame's edge, drawn whole from their first pixel, and Classic's reflection (46 pixels from 35% of the cover's
brightness), all in the top 220 pixels of the page
(Classic: 242, `coverflow.shelf.view_h`), the reflections' tail under the caption. The album name sits under the covers (`CF_TEXT_Y`) in 24-pixel white,
the artist under that in 20-pixel grey (`#AAAAAA`, stock secondary text), and both, like the
track list's title and rows and the progress and empty messages, keep 36 pixels (`CF_EDGE`) from
each side, where the lowest visible track row's text clears the bottom corners. The covers stand as an iPod's do in every theme, from the Cover Flow Parameter Lab in [#329](https://github.com/DiamondBond/q2-pod/issues/329); Classic's side covers have no darkening. Long names still
scroll within that. A card (an artist's All albums, the rated albums' Sort) uses the album line.
Values are `coverflow.shelf` in Minimal's `theme.xml` and `CF_*` in `patch/offsets.inc`.

In every theme the wheel holds the covers directly ([internals](internals.md#coverflow-thumb)): turned slowly, about 70 degrees moves one album at the default Wheel Sensitivity (50%; half that at 100%) and a cover stays part-way while your thumb does; let go, it settles on the nearest. Turned faster they speed up smoothly to at most 20 albums a second, and from about a turn and a half a second the covers bend into an arch: the ones near the middle drop back level with the side covers and lean in gradually, none swinging out towards you, and as you slow or stop they open out again onto the album the caption names. (With Fast Navigation on there is no arch: the side covers form a narrower row that slides along as the caption goes on ahead.) Turned at about a turn a second (at 50%) for 0.7 s, sorted by album or artist, they go by letters (with Fast Navigation on, at about two turns a second for a quarter of a second), and a thumb re-gripping on the way doesn't start the wait again. Sorted by recently added, most played or at random, and on an artist's or the rated albums, they never do. With Fast Navigation off (Display → Cover Flow; off unless you turn it on) the letters are as they were: the big letter shows and the covers keep flowing through the alphabet, about six letters a second however hard you spin, and a gentler turn steps through them more slowly. Slowing down or turning back carries straight on album by album from where you are. Letting go lands on the letter's first album, and picking the spin back up while the letter still shows goes on by letters. With Fast Navigation on, sorted by year they go by years too, newest or oldest first as Playback → Library & Folders' Year Order has them, each year shown big ("1994") and the albums with no year last, shown as "–", and letters and years go like this. Going in, you overtake the cover facing you: it leaves the screen the way you were turning as the first letter shows big, and the row carries your speed on into that letter's first album. Each further letter is about one tick of turn, at any speed and either way: the big letter changes at once and the row slides to that letter's first album, faster the farther it is, the caption naming each album it passes. Letters last until your thumb lifts: slowing down or resting your thumb keeps them, and turning back steps back a letter. Past the last letter it goes round to the first, and back, as the albums do. Lifting lets the row reach the letter's first album, then the covers fold out onto it in about half a second, none passing through another; turning meanwhile doesn't steer it. Centre, Return, a touch or the screen going dark land on the caption's album at once. To go by letters smoothly Fast Navigation keeps a half-size copy of each cover beside its cache (about 26 MB for 1000 albums, or up to 128 MB on a card formatted with 128 KB clusters), made while it sits idle, about ten a second, which Refresh Cover Flow removes with the rest. The numbers are `coverflow.shelf`'s.

Under Classic (`classic_pages()`), Centre on a cover turns it over: the album's query and its panel come first, so a slow album waits at the cover, and then over 270 ms, eased `.42, 0, .58, 1`, the cover turns 180 degrees about the vertical axis on the centre line, right edge toward the viewer, growing from its 160 × 160 place to the panel at (40, 35), while the side covers and their reflections part 24 pixels each way. The caption goes on the first frame and the turning cover has no reflection. Return runs the same frames backwards, from the panel as it then stands. A key during the turn waits or does nothing: the wheel's ticks land after it, a Centre or Return is dropped. The covers stay insensitive behind the panel, parted.

The panel is 294 × 285 on paper, framed by a flat 1-pixel border just outside it, `#B4B9C0` in light and `#333333` in dark, the same on all four sides. Its 57-row header doesn't scroll: fill rows `#6d8db2` to `#8bacd0` at alpha `0xf0`, darker on top, the album in the theme's 22-pixel face, the artist in stock's regular 16 pixels at alpha `0xc0`, both white in either half, and a soft shadow over rows 55 to 62 falling on the rows. Rows are the theme's 32 pixels and 294 wide in a list from row 57, seven showing and 4 pixels of the eighth, none of its text: the title in the theme's 18-pixel face, to 282. A song's length shows right of its title, stock's `03:45` in the default face at 20 px and `#AAAAAA`, 66 pixels wide (88 from an hour), the title then ending before it; the database keeps none, so a length is one [Long Songs](internals.md#long-songs) or Scan Song Durations measured ([Song durations](internals.md#song-durations)), shown only with Scan Song Durations on, and a song without one keeps its title to 282. The turn reads only that cache, never the card. The bar says Cover Flow; there is no caption row. Its bottom corners sit under the glass by design; no text crosses it. With no depth (no memory for the shelf's frame or the turn's), with no covers under the tracks (Go to Album), or under Minimal, Centre opens the text list as before ([internals](internals.md#coverflow-depth)).

**Playlists as covers.** Display → Cover Flow's **Playlist View: Cover Flow** (`Q2POD PLAYLISTCF`, List by default) opens the Playlists list ([internals.md](internals.md#playlist-art)) as covers, from every entry (Library, Home's list, the shortcut): the page opens veiled, and on the next pass the covers open over it, the playlists in the list's order, stopping at both ends. Return closes both and goes back to where Playlists was opened. With no playlists, or in the Add to Playlist picker, the list shows. A cover is the playlist's art at 160 pixels (a placeholder until it is made), in every theme and whatever Row Art says: the covers are the view; the caption is its name over "N songs" (or "1 song"), in the album's two lines. There are no letters, no Sort cards and no remembered position. Centre opens the playlist. Under Classic the cover turns over to the panel of its songs, audiobooks and podcasts left out as in its list: its header is the name over its song count, and its rows are 45 pixels, five showing, each the title in bold over the artist in `#AAAAAA`, both to 282, the title narrowed for a length as an album's are. Minimal and Modern open the playlist's own list over the covers, as a tap on its row does, and Return comes back to them. A song plays in the list's order; Now Playing's More then has no Delete, which for those songs would take the song out of the library, not the playlist, until another queue plays. Edits made from the covers show as they come back to them: after More…'s Rename or Delete a deleted playlist's cover goes and a renamed one shows its new name, a new playlist gets a cover, and a playlist whose songs changed shows its new count and art. Hold Play/Pause on a cover gives Play Next, Add to Queue, Shuffle and More… as on the list's row, then **New Playlist**, which closes the covers for stock's New over the Playlists page, and, under Classic, **Select Playlists…**, which closes them for its select mode (Minimal and Modern edit from the page's toolbar).

## Rounded corners

The panel's glass rounds its corners and hides what is drawn under them. `CORNER_R` in
`tools/ipod.py` (80 pixels) is the calibration knob: the radius, fitted to V5.4I photos and
to stock's 50-pixel status bar margins. `corner_inset(y)` gives the width hidden at each end of
screen row `y`, and `corner_x` adds `CORNER_SLACK` (4). The status bar groups, the Home labels
and Now Playing's top and bottom rows take their insets from it; settings notes moved under the
hidden navbar end their text clear of the top-right corner. With the defaults: status bar 52
pixels plus `STATUS_PAD` (2), the clock at least 57, Home dot 25 and text 39, "3 of 12" 16, Now Playing icons
ending at 353, the bar 21 and the times 46 pixels from the edges. The runtime layouts' values
(`SET_*`, `CF_*`, `CLOCK_EDGE`, `HOME_FULL_ROW` in `patch/offsets.inc`) are checked against the
same calibration by the tests.

`test/build.py` fails a build when any fixed text or icon in a changed asset reaches
under the glass (screen coordinates: the bar at y 0 to 30, windows at 30 to 320, the quick settings
and confirm pop-ups at 0 to 320): a label's font-high band, an image drawn centred at its size, a
slider's bar, else the widget. Backgrounds, tap targets and list rows, which scroll, are not
checked there; `test/patch.py` checks the settings rows in the first and last visible slots,
Cover Flow's labels and lowest track row, and the status bar clock against every combination of
icons. Raising `CORNER_R` until the clock drops
under `CLOCK_MIN` fails the build.

## Hold Return

The stock long-key function at 0x4f27e4 retains all instructions except the final
Home call at 0x4f29cc (`HOLD_RETURN`). The hold fires after 700 ms, as Play/Pause's does
([internals.md](internals.md#queue-menu), `HOLD_KEYS`); stock waited 1000 ms. Stock power, lock, test and key-lock gates and its release
latch execute first. The payload cancels centre confirmation and spin state, checks
the shared screen/navigation restrictions, and then, on Now Playing, goes back
(`navigator_back`) when the page it opened over (`np_origin`) lies right under it, or
calls the stock Home destination otherwise; anywhere else it remembers the page and calls the stock switch
function with `playing_page` and `{0, 0, 0xff, 2}`. The `0xff` context skips
player_start, so playback is not restarted. The latch stays armed, as on the way
Home: the stock release filter swallows the hold's key-up where it arrives, and
`chord_gate` clears a latch whose release never arrived on Return's next press
([internals.md](internals.md#hold-return)). The hold's release therefore leaves the unit on Now
Playing and the first short Return reaches the stock Back path, with position
memory restoring the browsing page and its selection. No short-Return callback or
other long-key destination changes.

Over Quick Settings (`statusbar_dialog` in front) the hold closes it, as a short Return does
(`dialog_quit`), and, unless the quit was refused, stamps `st.qs_hold`; `quick_poll`, every UI loop pass, runs the hold again once
the dialog has gone and no window moves, so it acts on the page under it, whichever the destination.
The dialog is modal and its own idle closes it a pass or two after the quit, so the hold waits
rather than navigating under its loop; one still up `QS_HOLD_MS` (1 s) later is forgotten. Opened
over the dialog, Now Playing left it open underneath, and stock's pull-down
(`on_wm_tsmove_fun`) starts none while `g_dropdown_menu_state` is not 0, which only the dialog's
close (`0x4a1d30`, on `EVT_WINDOW_CLOSE`) makes it: Now Playing had no Quick Settings until Return
had gone back to the one left open and closed it (#303). Inferred from the handlers; the emulator
mocks the window manager.

Over a prompt (`confirminfo_dialog` in front: Shut Down?, and Q2 Pod's own) the hold cancels it
with `dialog_quit(top, 4)`, the prompt's own Return (`0x499bdc`). `usable` is true there, so the
hold used to open Now Playing under the prompt's modal loop, and the prompt came back over it: a
second Shut Down? (#303). That it was the same prompt back, not a new one, is inferred from a
tester's video and stock's handlers; the emulator mocks the window manager.

**System settings → Controls → Hold Return** (`Q2POD HOLDRETURN`) picks the
destination: Recent (0, the default) is the behaviour above; Home (1) calls the stock Home
destination from any page but Home, Now Playing included, and clears `np_origin`. On Home the hold
still opens Now Playing, as does iPod's short Return on Home's first row (on any other row it first
selects that row). A list open on Home (Library, Streaming, Settings) closes first: at once under a page
going Home, with its slide when held on the list itself. The release latch on the way Home: [internals.md](internals.md#hold-return).

## Transitions

Pages slide with stock's own window animator; the payload adds no animation code. The build sets
`anim_hint` to `SLIDE` (`tools/ipod.py`) on the window of the nine local browsing pages, the
settings and streaming pages and the equalizer page. A hinted page slides in from the right,
pushing the page below it out to the left, and Return reverses that; the status bar is not part
of either page and stays still.

Left immediate: Home (opened once, never closed), Now Playing (its open starts the player, which
can hold the UI thread), Cover Flow (its open loads the albums and covers), the dialogs and quick
settings, the queue, More and jump pages over Now Playing, and the pages that keep their stock
assets: Tidal and the other online pages, Wi-Fi transfer and the firmware download. Moving between
folders reloads the folder page in place, so only opening and leaving that page slides. Stock
drops every key and touch while a page slides; none is queued
([internals.md](internals.md#page-transitions-ipod)).

A theme can set these from its `motion` view, but no builtin theme does: Classic's pages open and
close as Minimal's, with stock's slide or none, as above, and confirms, add to playlist, the hold
menu and every other dialog show at once, with no fade. Classic's own motion is inside a page: a
level's list pushes in from the right over 120 ms, and Return's from the left: a card app's levels
(when their rows arrive), an artist's text albums and their tracks, a Photos album and a folder and
its sub-folders. A list that only refreshes stays still, and keys and the wheel work during the
push. Home's Settings list slides in over 120 ms. The selection jumps between rows with no glide or
end bump, and a list shows its remembered row at once.

## Chevrons

Stock draws `list_into` on Local Music's categories, the `localclass_page` rows (artists, genres,
composers), the album list and folder rows that are not songs, hidden in multi-select. iPod adds
it, aligned with those (see [internals.md](internals.md#drawing)), only on the `DRILL` windows in
`patch/contexts.inc`: Home and the playlist list, whose rows open their tracks. Classic draws
only the highlighted row's, white, on Home and every list, so it moves with the selection band
(`chevron_sel`: stock's own are emptied); on Now Playing's More, only on rows that open a
screen, not A-B Repeat, Speed or Delete, and on a playlist's More, on Rename and Edit Playlist, not
Delete Playlist; Minimal's Home has none. Under Classic, Playlists is a list of two-line rows, each
with its art (the playlist's own picture, up to 12 megapixels, or the first four of its albums with
art in the order its songs were added,
[internals.md](internals.md#playlist-art)) at the row's start under Row Art, as an album's row, and
none with it off, with no toolbar: New Playlist… and Select Playlists… head it, Select Playlists is
stock's select mode with Select All, Import, Export, Delete and Done as rows, and Return leaves that
mode ([internals.md](internals.md#classics-playlists)). Tiles narrower than half the list (Minimal's
playlist Import/Export) get none. Those two tiles, built by stock at run time,
have no icon: the build gives the icon no width and stretches the label across the tile in stock's
centred `s_scrlabel_white24c` (`tools/ipod.py`). The artist page's Albums tab drills but has no
stock chevron or payload row layouter, so it has none.

The payload's own pages (`page_list`) draw the same `list_into` at x 302 on every row
that opens another page or screen: Spotify's menu, its Downloads hub and Storage Management, its
albums, playlists, artists, saved playlists and download jobs, and Download Controls; a card app's
lists, folders and phone-remote rows; Internet Radio's lists, Games, Photos, Ratings, Era's decades and
albums; Books; and the equalizer's Balance, Preamp, Presets, band, Frequency, Gain and Q rows and the
Presets menu, the value moved left of it (`patch/peq_ui.c` `INTO`). A 64 px row with an empty detail
centres its caption beside it. Rows that act in place (play, toggle, cycle a value) have none; on/off
rows (EQ Enabled, a band's Band, Spotify's Shuffle, Streaming Configuration's services) carry stock's
`switch_on`/`switch_off` (`list_toggle`), as Shanling's settings rows do.

## Fast-scroll letter

Spinning quickly through a list of more than 16 rows shows the first character of the selected
row's title in a large white letter, centred over the list on a rounded dark square. It appears
once a step moves more than one row, from 300 ms of continuous spin
([internals.md](internals.md#wheel-movement)), and disappears 400 ms after the last such step, or
at once on a slow tick, a touch or the end of the list. Latin letters show in
capitals; leading spaces are skipped, as is a leading "The", "A" or "An" in library lists
([internals.md](internals.md#sorting)), and any other character shows as it is. Home, Library's categories (on its page or beside Home's pane), settings menus
(`CTX_FIXED` windows, which are not alphabetical) and other short lists never show it, and nor does an album's track list, in Cover Flow or the Library, which is in album order (disc and track).
Nor do these lists, which are in an order of their own, where a row's letter would say nothing of
how far the spin has got: a playlist's songs in the order they were added (Sort: Time added, and
always in Edit Playlist), Recent, Frequent and Recently Added, Songs sorted by Time added, View
Queue while a shuffle is on (Up Next) and Chapters. Values are in `patch/offsets.inc` (`LETTER_*`); see
[internals.md](internals.md#drawing).

## Now Playing

`playing_page.bin` follows Rockbox's iVideo Now Playing in the 375x290 client area, below the
status bar and its clock, with Apple's finish: rounded art, a larger title over grey artist and
album, and a four-pixel progress line:

```
  0 +---------------------------------------------------------+
    | 3 of 12 (16,0 287x40)                          more 303 |  icon 50x40
 40 +---------------------------------------------------------+
    |  .-----------.                                          |  slide_view 0,40 375x186
    |  |    art    |  Title   (194,58 165x28, white 22)       |
    |  |   16,42   |  Artist  (194,92 165x22, grey 18)        |
    |  |  166x166  |  Album   (194,120 165x22, grey 18)       |
    |  '-----------'              mode 247  fav 303 (y 158)   |  icons 50x50
222 |                     . o .   (page dots)                 |
253 |  (=========================------------------------)   |  line 21,253 333x4
265 |      01:23 (46 80x16)           -02:34 (249 80x16)      |  grey 14
290 +---------------------------------------------------------+
```

The art and the metadata keep 16 pixels from the sides (`NP_MARGIN`) and 12 from each other; the
art is 166 pixels, as large as that leaves while the text column keeps its 165 pixels, and
"3 of 12" starts in line with it. Each band has its own space: the top row, then the art 2
pixels below it, so it has 14 pixels to "3 of 12"'s text above and to the page dots below
(`NP_ART_Y`), the bar with the stock A-B markers
(y 250 to 260) and the quiet time labels at y 265. The page dots keep their place while a swipe
moves the pages: they are anchored on their own row, at the slide_view's centre.

The art's corners are rounded at 12 pixels (`NP_ART_RADIUS`), the radius of stock's own
placeholder cover at this size, so real art and the placeholder match; Classic's art is always
an iPod's square instead (`ipod_square_art`: Now Playing, the full-screen art,
Cover Flow and list thumbnails, and the disc placeholders shown there, squared on their own grey as they load,
`square_disc`; Home's art and Home: Carousel's cards keep their corners). The payload paints them
over the scaled image in the page's black, with an anti-aliased
edge pixel, so opposite corners share the same pixel grid. The title is 22 pixels
white (`NP_TITLE_PX`) over the 18-pixel (`NP_META_PX`) artist, `#CCCCCC` (`NP_ARTIST_RGB`), and
album, `#BBBBBB` (`NP_ALBUM_RGB`); "3 of 12" and both times remain `#AAAAAA`. Spotify uses the same hierarchy. Long lines scroll, as stock.

The art follows the player's cover on both Now Playing pages (`np_art_poll`, each lit UI pass).
Stock's 250 ms timer reloads `img_cover` only when it finds one of three one-shot flags set: a
song change (`g_autochange_flag`), a forced refresh (`g_forcerefresh_flag`) or the cover thread's
finish (`g_playcover_finishflag`). Enlarged Art's timer (`0x52dac0`) clears all three while
`bigcover_page` is open, so a change it took left the page on the last track's art. The payload
keys the playing track and the cover it can use, once `g_lastcover_url` is this track's path:
`/tmp/coverpic.jpg` (type 1), `/tmp/externpic.jpg` (2, 4) or stock's placeholder (3, none), with
that file's size and modification time (`now_cover_written`, a `stat` of tmpfs). A card app's URL
queue needs that: its songs have no path and a cover by URL leaves `g_lastcover_url` empty, so the
key was the same for every song and the first one's art stayed. Stock's own request can't tell
them apart: the cover thread clears it before the download, and a cover asked for while the last
one downloads lands after that one, both as type 2. Each write has its own time, as the thread
waits 100 ms between covers. Home's art and Enlarged Art's shape take the file into their keys
too; a paint after a song change, before its cover lands, still shows the previous file. When
that key changes, it reloads the art the way stock does, dropping AWTK's cached bitmap of the name
first, since the player writes every track's cover to the same file. While the cover is still
being parsed, and for Tidal, it leaves stock's image. ponytail: one more decode per track change while the page is up, beside
stock's own reload.

**Theme: Minimal** draws the album in Sudo Music's small capitals (`NP_CAPS_PX`, 15 pixels,
`NP_ALBUM_RGB`, `SUDO_TRACK` apart, `paint_caps`) over its label's transparent ink; Classic and
Modern show the label, and Modern's title is bold. There is no on-screen transport row: the buttons play, pause and skip.

The art, title, artist and album are the slide_view's first page, so a swipe replaces all of them
with the lyrics or info page. The info page keeps its stock 225-pixel column, centred; the lyrics
page spans the screen (**Lyrics** below). The info page's lines read `Label: value`, with spaced units
(`35.92 MB`, `16-bit`, `950 kbps`, `44.1 kHz`), **Genre** and **Year** for stock's Genres and Era,
`Unknown` for a missing value and `Location:` before the folder (`ringnav_np_info`). A Genre line too long for the column scrolls at the album marquee's pace while the page shows (`genre_step`). The play/pause overlay has zero opacity, retaining its original gesture target without
a transport glyph; stock may update its visibility without making it appear. The loading
spinner stays centred on the artwork. The on-screen Return icon moves off-screen, as on the pages whose navbars are
hidden; the hardware Return does the same. More keeps its place at the top row's right. The play
mode (shuffle/order) and Favourite icons sit at the foot of the art page under the text, the
favourite centred directly beneath More at x 303, with the play-mode control 56 pixels to its left, leaving a 6 px gap between touch targets;
the three lines centre in the art's height above them. Both retain their 50 px touch targets and
vertical position. Both keep their stock images and handlers and swipe away with the art.
Internet Radio uses the same heart alignment in every theme.

The bar is a four-pixel plain-colour line inside a 44-pixel seek target: a `#1C1C1C` track
(`TRACK_COLOR`) and an off-white `MINIMAL_FILL` fill, with no thumb. **Theme: Modern** makes it an
8-pixel capsule (`NP_BAR_CLASSIC`; `bar_size` and `round_radius` set on the slider at runtime,
`np_theme`) filled in the accent's light tone (Graphite `#6E6E6E`, 3.3:1; see [Display
settings](#display-settings)), and shows stock's play/pause glyph on the art again. **Theme: Classic** makes it an iPod classic's
20-pixel bar with the times beside it and turns the art in perspective over its reflection ([internals.md](internals.md#slanted-art)). Spotify's bar follows the theme's height up to its 8-pixel view: Minimal's line and Modern's capsule; under Classic Spotify's page is laid out as the local one (see Theme, Streaming's Now Playing).
The asset holds Graphite's; `ringnav_playing` sets the current accent's. The volume shown over the page draws the same bar in the same fill. Tap or drag anywhere in the bar's taller target to seek; the page dots sit just above it so page swipes keep a separate target. The elapsed time
is stock's label; the remaining time replaces stock's total. Sizes are `NP_*` constants in
`tools/ipod.py`; see [internals.md](internals.md#now-playing-ipod).

A tap on the art opens stock's full-screen art (`bigcover_page`; any tap closes it). Stock fits the
cover to the whole screen, 320 pixels high, so the glass hid its corners; iPod centres it on black as
the largest box of the cover's own shape whose corners clear the glass by `GLASS_SLACK` (`bc_fit`; a
square cover is `BC_ART` in `tools/ipod.py`, 292 pixels with the defaults: 41 from the sides, 14 from
the top and bottom; a 16:9 one 360 by 202), so the whole cover shows,
rounded as Now Playing's is. `bc_fit` sizes it as the window paints. It decodes the image for its
shape only when the track, stock's cover or the image changes, past the image manager's cached copy
(the player reuses one file name, so that may be the last track's). A player file counts only once it
is its `g_playcover_type`'s and `g_lastcover_url` is this track's, as Home's art; until then the art
keeps the shape of `g_lastcover_url`'s cover, so a skip doesn't flash the square, or takes the square
once that is another track's (Tidal's URL never is a queue path, so its art stays square). Stock's
`default_bigcover` placeholder is the square. A layout back to the square refits from the kept shape. Its song name and the shade band behind it sit on the art's foot, show for `BC_MS` (3 s), then fade out together over `BC_STEPS` steps
`BC_STEP_MS` apart (`ringnav_bigcover`, after stock's init). They show again for each new song while it is open.

**Scrub.** A double press of the centre button starts scrubbing, as on an iPod classic (with the screen off it only
wakes it, [Wake](internals.md#wake)), and the bar fill turns white while it lasts. Each wheel tick moves 5 seconds, times a ramp of one more step per 100 ms of spin
(up to 40 seconds a tick; the lists' gentler ramp does not apply), within the
track. Both times and the bar follow the target at once;
the track jumps there once, when the scrub ends. A press of the centre button confirms it at once
and leaves the screen on (a double press does too), as do Return, a touch and
3 seconds without a tick, and each gives the wheel back to the volume; Return then stays on the
page. Ending without having moved the target does not seek. Paused, the track stays paused and
plays from the target at Play. Outside a scrub a single press turns
the screen off, as on every other page. With **Single Press Scrub** (below) the two swap: a single
press starts scrubbing, `DOUBLE_CLICK_MS` later as a row opens, and a double press turns the screen
off, as on the lists. The presses work the same while the volume shows over the page; starting a scrub closes it. The jump is stock's key seek, which can pause the player briefly, but only once, when the
scrub ends, instead of after each pause between ticks. Values are `SCRUB_*` in
`patch/offsets.inc`; see [internals.md](internals.md#scrub-ipod).

The top row's text and icons, the bar's ends and the times keep clear of the corners
([Rounded corners](#rounded-corners)).

**Lyrics.** The lyrics page shows the lyrics large, for reading at a glance, as Apple Music's do:
the playing line centred, the one before above it and the next below, its text `LYRIC_X` (16
pixels, as the art) from the left.
Every line is 34 pixels, as large as reads well in the page's width; a long one wraps, at a space
or beside a CJK character, onto as many rows as it takes, up to four, which still fit the page
(the rest cut). Every line is set in the system font, drawn three times a pixel apart for a heavy
weight like Apple Music's. A line's row is its rows
(38 pixels apart) plus 21 pixels between lines: `LYRIC_SLOT` (59 pixels) for one row, so three
such lines fill the 178-pixel page. A line the page's edge cuts fades with the square of the share
of it out of view, so no sliver of a line shows. The playing line lights white (`LYRIC_LIT`); the other lines are darker (`LYRIC_REST`,
`#5F5F5F`). A line with word stamps (A2's enhanced LRC, `<mm:ss.xx>` before a word, in a .lrc or a
LYRICS tag) lights word by word as it is sung, each word from its stamp to the next (the last, with
no stamp after it, at `LYRIC_UNIT_MS`, 90 ms, a letter, done by the next line), its unsung part
grey (`LYRIC_DIM`, `#808080`); a line timed by the line alone, as LRCLIB's are, lights whole as it
starts. Lines that start together, as a line and its translation, play
together. Blank lines in the lyrics (empty lines between verses, a stamp with no words, lines of
spaces) are left out and the spaces at a line's ends trimmed, so the lines keep an even spacing. Before the first line the first waits in the middle, unlit. Lines change at their
hundredths of a second, by a clock of the page's own between the player's whole seconds. On the
lyrics page, while the track has lyrics, the wheel scrolls them smoothly, a pixel at a time as it
turns and as far a turn as a line a tick, instead of changing the volume or scrubbing; a scrub
under way ends first, as any other end does. While the finger stays on the wheel they stay
exactly where it put them; let go, they glide to the nearest whole line. On the page, the follow
to a new line glides too. While they follow, the wheel meets a list end's resistance: travel short of
`LYRIC_PUSH_DETENTS` (2) ticks within `LYRIC_PUSH_MS` of the last only nudges them `BUMP_PX`, held while the
finger stays and springing back when it lets go, still following, so a stray touch keeps the follow.
Lyrics moved by the wheel or a finger stay where they are, while the bar and the
highlight go on, a swipe to another page and back included, until Now Playing closes or another
song starts at the top. A tap shows them full screen, on either layout, and another brings the
page back. See [internals.md](internals.md#scrub-ipod) and [Word-timed lyrics](internals.md#word-timed-lyrics).

### Visualizer

The slide_view gains a fourth page, after the art, lyrics and info pages, and the page dots a fourth
dot. It shows the music as heard, EQ included, in one of four styles on black, tinted with the
accent's light tone; a tap moves to the next, saved as `IPOD/VIS` in `config.ini`, and its name shows
at the bottom for a second and a half:

| Style            | After                     | Drawing                                                                                                                                      |
| ---------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Spectrum**     | Rockbox FFT, log bars     | 32 capsule bars, from a dark shade of the accent to a bright tint with height, a cap on each peak that holds, then falls; a faint reflection |
| **Oscilloscope** | Rockbox Oscilloscope      | The left channel in the accent with a glow, the right a faint accent line; it starts on a rising zero crossing, so the wave stands still     |
| **VU Meters**    | Rockbox VU Meter (analog) | Left and right needles over an arc from -20 to +3 VU, heavier and brighter past 0, with 300 ms ballistics and a peak LED                     |
| **Halo**         | Apple's radial spectrum   | 64 mirrored bars around Now Playing's art in a circle, turning slowly, the bass pulsing the ring                                             |

Everything is anti-aliased vector drawing (AWTK's vgcanvas) within the 375x186 page: 16-pixel side
margins (`NP_MARGIN`), labels in the stock grey `#AAAAAA`. It animates at `VIS_FPS` only while the
page shows with the screen on, and comes to rest when playback pauses; at rest the track's title
and artist show at the bottom, in the style name's place. Tunables are `VIS_*` in
`patch/offsets.inc`; `VIS_LATENCY_MS` sets how far the picture runs behind the decoder, to match
what you hear. See [internals.md](internals.md#visualizer-ipod).

## Pop-ups

The confirm and choice dialogs in `patch/contexts.inc` (flag `BUTTONS`) have no list: their buttons
sit directly in the dialog. For those, the dialog itself is the navigation surface, a kind that
never scrolls, and its clickable descendants are the rows in UI order. The wheel moves between
them with hard ends, Centre clicks the selected one after the usual double-press window, and the
bar is drawn in the dialog's background: the button's own rectangle for a button narrower than half
the dialog (the confirm pair), the full width otherwise. A new dialog starts on its first button,
Cancel on the confirm pair.

The confirm pair (`img_cancel`, `img_enter`) is two full-width 48-pixel text rows under the prompt,
**Cancel** at y 208 and **OK** at y 256 (`tools/ipod.py`, after maks112v/qpod), instead of stock's
80-pixel disc tiles: the wheel's selection is then the lists' own bar or card, with no frame. Stock
still finds both by name and binds its callbacks; the initial Cancel is stock's. The image hook
still gives the discs (`confirm_ok`, `confirm_cancel`) `CONFIRM_SURFACE` (`#2B2B2B`) under every
accent should stock draw them. Tidal's own confirm pop-up (cyan, black glyphs) keeps its look and
its tiles, framed in white when selected (Minimal's card only; Classic's band has its own top line and edge, as an iPod's).

| Dialog                        | Buttons                                         |
| ----------------------------- | ----------------------------------------------- |
| `confirminfo_dialog`          | Cancel, OK (full-width rows)                    |
| `tidal_confirminfo_dialog`    | `img_cancel`, `img_enter` (80x80, side by side) |
| `autoshutdown_dialog`         | `btn_cancel`                                    |
| `playlistsmore_page` (a page) | Rename, Delete Playlist, Edit Playlist          |
| `tidal_quality_select_dialog` | four quality rows, `btn_ok`                     |
| `tidal_sortmode_dialog`       | three sort rows, `btn_cancel`                   |

`sortselect_dialog` and the search result dialogs already navigate their lists. Left out, so the
wheel stays on the volume: the text-entry dialogs (`addplaylist`, `editwifi`, `kbwifiadd`,
`kbwifipass`, `renameplaylist`, `searchbox`, `tidal_searchbox` and Baidu's `edit_dialog`), the Update
Local Music progress (`updatemusic_dialog`, whose only button cancels a scan that can run for
minutes), the pull-down quick settings (`statusbar_dialog`, whose wheel sets the brightness, or the volume after a Play/Pause hold: see
[internals.md](internals.md#setting-editors)), and the dialogs without buttons
(`volume_dialog`, `msginfo_dialog`, `checkfw_dialog`, `showsn_dialog`, `dialog_wifibt_test`).

## Boot

The firmware starts on Home with the remembered queue and track restored paused; In-Vehicle Mode
keeps the stock start on Now Playing. See [internals.md](internals.md#boot-resume-ipod).

## Display settings

`systemset_display_page_init` (`0x4c7168`) destroys the children of `scroll_view_display` and
builds four rows with `0x4c6b94`: a `list_item_create(view, 0, 0, 0, 0)` in `s_listitem_black`
(the list view lays it out 78 pixels high), holding a `button_create(item, 20, 0, 335, 70)` in
`s_btn_listitem` with a click handler, and in it a 52-pixel icon at x 10, a
`s_scrlabel_white24l` `hscroll_label` at (72, 0, 210, 70) and `list_into` at x 282. The rows
show no value; each opens a sub-page (iPod's [settings rows](#settings) then lay them out 68
pixels high). The build drops the fourth, Show Navigation Bar, then adds a "Smart Awake" switch and Night Shift, and, the same way with stock's `list_into` chevron, **Appearance**
(`system_display`) and **Cover Flow** (Play settings' cover mode icon, `playset_covermode`), restacked as Brightness, Screen Off Time, Smart Awake, Night Shift, Lock Screen, Appearance, Cover Flow. System settings' **Controls** row (Play settings' folder jump icon, `playset_folderjump`) is the third sub-page. Each opens stock's
Display page again (`display_open`, `display_sub` naming the sub-page while its init runs), which
drops the stock rows, takes the sub-page's title and builds only its own rows:

- Appearance, under Minimal (Classic's rows: [Theme](#theme)): "Theme: Minimal", "Status Bar: Theme", a "Time in Title" switch ([Status bar and clock](#status-bar-and-clock)) and "Show Wordmark: On" (`IPOD SHOWQ2POD`, the key of its first name, Show Q2 Pod; on unless chosen, kept across themes; Off leaves Home's title empty, `coverflow_home_title`: Classic's bar's, and with Time in Title off Minimal's and Modern's), "Light Mode: Off" ([Light Mode](#light-mode)), "Accent: Graphite"
  and a "Rounded Corners" switch (stock's `switch_on`/`switch_off`, via `list_toggle`) with the System settings Display icon (`system_display`), "Home: Split" and "Shortcut: Rockbox" (`playset_covermode`) and "Battery: Icon" (Icon,
  Percent, Icon + Percent, Percent Inside; see [Status bar and clock](#status-bar-and-clock)) with
  the power manager icon (`system_powermanager`), then a "Centred Now Playing" switch (`IPOD
NPCENTRED`, off; `playset_covermode`): `ringnav_ui_load`, hooked on every window's asset load
  (`ui_loader_load_widget_with_parent`), builds the next Now Playing from stock's own page
  (`tools/ipod.py` `PLAYING_STOCK`, `playing_page_stock.bin`, still named `playing_page`), which
  `ringnav_playing` binds by the same widget names; it has no "3 of 12", album line or remaining time,
  so those stay unbound. Its art starts 8 px under the status bar, the play mode mirrors the heart
  (its box flipped to the right edge, centred), and its progress bar is the iPod page's (`np_slider`:
  plain track, the accent's fill and the Theme's height) where stock's images drew theirs. Its pages are stock's 225 px column: the lyrics wrap at their rows' width and the
  visualizer draws at its slide's (`lyrics.c` `ly.w`, `visualizer.c` `PAGE_W`). A tap on the lyrics, while there are
  lyrics, shows them full screen, as on the iPod page: the column goes black over the rest of the page at the window's width, the lyrics
  down to the page dots, wrapped again at that width (`np_full`, `lyrics_fit`) and back on the playing line; a tap or
  Return brings the column back, as do a swipe, another page and leaving the page; a window over the page leaves it
  full screen
  ([Word-timed lyrics](internals.md#word-timed-lyrics)); last, every theme's "Hide Streaming" and "Hide Folder" switches. Choosing a Theme loads its preset (`theme_preset`), before the restart where there is one: Minimal sets Light Mode Off, Graphite (`MINIMAL_ACCENT` 0), rounded corners, Split and Time in Title on; Classic sets Light Mode On, Classic Blue (`CLASSIC_LIGHT_ACCENT` 9), Split, and Time in Title, Library on Home, Row Preview and Row Art on; Modern sets Light Mode On, Cobalt (`ACCENT` 4), Square, Split and Time in Title off; Light Mode's Auto, Battery, Shortcut and Centred Now Playing are kept, and only changed values are saved.
- Display's main page: a "Smart Awake" switch (`IPOD KEEPLIT`, off;
  `keep_lit_poll`): with Now Playing lit in front on its lyrics, info or visualizer page, or its
  full-screen art (`bigcover_page`) in front, and a song playing, it calls
  `reset_poweroptions_timer(1, 1, 1)` once a second, so the screen stays on there (with Enlarged Album
  Art on, the art page too, once its countdown opens the full-screen art);
  paused, it times out as usual. A card game in Games is held open and lit while it is on, playing
  or not, with no idle quit ([Card games](internals.md#card-apps)). Enlarged Album Art never opens off the art page (#57): while Now Playing's slide view is on
  another page, each `ringnav_sleep` pass holds stock's countdown (`BC_IDLE`) at its full 10 s; under Classic it holds it over the rating picker too, whose Return and taps the art would take.
  Before, the window opened and closed a pass later, flashing over the lyrics every 10 s (#153).
- Cover Flow: "Artist View: Cover Flow" (`Q2POD ARTISTCF`, the default; Grid and List, same icon;
  Cover Flow ends with an All albums card, see
  [internals.md](internals.md#artist-albums-in-coverflow)), "Playlist View: List"
  (`Q2POD PLAYLISTCF`, List (default) or Cover Flow; [Playlists as covers](#cover-flow)), "Sort:
  Album" (the Sort card's words), "Refresh Cover Flow" (`system_update`; it empties `.coverflow/`, the playlists' art
  with the albums', which builds again), a "Fast Navigation" switch (`Q2POD BIGLIBRARY`, off;
  `playset_covermode`): letters held until the lift and years on a fast spin, and the half copies
  they read, a "Scan Song Durations" switch (`Q2POD SCANDURATIONS`, off; `playset_covermode`;
  durations.c), and a "Position Bar" switch (`Q2POD CFPOSITION`, off; `playset_covermode`;
  coverflow_position.c).
- Controls: Wheel Sensitivity, which opens its slider page; "Hold Return: Recent"
  (`Q2POD HOLDRETURN`, [Hold Return](#hold-return), `playset_folderjump`); a "Single Press Scrub" switch (`IPOD SCRUBSINGLE`, off; `np_key`): on Now Playing a single
  centre press starts the scrub and a double press turns the screen off (Scrub, above); "Key Tone"; "Click With Headphones" (`IPOD KEYTONEHP`), shown while Key Tone is on ([Key Tone](internals.md#key-tone)); Buttons Lock, which opens stock's page; "Wake: Single Press"; and "Volume Guard".

All are among the [settings icons](#settings-icons) the build pre-sizes. A value row has its value
in the label (260 pixels wide, to where the chevron ends) and no chevron, since Centre or a tap
changes it in place. Theme opens a picker of the themes instead; choosing Classic, or leaving it,
restarts the Q2 ([Theme](#theme)).
The build also appends **Boot to: Rockbox / Q2 Pod** to Power when Rockbox is present; see [Boot](boot.md#rockbox).
The page is `CTX_FIXED`, so the wheel walks onto them like the stock rows.

A change is saved at once with the stock `write_int_config(value, "IPOD", key)` (`0x4fe67c`):
`sprintf("%d")`, then `toolsWriteConfig("/mnt/data/config.ini", section, key, text)`, which
rewrites the key or appends `[IPOD]` with it (`"[%s]\n%s=%s\n"`). The keys are `MINIMAL_ACCENT` (Minimal's accent), `CLASSIC_LIGHT_ACCENT` (Classic's), `ACCENT` (Modern's), `ROUNDED` (Rounded Corners: 0 off, 1 on), `LIGHT` (Light Mode: 0 Off, 1 On, 2 Auto; missing is Off), `HOME`, `BATTERY`, `THEME`, and Classic's own `HOMELIB`, `ROWPREVIEW` and `ROWART`. The values are read once,
on the payload's first use (after stock `config_init`: `application_init` runs `platform_init`, which
calls it, before it opens any window), with `toolsReadConfig` (`0x5cfff4`), in the order stock `config_init`
calls it: `(path, section, key, out, default)`. It reads the file line by line
(`strcasecmp` on the section and the key), copies the trimmed value to `out` and returns 1; a
missing key copies the default and returns -1. The default must not be null (stock reads its
first byte). The payload passes `"0"`, so a missing or unreadable entry, or any value that is not
one valid digit, is Split, Icon and Minimal (a stored `THEME=3` or past reads as Minimal); a missing `MINIMAL_ACCENT` is Ivory (7), a missing `CLASSIC_LIGHT_ACCENT` Classic Blue (9), a missing `ACCENT` Graphite (0), and a missing `ROUNDED` Square. A
card from 1.0.1 or earlier, which has no `THEME`, keeps 1.0.1's look, and a Minimal card from before
1.4.6 keeps its off-white look whatever `ACCENT` holds.

| Accent           | Progress / active-control light tone | Red text / marks |
| ---------------- | ------------------------------------ | ---------------- |
| Graphite (0)     | `#6E6E6E`                            | `#D8D8D8`        |
| Crimson (1)      | `#EB2F56`                            | stock `#FF1448`  |
| Tidal (2)        | `#30929B`                            | `#30929B`        |
| Champagne (3)    | `#9A8446`                            | `#9A8446`        |
| Cobalt (4)       | `#447CDD`                            | `#447CDD`        |
| Tangerine (5)    | `#CB681E`                            | `#CB681E`        |
| Plum (6)         | `#9D4F7A`                            | `#9D4F7A`        |
| Ivory (7)        | `#EEEEEC` (Graphite's elsewhere)     | `#D8D8D8`        |
| Green (8)        | `#778E4C`                            | `#778E4C`        |
| Classic Blue (9) | `#447CDD` (Cobalt's)                 | `#447CDD`        |

The selection colours (Minimal's card under that accent, and the tone Classic's gloss band is
turned to) are Graphite `#424242`, Crimson `#E8123F`, Tidal `#13838D`, Champagne `#8C732C`, Cobalt
`#2A6AD8`, Tangerine `#C45400` (the orange Q2), Plum `#903868` (the purple Q2) and Green `#647F34`
(the green Q2). Tangerine, Plum and Green are as close to the shells as white text at 4.5:1 allows,
and no shade of theirs reads as stock red to the mapper, so nothing is mapped twice. Ivory (7) is
Minimal's only and its default: Classic's cycle goes from Plum to Green, and a stored `CLASSIC_LIGHT_ACCENT` of 7
reads as Classic Blue. Green is row 8, after Ivory, so cards that saved 7 keep Ivory. Classic Blue
(9) is Classic's own and its default: Cobalt's tones under its own gloss band.

Existing accent preference values and the red-tone mapping stay compatible; greys and artwork
remain unchanged by the live accent mapper. See [internals.md](internals.md#accent) for the mapping.

Now Playing uses 24 px monochrome control glyphs on 28 px canvases inside the existing touch targets. Favourite keeps its outline/filled states; inactive dots are muted. Artwork, scrolling metadata and playback controls retain their existing behavior.

### Theme

The Theme list shows Minimal, Modern, Classic (`theme_rows`), whatever their stored numbers.
**Theme: Minimal** (`THEME=0`, the default) is the look described above. **Theme: Classic**
(`1`) is a light iPod look with a dark half, on its own layout (below). **Theme: Modern** (`2`)
is 1.6.5's Classic on Minimal's layout (below). A stored `THEME=3` or past reads as Minimal.
Choosing Classic, or leaving it, asks "Restart to apply?", because Classic lays its rows out
from its own geometry sets ([internals.md](internals.md#themes)): Cancel changes nothing; OK
saves the theme and its preset ([Display settings](#display-settings)) and restarts the Q2.

Corners is Minimal's and Modern's setting, Square until chosen; Classic's covers are always square. Every theme insets an album grid's cover inside the tile (`album_children`), so the selection card frames it; Classic's and Modern's card is in the accent's colour, Classic's square.

Each theme keeps its own accent, and the Accent row follows Theme in each. Minimal's
(`MINIMAL_ACCENT`) cycles Ivory, Graphite, Crimson, Tidal, Champagne, Cobalt, Tangerine, Plum, Green;
its default is Graphite, as its preset, and Rounded Corners is on until chosen off; Ivory draws Graphite's greys with its own fills (the progress bars, Spotify's, the
PEQ curve and the visualizer) in `MINIMAL_FILL`, white on the dark track; a scrub's fill is pure
white. Another accent colours the selection card, those fills and every mapped red, as in Classic,
and the card keeps the rows' own ink and stock's `#AAAAAA` metadata. Minimal's grey icons and
codec badges stay grey, but `list_into`, the Bluetooth marks and `list_delete` stay white, as
their grey copies were only for the off-white card; the edit pages' OK stays off-white in every
theme. Classic's (`CLASSIC_LIGHT_ACCENT`) is Classic Blue, its own scheme and the default, then
Graphite, Crimson, Tidal, Champagne, Cobalt, Tangerine, Plum and Green, each a gloss band turned to
the accent's colour; Ivory is Minimal's only. Modern's (`ACCENT`, 1.6.5's Classic's key) skips
Ivory too, and choosing Modern starts it on Cobalt. Now Playing's bar has no thumb:
stock draws its dragger (a box and knob, the stock theme's blue) unless `border_color` is clear
and the style has no `icon`, so `playing_page` clears the first in every state and `slider_knobless`
the second, which stock's default slider style would otherwise supply. 1.3.9 first painted it white, which drew the box at rest.

#### Modern

**Theme: Modern** is the iPod look 1.0.1 to 1.6.5 called Classic, drawn at runtime over Minimal's
layout and assets: the accent's full-width selection bar under white text (an inset pill under
Rounded Corners), with Home's chevron on its bar, coloured settings icons and the stock
`list_into`, Home's menu in the stock font, a bold Now Playing title, and Now Playing's 8-pixel capsule and play/pause glyph.
Its battery is Minimal's, and so is its status bar, but in Light Mode the bar is an iPod's flat grey (`LIGHT_BAR`).

Minimal and Modern share a geometry set, so a change between them applies at once, as Accent
does: the image cache is dropped and the screen repaints (icons and chevrons reload in the new
theme's colours), Home re-lays its menus and art, and all open settings lists (including cached
pages behind Display) take the new top inset. The Accent row shows the new theme's accent, the
selection staying on Theme.

#### Classic

**Theme: Classic** takes its rows from its own geometry sets, so its lists, settings, Home and
status bar have their own layout, and it has its own Now Playing, Cover Flow shelf, Quick Settings and
dialogs. A screen it does not theme yet (Tidal's pages, which keep their navbars, its dialogs, and the games and
photo pages' own drawing) keeps Minimal's layout under this theme's colours and font.

- **The bar.** A gloss bar (the dark half's flat `#1c1c1c` over a hairline) with the page's title on the left, the clock centred, and Bluetooth, Wi-Fi, the play state and the battery on the right (no EQ mark), on every page, Home's own list included. The title and clock are 21 px, three quarters of a row's caps. Under this bar **Time in Title** decides the clock alone (on unless chosen, and choosing Classic turns it on): off, the clock makes way while the page has a title, so the bar names the page once, and the title takes the clock's room; on Home it makes way with Show Wordmark off too, though the title is then empty, and a pop-up over Home keeps that. The play state is a blue gloss triangle or pair of bars with a dark edge and no halo, as an iPod's. The battery has five states: low (a red sliver), part full, full, charging (a bolt) and full on power (a plug). **Battery** is Icon, Percent or Percent Inside (Icon by default); a stored Icon + Percent reads as Percent Inside.
- **Lists.** 32 px rows, eight to a screen, the values on the right, the white chevron on the selected row only, no settings or Library icons; other icons keep their colours. The selection is the accent's gloss band, square and full width, over the whole row on a taller one; its light 1 px top line is the light half's, and on black the band starts on its first stop. Stock's grey placeholder tiles, Folder view's folders and songs (`list_folder`, `list_song`; Album info's rows use `list_song` too, so theirs is 38 px as well), are drawn 38 px in the middle of their 52 px row (`list.art.placeholder`, by the image hook), so they have room above and below; a real cover keeps the row's size. A long title on the selected row scrolls, as in Minimal. Songs are two lines at 52 px, title and artist; Play All and Sort are a plain row at the top of Songs, reached by touch, not the wheel.
- **Row Art.** It decides album covers only, in 52 px rows, and they are square: the Library is always 32 px text. A change restarts the Q2.
- **Now Playing.** An iPod classic's: the art, 178 px, up beside More and turned in perspective over its reflection, with no play/pause glyph on it; "3 of 12" under the album, 2 px in from the scrolling labels' boxes as their own text is; the title in Classic's face and the album line as a label; and a 20-pixel bar, the accent's gloss over a near-white track with a faint reflection, the elapsed and remaining times beside it in Classic's face at 21 px, in boxes sized to the track's length, so the bar is as wide as the times allow, opening at its widest and narrowing only for a long track: 35 px boxes under ten minutes (the bar 66 to 309, 243 px), 43 px under an hour (74 to 301, 227 px) and 56 px from an hour, which hold "-1:02:03" (87 to 288, 201 px), the outer edges and the 8 px gap the same in each; Internet Radio's running time takes the under-an-hour boxes until it has played an hour; while you scrub, a diamond in the ink marks the point on the bar ([Now Playing](#now-playing), [internals.md](internals.md#slanted-art)). Turning the volume shows it on that bar, a speaker in each time's place and no number ([internals.md](internals.md#now-playing-ipod), Volume).
- **Appearance's rows.** Theme, Status Bar, Time in Title, Show Wordmark, Light Mode, Accent, Home, Shortcut, Battery, Library on Home, Row Preview and Row Art, then every theme's Hide Streaming and Hide Folder. Rounded Corners and Centred Now Playing are Minimal's and Modern's only, and their stored values stay for them. Light Mode Off is Classic's dark half; choosing Classic turns Light Mode on unless it is on Auto.
- **Home.** 32 px rows in Classic's face, the gloss band and the white chevron on the selected row. In Split, with **Library on Home** on, Library is a list on Home's left with the pane beside it (off, Library opens its page). With **Row Preview** on, the pane shows the highlighted row (off, it shows Now Playing's art on every row, and Home reads no counts). Both are Classic's own switches (`IPOD HOMELIB`, `IPOD ROWPREVIEW`, on unless chosen), saved at once with no restart, and choosing Classic turns them on. With both on: Now Playing and the Library's rows the current cover, Shuffle the note and the song count, Cover Flow the album count, Folder its icon, Games the game count, Streaming the services, Settings the card's free space.
- **Library's Search row.** The Library, its page and Home's list, leads with **Search** (`library_search`, a set without Library icons), the pull-down's stock search: Home's list hides the pull. Its pane is the Library's, and Shuffle's note moves to the row after it.
- **Update Library and USB mode.** A round icon on the page, not stock's screens. While the library updates the line reads "312 of 704 songs" over a progress bar, against the songs it had before, with Cancel by touch; a first scan or a folder's shows stock's text and no bar. Connected says to eject before disconnecting under USB Storage; USB Audio and DAC have no line. Once the computer ejects the card, USB Storage's page says OK to Disconnect, in every theme. See [internals.md](internals.md#themes).
- **Streaming's Now Playing.** Spotify and Internet Radio take local Now Playing's places: the 178 px art at x 16, y 22, turned over its reflection; the title, artist and album at x 206 from y 38 (34 and 62 under it); the 20-pixel bar from x 66 to 309 at y 245 with the 21 px times beside it, narrowed as local Now Playing's for a long track. Spotify's bar shows its position and the scrub's diamond; the page's name and play state are in the title bar. Internet Radio plays as an album: a generated station cover, the station, artist and song, the stream where "3 of 12" sits, the heart at x 300, y 165, and a bar that is always full with the running time on its left; the wheel stays the volume.
- **Info.** Now Playing's third page is a list of nine rows in Classic's face, each name on the left and its value on the right in grey, a long value ending in an ellipsis: 20 px rows from under the top row to the page dots, names 100 px wide from x 20, values to x 355.
- **Its font.** The face is a subset of TeX Gyre Heros Bold (`assets/fonts`, GUST Font License), a play mark added at U+25B6 (View Queue's, [Queue menu](internals.md#queue-menu)), renamed and shipped as `fonts/classic-light.ttf`; `assets/fonts/README-classic-light.txt` says what changed and where the original is. Where it lacks a character, `fonts/bold.ttf` stands in ([Home](#home)).

### Light Mode

**Light Mode: Auto** (`LIGHT=2`) is light from 07:00 to 18:59 by the device clock and dark the rest of the day. The row cycles Off, On, Auto on each click. It switches at 07:00 and 19:00 while the device is on, and the cached images are dropped and an open screen repaints then (`clock_show`, at each minute with the status bar's clock). The boot logo picks its mode by the same hours, in the saved time zone ([boot](boot.md#light-mode)).

**Light Mode: On** (`LIGHT=1`) turns every theme light at runtime, over the same assets. Every
neutral colour (channels within `LIGHT_SPREAD`) flips per channel, black to `LIGHT_PAPER` (`#F4F4F4`)
and white to `LIGHT_INK` (`#161616`), or Classic's white paper and black ink, alpha kept; other hues are left as they are. Stock's colours
flip in the style hooks (`accent_map`, after the accent), the payload's own in `ui_ink`, which
`patch/peq.h` puts in front of every canvas and vgcanvas colour setter. A colour read back from the
LCD to restore it goes through `stock_canvas_set_*`, since it has flipped already. Only the outermost
style hook call maps: a mutable style (inline `style:*`) answers from its default style through
`style_get_color` again, and the flip is not idempotent. A clear colour (`#00000000`) stays clear,
because stock tests for it (Now Playing's dragger).

Coloured accents keep their tones. A neutral one (Graphite, Ivory) takes its silver red tone for
fills as well, so it flips dark on paper; Minimal's own card (`MINIMAL_FILL`) flips to a dark card
under light ink. On a coloured card, or Classic's band under any accent, the rows' ink stays white,
as on an iPod's blue bar (`sel_accent`), and so does the `>`: Minimal's drill rows' from
`LIST_INTO_WHITE`, stock's own `list_into` drawn again over the card (`paint_into`), and Classic's
own `classic_light_chevron`. Classic's band and gloss bar are drawn as stated in both halves, never
flipped. A red tint over black (stock's pressed tints)
becomes the same tint of the tone over paper.

A theme image without colour (no pixel more than `LIGHT_CHROMA` saturated) flips too when it is
mostly glyph: under 1/`LIGHT_SURFACE` of its area opaque and darker than `LIGHT_GLYPH`. White marks,
chevrons, spinners and status icons turn dark, while a grey or dark disc keeps its white glyph.
Quick Settings' inactive discs (`drop_*`) flip whole, to light discs under dark glyphs, and the
active ones keep the accent. An image the accent mapped is final, and `switch_off` keeps its white
knob, as `switch_on` does. Cover Flow's frame composites over paper instead of black, its side covers and reflections fading to paper. Solitaire's face-up cards stay light with black suits (`unflipped`). The status bar's stock
black border is cleared so it does not flip to a paper line; Minimal's bar is paper, Classic's its gloss's light half. Album art,
photos, wallpapers and videos are never changed.

ponytail: a non-square or transparent cover in Cover Flow keeps black bars and a black background
inside its square (`cf_decode`).

## Games

**Library → Games** opens Sudoku, Vortex, Solitaire and Snake ([internals](internals.md#games)). A game
takes every key but Power and a held Return, so the music keeps playing untouched.

| Game      | Wheel                                    | Centre                                          | Play/Pause            | Previous/Next                                                           | Return                              |
| --------- | ---------------------------------------- | ----------------------------------------------- | --------------------- | ----------------------------------------------------------------------- | ----------------------------------- |
| Sudoku    | Moves through the cells; picks the digit | Opens the digit, then writes it                 | New game (asks first) | Up or down a row                                                        | Cancels the digit; back to Games    |
| Vortex    | Turns the paddle                         | Launches; plays again after a game              | Pauses and resumes    |                                                                         | Pause menu: Continue or Quit        |
| Solitaire | Moves between the piles                  | Draws; picks up, drops; twice sends a card home | New game (asks first) | Up or down a row                                                        | Drops the held cards; back to Games |
| Snake     | Disabled                                 | Plays again after a crash                       | Pauses and resumes    | Turn left/right; wheel or Previous/Next picks Continue/Quit in the menu | Pause menu: Continue or Quit        |

## Review sheets

Review sheets are native-size **composed previews**, not firmware captures.
Host and MIPS checks cover geometry, clipping, selection contrast, recycled rows,
preferences, input and packaging.
