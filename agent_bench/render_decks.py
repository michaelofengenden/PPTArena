#!/usr/bin/env python3
"""
Render decks to slide images on a machine other than the one that judges.

Rendering is the heavy, LibreOffice-bound part of judging. This script has no
dependency on the rest of the repo, so it can be copied to any Linux box that has
LibreOffice (`soffice`) and poppler (`pdftoppm`), run there, and its output copied
back into the judge's render cache:

    in/gt/<case_slug>.pptx                  ->  out/<case_slug>/gt/slide-N.png
    in/pred/<agent_id>/<case_slug>.pptx     ->  out/<case_slug>/pred_<agent_id>/slide-N.png

Every output folder gets a `.rendered` marker, also when LibreOffice could not open
the deck and no image was produced. Then judge with:

    python3 agent_bench/judge_predictions.py ... --render-cache out --no-local-render

Ground truth and prediction must come from the same render machine, so that fonts
and LibreOffice version are the same on both sides of every comparison.

Usage:
    python3 render_decks.py IN_DIR OUT_DIR [--jobs 8]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def render(deck: Path, out_dir: Path) -> tuple[Path, int, str]:
    """Deck -> PDF with LibreOffice, PDF -> one PNG per slide with pdftoppm."""
    if (out_dir / ".rendered").exists():
        return deck, len(list(out_dir.glob("slide-*.png"))), "already rendered"
    out_dir.mkdir(parents=True, exist_ok=True)
    note = ""
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "profile"
        try:
            subprocess.run(
                ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--nologo",
                 "--nodefault", "--norestore", "--convert-to", "pdf", str(deck), "--outdir", tmp],
                check=True, capture_output=True, timeout=600,
            )
            pdf = Path(tmp) / (deck.stem + ".pdf")
            if pdf.exists():
                subprocess.run(["pdftoppm", "-png", str(pdf), str(out_dir / "slide")],
                               check=True, capture_output=True, timeout=600)
            else:
                note = "LibreOffice produced no PDF"
        except subprocess.TimeoutExpired:
            note = "timed out"
        except subprocess.CalledProcessError as exc:
            note = f"failed: {(exc.stderr or b'').decode(errors='replace').strip()[-120:]}"
    (out_dir / ".rendered").write_text(note, encoding="utf-8")
    return deck, len(list(out_dir.glob("slide-*.png"))), note


def main() -> None:
    parser = argparse.ArgumentParser(description="Render decks to per-slide PNGs for the judge's render cache.")
    parser.add_argument("in_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--jobs", type=int, default=8, help="LibreOffice runs at once (default: 8).")
    args = parser.parse_args()
    for tool in ("soffice", "pdftoppm"):
        if not shutil.which(tool):
            parser.error(f"{tool} not found on PATH.")

    tasks = [(deck, args.out_dir / deck.stem / "gt") for deck in sorted((args.in_dir / "gt").glob("*.pptx"))]
    tasks += [(deck, args.out_dir / deck.stem / f"pred_{deck.parent.name}")
              for deck in sorted((args.in_dir / "pred").glob("*/*.pptx"))]
    print(f"{len(tasks)} deck(s) to render with {args.jobs} job(s)", flush=True)
    empty = 0
    with ThreadPoolExecutor(args.jobs) as pool:
        for deck, slides, note in pool.map(lambda task: render(*task), tasks):
            empty += slides == 0
            if slides == 0 or note not in ("", "already rendered"):
                print(f"  {deck.parent.name}/{deck.name}: {slides} slide(s) {note}", flush=True)
    print(f"RENDER DONE: {len(tasks)} deck(s), {empty} with no images", flush=True)


if __name__ == "__main__":
    main()
