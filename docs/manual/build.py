#!/usr/bin/env python3
"""Build Q2-Pod-User-Guide.pdf from guide.html with headless Chromium, and
the .epub and .md beside it with pandoc.

Two passes: the first finds which page each heading lands on (via hidden
@@id@@ markers read back with pdftotext), the second fills in the contents.
"""
import html, re, shutil, subprocess, sys, tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
src = (here / "guide.html").read_text()
chrome = next(filter(None, map(shutil.which, ["chromium", "google-chrome-stable", "google-chrome"])), None)
if not chrome:
    sys.exit("chromium/google-chrome not found")
pandoc = shutil.which("pandoc") or sys.exit("pandoc not found")

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

# The EPUB and Markdown: the text without the print cover, contents and page numbers.
# ponytail: the "at a glance" diagram is CSS-drawn, so there it is a line of labels.
flow = re.sub(r'<section class="(cover|toc)">.*?</section>|<div class="num">\d+</div>', "", src, flags=re.S)
subprocess.run([pandoc, "-f", "html-native_divs", "--toc", "--split-level=1", f"--resource-path={here}",
                "-o", str(out.with_suffix(".epub"))], input=flow, check=True, capture_output=True, text=True)
# The Markdown takes its pictures from GitHub at this commit, so the file reads anywhere, and its links
# go to GitHub's anchors, made from a heading's text.
rev = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
md = subprocess.run([pandoc, "-f", "html-native_divs", "-t", "gfm-raw_html"], check=True, capture_output=True, text=True,
                    input=flow.replace('src="../../', f'src="https://raw.githubusercontent.com/DiamondBond/q2-pod/{rev}/')).stdout
anchors = {i: re.sub(r"[^\w\- ]", "", html.unescape(re.sub(r"<[^>]+>", "", t)).lower()).replace(" ", "-")
           for i, t in re.findall(r'<h\d[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\d>', src, flags=re.S)}
out.with_suffix(".md").write_text(re.sub(r"\]\(#([^)]+)\)", lambda m: f"](#{anchors.get(m[1], m[1])})", md))
print(f"wrote {out.with_suffix('.epub')} and {out.with_suffix('.md')}")
