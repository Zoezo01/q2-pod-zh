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
7. Run every suite in [building.md](building.md#tests). The fixtures stand in for stock, so a stock list that gained a row needs its fixture to gain one too.
8. `check` must report nothing. Add the new functions that hold our sites to the stock map, then run `sitenotes.py` so the notes describe the new build, and `sitenotes.py --check` to confirm it.
9. Bring `docs/internals.md` up to what stock does now.

## What the tool cannot see

Each of these left a wrong value in place on the V1.34 rebase, and only a test or a sweep found it.

- **An address written with eight digits** (`0x005b7e80`) reads as an instruction word and is never carried. Write addresses unpadded.
- **A low half inside a replacement word.** A word we write that loads through a stock data address (`lw $2, 0x1e68($2)`) carries that address's low half, which moves. `docs/rebasing/memsweep.py` lists such words; derive them from the stock word at build time where possible.
- **Position-dependent words we write**: branches, `bal`, `j` and `addiu` off a non-stack base. `docs/rebasing/wordsweep.py`.
- **GOT loads left as they were** whose slot now names something else. `docs/rebasing/gpsweep.py`.
- **Arithmetic in tests**: a GOT page plus a low half, an import's GOT offset, the register a value sits in at a hook, a page literal held in a frame. The suites find these; nothing else does.
- **A literal the tool judged no address** stays as on `main`. `docs/rebasing/samesweep.py` lists them for a read.
- **A literal given to the wrong binary.** `demo` and `hciplayer` both have code at the same values, and a literal in prose with only weak evidence can be carried with the other binary's map. `docs/rebasing/weaksweep.py` lists the candidates; write the binary's name just before such a literal (`hciplayer's 0x42d9bc`), which decides between binaries that have only weak evidence for it.
- **A function's tail** can be lost when its start changed although the offsets inside it did not. `map.lookup` says "code changed"; the old offset in the new function is the first place to look.
- **A short run of identical words in two functions** can carry an address into the wrong one. Step 4 is the guard.

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

- Static functions change order between builds: align by callers and by address-forming code, never by position.
- The `$gp` set-up is not an address, and GOT pages are not function starts.
- Stock regroups its settings lists. A hook that counts stock's rows fails safe (it leaves the page as built) only if the count is checked first.
- A new stock option can duplicate a Q2 Pod system outright: V1.34 read a headset's keys on a thread of its own, and each press would have acted twice.
- `mutmut` ties a test to a function only when the function runs in that test, set-up included. Cached results in `mutants/` survive a test change: delete the folder for a fresh score.
