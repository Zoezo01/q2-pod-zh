<p align="center">
  <a href="https://www.youtube.com/watch?v=cc581MRAtp0" title="Watch the Q2 Pod video on YouTube">
    <img src="assets/banner.png" alt="Q2 Pod: the iPod firmware mod for the Shanling Q2" width="100%">
  </a>
</p>

<p align="center">
  iPod classic experience for the Shanling Q2.<br>
  Wheel navigation, Coverflow, PEQ, and optional Spotify and Rockbox, all built on top of the stock firmware.
</p>

<p align="center">
  <a href="https://github.com/DiamondBond/q2-pod/releases/latest"><img alt="Latest release" src="https://img.shields.io/badge/release-latest-3D424B?style=flat-square"></a>
  <a href="#features"><img alt="Device: Shanling Q2" src="https://img.shields.io/badge/device-Shanling%20Q2-B99AC8?style=flat-square"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-D77868?style=flat-square"></a>
  <a href="https://www.youtube.com/watch?v=cc581MRAtp0"><img alt="Watch on YouTube" src="https://img.shields.io/badge/YouTube-video-FF0000?style=flat-square&logo=youtube&logoColor=white"></a>
  <a href="https://discord.gg/pgsQpXhSQ"><img alt="Join the Discord" src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white"></a>
</p>

<p align="center">
  <b><a href="https://github.com/DiamondBond/q2-pod/releases/latest">Download</a></b> ·
  <b><a href="#install">Install</a></b> ·
  <b><a href="https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf">User guide</a></b> ·
  <b><a href="docs/changelog.md">Changelog</a></b>
</p>

## Features

- **iPod UI:** Home, menus, Now Playing, Coverflow and four visualizers, accent colour and Games.
- **Wheel navigation:** smooth scrolling, and menus remember your place.
- **EQ:** Simple's Low, Mid and High sliders, or Advanced's parametric EQ with up to 30 bands and a live curve.
- **Streaming:** Spotify (Premium): browse your library, search, play and download playlists on the Q2 after a one-time pairing from your phone; AirPlay from your iPhone, and Internet Radio.
- **Scrobbling:** send your listens to Last.fm or ListenBrainz over Wi-Fi.
- **Ratings:** rate songs and albums 0–5 hearts, then browse Top Rated, Loved and rated albums.
- **Browsing and playback:** favourites, playlists, Most Played, Long Songs, Crossfade, and shuffle by song, album or folder.
- **Podcasts and audiobooks:** `.m4b` audiobooks (including ones split by a CUE sheet) and podcasts resume where you stopped. A book too long for the Q2's memory (around 40 hours in one file) needs splitting into parts.
- **AirPods:** stem controls, ear detection and two-way volume sync.
- **Battery:** an optional 80% charge limit, Low Power mode, and a Standby limit that powers off a Q2 left asleep.

<details open>
<summary><h2>Screenshots</h2></summary>

<p align="center">
  <img src="assets/screenshots/home-split.png" alt="Home: the menu beside the playing album's cover" width="45%">
  &nbsp;
  <img src="assets/screenshots/now-playing.png" alt="Now Playing: the cover beside the title, artist and album, with the progress bar below" width="45%">
</p>
</details>

## Install

**Requirements:** a Q2 with a microSD card, a USB-C cable and a computer with internet.

**Optional:** The [user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf).

> [!IMPORTANT]
> Flashing firmware carries risk. Back up anything on the card you can't lose before you start.

