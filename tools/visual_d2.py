#!/usr/bin/env python3
"""visual_d2 — app-authored style drift (D2).

Two judgements, both needed: uniformity alone lets a whole class drift without a trace.

  1. intra-page  (no reference needed)
     same role, same page: one element breaks the style its page sets
     (ScreenProof: "local style fault" / arXiv 2611.10234)

  2. cross-platform  (needs the Android original)
     the whole HarmonyOS distribution of a role against the Android one.
     If most elements of a role moved the same way, that is ONE finding about
     the theme, not N findings about N buttons ("hard negatives: change every
     sibling alike").

Thresholds are measured, never invented:

  eps  = p95 of |delta| between two captures of the same untouched screen, per channel
  z    = |x - median| / max(MAD, eps)     flag when |delta| > eps AND z > 3.5

Nothing here uses a VLM: ScreenProof measured 18-62% false alarms on clean real
screenshots for frontier judges, so a model is not a gate.

Channels measured from the screenshot + dump geometry:
  fill    dominant colour inside the element (mode, not mean: text would poison the mean)
  text    colour of the dark pixels (only for elements that carry text)
  radius  corner radius, normalised by the shorter side
  height  element height / screen height (font-size proxy for text elements)

Usage:
    python tools/visual_d2.py --android-xml a.xml --android-png a.png \
        --hmos-json h.json --hmos-png h.jpeg [--android-png2 a2.png --hmos-png2 h2.jpeg]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visual_d1 as d1  # loaders, matching, lab conversion

CHANNELS = ("fill", "text", "radius", "height")

# Not every channel makes sense for every role: a label has no background of its own
# (its crop is the parent's fill), and an image has no text colour.
CHANNEL_MASK: dict[str, tuple[str, ...]] = {
    "label": ("text", "height"),
    "clock": ("text", "height"),
    "icon_button": ("radius", "height"),
    "image": ("radius", "height"),
    "video": ("radius",),
    "web": ("radius",),
    "divider": ("fill",),
}
DEFAULT_MASK = CHANNELS


def channels_for(role: str) -> tuple[str, ...]:
    return CHANNEL_MASK.get(role, DEFAULT_MASK)

# only used when no second capture is supplied; recorded as source="unmeasured"
DEFAULT_EPS = {"fill": 2.5, "text": 2.5, "radius": 0.02, "height": 0.004}
FLOOR_EPS = {"fill": 0.5, "text": 0.5, "radius": 0.004, "height": 0.001}
# measurement resolution: the MAD floor, so a flat group (MAD=0) still has a scale.
# One pixel of a ~200px element is 0.005; one 8-bit level is 1.0 dE.
RES_FLOOR = {"fill": 1.0, "text": 1.0, "radius": 0.005, "height": 0.001}
Z_K = 3.5          # Iglewicz-Hoaglin constant for MAD-based z
MIN_GROUP = 5      # below this a within-page median is not trustworthy


# ── measurement ─────────────────────────────────────────────────────────────
def crop(img: np.ndarray, rect, inset: float = 0.0) -> np.ndarray | None:
    x, y, w, h = rect
    H, W = img.shape[:2]
    dx, dy = int(w * inset), int(h * inset)
    x0, y0 = max(0, int(x) + dx), max(0, int(y) + dy)
    x1, y1 = min(W, int(x + w) - dx), min(H, int(y + h) - dy)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    return img[y0:y1, x0:x1, :3]


def dominant(c: np.ndarray) -> np.ndarray:
    q = (c.reshape(-1, 3) // 8 * 8).astype(np.int16)
    vals, counts = np.unique(q, axis=0, return_counts=True)
    return vals[counts.argmax()].astype(np.float32)


def text_colour(c: np.ndarray) -> np.ndarray | None:
    flat = c.reshape(-1, 3).astype(np.float32)
    lum = flat @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    dark = flat[lum <= np.percentile(lum, 25)]
    return dark.mean(axis=0) if dark.size else None


def corner_radius(c: np.ndarray, fill: np.ndarray, tol: float = 14.0) -> float:
    """How far down the top row the fill only reaches: 0 = square, r/min_side = rounded."""
    H, W = c.shape[:2]
    lim = min(H, W) // 2
    for row in range(max(1, lim)):
        line = c[row].astype(np.float32)
        d = np.sqrt(((line - fill) ** 2).sum(axis=1))
        idx = np.where(d < tol)[0]
        if idx.size and idx[0] <= 1:
            return row / float(min(H, W))
    return 0.5


def signature_full(img: np.ndarray, u: dict) -> dict:
    c = crop(img, u["rect"], inset=0.10)          # inset: colour, free of border AA
    c_edge = crop(img, u["rect"], inset=0.0)      # edge: the corner arc lives here
    out: dict[str, object] = {"height": u["rect"][3] / img.shape[0]}
    if c is None or c_edge is None:
        return out
    fill = dominant(c)
    lab = d1.lab(tuple(float(x) for x in fill))
    out["fill"] = lab
    out["radius"] = corner_radius(c_edge, fill)
    if u["text"]:
        flat = c.reshape(-1, 3).astype(np.float32)
        lum = flat @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        if float(lum.std()) > 15.0:          # a flat fill has no text to measure
            tc = text_colour(c)
            if tc is not None:
                out["text"] = d1.lab(tuple(float(x) for x in tc))
    return out


def delta(ch: str, a, b) -> float | None:
    if a is None or b is None:
        return None
    if ch in ("fill", "text"):
        return float(np.sqrt(sum((x - y) ** 2 for x, y in zip(a, b))))  # CIE76
    return abs(float(a) - float(b))


# ── noise floor ─────────────────────────────────────────────────────────────
def static_check(png1, png2, label: str) -> bool:
    """A noise floor is only valid if the two captures really are the same screen."""
    if png1.shape != png2.shape:
        print(f"  {label}: capture size changed {png1.shape} vs {png2.shape} — eps NOT measured")
        return False
    diff = float(np.abs(png1 - png2).mean())
    if diff > 5.0:
        print(f"  {label}: screen was not static between captures (mean|diff|={diff:.1f}) "
              f"— screen off / page changed? eps NOT measured")
        return False
    return True


def measure_eps(units: list[dict], png1, png2) -> dict[str, float]:
    """Same screen captured twice, nothing touched: that is the noise floor."""
    deltas: dict[str, list[float]] = {c: [] for c in CHANNELS}
    for u in units:
        s1 = signature_full(png1, u)
        s2 = signature_full(png2, u)
        for ch in CHANNELS:
            d = delta(ch, s1.get(ch), s2.get(ch))
            if d is not None:
                deltas[ch].append(d)
    eps = {}
    for ch in CHANNELS:
        vals = deltas[ch]
        eps[ch] = float(np.percentile(vals, 95)) if vals else DEFAULT_EPS[ch]
        eps[ch] = max(eps[ch], FLOOR_EPS[ch])
    return eps


# ── judgements ──────────────────────────────────────────────────────────────
def robust(series: list[float]) -> tuple[float, float]:
    med = statistics.median(series)
    mad = statistics.median([abs(x - med) for x in series])
    return med, mad


def level1(side: str, units: list[dict], sigs: dict[int, dict], eps) -> list[dict]:
    """Within one page: who does not look like its siblings."""
    out = []
    groups: dict[str, list[tuple[dict, dict]]] = {}
    for i, u in enumerate(units):
        groups.setdefault(u["role"], []).append((u, sigs[i]))
    for role, members in groups.items():
        if len(members) < MIN_GROUP:
            continue
        for ch in channels_for(role):
            vals = [s.get(ch) for _, s in members if s.get(ch) is not None]
            if len(vals) < MIN_GROUP:
                continue
            if ch in ("fill", "text"):
                # use the L* component only for the scalar statistics
                series = [v[0] for v in vals]
            else:
                series = [float(v) for v in vals]
            med, mad = robust(series)
            for (u, s) in members:
                v = s.get(ch)
                if v is None:
                    continue
                x = v[0] if ch in ("fill", "text") else float(v)
                d = abs(x - med)
                z = d / max(mad, RES_FLOOR[ch])
                if d > eps[ch] and z > Z_K:
                    out.append({"level": "intra_page", "side": side, "role": role,
                                "channel": ch, "text": u["text"][:32],
                                "value": round(x, 3), "median": round(med, 3),
                                "mad": round(mad, 4), "z": round(z, 2),
                                "eps": round(eps[ch], 4),
                                "verdict": "isolated_style_fault"})
    return out


def level3(pairs, sig_a, sig_h, eps) -> list[dict]:
    """Whole-class drift vs the Android original: one finding per role+channel."""
    out = []
    role_deltas: dict[tuple[str, str], list[float]] = {}
    for k, (au, hu, _) in enumerate(pairs):
        for ch in channels_for(au["role"]):
            d = delta(ch, sig_a[k].get(ch), sig_h[k].get(ch))
            if d is None:
                continue
            a_val = sig_a[k].get(ch)
            h_val = sig_h[k].get(ch)
            sign = 1.0
            if ch in ("fill", "text"):
                sign = 1.0 if h_val[0] > a_val[0] else -1.0
            else:
                sign = 1.0 if float(h_val) > float(a_val) else -1.0
            role_deltas.setdefault((au["role"], ch), []).append(d * sign)

    for (role, ch), ds in role_deltas.items():
        if len(ds) < 3:
            continue
        over = [d for d in ds if abs(d) > eps[ch]]
        same_dir = sum(1 for d in over if d > 0)
        agree = max(same_dir, len(over) - same_dir) / len(over) if over else 0.0
        if len(over) >= max(3, int(0.8 * len(ds))) and agree >= 0.8:
            med_d = statistics.median(ds)
            # text height is confounded by the platform font metrics (Roboto vs
            # HarmonyOS Sans): report it as something to verify, not as a defect
            verdict = ("typography_check" if ch == "height"
                       else "class_style_drift")
            out.append({"level": "cross_platform", "role": role, "channel": ch,
                        "n": len(ds), "n_over_eps": len(over),
                        "median_delta": round(med_d, 3), "eps": round(eps[ch], 4),
                        "agreement": round(agree, 2),
                        "verdict": verdict})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--android-xml", required=True)
    ap.add_argument("--android-png", required=True)
    ap.add_argument("--android-png2", default="")
    ap.add_argument("--hmos-json", required=True)
    ap.add_argument("--hmos-png", required=True)
    ap.add_argument("--hmos-png2", default="")
    ap.add_argument("--map", default="tools/role_map.yaml")
    ap.add_argument("--bundle", default="com.simplemobiletools.gallery.pro")
    ap.add_argument("--top-mask", type=float, default=0.055)
    ap.add_argument("--pos-tol", type=float, default=0.06)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    d1.load_map(a.map)
    apng = np.asarray(Image.open(a.android_png).convert("RGB"), dtype=np.float32)
    hpng = np.asarray(Image.open(a.hmos_png).convert("RGB"), dtype=np.float32)
    a_size = (apng.shape[1], apng.shape[0])
    h_size = (hpng.shape[1], hpng.shape[0])

    au = d1.dedupe(d1.load_android(Path(a.android_xml)))
    hu = d1.dedupe(d1.load_hmos(Path(a.hmos_json), a.bundle))
    for units, size, mask in ((au, a_size, a.top_mask), (hu, h_size, a.top_mask)):
        cut = size[1] * mask
        units[:] = [u for u in units if u["rect"][1] + u["rect"][3] > cut]

    # D2 only looks at roles the app owns
    au = [u for u in au if u["check_style"]]
    hu = [u for u in hu if u["check_style"]]
    print(f"app-authored units: android={len(au)}  hmos={len(hu)}")

    eps = dict(DEFAULT_EPS)
    src = {c: "unmeasured" for c in CHANNELS}
    if a.android_png2:
        apng2 = np.asarray(Image.open(a.android_png2).convert("RGB"), dtype=np.float32)
        if static_check(apng, apng2, "android"):
            e1 = measure_eps(au, apng, apng2)
            for c in CHANNELS:
                eps[c] = max(FLOOR_EPS[c], e1[c])
                src[c] = "measured(android)"
    if a.hmos_png2:
        hpng2 = np.asarray(Image.open(a.hmos_png2).convert("RGB"), dtype=np.float32)
        if static_check(hpng, hpng2, "hmos"):
            e2 = measure_eps(hu, hpng, hpng2)
            both = src["fill"].startswith("measured")
            for c in CHANNELS:
                eps[c] = max(eps[c], e2[c])
                src[c] = "measured(both)" if both else "measured(hmos)"
    print("noise floor (eps):", {c: round(eps[c], 4) for c in CHANNELS}, "source:", src)

    sig_a = {i: signature_full(apng, u) for i, u in enumerate(au)}
    sig_h = {i: signature_full(hpng, u) for i, u in enumerate(hu)}

    findings = []
    findings += level1("android", au, sig_a, eps)
    findings += level1("hmos", hu, sig_h, eps)

    pairs, _, _ = d1.match(au, hu, a_size, h_size, a.pos_tol)
    a_idx = {id(u): i for i, u in enumerate(au)}
    h_idx = {id(u): i for i, u in enumerate(hu)}
    findings += level3(pairs,
                       [sig_a[a_idx[id(p[0])]] for p in pairs],
                       [sig_h[h_idx[id(p[1])]] for p in pairs],
                       eps)

    print(f"\n{'level':15s} {'role':10s} {'channel':7s} {'what':34s} detail")
    for f in findings:
        what = f.get("text", f"n={f.get('n')} over_eps={f.get('n_over_eps')}")
        detail = (f"value={f.get('value')} median={f.get('median')} z={f.get('z')}"
                  if f["level"] == "intra_page" else
                  f"median_delta={f.get('median_delta')} agree={f.get('agreement')}")
        print(f"{f['level']:15s} {f['role']:10s} {f['channel']:7s} {str(what)[:34]:34s} "
              f"{detail}  -> {f['verdict']}")

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
    print(f"\nsummary: {counts or 'no style findings'}")
    print(f"eps: { {c: round(eps[c], 4) for c in CHANNELS} }")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"eps": eps, "eps_source": src, "counts": counts, "findings": findings},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {a.json}")


if __name__ == "__main__":
    main()
