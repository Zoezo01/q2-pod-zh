# q2remote: drive a development Q2 over Wi-Fi

`q2remote` gives a developer, or an AI agent working on the project, a root shell, screenshots and input on a Q2 over the local network, so a change can be tried and checked without touching the device. It ships **only in `--dev` builds** ([building.md](building.md)): `tools/build.py --dev` compiles `patch/remote.c` into `/usr/bin/q2remote` and starts it from `S90play` after dbus; release builds don't contain it, and `test/build.py` checks that.

It listens on TCP port 2302 and sends the key and every command **unencrypted**. Use it only on a network you trust, such as your own phone's hotspot, and never leave a key on a card you lend or sell.

## Setup

1. Install a `--dev` update on the Q2 and join it to the same Wi-Fi network as your PC.
2. Put a key on the card: 16-126 characters from `A-Z a-z 0-9 _ -`, one line. With the Q2 connected over USB storage:

   ```sh
   openssl rand -hex 16 > /run/media/$USER/<card>/q2remote.key
   ```

3. **Eject, then unplug the cable or restart the Q2.** While the card is shared over USB storage the Q2 unmounts it, so a new key is read only once the cable is out, or at the next boot, before USB storage takes the card. q2remote keeps a copy in `/mnt/data/q2remote.key` and uses it while the card is away, so a cable in, even from boot, doesn't lock you out.
4. Find the Q2's address: `python3 tools/q2remote.py find` tries every address of the /24 your PC's default route uses, without sending the key, and lists those where q2remote answers, or use the hotspot's or router's client list.
5. Load the key into the environment without echoing it:

   ```sh
   export Q2_HOST=10.0.0.42
   export Q2_KEY="$(cat path/to/q2remote.key)"
   python3 tools/q2remote.py sh id        # uid=0(root) gid=0(root)
   ```

   If you keep a copy of the key on the workstation, a temporary location such as
   `$XDG_RUNTIME_DIR/q2remote.key` is a good fit: it may be cleared at logout or reboot.
   Instead of `Q2_KEY`, `Q2_KEY_FILE` can name the file, and without either the client reads
   `q2remote.key` from the repository root, which git ignores.

Each connection rereads `/mnt/mmc/q2remote.key` while the card is mounted, so replacing the file rotates the key and deleting it revokes access, the internal copy included. A change made over USB storage applies once the cable is out.

## Commands

