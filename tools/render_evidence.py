"""
Regenerate the evidence images from a real local run (tests + synthetic demo).

    python tools/render_evidence.py
"""
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
DEMO = EVIDENCE / "demo-output"
WRAP = 150
FONT_CANDIDATES = ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                   "/System/Library/Fonts/Menlo.ttc"]
COLORS = {"bg": (24, 26, 33), "bar": (44, 47, 58), "text": (220, 223, 228), "title": (150, 156, 170),
          "ok": (126, 211, 132), "warn": (240, 190, 90), "err": (240, 110, 110), "accent": (110, 180, 245)}


def font(size):
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def line_color(line):
    s = line.strip()
    if "FAIL" in s or "ERROR" in s:
        return COLORS["err"]
    if s.endswith(" ok") or s == "OK" or "drive.example.invalid" in s or "merged/" in s:
        return COLORS["ok"]
    if "permission_denied" in s or "unsupported_file" in s or "Out of scope" in s:
        return COLORS["warn"]
    if s.startswith(("$", "#", "=====")):
        return COLORS["accent"]
    return COLORS["text"]


def render(name, title, text, width=1500):
    lines = []
    for raw in text.rstrip().splitlines():
        lines.extend(textwrap.wrap(raw, WRAP, subsequent_indent="    ") or [""])
    body_font, title_font = font(14), font(14)
    line_height, pad, bar = 20, 18, 34
    image = Image.new("RGB", (width, bar + pad * 2 + line_height * len(lines)), COLORS["bg"])
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, bar], fill=COLORS["bar"])
    for i, color in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        draw.ellipse([14 + i * 20, 11, 26 + i * 20, 23], fill=color)
    draw.text((86, 9), title, font=title_font, fill=COLORS["title"])
    for i, line in enumerate(lines):
        draw.text((pad, bar + pad + i * line_height), line, font=body_font, fill=line_color(line))
    image.save(EVIDENCE / name)
    print(f"wrote evidence/{name}")


def run(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, env=env)
    return done.stdout + done.stderr


def without_run_times(md):
    """Batch rows carry the wall-clock start time of the local run; keep the image about the data."""
    return re.sub(r"\| (\d+) \| \d{4}-\d\d-\d\dT[\d:]+Z \| \d+ \|", r"| \1 | (local run) | 0 |", md)


def main():
    tests = run("-m", "unittest", "discover", "-s", "tests", "-v")
    render("01-unit-tests-passing.png", "local run - POD validator and merger unit tests",
           "$ python -m unittest discover -s tests -v\n" + tests, width=1300)

    run("local_runner/run_demo.py")
    render("02-run1-merge-and-validation.png", "evidence/demo-output/run-1-summary.md (synthetic PODs)",
           without_run_times((DEMO / "run-1-summary.md").read_text(encoding="utf-8")))
    render("03-run2-append-and-replace.png", "evidence/demo-output/run-2-summary.md (same day, later run)",
           without_run_times((DEMO / "run-2-summary.md").read_text(encoding="utf-8")))
    render("04-object-storage-layout.png", "Object Storage layout after both runs (in-memory store, synthetic data)",
           (DEMO / "object-storage-layout.txt").read_text(encoding="utf-8") + "\n# links/2026-01-05.json\n"
           + (DEMO / "links-2026-01-05.json").read_text(encoding="utf-8"), width=1100)


if __name__ == "__main__":
    main()
