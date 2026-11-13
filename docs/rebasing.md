# Rebasing onto a new stock firmware

Q2 Pod patches Shanling's `demo`, `hciplayer` and `bluealsa` at fixed addresses, each with the stock word it expects. A new stock release moves nearly all of them. `tools/rebase.py` carries what it can prove; this is the routine for the rest.

The stock ZIPs are untrusted input and Shanling's copyright. Run everything that reads them with `python3 -I`, extract outside the repo, and commit nothing of theirs but addresses, instruction words and hashes. Decompiler output stays outside the repo too.

## The tool

```sh
python3 -I tools/rebase.py report OLD.zip NEW.zip   # what Shanling changed, and which of our addresses sit in it
python3 -I tools/rebase.py apply OLD.zip NEW.zip    # rewrite addresses, expected words and pins in patch/, tools/, test/, docs/
python3 -I tools/rebase.py check OLD.zip NEW.zip    # fail while an address is still on OLD's layout
python3 -I tools/rebase.py refs ZIP BINARY WHAT...  # where code forms an address, calls a function or loads a string
```

It aligns the two `.text` streams between exported symbols on words with their address operands masked, follows calls to re-find functions the alignment lost, and lets each aligned address-forming pair vote for where its data went. A literal in our files is rewritten only on evidence that it is an address of one binary: the stock word beside it, a name, a function start, data the code names.

Two tracked tables go with it, both carried by `apply`:

- `tools/stock_map.tsv`: our names for stock's unnamed functions, with a line on what each does. A name goes in only on evidence (a string it loads, what it calls, how its caller uses it). `report` and `refs` print these names.
- `tools/stock_sites.tsv`: for every stock code address `patch/` and `tools/` name, the function it is in and how to find it again: the call it is, or the instruction, with the last string loaded before it and the next call after it, and what of ours uses it. `docs/rebasing/sitenotes.py` writes it. A row `apply` cannot carry keeps its old address, and `report` lists it under "Patch sites to find by hand".

## The routine

