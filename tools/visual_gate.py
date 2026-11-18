#!/usr/bin/env python3
"""Visual gate: score one screen (source vs target) against docs/visual-rubric.json.

Pipeline, cheapest first:

    1. perceptual diff pre-filter  (tools/visual_diff.py, 0 tokens)
         obviously different  -> FAIL with heatmap, stop
         obviously identical  -> PASS, stop
         in between           -> continue
    2. structural metrics from the control tree (no model calls)
         block_match, text, position, colour
    3. weighted verdict + hard floors

The metrics are the Design2Code element-matching family (arXiv 2403.03163), with
the control tree standing in for OCR-based block detection: `uitest dumpLayout`
(HarmonyOS JSON) and `uiautomator dump` (Android XML) already give text plus a
bounding box, which is exactly what a "block" is.

Usage:
    python tools/visual_gate.py --android a.png --harmony h.png \
        --android-dump a.json --harmony-dump h.json --out dir/ --json res.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("visual_diff", HERE / "visual_diff.py")
vdf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vdf)          # type: ignore[union-attr]

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


# ── control-tree parsing ─────────────────────────────────────────────────────

def _text_of(attrs: dict) -> str:
    for k in ("text", "content-desc", "contentDescription", "description",
              "accessibilityText", "hint"):
        v = attrs.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _block_from(attrs: dict) -> dict | None:
    m = BOUNDS_RE.search(str(attrs.get("bounds") or ""))
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    text = _text_of(attrs)
    return {"text": text, "bbox": [x1, y1, x2, y2],
            "area": (x2 - x1) * (y2 - y1),
            "cx": (x1 + x2) / 2.0, "cy": (y1 + y2) / 2.0}


def parse_dump(path: Path) -> list[dict]:
    """Blocks from a HarmonyOS dumpLayout JSON or an Android uiautomator XML."""
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    blocks: list[dict] = []
    if raw.startswith("{"):
        data = json.loads(raw)

        def walk(node):
            if isinstance(node, list):
                for v in node:
                    walk(v)
                return
            if not isinstance(node, dict):
                return
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
            if isinstance(attrs, dict):
                b = _block_from(attrs)
                if b:
                    blocks.append(b)
            for k in ("children", "childNodes", "nodes"):
                walk(node.get(k))

        walk(data)
    else:
        root = ET.fromstring(raw)
        for el in root.iter():
            if el.tag.split("}")[-1] != "node":
                continue
            b = _block_from(dict(el.attrib))
            if b:
                blocks.append(b)
    # text-bearing blocks first; boxes with no text still count for block_match
    # A "block" is a text element plus its box (Design2Code detects blocks with a
    # text detector). Container nodes cover the whole screen and would swamp the
    # area-weighted score, so only nodes that actually carry readable text count.
    clean: dict[tuple[str, tuple[int, ...]], dict] = {}
    for b in blocks:
        if not b["text"]:
            continue
        key = (b["text"], tuple(b["bbox"]))
        clean.setdefault(key, b)
    return list(clean.values())


def dice(a: str, b: str) -> float:
    """Character-level Sørensen-Dice, the Design2Code text similarity."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    overlap = sum((ca & cb).values())
    return 2.0 * overlap / (len(a) + len(b))


def match_blocks(a: list[dict], b: list[dict], min_sim: float) -> list[tuple[int, int, float]]:
    """One-to-one matching by text similarity (Hungarian; greedy fallback)."""
    if not a or not b:
        return []
    sim = np.zeros((len(a), len(b)), dtype=np.float64)
    for i, ba in enumerate(a):
        ta = ba["text"].casefold()
        for j, bb in enumerate(b):
            sim[i, j] = dice(ta, bb["text"].casefold()) if (ta or bb["text"]) else 0.0
    try:
        from scipy.optimize import linear_sum_assignment
        rows, cols = linear_sum_assignment(-sim)
        pairs = [(int(r), int(c), float(sim[r, c])) for r, c in zip(rows, cols)]
    except ImportError:                                     # greedy fallback
        pairs, used_r, used_c = [], set(), set()
        for idx in np.dstack(np.unravel_index(np.argsort(-sim, axis=None), sim.shape))[0]:
            r, c = int(idx[0]), int(idx[1])
            if r in used_r or c in used_c or sim[r, c] <= 0:
                continue
            used_r.add(r)
            used_c.add(c)
            pairs.append((r, c, float(sim[r, c])))
    return [p for p in pairs if p[2] >= min_sim]


