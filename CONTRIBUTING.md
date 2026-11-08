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

Apps live in their own repos. To list one on the App Store page, open a pull request adding a line to [`sources.list`](sources.list): `owner/repo Name`. The repo needs a release with a zip; see [internals.md](docs/internals.md#q2-shelf).

## Changing the firmware

1. Build and test as in [building.md](docs/building.md). `python3 -m pytest test/unit` and the [type check](docs/building.md#type-check) run on every pull request; run the scripts in `test/` that cover what you changed too.
2. Try it on a Q2 if you can. A `--dev` build installs over any other and starts [`q2remote`](docs/remote.md) for screenshots and input over Wi-Fi.
3. Run `./tools/format.sh`.
4. Add a line for anything a user would notice under the top version in [changelog.md](docs/changelog.md), in plain words: what changed, not how. Don't change `VERSION`.
5. Explain how a fix works, and anything it doesn't cover, in [internals.md](docs/internals.md) and the code.

Commit titles are `area: what changed`, such as `audio: a skip on Bluetooth no longer crackles`. Keep one change per pull request.

### Where new code goes

`patch/navigation.c` is the UI's core: the hooks, input, the shared paint helpers and Now Playing. Don't add a feature to it. Give the feature its own file in `patch/`, list it in `NAVIGATION` in [`tools/build.py`](tools/build.py) and in `tools/format.sh`, and declare what other files use in `patch/navigation.h` under its file's name, as `smart_wifi.c` and `system.c` do. Change navigation.c only where the code you change lives there, and say why in the pull request if you add to it.

A helper another file calls for every row or pixel goes in `navigation.h` as `static inline`, and one that only reads goes there marked `__attribute__((pure))`. Called across files without that, it runs slower, and the paints can outgrow `test/patch.py`'s instruction limit.

## License

Contributions are under the [MIT](LICENSE) license.
