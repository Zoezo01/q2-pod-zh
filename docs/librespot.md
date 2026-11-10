# Spotify (librespot)

Q2 Pod plays Spotify through a librespot port that runs from the microSD card. Spotify Premium is required. After a one-time Spotify Connect pairing from a phone, the Q2 browses and plays on its own over Wi-Fi; the phone can still pick the music over Spotify Connect. Either way the Q2 streams and decodes it itself.

## Port

The port lives in [DiamondBond/q2-librespot](https://github.com/DiamondBond/q2-librespot), a librespot fork. Its branch `shanlingq2` is v0.8.0 plus an IPv4 fallback for discovery, because the Q2's kernel has no IPv6. Its `contrib/shanlingq2/` holds the build, the card launcher and the README.

- **Release:** [q2-librespot's releases](https://github.com/DiamondBond/q2-librespot/releases/latest) ship the prebuilt card files as `q2-librespot-*.zip`, unzipped to the card's root.
- **On the card:** the binary, its launcher and its sink go in the card's `.spotify` folder, with the saved login in `.spotify/cache/`. Q2 Pod starts it; see the [user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf) to install it.
- **Build:** since `q2-v1.7`, librespot is built **hard-float** for the X2000's FPU, with the stock binaries' ABI (`-mfp64`, NaN-2008). It is linked dynamically against the Q2's own glibc 2.28 through `/lib/ld-linux-mipsn8.so.1`, the way Q2 Pod's helpers are. The libraries it links against come from the V1.32 firmware ZIP's rootfs. Earlier releases were static soft-float (musl).
- **Audio:** goes through the stock `aplay` to `plughw:0,0`, the CS43131 headphone DAC, started only while librespot plays (its subprocess backend), so local music can open the DAC whenever Spotify is paused. With Bluetooth or a USB DAC as the output, Q2 Pod names the device and volume in `/tmp/q2-librespot.out` and `aplay.sh` runs Q2 Pod's `q2video -s` there instead, at hciplayer's soft volume ([internals](internals.md#spotify)).

**It plays.** On the device, librespot appears in Spotify Connect, the phone's session authenticates, and audio comes out of the headphones at 160 kbit/s. Hard-float decoding takes about 3% of a core, against about 31% for the earlier soft-float build.

**Playback doesn't start.** Spotify refuses librespot the key that unlocks a track's audio for some accounts, mostly newer ones, though older ones too ([librespot#1649](https://github.com/librespot-org/librespot/issues/1649)). The account signs in and the Q2 shows up in Spotify Connect, but every track fails, and `.spotify/spot.log` on the card has `error audio key 0 1` for each one. Q2 Pod then shows "Spotify refused this account" and restarts librespot, which would otherwise skip through the whole list. Neither the Q2 nor librespot can fix it. Another Premium account (a family member's, say) may play, and Spotify's own headless client, Soloist, runs only on ARM and x86 Linux, macOS and Windows, not on the Q2's MIPS processor.

## Q2 Pod

**Streaming → Spotify** opens a browser: Now Playing (the cover, title, artist and album, a moving progress bar and the play state, in Now Playing's layout), Liked Songs, Playlists, Albums, Artists, Search, Queue, Playback Settings (shuffle, repeat), Account (sign out), Downloaded Tracks and Downloads. It needs q2-librespot v1.4 or later, Spotify Premium and Wi-Fi for new tracks. Downloaded Tracks replays previously played, completely cached tracks without Wi-Fi. Without a saved login it asks for a one-time pairing: pick Q2 in the Spotify app on a phone on the same Wi-Fi, and the page turns into the menu. With an older add-on, Now Playing still works from the phone and the page asks for the update. Q2 Pod treats librespot as a source of its own playback, as it treats Videos' q2video:

- **Start:** only from the row, once per boot; until then nothing Spotify runs or polls. Idle 30 s after playing (2½ min if it never played) with the browser shut, or **Streaming → Configuration → Stop Streaming Services**, it stops; the row starts it again.
- **Cache:** the launcher caps the audio cache at `.spotify/.cache-size` (`2048M`, written by Streaming → Configuration's Spotify Cache, 512 MB to 8 GB), read at each librespot start; `2G` when the file is missing or malformed.
- **Power:** while it plays, standby, auto power-off and the DAC's power-off are held off; the screen still turns off.
- **DAC:** set up as stock's AirPlay receiver sets it (`config_outputchannel`, as a headset insert does, then PCM mode, unmuted and the Q2's volume).
- **Keys:** Play/Pause and the side buttons control Spotify while it was the last thing played; the wheel is the Q2's volume, which is Spotify's.
- **Hand-over:** casting stops local music; starting a local track pauses Spotify first.

The fork adds three options for this: `--status-file`, the player's state, track, position and cover for the page, `--control-socket`, the commands from the keys, and `--browse-socket`, the browser's requests (below). How they fit together is in [internals.md](internals.md#spotify).

**Downloads** is a persistent hub for Downloaded Playlists, Downloaded Tracks, live progress and Storage Management. **Download Playlist** and **Download Liked Songs** preserve names, source order and duplicate positions. Partial collections remain browsable; playback skips missing entries without fetching online. Controls offer **Update Download**, **Retry Failed**, **Pause**, **Resume** and **Cancel Download**. Updates and resuming after reboot are manual; repeated requests do not duplicate jobs. Collections queue sequentially.

Explicit downloads use protected storage under `.spotify/cache/offline/audio/<account>/`, separate from the evictable playback cache. The Spotify Cache setting caps temporary playback storage and cannot evict protected downloads. Existing cached tracks remain available without invented playlist grouping: download that playlist again. Storage Management selects multiple tracks/playlists or clears all music after showing a count and confirmation. Removing a playlist keeps shared audio; removing tracks warns about incomplete playlists. Clearing downloads preserves login/settings; signing out removes login and offline music. Keep `.spotify/cache` when updating.

Collection controls require matching firmware and a backend advertising the saved-download capabilities (`q2-v1.10` or later). Older backends retain supported flat-list actions and an update explanation; older firmware's original operations still work with the new backend. See the [shared download UX/protocol contract](downloads.md) for examples, job states, removal semantics and Deezer/Qobuz guidance.

## Browsing protocol (Q2SPOT/1)

The fork's `--browse-socket /tmp/q2-librespot.browse.sock` serves Q2 Pod's Spotify browser. Older backends lack it; Q2 Pod then keeps the Connect Now Playing page and asks for the add-on to be updated.

The browser integration uses q2-librespot `shanlingq2` (`q2-v1.6` or later, Q2SPOT/1). Card updates replace `.spotify/librespot`, `.spotify/run` and `.spotify/aplay.sh`; preserve `.spotify/cache` to keep the paired login and complete downloads, and `.spotify/.cache-size` to keep the chosen cache size. Signing out deletes that account's saved audio, keys and artwork.

**Transport.** A Unix stream socket. One request per connection: the client writes the request and shuts down its write side; the backend answers and closes. Requests are at most 8 KiB, responses at most 64 KiB. The client gives up after 15 s.

**Framing.** Text lines ending in `\n`, fields separated by tabs. The first line of a request and of a response is `Q2SPOT/1`. In every field, the bytes `%`, tab, LF and CR are written as `%25`, `%09`, `%0A` and `%0D`; any other byte, UTF-8 included, is literal. A `%` not followed by two hex digits is malformed. Fields are opaque: IDs, URIs and cursors are passed back exactly as received, never rebuilt from displayed text.

Request, after the version line, one line:

    <id>	<op>	<arg>…

`id` is the client's decimal request number, echoed back so a late reply can be dropped. Response, after the version line:

    <id>	<status>	<message>	<cursor>
    <kind>	<uri>	<title>	<subtitle>	<context>	<position>
    …

The header is followed by 0 to 50 rows. `message` is one line of English for the screen (at most 120 bytes), usually empty on `ok`; capabilities and downloads use it for version or progress. `cursor`, when not empty, fetches the next page when passed as the op's last argument; it is at most 1 KiB. A page may hold fewer than 50 rows to stay within 64 KiB; its cursor then continues after the last row sent. `title` and `subtitle` are display text, at most 255 bytes each, cut on a character boundary; URIs and cursors are never cut.

Card apps (`patch/addon.c`) take three `message` values of an `ok` list response as instructions, not text. `refresh` asks the firmware to ask again every 2 s while the list is on screen; after an action on the list it is asked again at once, after the message's toast, so a choice row should leave the message empty for its mark to move straight away. `pages` with a cursor asks it to fetch the following pages itself, each with the list's arguments and the newest cursor last, as More… would, and append them to one list, up to 10 responses (500 rows); it continues while each response says `pages` and has a cursor, and ends at a **More…** row otherwise. Any other message with a cursor shows **More…**. Firmware lists `pages` in `/tmp/q2-addon.caps` when it does this; older firmware ignores the word and keeps More…. `sections`, on a first-level response, makes each `list` row a section (one with an empty uri is left out): the firmware shows the first section's list in its place, titled with its name and, when there are two or more, its position (`Albums 2/3`), and a sideways swipe moves to the next or previous section ([internals.md](internals.md#card-apps)). At most 8 sections are taken, the rest left out, and a section whose context doesn't fit is left out too; a swipe back returns a section paged by `page` rows to its first window. Firmware lists `sections` in the caps file when it does this; older firmware shows the rows as an ordinary list, so the sections stay reachable as rows.

Row fields:

- `kind`: `track`, `album`, `artist`, `playlist`, `account`, `setting` or `op`.
- `uri`: the item's Spotify URI (for `setting`, its name; for `op`, an op name).
- `title`, `subtitle`: what to show (a track's subtitle is its artists, an album's its artist, a playlist's its owner).
- `context`, `position`: for a playable track, `play <context> <position> <uri>` (the row's own URI last) starts it in its list, so the next track follows from Spotify's own context (even past the visible page) and a duplicated playlist entry starts at the copy chosen. An empty context means the track plays on its own: `play <uri>`.

Card apps (`patch/addon.c`) have row kinds of their own besides these. `progress` draws a bar, for an app's sync or download progress: `title` is the text on its left and `subtitle` the text on its right, on one line over a flat bar, and `position` is how much of the bar is filled, in permille (decimal, 0 to 1000; a number outside that range is clamped to it, and text that is not a number counts as 0). `uri` and `context` are empty. The row is inert: a press does nothing, and neither does a held Play/Pause. An app sends `progress` rows only when the word `progress` is in `/tmp/q2-addon.caps`, and `info` rows otherwise (the title and subtitle as plain text, `position` empty): firmware without the word does not know the kind and takes `position` as the op a held Play/Pause sends, so a `progress` row at 47% would send `470` to the app as an op. Progress rows belong in a `refresh` list, which the firmware asks again every 2 s, so the bars move; a `pages` list is asked once.

A card app's op `stop` is reserved: an `action` row whose `uri` is exactly `stop`, or a row whose held-Play/Pause op (`position`) is exactly `stop`, means the app is ending. Once the app answers it `ok`, the firmware counts the app as stopped (it no longer keeps standby off or Wi-Fi on for it) and leaves its page; it sends no signal, so the app ends itself after replying.

A card app can show a cover beside its rows when the word `art` is in `/tmp/q2-addon.caps`. It leaves a JPEG at `/tmp/q2-<id>.art/<key>.jpg`, one file per row: `<key>` is the FNV-1a 32 hash of the row's `uri`, a tab and its `context` (the values, before the `%` escapes), written as 8 lowercase hex digits. A cover is at most 128 px either way, and is shown 64 px square; a larger one, or one whose size can't be read, counts as no cover. Write each file whole and rename it into place, so the firmware never reads half of one. The app cleans `/tmp/q2-<id>.art` itself. Covers are decided when a list is drawn: fetch them before replying, or change the reply on a `refresh` level, which draws the list again when the number of covers has changed. A list with any cover is drawn with covers, and a row without one keeps its room empty; a list with a `progress` row never has covers, and the first page of a `pages` list decides.

A `page` row moves a long list to another window of it: `title` is what it shows (Next page, Previous page), `context` a cursor of the same list (empty for its start), and `uri` `prev` for an earlier window (its last row is selected) or `next` for a later one (its first row past Previous page is selected, and past a Search row left as a row, and a Sort row after it). A `page` row's cursor has to fit with the list's own arguments in 400 bytes, past which the press does nothing. The firmware asks for the list's own op and arguments with that cursor as the last argument, none if it is empty, and shows the reply in the list's place, so Back leaves the list. Send it only when the word `window` is in `/tmp/q2-addon.caps`; firmware without it doesn't know the kind.

A `sort` row is a `list` row whose list is a sort choice, such as a page of sort orders: `uri` is the op it opens, `context` the op's argument and `title` the page's name, and a press opens it. Where the list's first row is a `search` row shown as the title bar's magnifying glass (a theme with a title bar), a `sort` row sent right after it, and not as the list's last row, is shown as stock's sort icon left of the glass instead, and a tap on it opens the same level. Anywhere else it is a list row with a chevron. Send it only when the word `sort` is in `/tmp/q2-addon.caps`, and a `list` row otherwise: firmware without the word doesn't know the kind.

Card apps also get one level of their own from the firmware: `goto`, `album` or `artist` then a song's path, the first level of the app's page when Go to Album or Go to Artist is used on one of its songs (under its `.<id>/` folder) and its `q2app` holds `goto=1`. The app answers with that album's or artist's own list. Firmware that sends it lists `goto` in `/tmp/q2-addon.caps`.

A card app whose songs would only clutter a listening history, such as live recordings that run into one another, can put `scrobble=0` in its `q2app`. A song under its `.<id>/` folder then still counts as a play but stays out of `.scrobbler.log`; a song it names elsewhere on the card logs as usual, and so does any other value of the key or none. There is no capability word: older firmware ignores the key. A user's own edit lasts until an update of the app replaces the file.

A card app whose `q2app` holds `game=1` is a game, listed in Library's Games and run over the whole screen with the input as text datagrams on its socket ([internals](internals.md#q2-shelf), Card games); firmware that does this lists `game` in `/tmp/q2-addon.caps`. One that also holds `audio=1` gets the audio output handed over before it starts (the player stopped, the headphone output readied), the output device and a soft output's volume as its arguments, and Play/Pause and the side buttons as key datagrams; its idle close is its own. The arguments are q2video's (`patch/video.c`), and so are the duties: `usb` names the USB DAC's card in `/proc/asound/cards` (`N [id]: USB-Audio - name`, so `plughw:N,0`); hciplayer lets go of the PCM, and mutes the DAC, a moment after the firmware's stop, so the open is retried for a couple of seconds (5 s on Bluetooth and USB, and up to 5 s more at `snd_pcm_set_params` off the jack, since bluealsa claims its PCM there, busy until then) and, on the jack, the DAC's mute and PCM mode are cleared after it (`/dev/shanling_dac`, ioctls `0xc0044d1f` and `0xc0044d1b` with an int 0) and the mute set to 1 again when the game closes the PCM; `hNN` is a volume the DAC applies, to show at most; Mono Audio and Swap L/R are `/tmp/q2mono` and `/tmp/q2swap`, an int each, 1 when on. q2-groovebox's `src/audio.c` and `src/platform_q2.c` do all of it. Firmware that does this lists `audio`; older firmware ignores the key, and the game then finds the PCM busy.

A card app can also ask for a sleep timer by writing `/tmp/q2-<id>.sleep` (whole, by rename): `until <unix time>` pauses playback at that time, at most a week away (a time the Q2 slept through by more than a minute is let go), and `end` pauses as the track playing ends (the next one waits at its start). The firmware keeps the timer itself, so it fires after the app has stopped too, and removes the file when it fires or is let go (playback stopping, or another app's timer); the app removes the file to cancel, and a new request replaces its timer. A time is counted from when the firmware reads it, so the clock being set later (over Wi-Fi) doesn't move it. Firmware that does this lists `sleep` in `/tmp/q2-addon.caps`.

Statuses: `ok`; `login` (no saved login: pair from the phone); `offline` (Spotify can't be reached); `premium` (the account isn't Premium); `refused` (Spotify refused the login, 401/403); `unavailable` (the item is gone or can't play here, a stale queue position included); `stale` (search's query hash changed, 412: the add-on needs updating); `throttled` (429: the message says when to retry; until then the backend answers `throttled` without asking Spotify); `unsupported` (unknown op); `bad` (malformed or oversized request); `error` (anything else). A temporary failure never removes the saved login.

Ops (`[cursor]` is optional):

| Op               | Arguments                  | Rows                                                                                            |
| ---------------- | -------------------------- | ----------------------------------------------------------------------------------------------- |
| `caps`           |                            | `op` rows, one per supported op; `message` is the backend's version, e.g. `q2-v1.3`             |
| `account`        |                            | one `account` row: the user's URI, display name, `Premium`; or status `login`                   |
| `playlists`      | `[cursor]`                 | the user's `playlist` rows                                                                      |
| `playlist`       | `uri [cursor]`             | its `track` rows                                                                                |
| `liked`          | `[cursor]`                 | Liked Songs' `track` rows                                                                       |
| `albums`         | `[cursor]`                 | saved `album` rows                                                                              |
| `artists`        | `[cursor]`                 | followed `artist` rows                                                                          |
| `album`          | `uri [cursor]`             | its `track` rows                                                                                |
| `artist`         | `uri`                      | its top `track` rows, then its `album` rows                                                     |
| `search`         | `category term [cursor]`   | `category` is `tracks`, `albums`, `artists` or `playlists`                                      |
| `play`           | `context [position [uri]]` | none: Q2 becomes the active device and plays                                                    |
| `queue`          |                            | the next `track` rows, `position` its index in this list                                        |
| `skip`           | `position uri`             | none: plays that queue row; `unavailable` if the queue changed                                  |
| `enqueue`        | `uri`                      | none: the track is added to the queue                                                           |
| `settings`       |                            | `setting` rows `shuffle` (`on`/`off`) and `repeat` (`off`/`context`/`track`) as title           |
| `shuffle`        | `on\|off`                  | none                                                                                            |
| `repeat`         | `off\|context\|track`      | none                                                                                            |
| `signout`        |                            | none: playback stops, the saved login is removed, and the backend waits for pairing             |
| `downloaded`     | `[cursor]`                 | complete cached `track` rows, most recently played first; works without Wi-Fi                   |
| `playdownload`   | `uri`                      | none: starts that cached track without requesting its stream URL                                |
| `download`       | `uri`                      | none: idempotently queues a background playlist download; message reports acceptance or failure |
| `downloadstatus` |                            | none: message reports progress or the completed job's result                                    |

Playlist downloads publish `/tmp/q2-librespot.download` atomically, with `downloading=1` while active and `0` when finished, paused, backing off or waiting for a rate-limit cooldown. Q2 Pod keeps the service and power timers alive during the job, without claiming the audio output. Stop Streaming Services or signing out stops the job.

Example, Liked Songs' first page:

    Q2SPOT/1
    7	liked

    Q2SPOT/1
    7	ok		Cg4IARIK…
    track	spotify:track:4uLU6hMCjMI75M1A2tKUQC	Never Gonna Give You Up	Rick Astley	spotify:user:alice:collection	0

Saved collection operations extend these existing operations through advertised capabilities; see [downloads.md](downloads.md#protocol).