1. **Download** `update.tar` from the [latest release](https://github.com/DiamondBond/q2-pod/releases/latest).
2. **Plug the Q2 into your computer** with the USB-C cable. The card shows up as a drive.
3. Copy `update.tar` to the **root** of the card (not inside a folder), then eject and unplug.
4. **Update:** open **System settings → System Update → TF card update**. Leave the card in until the Q2 restarts.

> [!TIP]
> Prefer Shanling's original look? Download `update-legacy.tar` instead, then rename `update-legacy.tar` to `update.tar` before you copy it to the card. It has the same features and fixes, without the iPod UI.

## Controls

| Control             | Action                                                                      |
| ------------------- | --------------------------------------------------------------------------- |
| **Wheel**           | Scroll. Spin faster to speed up. Adjust a slider when one is open.          |
| **Centre**          | Open the selected item. Double-press to turn the screen off.                |
| **Hold Play/Pause** | View the playing queue, or open track, playlist and shuffle/repeat options. |
| **Hold Return**     | Jump to Now Playing; hold again to go back. Display can make it go Home.    |

The user guide covers every control, EQ, Coverflow and more tips.

## Updating

Over Wi-Fi: **System settings → System Update → Network Update** downloads the newest release to the card's `update.tar`, replacing any that's there, then opens the TF card update's confirmation. Press the row again to cancel the download.

Manual: Repeat the [install steps](#install) with the newest release. Your settings, ratings and play counts are kept. The updater skips a version that's already installed, so a newer release always goes on.

## Extras

**System settings → App Store** installs, updates and removes Rockbox, its themes, Spotify and the card apps (Deezer, Soulseek, File Browser) over Wi-Fi from their GitHub releases (Q2 Pod 1.4 or later), keeping your Rockbox settings and Spotify's login.

- **Spotify (optional, Premium):** with the Q2 on Wi-Fi, open **System settings → App Store → Spotify** and press the row to install it. Then open **Streaming → Spotify** and, the first time, pick the Q2 in the Spotify app on your phone on the same Wi-Fi. [More](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf).
- **Scrobbling:** connect Last.fm or ListenBrainz with the [user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf).
- **Lock screen:** six built-in wallpapers, a large clock, or your own images ([user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf)).
- **Boot logo:** put your own JPEG at `/Q2Pod/boot/boot-logo.jpg` on the card. It shows from the next power-on ([user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf)).
- **Rockbox (optional):** a separate player. With the Q2 on Wi-Fi, open **System settings → App Store → Rockbox**. The first press says that Rockbox becomes what the Q2 starts at power-on; press again to install it.

## Library

Copy your music to the card over USB-C, then run **Library → Update Local Music** on the Q2.

These folders at the card's root each add their own Library entry: `Podcasts/`, `Audiobooks/`, `Photos/`, `Books/` and `Videos/`; `EQ/` holds EQ presets to import. See the user guide for supported formats.

## Help

- **Question or suggestion?** Please join the [Discord](https://discord.gg/pgsQpXhSQ).
- **Bugs:** Please [open an issue](https://github.com/DiamondBond/q2-pod/issues).

## Restoring stock firmware

- **Back to Shanling's firmware:** install the [official Shanling firmware](https://en.shanling.com/download/150) the same way.
- **If the Q2 won't start up:** copy the `recovery-update` folder from [Shanling's recovery package](https://drive.google.com/file/d/1aINQfJu6n0JTQ4hOzzD1uSpSj3TS_NJj/view?usp=drive_link) to the card, then hold **Previous** and power on with **Centre**.

## Documentation

- **For users:** [User guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf) · [Setup](docs/setup.md) · [Changelog](docs/changelog.md)
- **Library folder markers:** [Hide folders while keeping playlists](docs/discovery.md)
- **For developers:** [Building](docs/building.md) · [Releasing](docs/releasing.md) · [Internals](docs/internals.md) · [Remote control (dev builds)](docs/remote.md)

## License

[MIT](LICENSE) covers this repository's code and docs. The Shanling firmware included in release images remains Shanling's. Q2 Pod is an independent project and is not affiliated with Shanling.

## Support

Q2 Pod is free and built with love in spare time. If it has made your Q2 better and you'd like to give something back, you can support development on Ko-fi. Otherwise if you can, please consider donating to a good cause instead ❤️

<p align="center">
  <a href="https://ko-fi.com/diamondbond"><img alt="Support Q2 Pod on Ko-fi" src="https://img.shields.io/badge/Ko--fi-support%20Q2%20Pod-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white"></a>
</p>
