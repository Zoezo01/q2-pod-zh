# Boot

The Q2 splash shown on the screen at power-on is a JPEG inside the rootfs. A JPEG on the microSD card replaces it without a firmware update; a build can also change the built-in one ([Custom boot logo](#custom-boot-logo)).

| What       | Where                                                                                                                                                 |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Image      | `/mnt/data/boot-logo.jpg` when present, else under Light Mode `logo-light.jpg`, else `/release/assets/default/raw/images/xx/logo.jpg` (320x375 JPEGs) |
| Drawn by   | `/usr/bin/display_logo` (libjpeg, writes `/dev/fb0`)                                                                                                  |
| Started by | `/etc/init.d/S11logo_display_shell`, as soon as `/dev/fb0` exists                                                                                     |

## Boot sequence

BusyBox `init` runs `/etc/init.d/rcS`, which starts the `S??*` scripts in order:

1. `S10mdev`, `S10mount_ubifs`: devices and the data partition.
2. `S11logo_display_shell`: in the background, polls every 20 ms for `/dev/fb0`, then runs `display_logo` on `/mnt/data/boot-logo.jpg` if it exists, else on the built-in splash ([Light Mode](#light-mode)).
3. `S11module_driver_default`: `/module_driver/driver_default_init_script.sh` loads the drivers, among them `soc_fb.sh` (the framebuffer, so `/dev/fb0` appears and the splash is drawn) and the LCD panel.
4. `S20urandom`, `S40network`.
5. `S90play`: starts `dbus-daemon`, then the player: `/release/bin/demo`, or Rockbox first when the card has it ([Rockbox](#rockbox)).

demo's `platform_init` starts `checkappprocess.sh &`, the watchdog: it checks with `pgrep` that `/release/bin/demo` runs and reboots if not. Stock checks every 2 s; Q2 Pod every 10 s ([internals.md](internals.md#battery)).

## Framebuffer

`/dev/fb0` is 320x375 (portrait), 32 bpp, red at bit 16 (BGRA in memory), with two pages: `module_driver/soc_fb.sh` loads `soc_fb.ko` with `layer0_frames=2` and `is_rotated=0`, and the driver makes `yres_virtual` = `yres` x frames. Landscape content is stored turned 90° clockwise, as the stock splash is.

- `display_logo` opens the framebuffer through `libhardware2` (`fb_open`, `fb_enable`), refuses a depth under 18 bpp, writes each pixel as `0xff000000 | R << 16 | G << 8 | B` and pans to page 0 (`fb_pan_display(fd, info, 0)`). It does not scale.
- demo's AWTK flush thread draws one page while the other is shown and swaps them with `FBIOPAN_DISPLAY`, then `FBIO_WAITFORVSYNC`. Q2 Pod's video player shares the same two pages ([internals.md](internals.md#videos)).

## Rockbox

Q2 Pod can share the device with [Rockbox](https://github.com/DiamondBond/q2-rockbox/tree/shanlingq2), which runs from the microSD card. Rockbox is the initial power-on default when installed: **holding Play/Pause while powering on** starts Q2 Pod for that session instead, and Rockbox's **Boot stock OS** returns to Q2 Pod the same way. Both variants have a final **System settings → Boot to: Rockbox / Q2 Pod** row while `/mnt/mmc/.rockbox/rockbox` is a regular file. Tap or Centre toggles the next power-on choice. The choice is saved in `/mnt/data/boot-target` as `rockbox` or `q2pod`, through a synced temporary file and rename. A failed save reports an error and keeps the displayed choice. Missing or invalid preferences default to Rockbox, and missing Rockbox always falls back to Q2 Pod.

| What       | Where                                                                                      |
| ---------- | ------------------------------------------------------------------------------------------ |
| Key check  | `/usr/bin/q2boot` (`patch/boot.c`): exits 0 while Play/Pause (`md-gpio-keys`, 108) is held |
| Rockbox    | `/mnt/mmc/.rockbox/rockbox`, run from that folder, its output in `rockbox.log` there       |
| Started by | `S90play`, in place of `/release/bin/demo &`                                               |

When the saved choice is Rockbox and Play/Pause is not held, `S90play` waits up to 3 s for the card (`/tmp/mmc_add`); the card's driver polls for it, so it can't tell an empty slot from a card still being read. If the card has Rockbox, it runs Rockbox and appends `exit N` to `rockbox.log`; any exit, including 0x51 (81) from **Boot stock OS**, falls through to demo, which starts with its usual name so the watchdog finds it. Install Rockbox by unzipping its `rockbox.zip` to the card's root. A Rockbox that hangs keeps Q2 Pod away: hold Play/Pause at the next power-on, or remove `/.rockbox/rockbox` with a card reader.

### Rockbox from Home

iPod's Home has a **Rockbox** row between Folder and Streaming, shown only while the Rockbox binary is a regular file ([ipod.md](ipod.md#home)). Activation checks the file again in case the card was removed after the row appeared. This handover always launches Rockbox regardless of the saved boot choice. It is the way to Rockbox with Bluetooth: shut Rockbox down (Q2 Pod starts), connect the headphones in Q2 Pod, then pick **Rockbox** on Home, and Rockbox plays through them.

demo cannot start Rockbox itself, as Rockbox takes the screen, the keys and ALSA, so it hands over to `S90play` (`rockbox_open`, `patch/navigation.c`):

1. With no `/mnt/mmc/.rockbox/rockbox` it shows "Rockbox is not on the card" and stays.
2. It creates `/tmp/q2pod-rockbox` (`ROCKBOX_FLAG`), saves Memory Playback's queue and position with stock `save_memoryplay_info` (what power-off does), stops the player, and hands back charging (Charge Limit) and the second core (Low Power) as Rockbox finds them at power-on.
3. `system("killall checkappprocess.sh; killall -9 hciplayer; sync; kill -9 $PPID")`: the watchdog would reboot once demo is gone, and hciplayer holds the DAC's PCM. demo is killed outright, so none of its exit handlers can hang.

`S90play` runs demo in a loop rather than `exec`ing it: when demo ends and the flag is there, it removes the flag, stops the watchdog again (in case demo's kill came first), runs Rockbox as at power-on, and starts demo when Rockbox exits. Any other end of demo ends the loop, and the watchdog reboots as stock's does.

Bluetooth survives the hand-over: demo starts `rtk_hciattach`, `bluetoothd` and `bluealsa` with `system("... &")`, so none is demo's child and the headphones stay connected. Rockbox opens bluez-alsa's `bluealsa` PCM (the most recently connected device, through `plug` for its rate and format) whenever it opens, and the DAC otherwise ([q2-rockbox](https://github.com/DiamondBond/q2-rockbox), `shanlingq2_codec.c`). bluealsa is Q2 Pod's, so AirPods keep its 48 kHz AAC fix ([internals.md](internals.md#bluetooth-aac)). When Rockbox exits, demo starts afresh and sets Bluetooth up again. Rockbox's volume is pcm-alsa's software mixer alone, so before the hand-over `rockbox_open` sets the headset's AVRCP absolute volume to the Maximum volume limit (`btctl_transport_set_volume`); left at the Q2's volume it would cap Rockbox's. demo syncs the headset back to the Q2's volume once it starts again.

## Custom boot logo

### From the card

Put your logo on the microSD card as `/Q2Pod/boot/boot-logo.jpg`. While Q2 Pod is running, it checks the file on each card mount (including startup and leaving USB storage mode) and copies it to `/mnt/data/boot-logo.jpg`, through a synced temporary file and rename; the splash is drawn before the card is read, so the new logo shows from the next power-on after synchronization. Booting directly into Rockbox does not import a new card logo; start Q2 Pod once first.

- **Size: 320x375, stored turned 90° clockwise as the stock splash is, or upright at 375x320**, which Q2 Pod turns itself with the Q2's own `ffmpeg`.
- **Format: 8-bit, three-component baseline JPEG, at most 512 KB** ([Making a logo that works](#making-a-logo-that-works)).
- A file that doesn't qualify shows "Boot logo must be a 320x375 JPEG", and one that can't be saved "Could not save boot logo"; either way the current logo stays.
- Removing the file from the card deletes `/mnt/data/boot-logo.jpg` at the next mount while Q2 Pod is running; the following power-on shows the built-in splash.

### In a build

Every build replaces the built-in splash with `assets/boot-logo.jpg`. Pass another 320x375 JPEG with `--logo` to use your own:

```sh
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-logo --logo my-logo.jpg
```

Flash `/tmp/q2-logo/update.tar` the normal way: copy it to the root of the microSD card, then **System settings → System Update → TF card update**. The update keeps the scroll-wheel patch, and **About** still shows the build on its **CFW. Version** row (`VERSION` in `tools/build.py`).

### Light Mode

iPod builds of the default logo also ship `assets/boot-logo-light.jpg`, the same logo inverted (dark lettering on white), as `release/assets/default/raw/images/xx/logo-light.jpg`. While `/mnt/data/config.ini` has `LIGHT=1`, or `LIGHT=2` and the boot's `date` says 07:00 to 18:59 ([Light Mode](ipod.md#light-mode)), the splash hook shows it instead of the built-in one, so a power-on after turning Light Mode on or off matches it. A card logo still wins, and a build with a custom `--logo` ships no light twin and shows its logo in both modes. ponytail: the boot shell has no `TZ` (only the player sets it, [Clock](internals.md#clock)) and the RTC holds UTC, so `LIGHT=2` picks the logo by the UTC hour; a saved zone the hook could read would fix it. To regenerate it from the default:

```sh
magick assets/boot-logo.jpg -negate -colorspace sRGB -type TrueColor -strip -interlace none \
  -sampling-factor 4:4:4 -quality 92 assets/boot-logo-light.jpg
```

## Making a logo that works

- **Orientation: rotate upright artwork 90° clockwise before fitting it.** The stock JPEG is stored sideways because of the framebuffer orientation. The supplied logo therefore reads sideways in the stored JPEG and appears upright on the device. The card also takes an upright 375x320 JPEG and turns it for you.
- **Canvas: exactly 320x375 pixels, black.** `display_logo` does not scale the image. Fit the rotated artwork within both dimensions, preserve its aspect ratio, and centre it horizontally and vertically without cropping. The build rejects any other size, and the card any other than 320x375 or upright 375x320.
- **Format: 8-bit, three-component baseline JPEG (RGB/YCbCr).** No PNG, alpha, grayscale or CMYK. The stock renderer assumes three decoded bytes per pixel; the builder rejects incompatible frame headers.
- **Keep it small.** On the card, at most 512 KB. In a build, JPEG data barely compresses and the repacked rootfs must stay within the stock image size (50,442,240 bytes). A rebuilt Stock rootfs with `assets/boot-logo.jpg` (about 7 KB) leaves about 2.4 MB of slack (iPod about 2.8 MB), so any logo within the card's 512 KB fits; if the build ever fails with `Repacked rootfs exceeds stock size`, re-save at lower quality.

The shared default for both firmware variants is an outlined URW Gothic Book wordmark on black, in warm off-white (#F2F2F0), about 140x72 pixels. The Q keeps its sweeping tail, while the 2 is widened for a balanced base. Below it, widely spaced POD in muted warm grey (#BDBDB8) has a tiny centre button inside the O, a subtle reference to the Q2's click wheel. The group is centred at about 47% of the screen height, because perfect centring looks low. There are no gradients or glows. All lettering is converted to paths, so no fonts are required to render it. Its upright source is `assets/boot-logo.svg` (375x320). To reproduce the firmware JPEG with librsvg and ImageMagick:

```sh
rsvg-convert -w 1500 -h 1280 assets/boot-logo.svg | \
  magick png:- -resize 375x320 \
  -rotate 90 -colorspace sRGB -type TrueColor -strip -interlace none \
  -sampling-factor 4:4:4 -quality 92 assets/boot-logo.jpg
```

To check what landed in the image:

```sh
unsquashfs -cat /tmp/q2-logo/rootfs.squashfs release/assets/default/raw/images/xx/logo.jpg | sha256sum
sha256sum my-logo.jpg
```

## Notes

- Only `rootfs.squashfs` and the kernel are shipped in `update.tar`; the bootloader is untouched. Anything displayed before Linux starts is not affected.
- `manifest.json` records the logo's SHA-256, and identical inputs (including the logo) still produce an identical `update.tar`.
- The splash is only shown while the app starts, so a logo you dislike is cosmetic: remove it from the card, or flash a corrected update to replace a built-in one.
