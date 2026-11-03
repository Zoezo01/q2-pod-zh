#!/usr/bin/env python3
"""Build Q2-Pod-User-Guide.pdf from guide.html with headless Chromium.

Two passes: the first finds which page each heading lands on (via hidden
@@id@@ markers read back with pdftotext), the second fills in the contents.
"""
import re, shutil, subprocess, sys, tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
src = (here / "guide.html").read_text()
chrome = next(filter(None, map(shutil.which, ["chromium", "google-chrome-stable", "google-chrome"])), None)
if not chrome:
    sys.exit("chromium/google-chrome not found")

def render(html, out):
    tmp = here / ".build.html"  # same dir so ../../assets paths resolve
    tmp.write_text(html)
    try:
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", tmp.as_uri()], check=True, capture_output=True)
    finally:
        tmp.unlink()

marked = re.sub(r'(<h[12][^>]*\bid="([^"]+)"[^>]*>)', r'\1<i class="m">@@\2@@</i>', src)
with tempfile.TemporaryDirectory() as d:
    first = Path(d) / "pass1.pdf"
    render(re.sub(r"\{\{p:[^}]+\}\}", "00", marked), first)
    text = subprocess.run(["pdftotext", str(first), "-"], check=True, capture_output=True, text=True).stdout
pages = {m: i + 1 for i, t in enumerate(text.split("\f")) for m in re.findall(r"@@([^@\s]+)@@", t)}
missing = set(re.findall(r"\{\{p:([^}]+)\}\}", src)) - pages.keys()
if missing:
    sys.exit(f"headings not found in PDF: {sorted(missing)}")
out = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "Q2-Pod-User-Guide.pdf"
render(re.sub(r"\{\{p:([^}]+)\}\}", lambda m: str(pages[m[1]]), src), out)
print(f"wrote {out}")
