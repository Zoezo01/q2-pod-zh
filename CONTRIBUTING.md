# Contributing

Thanks for helping. Bug reports, fixes and new apps are all welcome.

## Reporting a bug

Open an issue with:

- the **CFW. Version** row from **System settings → About**
- what you did, what you expected and what happened
- the output in use (wired, Bluetooth with the codec, or USB), for sound problems
- `q2-crash.log` from the card's root, if there is one, and `q2-crash.log.1` and `.2` beside it (the two before it)

Ideas go in a feature request issue or on Discord. Security problems go through [SECURITY.md](SECURITY.md), not an issue.

## Adding an app

Apps live in their own repos. To list one on the App Store page, open a pull request adding a line to [`sources.list`](sources.list): `owner/repo Name`. A game goes under `[Games]`, any other app above it. The repo needs a release with a zip; see [internals.md](docs/internals.md#q2-shelf).

## Changing the firmware

1. Build and test as in [building.md](docs/building.md). `python3 -m pytest test/unit` and the [type check](docs/building.md#type-check) run on every pull request; run the scripts in `test/` that cover what you changed too. `tools/check.sh` runs what CI does that needs no stock ZIP; `tools/check.sh --full` adds a `--dev` build and every suite that reads one, in `dist/dev-<hash>`: a folder per state of the build's sources, reused while they're unchanged whatever the commit, which you delete by hand.
2. Try it on a Q2 if you can. A `--dev` build installs over any other and starts [`q2remote`](docs/remote.md) for screenshots and input over Wi-Fi. Without a Q2, [q2-emulation](https://github.com/lampsonlnguyen/q2-emulation) is a desktop simulator that opens an `update.tar` and a card folder: it previews the build's assets, settings and card apps and runs card games, and it doesn't boot the firmware itself.
3. Run `./tools/format.sh`. It formats every tracked C file; CI fails a pull request whose C it would change (`tools/format.sh --check`), with clang-format 23 (`pip install clang-format==23.1.5`).
4. Add a line for anything a user would notice under the top version in [changelog.md](docs/changelog.md), in plain words: what changed, not how. Don't change `VERSION`.
5. Explain how a fix works, and anything it doesn't cover, in [internals.md](docs/internals.md) and the code.

A comment or doc line starting `ponytail:` marks a deliberate shortcut: it names the limit (a case left out, a cost, a guess about stock) and what would lift it, such as `ponytail: the touch is polled per tick, so a tap shorter than a tick is missed; ending it on the pointer's own down event would catch every one`. Write one wherever you leave such a limit, so it can be found again.

Commit titles are `area: what changed`, such as `audio: a skip on Bluetooth no longer crackles`. Keep one change per pull request.

### Where new code goes

`patch/navigation.c` is the UI's core: the hooks, input, the shared paint helpers and Now Playing. Its sections are listed at its top, each under a banner of asterisks. Don't add a feature to it. Give the feature its own file in `patch/`, list it in `NAVIGATION` in [`tools/build.py`](tools/build.py), and declare what other files use in `patch/navigation.h` under its file's name, as `smart_wifi.c` and `system.c` do. Change navigation.c only where the code you change lives there, and say why in the pull request if you add to it.

Per-script display code (which face draws which characters, a script's line-break or capital rule) goes in `patch/script.c`, and the fonts' ranges and letters in its half of `tools/build.py`: see Scripts under [Themes](docs/internals.md#themes).

A helper another file calls for every row or pixel goes in `navigation.h` as `static inline`. Mark one `__attribute__((pure))` only if it writes nothing, not even a lazy first load (`accent()` reads the settings on its first call), always returns, and reads no `volatile` memory or state another thread writes: clang may keep one call for several, across a change it can't see. Called across files without that, it runs slower, and the paints can outgrow `test/patch.py`'s instruction limit.

## License

Contributions are under the [GNU GPL v3](LICENSE) (GPL-3.0-only), the project's licence. By opening a pull request you license your change under the same terms as the file it changes.
