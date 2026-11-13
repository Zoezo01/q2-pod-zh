# Standard release procedure

To prepare a release, bump `VERSION` in `tools/build.py` (and set `BETA` there to the beta number for a pre-release, 0 for the release itself) and add a `- **<version>**` entry to the top of `docs/changelog.md`, then commit both, since packaging refuses uncommitted build inputs. Packaging turns that entry into the release notes (the bold version heading, then one bullet a sentence) and fails without it, or when it isn't the newest entry. Keep the proprietary stock ZIP local. Use the Python environment with `requirements.txt`
installed for these commands:

```sh
python3 tools/release.py package 'Q2 Firmware V1.36.zip' --out /tmp/q2-release
python3 test/release.py
# Create or update the release with both validated update tars and verify remote bytes.
python3 tools/release.py upload /tmp/q2-release
# Same, then publish after verification.
python3 tools/release.py upload /tmp/q2-release --publish
```

Packaging also rebuilds the user guide from `docs/manual/guide.html` (needs Chromium and `pdftotext`), and upload attaches it to every release, since the README links `releases/latest/download/Q2-Pod-User-Guide.pdf`.

Packaging requires a fresh output directory, builds the update twice, runs the shared MIPS
suite and asset checks on the first build, and compares the two builds' update.tar bytes. The checks
are twenty suites, each a process of its own, all started at once: they take as long as the longest,
the MIPS suite (about 3.5 minutes), and about 3.4 GB of memory between them. It writes
update.tar, the manifest, SHA256SUMS, release notes and source revision/hash. A single direct build is never a release input. Upload sends the release notes as packaged (to fix them afterwards, use `gh release edit --notes-file`), re-checks them, the packaged bytes and the source hash
and refuses stale, missing or changed artifacts and published releases. An upload/download
failure leaves the release unpublished; rerun upload to repair the draft. Packaging uses no
GitHub credentials or network.

## Betas, and the release after them

A beta keeps the release's `VERSION` and so its tag. `upload --publish` refuses one (`BETA` is not 0), so a beta goes out by hand, and the flag matters: without `--prerelease` it becomes Latest, which Network Update offers to everyone.

```sh
python3 tools/release.py upload /tmp/q2-release
gh release edit 1.8.0 --draft=false --prerelease
```

Before the next beta, or the release itself, delete the published one together with its tag:

```sh
gh release delete 1.8.0 --yes --cleanup-tag
```

GitHub's web page deletes the release and leaves the tag, and GitHub then keeps that tag on the beta's commit whatever `--target` says. Upload refuses a tag that is on another commit than the packaged one, so a tag left behind stops it there. For the release itself set `BETA = 0`, package into a fresh directory and `upload --publish`.