| Command                        | Does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sh [--timeout S] 'COMMAND'`   | Runs `COMMAND` with busybox `sh` as root, printing stdout and stderr together, and exits with its status. After `S` seconds without output (default 30) the client gives up and names the process group still running it; Ctrl-C kills it.                                                                                                                                                                                                                                                                                                   |
| `shot [--settle] OUT.png`      | Saves the displayed framebuffer as an upright 375×320 PNG, and warns if the screen is off. `--settle` first waits, up to 5 s, until the screen stops changing.                                                                                                                                                                                                                                                                                                                                                                               |
| `state`                        | Prints demo's pid, the uptime, and from demo's memory the backlight, USB, key lock, lock screen and volume bytes.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `wake`                         | Lights the screen with Centre (twice with **Wake: Double Press**) only if it is off, and checks it came on.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `key NAME [MS]`                | Presses a button for `MS` ms (default 80).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `wheel N [--step-ms MS]`       | Turns the wheel `N` ticks: positive is down/next, negative up/back. A sample every `MS` ms (default 20); 0 sends the whole turn in one write.                                                                                                                                                                                                                                                                                                                                                                                                |
| `tap X Y`                      | Taps the screen at upright screenshot coordinates (0-374, 0-319).                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `swipe X1 Y1 X2 Y2 [MS]`       | Drags from one point to the other over `MS` ms (default 300).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `push LOCAL REMOTE`            | Copies a file to the Q2 through `REMOTE.part`, synced, and checks its MD5. Refuses `/mnt/mmc` while USB storage has the card.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `pull REMOTE LOCAL`            | Copies a file from the Q2.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `install UPDATE.TAR`           | Replaces the card's `update.tar`, checks it as read back from the card, and names the menu that flashes it.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `log [LINES\|-f\|--clear]`     | Prints the last `LINES` (default 100) of demo's output, follows it, or empties it.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `events N [S]`                 | Prints raw events from `/dev/input/eventN` for `S` seconds (default 5).                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `devices`                      | Lists the input devices and their key and axis codes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `find`                         | Lists the addresses on this network where q2remote answers.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `prof S [--all] [--build DIR]` | Samples demo's main thread (`--all`: every thread, at 20 Hz) at 100 Hz for `S` seconds through `q2remote --prof`, which stops each thread for an instant with ptrace, and prints the most frequent stacks by state (running, blocked in D, sleeping). `--build` (or `Q2_BUILD`) names the installed build's directory for demo's and the payload's symbols; libraries are pulled once into `~/.cache/q2-prof`. Callers past `$ra` come from a scan of the stack and may include a stale word. `Q2_PROF_BIN` runs another copy of the helper. |
| `reboot [--wait]`              | Syncs and reboots into the saved boot target; q2remote answers again once the Q2 rejoins Wi-Fi, which can take a few minutes. `--wait` returns once it does.                                                                                                                                                                                                                                                                                                                                                                                 |

## Input

| Control        | How                                                                | Device                                                    |
| -------------- | ------------------------------------------------------------------ | --------------------------------------------------------- |
| Centre         | `key centre`; hold 5 s (`key centre 5000`) for the shutdown prompt | `md-gpio-keys`, `KEY_POWER` 116                           |
| Return         | `key return`                                                       | `md-gpio-keys`, `KEY_UP` 103                              |
| Play/Pause     | `key play`                                                         | `md-gpio-keys`, `KEY_DOWN` 108                            |
| Next, Previous | `key next`, `key prev`                                             | `md-gpio-keys`, 163, 165                                  |
| Wheel          | `wheel N`                                                          | `hyn_sliderbar`: x 1-200 a turn, read through tslib       |
| Touch          | `tap X Y`, `swipe X1 Y1 X2 Y2`                                     | `hyn_ts`: the portrait panel's axes, which `tap` converts |

All of it is injected by writing input events to `/dev/input/eventN`, so the firmware sees exactly what the hardware would send and `demo` needs no change. `wheel` reads the saved **Wheel Sensitivity** from `/mnt/data/config.ini`, so one tick is one tick of the firmware at any setting ([internals.md](internals.md), the wheel). The PMIC's 10-15 s Centre reset is wired in hardware and can't be injected; use `reboot`.

## Working with it

- Take a `shot --settle` after every action and look at it before the next step.
- If input seems to do nothing, the screen may be off: `shot` warns then, and `wake` lights it. While it's dark the wheel changes the volume. Don't wake it with `key centre`: on a lit screen that clicks the selected row.
- Quick Settings opens with `swipe 187 5 187 220` and closes with a swipe up from the bottom edge, `swipe 187 310 187 0`.
- With a cable in, the Q2 shows **USB Connected** and the wheel changes the volume. `key return` ends USB storage and the Q2 mounts its card again; eject it on the PC first.
- With **Wake: Double Press** on, a dark Q2 needs two Centre presses to wake.
- A tap hides the wheel's selection, and the next tick only shows it again without moving.
- Busybox has no `timeout`, `pkill` or `killall -r`. Background a command and `kill $!` it. Redirect a job you leave running in the background (`>/dev/null 2>&1 &`): its output has nowhere to go once `sh` returns.
- Settings: `/mnt/data/config.ini`; the card is at `/mnt/mmc`. A changed demo pid in `state` means demo restarted.

## Log

`--dev` builds start demo with its output, and that of the hciplayer it starts, appended to `/tmp/q2-demo.log` instead of the serial console; release builds are unchanged. The file is in RAM: once a minute it is emptied if it has grown past 4 MB, and it is gone after a reboot. demo's stdout is block-buffered once it goes to a file, so its lines arrive in batches rather than as they are printed; stderr arrives at once.

## Troubleshooting

- **`authentication rejected`:** the key differs from the card's file, the file is malformed, or the key is new and the USB cable is still connected (setup step 3).
- **`cannot reach Q2` / `No route to host`:** the Q2 isn't on the network yet (after a boot or a reboot it can take a few minutes), or is in Rockbox, which has no q2remote. Make sure the PC is on the same network and not still on wired Ethernet.
- **`no md-gpio-keys input device`:** run `devices`; the firmware may name its devices differently.

## Protocol

The client sends the key, a newline, the command, a newline, then any stdin. `sh` sends its script as that stdin and runs it under `setsid`, recording its pid in `/tmp/q2remote-*.pid` so Ctrl-C can kill its process group, and ends the output with `\n\0q2rc=STATUS\n`, where the client stops reading: a job left in the background doesn't hold the reply. The script isn't exported, so it may be any size. The helper answers `OK\n` and then streams the command's output until it exits, or answers `ERR\n` and closes. A handshake has to arrive within 10 seconds. `q2remote --shot` writes `Q2FB\0`, then the width, height, stride and size as little-endian 32-bit integers, then the displayed page; the client has it gzipped on the Q2, then rotates and encodes it. `state` reads demo's byte globals with `dd` from `/proc/<pid>/mem` at stock V1.32's addresses (`GLOBALS` in `tools/q2remote.py`). `python3 test/remote.py` builds the helper for the host and checks authentication, rotation, revocation, the internal copy, framing and the input encoders.