1. Work in a new worktree on a branch from `main`. Save `report`'s output: the rootfs files that changed, new config keys and strings, the functions that changed, and our addresses inside them.
2. Run `apply` and commit its output alone, so the hand work reads apart from it.
3. Build. Each failing check names a site. Find it by its row in `tools/stock_sites.tsv` and `refs` on the string or callee the row names, then disassemble the old and new function side by side. Put the reason for any change beside the literal.
4. Read every site that sits in a function Shanling changed against the new code, passing check or not. A word can match while its meaning moved: a row index, a table's length, a register that now holds something else, a count of children.
5. Run the sweeps below for what the tool cannot see.
6. Decide each new stock option by the rule in use: shown where our Settings order puts it, unless it conflicts with a Q2 Pod system; then held in RAM at the value that behaves as before, its row not offered, its key never written.
7. Check which of our patches stock made unnecessary. A fix of ours inside a function Shanling changed may now be stock's own: read the new function, and where it does the job, drop the hook with its test and its docs in a commit of its own.
8. Run every suite in [building.md](building.md#tests). The fixtures stand in for stock, so a stock list that gained a row needs its fixture to gain one too.
9. `check` must report nothing. Add the new functions that hold our sites to the stock map, then run `sitenotes.py` so the notes describe the new build, and `sitenotes.py --check` to confirm it.
10. Bring `docs/internals.md` up to what stock does now.

## Finding a site again

Start from the site's row in `tools/stock_sites.tsv`, which names the function and what the site is in it. `refs` then finds that in the new build:

- **From a string to the function that uses it:** `refs NEW.zip demo scroll_view_playset` lists each load of a string holding the text, with the function it is in. Widget and page names, config keys and log lines all work.
- **From a callee to its call sites:** `refs NEW.zip demo 0x4f2cac` lists every call of the function at that address, direct or through its GOT slot, and every place its address is formed (a handler being bound). A row that says "the 2nd of 3 calls to" a function is the second of these inside its own function.
- **From a page's init to its row and click handlers:** the init is the function that loads the page's widget names. The handlers are the addresses it passes to `widget_on` and `widget_foreach`, which the decompiler's export lists in the function's header; `refs` on a handler's address leads back to the init that binds it.

A function that kept its name in the stock map is found by the name: `report` and `refs` print it, and `apply` moves its row.

## What the tool cannot see

Each of these left a wrong value in place on the V1.34 or the V1.36 rebase, and only a test or a sweep found it.

- **An address written with eight digits** (`0x005b7e80`) reads as an instruction word and is never carried. Write addresses unpadded.
- **A low half inside a replacement word.** A word we write that loads through a stock data address (`lw $2, 0x1e68($2)`) carries that address's low half, which moves. `docs/rebasing/memsweep.py` lists such words; derive them from the stock word at build time where possible.
- **Position-dependent words we write**: branches, `bal`, `j` and `addiu` off a non-stack base. `docs/rebasing/wordsweep.py`.
- **GOT loads left as they were** whose slot now names something else. `docs/rebasing/gpsweep.py`. The three loads Q2 Pod writes (of `tk_strncpy`, `navigator_close` and `dummy_destroy`) are checked by the build since V1.36, where two of them had come to name other functions' slots.
- **A string's low half inside a replacement word** (`addiu $a0, $s1, 0x6404`): every string moved in V1.36, and `wordsweep.py` passes over an `addiu` on one register. Make such a word from the string's address (`0x26240000 | 0x786994 & 0xffff`), which `apply` carries as data the code names.
- **A return address or a site with nothing beside it** (`#define VIEW_QUEUE_RET 0x53acf8`) is a bare literal, listed by `report` and never rewritten. Name its function in the line's comment and it is carried.
- **A file offset** (`0x3852bd`, a byte of `demo` the build patches by position) is no address. `test/build.py` finds it.
- **Arithmetic in tests**: a GOT page plus a low half, an import's GOT offset, the register a value sits in at a hook, a page literal held in a frame. The suites find these; nothing else does.
- **A literal the tool judged no address** stays as on `main`. `docs/rebasing/samesweep.py` lists them for a read.
- **A colour that is also an address.** `apply` rewrote two colours in V1.36 (`0x8cc4f8` in a gradient's stops, `0xa238ff` in a test) because stock's data has a symbol at those values. Read `apply`'s diff for literals outside comments on lines that hold colours.
- **A literal given to the wrong binary.** `demo` and `hciplayer` both have code at the same values, and a literal in prose with only weak evidence can be carried with the other binary's map. `docs/rebasing/weaksweep.py` lists the candidates; write the binary's name just before such a literal (`hciplayer's 0x42d9bc`), which decides between binaries that have only weak evidence for it.
- **A function's tail** can be lost when its start changed although the offsets inside it did not. `map.lookup` says "code changed"; the old offset in the new function is the first place to look.
- **A static function that moved whole** past a neighbour that changed reads as rewritten too (V1.36's Settings row click, now ahead of the row builder). Find it by a string it loads (`refs`) and compare it with the old one before re-deriving anything.
- **A short run of identical words in two functions** can carry an address into the wrong one. Step 4 is the guard.
- **A stock handler that changed with no patch site in it** (V1.34's Shut Down prompt Centre and dark volume, #303; V1.36's Centre on Now Playing). `test/patch.py`'s `stock_keyups` runs stock's key-up handlers on both builds; a failure there means stock's behaviour changed, not that an address moved.
- **A dated statement that names the stock version.** `apply` renames the version wherever it stands, so an audit dated before the new release came out as an audit of it. Read the lines `apply` changed that hold a date, and put the version the work was done on back.

Beyond addresses, read the UI pages a build makes against the last build's (decode both with `tools/ipod.py` and diff): stock moves boxes and renames styles in assets, and a theme that leaves a widget alone inherits the move.

## Decompiling

Ghidra 11 headless, with the scripts in `docs/rebasing/`:

- The stock binaries are built `-mfp64`. Ghidra's `MIPS:LE:32:default` has 32-bit FP registers and stops at each such instruction, ending the function there. Copy `mips32le.slaspec` to `mips32le_fp64.slaspec` with `@define FREGSIZE "8"`, add a `<language>` for it to `mips.ldefs` (id `MIPS:LE:32:fp64`), compile it with `support/sleigh`, and import with `-processor MIPS:LE:32:fp64`.
- `Options.py` (pre-script) turns off the analyser that guesses which functions never return: it guessed `mp_msg`, and every caller lost its code after the call.
- `Functions.py` (post-script, `demo`) makes a function at every PIC prologue analysis left without one. `demo`'s handlers are reached only by address.
- `ExportDecomp.py LIST OUTDIR [NAMES.tsv]` decompiles the function holding each listed address, with its callers, callees and strings in a header.

```sh
analyzeHeadless PROJ demo -import release/bin/demo -processor MIPS:LE:32:fp64 -scriptPath docs/rebasing -preScript Options.py
analyzeHeadless PROJ demo -process demo -noanalysis -scriptPath docs/rebasing -postScript Functions.py
analyzeHeadless PROJ demo -process demo -noanalysis -scriptPath docs/rebasing -postScript ExportDecomp.py LIST OUTDIR
```

`analyzeHeadless` needs `MAXMEM=4G` for `demo`.

## What V1.32 to V1.34 taught

- What moved: every GOT offset (imports by one constant, the rest unevenly), the low halves of `hciplayer`'s `.bss` (`0x1000` up), the order of static functions, and the page inits with the row builders and click handlers after them, which is where the new options went.
- What stayed: `bluealsa`, the shared libraries `demo` and `hciplayer` link and the kernel modules the experimental flags patch were byte for byte the same, 99.8% of `hciplayer`'s code and 98.8% of `demo`'s aligned, and an exported function that only moved kept every word once its address operands were masked.
- Static functions change order between builds: align by callers and by address-forming code, never by position.
- The `$gp` set-up is not an address, and GOT pages are not function starts.
- Stock regroups its settings lists. A hook that counts stock's rows fails safe (it leaves the page as built) only if the count is checked first.
- A new stock option can duplicate a Q2 Pod system outright: V1.34 read a headset's keys on a thread of its own, and each press would have acted twice.
- `mutmut` ties a test to a function only when the function runs in that test, set-up included. Cached results in `mutants/` survive a test change: delete the folder for a fresh score.

## What V1.34 to V1.36 taught

- A small release still moved everything in `demo`: every string by `0x590` to `0x610`, `.data` and `.bss` by `0x610`, the imports' GOT slots by three, the local ones unevenly. `hciplayer`, `bluealsa`, the libraries and the modules the experimental flags patch were byte for byte the same, so nothing of theirs was touched.
- Stock can drop what a patch stood on. V1.36 has no 20,000-song limit to raise, so the patch that raised it went, and Q2 Pod's own limit with it; and it took the navigation bar's resize off one page, so that entry left `navbar_resize_calls`.
- Stock can rework a subsystem Q2 Pod reshapes. V1.36 gave most rows' labels AWTK's seamless loop through one helper; one dropped call there keeps every label's pass as Q2 Pod's hooks expect it. Decide such a change as a new stock option is decided: taken, or held at what behaved as before.
- An arithmetic in a test (a GOT page less a low half, `GP` less an import's offset) is not carried. Write the address whole and `apply` carries it.

## Writing hooks that survive

GOT offsets move in every build, so an expected word of the form `lw reg, off($gp)` changes where the code did not; `apply` rewrites such a word itself and leaves to the build's checks only the words that really changed. A mid-function word patch still has to be found again whenever its function is rewritten. When a site has to be re-derived anyway, and an entry hook on an exported function or a call site named by its callee would do the same job at equal cost, prefer that, and say so in the site's note. Sites that still map are left as they are.
