# Standard release procedure

To prepare a release, bump `VERSION` in `tools/build.py` and add a `- **<version>**` entry to the top of `docs/changelog.md`, then commit both, since packaging refuses uncommitted build inputs. Packaging turns that entry into the release notes (the bold version heading, then one bullet a sentence) and fails without it. Keep the proprietary stock ZIP local. Use the Python environment with `requirements.txt`
installed for these commands:

```sh
python3 tools/release.py package 'Q2 Firmware V1.32.zip' --out /tmp/q2-release
python3 test/release.py
# Create or update the release with both validated update tars and verify remote bytes.
python3 tools/release.py upload /tmp/q2-release
# Same, then publish after verification.
python3 tools/release.py upload /tmp/q2-release --publish
```

Packaging also rebuilds the user guide from `docs/manual/guide.html` (needs Chromium and `pdftotext`), and upload attaches it to every release, since the README links `releases/latest/download/Q2-Pod-User-Guide.pdf`.

Packaging requires a fresh output directory, builds the update twice, runs the shared MIPS
suite and asset checks on the first build, and compares the two builds' update.tar bytes. It writes
update.tar, the manifest, SHA256SUMS, release notes and source revision/hash. A single direct build is never a release input. Upload re-checks the packaged bytes and source hash
and refuses stale, missing or changed artifacts and published releases. An upload/download
failure leaves the release unpublished; rerun upload to repair the draft. Packaging uses no
GitHub credentials or network.
