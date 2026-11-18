#!/usr/bin/env python3
"""Perceptual-diff pre-filter for the visual gate.

Cheap (0-token) comparison between a source screenshot and a target screenshot
of the *same* screen. Purpose: decide which screens deserve expensive judgement
(structural metrics / VLM) and which are obviously fine.

Two scores per screen, because "different" has two very different meanings:

    structure  per-tile SSIM on luminance. Insensitive to a global brightness
               or contrast shift, sensitive to layout, missing content, size
               and position changes.
    colour     per-tile mean colour distance in CIE Lab. Catches theme
               regressions (dark mode left on, brand colour changed) that SSIM
               deliberately ignores.

Verdicts are three-way so a suspicious screen can escalate instead of dropping
straight into FAIL:

    PASS      both ratios under the suspect thresholds -> skip everything expensive
    SUSPECT   over suspect, under fail                  -> structural metrics + VLM
    FAIL      over fail thresholds                      -> report with heatmap

Playwright's screenshot assertions (`threshold`, `maxDiffPixelRatio`, `mask`)
are the reference for the parameter model; the two-signal split is ours,
because a colour-cast regression must not be hidden by luminance normalisation.

Usage:
    python tools/visual_diff.py --android a.png --harmony h.png --out dir/
    python tools/visual_diff.py --dir <ui_align/screenshots> --out dir/ --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

STATUS_BAR = 0.05     # top fraction masked (clock, signal, battery)
NAV_BAR = 0.04        # bottom fraction masked (gesture bar)
TARGET_W = 720        # both sides rescaled to this width before comparison
GRID = (12, 24)       # tiles across x down
SSIM_TILE_MIN = 0.80  # a tile below this counts as structurally changed
LAB_TILE_MAX = 8.0    # a tile above this mean Lab distance counts as recoloured
SUSPECT_STRUCT = 0.03
SUSPECT_COLOR = 0.06
FAIL_STRUCT = 0.10
FAIL_COLOR = 0.15


def load_rgb(path: Path) -> Image.Image:
    """Open as RGB, compositing alpha onto white (HAP screenshots are RGBA)."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def normalise(im: Image.Image, width: int = TARGET_W) -> Image.Image:
    if im.width == width:
        return im
    return im.resize((width, max(1, round(im.height * width / im.width))), Image.BILINEAR)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb float 0..255 (...,3) -> CIELAB (...,3). Vectorised."""
    c = rgb / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = (c @ m.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = np.clip(116 * f[..., 1] - 16, 0, 100)
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def tile_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """SSIM on a single luminance tile (both float, same shape)."""
    c1, c2 = 6.5025, 58.5225        # (0.01*255)^2, (0.03*255)^2
    mu_a, mu_b = a.mean(), b.mean()
    va, vb = a.var(), b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2)
    return float(num / den) if den else 1.0


def _masked_row(r: int, th: int, top: int, bottom: int) -> bool:
    y0, y1 = r * th, (r + 1) * th
    return y1 <= top or y0 >= bottom


def best_shift(ga: np.ndarray, gb: np.ndarray, max_frac: float = 0.03) -> int:
    """Vertical offset (px) that best aligns the two luminance profiles.

    Cross-device captures never share a pixel grid: status bars, density and
    safe areas shift the whole content by a few pixels. A global translation is
    not a visual defect, so it is measured and removed before tiling; whatever
    remains is real layout difference. The offset itself is reported.
    """
    h = ga.shape[0]
    maxs = max(1, int(h * max_frac))
    ra = ga.mean(axis=1)
    rb = gb.mean(axis=1)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    den = ra.std() * rb.std() + 1e-9
    best_s, best_c = 0, -2.0
    for s in range(-maxs, maxs + 1):
        c = float((ra * np.roll(rb, s)).mean() / den)
        if c > best_c:
            best_s, best_c = s, c
    return best_s


def compare_arrays(a: Image.Image, b: Image.Image):
    """Returns (struct_ratio, color_ratio, struct_map, color_map, offset)."""
    h = min(a.height, b.height)
    a = a.crop((0, 0, a.width, h))
    b = b.crop((0, 0, b.width, h))
    aa = np.asarray(a, dtype=np.float32)
    bb = np.asarray(b, dtype=np.float32)

    ga, gb = aa.mean(axis=2), bb.mean(axis=2)
    offset = best_shift(ga, gb)
    if offset:
        # align target onto source; the wrapped band is discarded via masking
        bb = np.roll(bb, offset, axis=0)
        ga, gb = aa.mean(axis=2), bb.mean(axis=2)

    top, bottom = int(h * STATUS_BAR), h - int(h * NAV_BAR)
    if offset > 0:
        top = max(top, offset)
    elif offset < 0:
        bottom = min(bottom, h + offset)
    la, lb = srgb_to_lab(aa), srgb_to_lab(bb)

    cols, rows = GRID
    th, tw = h // rows, a.width // cols
    struct_map = np.zeros((rows, cols), dtype=bool)
    color_map = np.zeros((rows, cols), dtype=bool)
    live = 0

    for r in range(rows):
        if _masked_row(r, th, top, bottom):
            continue
        y0, y1 = r * th, (r + 1) * th
        for c in range(cols):
            x0, x1 = c * tw, (c + 1) * tw
            live += 1
            s = tile_ssim(ga[y0:y1, x0:x1], gb[y0:y1, x0:x1])
            d = float(np.linalg.norm(la[y0:y1, x0:x1].reshape(-1, 3).mean(0)
                                     - lb[y0:y1, x0:x1].reshape(-1, 3).mean(0)))
            struct_map[r, c] = s < SSIM_TILE_MIN
            color_map[r, c] = d > LAB_TILE_MAX

    live = max(1, live)
    return (float(struct_map.sum() / live), float(color_map.sum() / live),
            struct_map, color_map, int(offset))


def heatmap(a: Image.Image, struct_map: np.ndarray, color_map: np.ndarray, out_png: Path) -> None:
    """Red = structural change, amber = colour-only change."""
    base = a.convert("RGBA")
    rows, cols = struct_map.shape
    th, tw = base.height // rows, base.width // cols
    arr = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            if not (struct_map[r, c] or color_map[r, c]):
                continue
            arr[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = (
                (255, 40, 40, 120) if struct_map[r, c] else (255, 176, 0, 120))
    Image.alpha_composite(base, Image.fromarray(arr, "RGBA")).convert("RGB").save(out_png)


def verdict_for(struct_ratio: float, color_ratio: float) -> str:
    if struct_ratio >= FAIL_STRUCT or color_ratio >= FAIL_COLOR:
        return "fail"
    if struct_ratio >= SUSPECT_STRUCT or color_ratio >= SUSPECT_COLOR:
        return "suspect"
    return "pass"


def compare_pair(android: Path, harmony: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    a = normalise(load_rgb(android))
    b = normalise(load_rgb(harmony))
    struct_ratio, color_ratio, s_map, c_map, offset = compare_arrays(a, b)
    name = android.stem[: -len("_android")] if android.stem.endswith("_android") else android.stem
    heat = out_dir / f"{name}_diff.png"
    heatmap(a, s_map, c_map, heat)
    return {
        "screen": name,
        "android": str(android),
        "harmony": str(harmony),
        "android_size": list(load_rgb(android).size),
        "harmony_size": list(load_rgb(harmony).size),
        "tiles": {"grid": list(GRID), "struct_changed": int(s_map.sum()), "color_changed": int(c_map.sum())},
        "struct_ratio": round(struct_ratio, 4),
        "color_ratio": round(color_ratio, 4),
        "offset_px": int(offset),
        "verdict": verdict_for(struct_ratio, color_ratio),
        "heatmap": str(heat),
    }


def discover(directory: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for a in sorted(directory.glob("*_android.png")):
        h = directory / f"{a.name[: -len('_android.png')]}_harmony.png"
        if h.exists():
            pairs.append((a, h))
    return pairs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Perceptual-diff pre-filter (PASS / SUSPECT / FAIL)")
    ap.add_argument("--android")
    ap.add_argument("--harmony")
    ap.add_argument("--dir", help="directory of <Screen>_android.png / _harmony.png pairs")
    ap.add_argument("--out", required=True, help="directory for heatmaps")
    ap.add_argument("--json", help="write the full result here")
    args = ap.parse_args(argv)

    pairs = discover(Path(args.dir)) if args.dir else (
        [(Path(args.android), Path(args.harmony))] if args.android and args.harmony else [])
    if not pairs:
        ap.error("give --dir, or both --android and --harmony")

    results = [compare_pair(a, h, Path(args.out)) for a, h in pairs]
    payload = {
        "summary": {
            "pairs": len(results),
            "pass": sum(1 for r in results if r["verdict"] == "pass"),
            "suspect": sum(1 for r in results if r["verdict"] == "suspect"),
            "fail": sum(1 for r in results if r["verdict"] == "fail"),
        },
        "thresholds": {
            "struct_tile_ssim_min": SSIM_TILE_MIN, "color_tile_lab_max": LAB_TILE_MAX,
            "suspect": [SUSPECT_STRUCT, SUSPECT_COLOR], "fail": [FAIL_STRUCT, FAIL_COLOR],
        },
        "screens": results,
    }
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in results:
        print(f"{r['verdict']:7s} {r['screen'][:30]:32s} struct={r['struct_ratio']:.3f} "
              f"color={r['color_ratio']:.3f} -> {r['heatmap']}")
    print(json.dumps(payload["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
