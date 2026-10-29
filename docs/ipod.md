# iPod UI

The Legacy and iPod builds share one navigation payload. `--ipod` enables the compact layout
payload helpers and build-time edits in `tools/ipod.py`; Legacy receives no
compact executable sites or UI assets. `patch/ipod.json` records the original
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

Native local row-pool constructors are at 0x523038 (folder), 0x4aa2cc (songs),
0x4b0efc (local categories) and 0x4a4ae8 (album list/grid). The album grid branch
is unchanged. Album detail, artist track and playlist constructors have their own
explicit sites in the audit. Folder reset at 0x5217d4, return offset division at
0x521a84 and scrolling cover division at 0x5228f8 all use the same compact pitch.
The category cover callback originally divides by 120 despite using 78-pixel
rows; iPod corrects its audited divisor at 0x4b0608 to 72. Rebinding and delayed
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
every row's scrolling once as the page opens.

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
unchanged; Classic keeps its existing colour handling.
The pinned style edits in `patch/ipod.json` remove list fills and corners and make pressed
feedback neutral grey. Playing titles keep the separate playing glyph.

In Minimal, wheel selection under its own accent (**Accent: Ivory**, the default) is off-white (`#EEEEEC`): a card, under **Rounded Corners** on inset
`SEL_INSET` (6) pixels from each side of the row, a pill (corners half its height, less on a row whose cover the round end would cut, such as Search's results: `pill_radius` shrinks them until the cover's corners are inside, checking only the row's own left-hand, non-centred images; grid tiles `SEL_RADIUS`, 6), and under **Rounded Corners** off (the default) a full-width square bar with square tiles,
drawn with the anti-aliased rounded fill (square without a vgcanvas). Any other accent fills the
card with its colour and leaves the rows' own (white) ink, as Classic's bar does. Minimal's unselected
secondary text, stock's `#AAAAAA`, is the quieter `SUDO_MUTED` (`#8C8C8C`) everywhere. **Theme:
Classic** draws the accent's gradient bar instead, full width, or under **Rounded Corners** on the same inset pill with anti-aliased ends, and leaves the rows' own (white) ink. The background
hook resolves the selected row; the color hook maps its title to `#171717` and metadata to
`#484848`, preserving alpha. Paint ancestry determines the colors, so no style overrides can
remain on recycled rows or stale scrolling labels. Selected backgrounds stay transparent over
the selection. Touch hides wheel selection until wheel/centre input; the native pressed state
remains visible. Under **Rounded Corners** on a pressed list row (`s_btn_listitem`) is that grey as the
selection's inset pill instead (`pressed_pill`); other pressed widgets keep stock's box. The same hooks cover settings, streaming, dialogs, PEQ rows and media browser
chrome. Photos, video, book content, EQ plots and visualizers keep their own drawing.

## Home

Home splits the screen: the menu on the left, the current local track's art in the right panel.
Selection is a small white dot and bright text; other destinations use `#AAAAAA`. There is no
selection rectangle or chevron. **Theme: Classic** styles the menu as before 1.0.1 instead
(below).

The seven destinations remain Now Playing, Library, Coverflow, Folder, Rockbox, Streaming and
Settings. Rockbox is visible only when its binary is on the card, unless Display → Controls' **Shortcut**
(`IPOD/SHORTCUT` 0-6) turns that row into Artists, Albums, Favorites, Rated Albums, Shuffle or Games (`home_shortcut_open`; Rated Albums is Ratings → Albums, `rated_albums`; Shuffle is the Library's Shuffle page, `shuffle_menu`); with no key it is Rockbox when the card has it, else Favorites. Centre cycles Rockbox, Favorites, Shuffle, Rated Albums, Artists, Albums, Games. Settings opens Playback and
System; Return restores the menu's previous selection, and a short Return on the main menu selects
Now Playing; a short Return with Now Playing selected opens it without changing playback. Return there goes back to Home. The wheel bumps at either end of both lists and wraps after a pause, as ring lists do ([internals.md](internals.md#wheel-movement)).
Folder opens directly at the SD root when a card is in and no OTG drive is connected; Back at that root returns
to Home. With OTG connected or no card in on entry, Folder keeps the storage selector. Nested folders and
Library’s Podcasts/Audiobooks shortcuts keep their usual Back navigation.
The native stock handlers, translation keys, full-row tap targets and submenu animation remain intact.

`HOME_ROW=39` stays the same in both themes. Minimal retains `HOME_TOP=8`; Classic starts
both Home menus directly below the status bar, matching Library. The 6-pixel dot sits at x 25, clear of the
bottom row's corner, and labels start 8 pixels after it (x 39) and end 33 pixels before the row's
edge. Both menus are 375 pixels wide in the asset; the payload lays them out (below). Long
translated labels ellipsize and scroll using the stock font.
The background view paints before the transparent menus, and both menus are clipped to the
window while sliding.

Display settings offers **Home: Split / Full**, stored as `IPOD/HOME` 0 or 1; invalid or absent
values mean Split. `coverflow_home_layout` moves the asset's widgets at runtime, in both themes:
Split narrows both menus to `HOME_SPLIT_W` (187, half the screen) and shows the cover itself in the 290-pixel-high panel right of them
(`img_homeart` is an `image` for this, fitted to the cover's proportions and clipped to the panel,
the menus clipped left of it; `default_album_home` without a cover); Full hides the art and
widens the menus to the screen, their rows to `HOME_FULL_ROW` (375), or `HOME_CLASSIC_ROW` (369)
in Classic. Minimal's labels keep x 39 and end 33 pixels from the screen edge in Full, `HOME_SPLIT_LABEL_END` (8) before the art in Split, where no corner needs clearing (140 pixels wide). Classic's
start at `HOME_CLASSIC_TEXT_X` (33) with their existing widths. The chevron moves 15 pixels
right in Split and 10 in Full, staying clear of the rounded glass, and rides the accent's selection bar alone.

The art is the player's cover once `g_lastcover_url` matches the current queue path, else the
current album's Coverflow thumbnail, else the placeholder; the previous track's player file is
never used as a fallback. It reloads only when its key (the queue path, usable player-cover type
and parsed album tags) changes, and not at all in Full. The reload runs inside a paint, and AWTK
drops what a paint invalidates when the frame ends, so a 0 ms timer then repaints the whole screen;
otherwise the new art and its fit would reach only the regions repainted later. `g_playcover_finishflag` remains
stock-owned. Now Playing and Spotify's page keep their black background behind the art.

## Status bar and clock

`systembar_showface` (`0x52f610`, run by `system_bar_init` and then a 1 s widget
timer) finds each status bar widget with a recursive `widget_lookup` from the bar,
so parents and order are free to change. Every tick it re-shows the volume,
EQ, Bluetooth, SyncLink and Wi-Fi widgets and sets their images and text, but
never their geometry. `system_bar.bin` (iPod) therefore keeps the play state
and EQ in `view_left`, as stock does, and puts Bluetooth/codec, Wi-Fi and the
battery in `view_right`. EQ uses an explicit 16 px font and a 28 px cell: its text is 24 px wide in the shipped font, so stock's 20 px cell clips it. A 16 px `Q2` label at the left inset appears only when both playback state and EQ are hidden. Stock's tick also hides `img_state` whenever its own player is stopped, as it is under Spotify, radio or a video; `ringnav_showface` (iPod only) runs `bar_state_sync`, the play state and the `Q2` label alone, straight after the tick, so the payload's play state is back before any paint, dark or not (no file reads; a relayout waits for the paint); the codec badge, accent, Rockbox check and battery wait for paint (`bar_sync`). Correcting it at paint time instead relaid the row twice a second, and EQ was drawn for a frame in the play icon's place. When `bar_sync` changes `img_state` while another window paints (Now Playing paints every 250 ms, so it usually sees a Play/Pause first), it arms a 0 ms timer that runs `widget_invalidate_force` on the bar after the frame: an invalidate made mid-paint is dropped with that frame's dirty rects, and `img_state` keeps its dirty flag (`+0x24` bit `0x200`), which makes stock `widget_invalidate` return early, so the 1 s tick's own invalidates skipped it until something else repainted the bar (#116). `bar_sync` updates its visibility without moving the centred clock. The volume icon and number and SyncLink move to `x = -200`, where they
draw off-screen. Both groups sit 54 pixels
from the edges (`STATUS_MARGIN`): the 50 where the 16-pixel icons clear the top corners
([Rounded corners](#rounded-corners)) plus 2 (`STATUS_PAD`) so they don't crowd the glass. A new
`label_clock` (`s_scrlabel_white20c`, overridden to 16 px) is centred on the screen. It has the width left in the
narrowest case, 109 pixels at x 133, clear of either group with every icon shown (left 94
pixels, right 133); the build fails if that would be under `CLOCK_MIN` (105). The payload paints
the bar and writes the time into `label_clock` (see
[internals.md](internals.md#status-bar-ipod)); the widest text, `12:59 PM`, is 69 pixels in the
stock font at 16 pixels.

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
The bar is black in Minimal and charcoal in Classic. Every mode fits with ordinary Bluetooth and Wi-Fi, with at least
4 pixels (`CLOCK_GAP`) before the widest clock text (`BATT_ROOM`, 94 pixels from the margin).
Wide codec badges temporarily show the icon alone until they fade.

The navbar is hidden, as on the local pages, on the settings pages
(`systemset/*`, `playset/*`), `audiosetting_page` and `stream_page`, listed in
`navbar_only` in `ipod.json` with their pinned hashes. Their native
inits destroy the navbar's children and create an unnamed title `hscroll_label`,
back, Home and Now Playing buttons; none has a control the keys lack, except the on/off switches
of the sleep timer, Startup volume (`img_bootswitch`, which `playset_bootvol_page_init` puts in
the navbar; Crossfade reuses the page) and Lock screen (`img_switch`), whose fields hide while off:
those three pages (`TOOLBAR_SWITCHES`) keep it. There the switch takes the toolbar's right-hand
slot and the title runs up to it (225 pixels); the Home and Now Playing buttons are 0 wide, as the
keys do what they did (`immediates` in `ipod.json`). Lists hold
settings rows (see [Settings](#settings)); other panels move up 50 pixels and keep
their size. The settings inits never move or resize
these widgets. Left out: `wifitransport_page` (its image starts above the
navbar's bottom edge), `fwdownload_page` (no navbar), the PEQ page (it replaces
all children) and every Tidal page, whose navbars hold the search and sort
buttons. A page with a visible navbar keeps its own title. The artist page shows its stock
navbar (multi-select, the Album Artist switch) again at run time when Artist Albums is Grid, its
tabs 50 pixels lower ([internals.md](internals.md#artist-albums-in-coverflow)).

## Settings

Settings, Playback, Audio settings and Streaming lists use 68-pixel controls
(`SET_ROW`, `SET_ROWS`). Minimal starts 8 pixels (`SET_TOP`) below the status bar; Classic
starts directly below it. The list stays 272 pixels high in both themes, preserving scrolling
and full-row tap targets. The `navbar_only` assets set their
`list_view`'s `default_item_height` from 78 to 68, which no stock asset uses, and nothing else.
Constants are `SET_*` in `patch/offsets.inc`.

System Settings puts Wireless and Network first, then personal and daily controls, then device
maintenance, with About last; neither list has headings. Play Mode, EQ, Gapless and Crossfade come first; specialist output controls come last. Added binary settings
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

- iPod points the `list_view` children layouter's vtable layout slot (`0x926930`, vtable
  `0x926928` + 8, stock `0x5e9eb4`) at `ipod_list_layout`. Every list lays out through it; only a
  list with no `item_height` and a `default_item_height` of 68 gets settings rows, so Home (an
  `item_height` of 39), the local lists (72) and every stock list are untouched.
- Before the stock layout it sets list items that stock made 78 high to 68: the stock layout keeps
  an item's own height over `default_item_height` (`0x5ea5c4` onward). The stock layout then
  stacks the rows and sizes the scroll view, so scrolling, the scroll bar and the payload's
  selection all see 68-pixel rows.
- After it, each stock button (x 20, 335 wide) in a visible row spans its row, 375 by 68, so the
  selection bar and the tap target are the whole row. The stock layout skips hidden rows
  (`widget_get_children_for_layout` without `keep_invisible`, `0x5ea1e4`) and leaves them unsized,
  so a hidden row is mapped once it is shown and laid out. Its icon box shrinks to 40 pixels (`SET_ICON`, drawn
  `scale_down`, value 5 in the stock draw type table at `0x9272c0`, which draws a bitmap that
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
iPod build runs ImageMagick (`magick`, else `convert`) with an alpha-weighted Lanczos resize to
40x40, strips metadata and date chunks so the bytes are reproducible, and replaces the file in
place, keeping its inode metadata. The build fails if an input's hash or format differs, or if an
output is not 40-pixel 8-bit RGBA with the same transparency; the manifest records both hashes
and the ImageMagick version under `changed_assets` and `tools`. `test/build.py` checks the
packaged bytes, the sizes, and that each icon's average colour on black and on the Graphite
selection grey matches the stock icon's, and that Minimal's runtime grey is neutral on black. Other 52-pixel images that land in settings rows, such as
Streaming's Tidal logo (`list_tidal`, which the folder root may also use) and the Spotify row's
build-added icon ([internals.md](internals.md#spotify)), keep their bytes and still scale down. The
Spotify row's Now Playing page uses Now Playing's layout (`NP_*`), rounded art included. The Legacy build keeps every icon stock. The recolouring of accent-red artwork
(`ringnav_image_add`) works on the decoded bitmap, so it applies at either size.

Brightness, Balance, Maximum Volume and Default Volume use eight-pixel rounded, theme-aware bars
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
`tools/ipod.py`. `dialog_statusbar_dialog_init` (`0x4a0d88`) finds every widget by name and
never moves or resizes one.

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

## Coverflow

The covers are drawn with depth in both builds ([internals](internals.md#coverflow-depth)): the
selected cover at its native 160 pixels, two angled neighbours a side and a faint reflection, all
in the top 210 pixels of the page. The album name sits under them (`CF_TEXT_Y`) in 24-pixel white,
the artist under that in 20-pixel grey (`#AAAAAA`, stock secondary text), and both, like the
track list's title and rows and the progress and empty messages, keep 36 pixels (`CF_EDGE`) from
each side, where the lowest visible track row's text clears the bottom corners. Long names still
scroll within that. A card (an artist's All albums, the rated albums' Sort) uses the album line.
Values are `CF_*` in `patch/offsets.inc`; Legacy keeps its track list layout.

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

`test/build.py` fails an iPod build when any fixed text or icon in a changed asset reaches
under the glass (screen coordinates: the bar at y 0 to 30, windows at 30 to 320, the quick settings
and confirm pop-ups at 0 to 320): a label's font-high band, an image drawn centred at its size, a
slider's bar, else the widget. Backgrounds, tap targets and list rows, which scroll, are not
checked there; `test/patch.py` checks the settings rows in the first and last visible slots,
Coverflow's labels and lowest track row, and the status bar clock against every combination of
icons. Raising `CORNER_R` until the clock drops
under `CLOCK_MIN` fails the build.

## Hold Return

The stock long-key function at 0x4e873c retains all instructions except the final
Home call at 0x4e8924, in both builds (`HOLD_RETURN`). The hold fires after 700 ms, as Play/Pause's does
([internals.md](internals.md#queue-menu), `HOLD_KEYS`); stock waited 1000 ms. Stock power, lock, test and key-lock gates and its release
latch execute first. The payload cancels centre confirmation and spin state, checks
the shared screen/navigation restrictions, and then, on Now Playing, goes back
(`navigator_back`) when the page it opened over (`np_origin`) lies right under it, or
calls the stock Home destination otherwise; anywhere else it remembers the page and calls the stock switch
function with `playing_page` and `{0, 0, 0xff, 2}`. The `0xff` context skips
player_start, so playback is not restarted. The switch keeps the hold's key-up
from reaching the stock release filter, which would leave the latch that filter
shares armed and swallow the next Return, so the payload clears that latch once
the switch has landed. The hold's release therefore leaves the unit on Now
Playing and the first short Return reaches the stock Back path, with position
memory restoring the browsing page and its selection. No short-Return callback or
other long-key destination changes.

**System settings → Display → Controls → Hold Return** (`Q2POD HOLDRETURN`, both builds) picks the
destination: Recent (0, the default) is the behaviour above; Home (1) calls the stock Home
destination from any page but Home, Now Playing included, and clears `np_origin`. On Home the hold
still opens Now Playing, as does iPod's short Return on Home's first row (on any other row it first
selects that row). The release latch on the way Home: [internals.md](internals.md#hold-return).

## Transitions

Pages slide with stock's own window animator; the payload adds no animation code. The build sets
`anim_hint` to `SLIDE` (`tools/ipod.py`) on the window of the nine local browsing pages, the
settings and streaming pages and the equalizer page. A hinted page slides in from the right,
pushing the page below it out to the left, and Return reverses that; the status bar is not part
of either page and stays still.

Left immediate: Home (opened once, never closed), Now Playing (its open starts the player, which
can hold the UI thread), Coverflow (its open loads the albums and covers), the dialogs and quick
settings, the queue, More and jump pages over Now Playing, and the pages that keep their stock
assets: Tidal and the other online pages, Wi-Fi transfer and the firmware download. Moving between
folders reloads the folder page in place, so only opening and leaving that page slides. Stock
drops every key and touch while a page slides; none is queued
([internals.md](internals.md#page-transitions-ipod)).

## Chevrons

Stock draws `list_into` on Local Music's categories, the `localclass_page` rows (artists, genres,
composers), the album list and folder rows that are not songs, hidden in multi-select. iPod adds
it, aligned with those (see [internals.md](internals.md#drawing)), only on the `DRILL` windows in
`patch/contexts.inc`: Home and the playlist list, whose rows open their tracks. Classic's Home
draws only the highlighted row's, so it moves with the selection bar; Minimal's Home has none. Tiles narrower
than half the list (playlist Import/Export) get none. Those two tiles, built by stock at run time,
have no icon: the build gives the icon no width and stretches the label across the tile in stock's
centred `s_scrlabel_white24c` (`tools/ipod.py`). The artist page's Albums tab drills but has no
stock chevron or payload row layouter, so it has none.

The payload's own pages (`page_list`, in both builds) draw the same `list_into` at x 302 on every row
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
([internals.md](internals.md#sorting)), and any other character shows as it is. Home, settings menus
(`CTX_FIXED` windows, which are not alphabetical) and other short lists never show it. Values are in `patch/offsets.inc` (`LETTER_*`); see
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
placeholder cover at this size, so real art and the placeholder match. The payload paints them
over the scaled image in the page's black, with an anti-aliased
edge pixel, so opposite corners share the same pixel grid. The title is 22 pixels
white (`NP_TITLE_PX`) over the 18-pixel (`NP_META_PX`) artist, `#CCCCCC` (`NP_ARTIST_RGB`), and
album, `#BBBBBB` (`NP_ALBUM_RGB`); "3 of 12" and both times remain `#AAAAAA`. Spotify uses the same hierarchy in the iPod build; Legacy
keeps its existing artist color. Long lines scroll, as stock.

**Theme: Minimal** draws the album in Sudo Music's small capitals (`NP_CAPS_PX`, 15 pixels,
`NP_ALBUM_RGB`, `SUDO_TRACK` apart, `paint_caps`) over its label's transparent ink; Classic shows
the label. There is no on-screen transport row: the buttons play, pause and skip.

The art, title, artist and album are the slide_view's first page, so a swipe replaces all of them
with the lyrics or info page. The info page keeps its stock 225-pixel column, centred; the lyrics
page spans the screen (**Lyrics** below). The info page's lines read `Label: value`, with spaced units
(`35.92 MB`, `16-bit`, `950 kbps`, `44.1 kHz`), **Genre** and **Year** for stock's Genres and Era,
`Unknown` for a missing value and `Location:` before the folder (`ringnav_np_info`, both builds). The play/pause overlay has zero opacity, retaining its original gesture target without
a transport glyph; stock may update its visibility without making it appear. The loading
spinner stays centred on the artwork. The on-screen Return icon moves off-screen, as on the pages whose navbars are
hidden; the hardware Return does the same. More keeps its place at the top row's right. The play
mode (shuffle/order) and Favourite icons sit at the foot of the art page under the text, the
favourite centred directly beneath More at x 303, with the play-mode control 56 pixels to its left, leaving a 6 px gap between touch targets;
the three lines centre in the art's height above them. Both retain their 50 px touch targets and
vertical position. Both keep their stock images and handlers and swipe away with the art.
Internet Radio uses the same heart alignment in both iPod themes.

The bar is a four-pixel plain-colour line inside a 44-pixel seek target: a `#1C1C1C` track
(`TRACK_COLOR`) and an off-white `MINIMAL_FILL` fill, with no thumb. **Theme: Classic** makes it an
8-pixel capsule (`NP_BAR_CLASSIC`; `bar_size` and `round_radius` set on the slider at runtime,
`np_theme`) filled in the accent's light tone (Graphite `#6E6E6E`, 3.3:1; see [Display
settings](#display-settings)), and shows stock's play/pause glyph on the art again. Spotify's bar follows the same theme.
The asset holds Graphite's; `ringnav_playing` sets the current accent's. Tap or drag anywhere in the bar's taller target to seek; the page dots sit just above it so page swipes keep a separate target. The elapsed time
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
the screen off, as on every other page. The jump is stock's key seek, which can pause the player briefly, but only once, when the
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
to a new line glides too. Lyrics moved by the wheel or a finger stay where they are, while the bar and the
highlight go on, until the page swipes away from them; back on the lyrics page they are on the
current line again. Another song starts at the top. See [internals.md](internals.md#scrub-ipod) and [Word-timed lyrics](internals.md#word-timed-lyrics).

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
its tiles, framed in white when selected.

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

The iPod build starts on Home with the remembered queue and track restored paused; In-Vehicle Mode
keeps the stock start on Now Playing. See [internals.md](internals.md#boot-resume-ipod).

## Display settings

`systemset_display_page_init` (`0x4c1d04`) destroys the children of `scroll_view_display` and
builds three rows with `0x4c19bc`: a `list_item_create(view, 0, 0, 0, 0)` in `s_listitem_black`
(the list view lays it out 78 pixels high), holding a `button_create(item, 20, 0, 335, 70)` in
`s_btn_listitem` with a click handler, and in it a 52-pixel icon at x 10, a
`s_scrlabel_white24l` `hscroll_label` at (72, 0, 210, 70) and `list_into` at x 282. The rows
show no value; each opens a sub-page (iPod's [settings rows](#settings) then lay them out 68
pixels high). Both builds then add, the same way with stock's `list_into` chevron, **Appearance**
(iPod only, `system_display`), **Coverflow** (Play settings' cover mode icon, `playset_covermode`)
and **Controls** (Play settings' folder jump icon, `playset_folderjump`). Each opens stock's
Display page again (`display_open`, `display_sub` naming the sub-page while its init runs), which
drops the stock rows, takes the sub-page's title and builds only its own rows:

- Appearance: "Theme: Minimal", "Light Mode: Off" ([Light Mode](#light-mode)), "Accent: Graphite"
  and a "Rounded Corners" switch (stock's `switch_on`/`switch_off`, via `list_toggle`) with the System settings Display icon (`system_display`), "Home: Split" (`playset_covermode`) and "Battery: Icon" (Icon,
  Percent, Icon + Percent, Percent Inside; see [Status bar and clock](#status-bar-and-clock)) with
  the power manager icon (`system_powermanager`).
- Coverflow: "Artist Albums: Coverflow" (`Q2POD ARTISTCF`, the default; Grid and List, same icon;
  Coverflow ends with an All albums card, see
  [internals.md](internals.md#artist-albums-in-coverflow)), "Coverflow Sort: Album" and "Refresh
  Coverflow" (`system_update`).
- Controls: "Shortcut: Rockbox" (iPod, `playset_covermode`), "Hold Return: Recent"
  (`Q2POD HOLDRETURN`, [Hold Return](#hold-return), `playset_folderjump`) and Wheel Sensitivity,
  which opens its slider page.

All are among the [settings icons](#settings-icons) the build pre-sizes. A value row has its value
in the label (260 pixels wide, to where the chevron ends) and no chevron, since Centre or a tap
changes it in place. Theme, Accent and Home share the Appearance page, so a new theme relabels the
other two in place.
Both variants also append **Boot to: Rockbox / Q2 Pod** to System settings when Rockbox is present; see [Boot](boot.md#rockbox).
The page is `CTX_FIXED`, so the wheel walks onto them like the stock rows.

A change is saved at once with the stock `write_int_config(value, "IPOD", key)` (`0x4f3f4c`):
`sprintf("%d")`, then `toolsWriteConfig("/mnt/data/config.ini", section, key, text)`, which
rewrites the key or appends `[IPOD]` with it (`"[%s]\n%s=%s\n"`). The keys are `ACCENT` (Classic's accent), `MINIMAL_ACCENT` (Minimal's), `ROUNDED` (Rounded Corners: 0 off, 1 on), `LIGHT` (Light Mode: 0 Off, 1 On, 2 Auto; missing is Off), `HOME`, `BATTERY` and `THEME`. The values are read once,
on the payload's first use (after stock `config_init`: `application_init` runs `platform_init`, which
calls it, before it opens any window), with `toolsReadConfig` (`0x5bd464`), in the order stock `config_init`
calls it: `(path, section, key, out, default)`. It reads the file line by line
(`strcasecmp` on the section and the key), copies the trimmed value to `out` and returns 1; a
missing key copies the default and returns -1. The default must not be null (stock reads its
first byte). The payload passes `"0"`, so a missing or unreadable entry, or any value that is not
one valid digit, is Graphite, Split, Icon and Minimal; a missing `MINIMAL_ACCENT` is Minimal (7), and a missing `ROUNDED` is Square in both themes. A
card from 1.0.1 or earlier, which has no `THEME`, keeps 1.0.1's look, and a Minimal card from before
1.4.6 keeps its off-white look whatever `ACCENT` holds.

| Accent                | Progress / active-control light tone | Red text / marks |
| --------------------- | ------------------------------------ | ---------------- |
| Graphite (0, default) | `#6E6E6E`                            | `#D8D8D8`        |
| Crimson (1)           | `#EB2F56`                            | stock `#FF1448`  |
| Tidal (2)             | `#30929B`                            | `#30929B`        |
| Champagne (3)         | `#9A8446`                            | `#9A8446`        |
| Cobalt (4)            | `#447CDD`                            | `#447CDD`        |
| Tangerine (5)         | `#CB681E`                            | `#CB681E`        |
| Plum (6)              | `#9D4F7A`                            | `#9D4F7A`        |
| Ivory (7)             | `#EEEEEC` (Graphite's elsewhere)     | `#D8D8D8`        |
| Green (8)             | `#778E4C`                            | `#778E4C`        |

The bars are Graphite `#424242`, Crimson `#E8123F`, Tidal `#13838D`, Champagne `#8C732C`, Cobalt
`#2A6AD8`, Tangerine `#C45400` (the orange Q2), Plum `#903868` (the purple Q2) and Green `#647F34`
(the green Q2). Tangerine, Plum and Green are as close to the shells as white text at 4.5:1 allows,
and no shade of theirs reads as stock red to the mapper, so nothing is mapped twice. Ivory (7) is
Minimal's only: Classic's cycle goes from Plum to Green, and a Classic `ACCENT` of 7 reads as
Graphite. Green is row 8, after Ivory, so cards that saved 7 keep Ivory.

Existing accent preference values and the red-tone mapping stay compatible; greys and artwork
remain unchanged by the live accent mapper. See [internals.md](internals.md#accent) for the mapping.

Now Playing uses 24 px monochrome control glyphs on 28 px canvases inside the existing touch targets. Favourite keeps its outline/filled states; inactive dots are muted. Artwork, scrolling metadata and playback controls retain their existing behavior.

### Theme

**Theme: Minimal** (`THEME=0`, the default) is the look described above. **Theme: Classic**
(`1`) is the iPod look before 1.0.1, drawn at runtime over the same assets: the accent's
full-width selection bar under white text, with Home's chevron on its bar, coloured settings icons and the stock `list_into`, and Now Playing's 8-pixel capsule and play/pause
glyph. Its status bar is black, as Minimal's.

Corners is one setting for both themes, Square until chosen, and Theme leaves it alone. Both themes inset an album grid's cover inside the tile (`album_children`), so the selection card frames it.

Each theme keeps its own accent, and the Accent row follows Theme in both. Minimal's
(`MINIMAL_ACCENT`) cycles Ivory, Graphite, Crimson, Tidal, Champagne, Cobalt, Tangerine, Plum, Green;
its default, Ivory, draws Graphite's greys with its own fills (the progress bars, Spotify's, the
PEQ curve and the visualizer) in `MINIMAL_FILL`, white on the dark track; a scrub's fill is pure
white. Another accent colours the selection card, those fills and every mapped red, as in Classic,
and the card keeps the rows' own ink and stock's `#AAAAAA` metadata. Minimal's grey icons and
codec badges stay grey, but `list_into`, the Bluetooth marks and `list_delete` stay white, as
their grey copies were only for the off-white card; the edit pages' OK stays off-white in both
themes. Classic's (`ACCENT`) skips Ivory, so switching
to Classic shows Classic's own accent, Graphite until another is chosen. Now Playing's bar has no thumb:
stock draws its dragger (a box and knob, the stock theme's blue) unless `border_color` is clear,
so `playing_page` clears it in every state. 1.3.9 first painted it white, which drew the box at rest.

A change applies at once, as Accent does: the image cache is dropped and the screen repaints
(icons and chevrons reload in the new theme's colours), Home re-lays its menus and art, and all
open settings lists (including cached pages behind Display) take the new top inset. An open
Now Playing gets its bar and glyph, and the Accent row shows the new theme's accent, the
selection staying on Theme. Nothing needs a restart.

What Classic does not restore: the pinned theme edit of `s_btn_listitem`'s pressed colour
(`#3D1920` to `#333333`), so a touched row flashes grey rather than an accent tint, and the
status bar asset's own background (`#000000`, not `#161616`), which the payload's fill covers.

### Light Mode

**Light Mode: Auto** (`LIGHT=2`) is light from 07:00 to 18:59 by the device clock and dark the rest of the day. The row cycles Off, On, Auto on each click. It switches at 07:00 and 19:00 while the device is on, and the cached images are dropped and an open screen repaints then (`clock_show`, at each minute with the status bar's clock). The boot logo picks its mode by the UTC hour ([boot](boot.md#light-mode)).

**Light Mode: On** (`LIGHT=1`) turns both themes light at runtime, over the same assets. Every
neutral colour (channels within `LIGHT_SPREAD`) flips per channel, black to `LIGHT_PAPER` (`#F4F4F4`)
and white to `LIGHT_INK` (`#161616`), alpha kept; other hues are left as they are. Stock's colours
flip in the style hooks (`accent_map`, after the accent), the payload's own in `ui_ink`, which
`patch/peq.h` puts in front of every canvas and vgcanvas colour setter. A colour read back from the
LCD to restore it goes through `stock_canvas_set_*`, since it has flipped already. Only the outermost
style hook call maps: a mutable style (inline `style:*`) answers from its default style through
`style_get_color` again, and the flip is not idempotent. A clear colour (`#00000000`) stays clear,
because stock tests for it (Now Playing's dragger).

Coloured accents keep their tones. A neutral one (Graphite, Ivory) takes its silver red tone for
fills as well, so it flips dark on paper; Minimal's own card (`MINIMAL_FILL`) flips to a dark card
under light ink. On a coloured bar or card the rows' ink stays white, as on an iPod's blue bar
(`sel_accent`), and so does the `>`: Classic Home's and drill rows' from `LIST_INTO_WHITE`, stock's own
`list_into` drawn again over the bar (`paint_into`). A red tint over black (stock's pressed tints)
becomes the same tint of the tone over paper.

A theme image without colour (no pixel more than `LIGHT_CHROMA` saturated) flips too when it is
mostly glyph: under 1/`LIGHT_SURFACE` of its area opaque and darker than `LIGHT_GLYPH`. White marks,
chevrons, spinners and status icons turn dark, while a grey or dark disc keeps its white glyph.
Quick Settings' inactive discs (`drop_*`) flip whole, to light discs under dark glyphs, and the
active ones keep the accent. An image the accent mapped is final, and `switch_off` keeps its white
knob, as `switch_on` does. Cover Flow's frame composites over paper instead of black, its side covers and reflections fading to paper. Solitaire's face-up cards stay light with black suits (`unflipped`). Classic's status bar is an iPod's flat
grey (`LIGHT_BAR`), its stock black border cleared so it does not flip to a paper line; Minimal's is paper. Album art,
photos, wallpapers and videos are never changed.

ponytail: a non-square or transparent cover in Cover Flow keeps black bars and a black background
inside its square (`decode`).

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