def block_colour(img, bbox, scale: float) -> np.ndarray | None:
    """Mean CIELab colour inside a block, ignoring near-transparent padding."""
    x1, y1, x2, y2 = (int(v * scale) for v in bbox)
    w, h = img.size
    x1, x2 = max(0, min(x1, w - 1)), max(0, min(x2, w))
    y1, y2 = max(0, min(y1, h - 1)), max(0, min(y2, h))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = np.asarray(img.crop((x1, y1, x2, y2)), dtype=np.float32)
    lab = vdf.srgb_to_lab(crop)
    return lab.reshape(-1, 3).mean(axis=0)


# ── scoring ──────────────────────────────────────────────────────────────────

def load_rubric(path: Path | None) -> dict:
    default = HERE.parent / "docs" / "visual-rubric.json"
    p = path or default
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read rubric {p}: {exc}")


def score_screen(android_png: Path, harmony_png: Path, android_dump: Path | None,
                 harmony_dump: Path | None, rubric: dict, out_dir: Path) -> dict:
    weights = dict(rubric["weights"])
    floors = dict(rubric.get("floors", {}))
    vthr = rubric.get("verdict", {"pass": 0.90, "suspect": 0.75})
    min_sim = float(rubric.get("matching", {}).get("min_text_similarity", 0.5))

    out_dir.mkdir(parents=True, exist_ok=True)
    pixel = vdf.compare_pair(android_png, harmony_png, out_dir)
    result = {
        "screen": pixel["screen"],
        "pre_filter": {k: pixel[k] for k in ("struct_ratio", "color_ratio", "offset_px", "verdict", "heatmap")},
        "metrics": {},
        "missing": [],
        "verdict": None,
        "evidence": {"android": str(android_png), "harmony": str(harmony_png)},
    }

    # The pre-filter may only short-circuit a PASS: a FAIL still deserves the
    # element-level breakdown (which block is missing, how far it moved), and on
    # cross-device captures the pixel signal is noisy enough that it must not be
    # the only thing standing between a screen and "structurally different".
    if pixel["verdict"] == "pass" and not all([android_dump, harmony_dump]):
        result["verdict"] = "pass"
        result["reason"] = "pre-filter: no structural difference"
        result["score"] = round(1.0 - pixel["struct_ratio"], 4)
        return result

    a_img = vdf.load_rgb(android_png)
    h_img = vdf.load_rgb(harmony_png)

    metrics = {"pixel": max(0.0, 1.0 - pixel["struct_ratio"])}
    if android_dump and harmony_dump and Path(android_dump).exists() and Path(harmony_dump).exists():
        blocks_a = parse_dump(Path(android_dump))
        blocks_b = parse_dump(Path(harmony_dump))
        # only what is actually on screen: dumps also list scrolled-out nodes, and
        # those inflate "unmatched area" for whichever side exposes them
        wa0, ha0 = a_img.size
        wb0, hb0 = h_img.size
        blocks_a = [b for b in blocks_a if b["bbox"][1] < ha0 and b["bbox"][0] < wa0]
        blocks_b = [b for b in blocks_b if b["bbox"][1] < hb0 and b["bbox"][0] < wb0]
        pairs = match_blocks(blocks_a, blocks_b, min_sim)
        area_a = sum(b["area"] for b in blocks_a) or 1
        area_b = sum(b["area"] for b in blocks_b) or 1
        matched = sum(min(blocks_a[i]["area"], blocks_b[j]["area"]) for i, j, _ in pairs)

        metrics["block_match"] = matched / max(area_a, area_b)
        metrics["text"] = (sum(s for _, _, s in pairs) / len(pairs)) if pairs else 0.0

        wa, ha = a_img.size
        wb, hb = h_img.size
        pos, cols = [], []
        for i, j, _ in pairs:
            ba, bb = blocks_a[i], blocks_b[j]
            dx = abs(ba["cx"] / wa - bb["cx"] / wb)
            dy = abs(ba["cy"] / ha - bb["cy"] / hb)
            pos.append(1.0 - max(dx, dy))
            ca = block_colour(a_img, ba["bbox"], 1.0)
            cb = block_colour(h_img, bb["bbox"], 1.0)
            if ca is not None and cb is not None:
                de = float(np.linalg.norm(ca - cb))
                cols.append(1.0 - min(de / 100.0, 1.0))
        metrics["position"] = float(np.mean(pos)) if pos else 0.0
        metrics["color"] = float(np.mean(cols)) if cols else 0.0
        result["blocks"] = {"android": len(blocks_a), "harmony": len(blocks_b), "matched": len(pairs)}
    else:
        result["missing"].append("control-tree dumps (structure scored on pixels only)")
        weights.pop("block_match", None)
        weights.pop("text", None)
        weights.pop("position", None)

    total_w = sum(weights[k] for k in metrics if k in weights) or 1.0
    score = sum(weights[k] * v for k, v in metrics.items() if k in weights) / total_w
    result["metrics"] = {k: round(v, 4) for k, v in metrics.items()}
    result["score"] = round(score, 4)

    breach = [f"{k}={metrics[k]:.3f} < {floors[k]}" for k in floors if k in metrics and metrics[k] < floors[k]]
    if breach:
        result["verdict"] = "fail"
        result["reason"] = "floor: " + "; ".join(breach)
    elif score >= vthr["pass"]:
        result["verdict"] = "pass"
        result["reason"] = "weighted score"
    elif score >= vthr["suspect"]:
        result["verdict"] = "suspect"
        result["reason"] = "weighted score"
    else:
        result["verdict"] = "fail"
        result["reason"] = "weighted score"
    return result


