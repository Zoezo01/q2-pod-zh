## Scope

Q2 Pod's base is a music player. Something that isn't about playing music is a card app in its own repo, listed in [`sources.list`](sources.list), not firmware code.

The firmware never names one card app: no test on an app's id, no file or variable named for it, no reading its settings. What an app needs from the firmware is a `q2app` key or a word in `/tmp/q2-addon.caps` that any app can use ([Card apps](docs/internals.md#card-apps)).

## Where code goes

Don't add features to `patch/navigation.c`: give each its own file, as CONTRIBUTING.md's [Where new code goes](CONTRIBUTING.md#where-new-code-goes) says. Add to navigation.c only where the code being changed already lives, and say why.

## Themes

Classic keeps its look: don't restyle its screens in place. A different look for a theme's own screens (Quick Settings, the slider and message pages, prompts, Set Date and Set Time, the hold menu) is a second set of painters that another theme's `theme.xml` selects, with Classic drawing exactly as before; `test/tracecmp.py` on two `Q2_TRACE` runs of `test/patch.py` shows whether it does. New UI is flat in every theme: no gloss, gradients or glass.

## Pull requests

[CONTRIBUTING.md](CONTRIBUTING.md) has the steps. What gets a pull request sent back:

- **Bigger than its feature.** Reuse the parser, helper or page that's there: no new tab, editor, helper process or CI step where a file or a line does it.
- **More than its change.** No rewrapped comments or reformatted code you didn't change, and no workflow or instruction-file edits riding along. A `sources.list` pull request adds its one app and leaves the other lines alone.
- **Testing notes in the repo.** "Not yet tried on a Q2" goes in the pull request's description. Docs and comments say what the code does.
- **A stale base.** Rebase onto main before asking for review.

CI doesn't run `test/patch.py`, which needs a stock ZIP. It pins the App Store's rows, the painters and stock's offsets, so after changing `sources.list`, a painter, a row's label or `patch/offsets.inc`, run `tools/check.sh --full`, or say in the description that you couldn't.

Listing an app: its release zip packs `.<id>/q2-<id>`, the id being the repo's name less `q2-`, in lower case; each release has its own version tag (a fixed tag such as `latest` never reads as an update); and the pull request adds the README's row with the line.

## Reviewing a pull request

Check the claim against main before the code: is the bug real today, and has main fixed it since? Then merge what is small and right, fixing it up after; send back, with its must-fixes, what is substantial and over-built; leave open what only a Q2 can judge. To the maintainer, name a pull request by what it does, not by its number, and when unsure, ask before merging.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:

- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