def to_markdown(results: list[dict], rubric: dict) -> str:
    lines = ["# Visual gate report", "",
             f"weights: {json.dumps(rubric['weights'])}", "",
             "| screen | verdict | score | block_match | text | position | color | pixel | reason |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m = r["metrics"]
        f = lambda k: f"{m[k]:.3f}" if k in m else "—"
        lines.append(f"| {r['screen']} | **{r['verdict']}** | {r.get('score', 0):.3f} | "
                     f"{f('block_match')} | {f('text')} | {f('position')} | {f('color')} | {f('pixel')} | "
                     f"{r.get('reason', '')} |")
    return "\n".join(lines) + "\n"


def batch(directory: Path, rubric: dict, out_dir: Path) -> list[dict]:
    """Score every `<Screen>_android.png` / `_harmony.png` pair in a directory."""
    results = []
    for a in sorted(directory.glob("*_android.png")):
        name = a.name[: -len("_android.png")]
        h = directory / f"{name}_harmony.png"
        if not h.exists():
            continue
        ad = directory / f"{name}_android.json"
        hd = directory / f"{name}_harmony.json"
        results.append(score_screen(a, h, ad if ad.exists() else None,
                                    hd if hd.exists() else None, rubric, out_dir))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Visual gate (pre-filter + Design2Code-style metrics)")
    ap.add_argument("--android")
    ap.add_argument("--harmony")
    ap.add_argument("--android-dump")
    ap.add_argument("--harmony-dump")
    ap.add_argument("--dir", help="batch: directory of <Screen>_android.png / _harmony.png pairs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--json")
    ap.add_argument("--rubric")
    args = ap.parse_args(argv)

    rubric = load_rubric(Path(args.rubric) if args.rubric else None)
    out_dir = Path(args.out)

    if args.dir:
        results = batch(Path(args.dir), rubric, out_dir)
        if not results:
            print(json.dumps({"error": "no screenshot pairs found"}))
            return 2
    else:
        if not (args.android and args.harmony):
            ap.error("give --dir, or --android and --harmony")
        results = [score_screen(Path(args.android), Path(args.harmony),
                                Path(args.android_dump) if args.android_dump else None,
                                Path(args.harmony_dump) if args.harmony_dump else None,
                                rubric, out_dir)]

    payload = {
        "summary": {
            "screens": len(results),
            "pass": sum(1 for r in results if r["verdict"] == "pass"),
            "suspect": sum(1 for r in results if r["verdict"] == "suspect"),
            "fail": sum(1 for r in results if r["verdict"] == "fail"),
        },
        "rubric": {"weights": rubric["weights"], "floors": rubric.get("floors", {})},
        "screens": results,
    }
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "visual_gate.md").write_text(to_markdown(results, rubric), encoding="utf-8")
    for r in results:
        print(f"{r['verdict']:7s} {r['screen'][:28]:30s} score={r.get('score', 0):.3f} "
              f"bm={r['metrics'].get('block_match', float('nan')):.3f} "
              f"px={r['metrics'].get('pixel', 0):.3f} {r.get('reason', '')}")
    print(json.dumps(payload["summary"]))
    return 0 if payload["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
