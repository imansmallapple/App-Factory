#!/usr/bin/env python3
"""visual_d1 — element-level Android vs HarmonyOS compare with a role map.

Implements the D1/D2/D3 split in the simplest form that works:

    D3  structure : element exists / position  (always checked)
    D1  convention: role marked check_style=false -> appearance is NOT compared
                    (only existence, position and state are)
    D2  style     : role marked check_style=true  -> appearance compared (CIE76 dE)

Matching is per-role greedy nearest-centre in normalised coordinates, so the two
sides can be different resolutions (1080x2400 vs 1280x2832 here).

Usage:
    python tools/visual_d1.py --android-xml a.xml --android-png a.png \
        --hmos-json h.json --hmos-png h.jpeg [--json out.json] [--map role_map.yaml]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# ── role map ────────────────────────────────────────────────────────────────
# android class / hmos type -> (role, check_style)
# check_style=False is D1: the platform owns the appearance, so differences there
# are convention, not defects.
DEFAULT_MAP: dict[str, tuple[str, bool]] = {
    # Android classes
    "Switch": ("toggle", False),
    "SwitchCompat": ("toggle", False),
    "CheckBox": ("checkbox", False),
    "RadioButton": ("radio", False),
    "EditText": ("input", False),
    "SeekBar": ("slider", False),
    "ProgressBar": ("progress", False),
    "Button": ("button", True),
    "MaterialButton": ("button", True),
    "AppCompatButton": ("button", True),
    "TextView": ("label", True),
    "AppCompatTextView": ("label", True),
    "ImageView": ("image", True),
    "ImageButton": ("image", True),
    # HarmonyOS types
    "Toggle": ("toggle", False),
    "Checkbox": ("checkbox", False),
    "Radio": ("radio", False),
    "TextInput": ("input", False),
    "Search": ("input", False),
    "Slider": ("slider", False),
    "Progress": ("progress", False),
    "Button": ("button", True),
    "Text": ("label", True),
    "Image": ("image", True),
    "SymbolGlyph": ("icon", True),
    "TextClock": ("label", True),
}

# pure layout nodes: dropped unless they carry text / id / clickability
DECOR = {"root", "WindowScene", "Column", "Row", "Flex", "Stack", "Grid", "GridItem",
         "RelativeContainer", "Blank", "__Common__", "metaballNode", "Scroll",
         "List", "ListItem", "Swiper", "Tabs", "TabsItem", "Navigation", "NavDestination"}


ROLE_A: dict[str, tuple[str, bool]] = {}
ROLE_H: dict[str, tuple[str, bool]] = {}
ROLE_HVIA: dict[str, str] = {}   # role -> how HarmonyOS realises it
ROLE_AVIA: dict[str, str] = {}   # role -> how Android realises it


def load_map(path: str) -> None:
    """Load tools/role_map.yaml (built by tools/build_role_map.py).

    The yaml also knows which components have no counterpart at all: those are
    deliberately absent from ROLE_* so they surface as missing/extra instead of
    being silently treated as 'unknown'.
    """
    global ROLE_A, ROLE_H
    if not path:
        ROLE_A = dict(DEFAULT_MAP)
        ROLE_H = dict(DEFAULT_MAP)
        return
    import yaml
    m = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    roles = m["roles"]
    ROLE_A = {cls: (r, bool(roles[r]["check_style"])) for cls, r in m["android"].items()}
    ROLE_H = {cls: (r, bool(roles[r]["check_style"])) for cls, r in m["hmos"].items()}
    ROLE_HVIA.update({r: v.get("hmos_via", "component") for r, v in roles.items()})
    ROLE_AVIA.update({r: v.get("android_via", "component") for r, v in roles.items()})
    print(f"role map: {path}  android {len(ROLE_A)} classes  hmos {len(ROLE_H)} components  "
          f"roles {len(roles)}")


def rect_of(s) -> tuple[int, int, int, int] | None:
    m = BOUNDS.search(str(s or ""))
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2 - x1, y2 - y1


def lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    def f(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.00000
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def g_(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = g_(x), g_(y), g_(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def mean_lab(img: np.ndarray, box) -> tuple[float, float, float] | None:
    x, y, w, h = box
    H, W = img.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = img[y0:y1, x0:x1, :3].reshape(-1, 3).mean(axis=0)
    return lab(tuple(crop))


# ── loaders ─────────────────────────────────────────────────────────────────
def load_android(xml_path: Path) -> list[dict]:
    root = ET.parse(xml_path).getroot()
    units = []
    for n in root.iter():
        r = rect_of(n.get("bounds"))
        if not r:
            continue
        cls = (n.get("class") or "").split(".")[-1]
        text = ((n.get("text") or "") + " " + (n.get("content-desc") or "")).strip()
        click = n.get("clickable") == "true"
        role, check_style = (ROLE_A or DEFAULT_MAP).get(cls, ("unknown", True))
        if role in ("unknown", "container") and not (text or click):
            continue                      # bare container: derived from leaves, never aligned
        if role == "container":
            continue                      # structural only (D3 blocks later), not an alignable unit
        units.append({"side": "android", "cls": cls, "role": role,
                      "check_style": check_style, "text": text, "rect": r,
                      "click": click, "checked": n.get("checked") == "true"})
    return units


def load_hmos(json_path: Path, bundle: str = "") -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    units = []
    dropped_system = 0

    def walk(node):
        nonlocal dropped_system
        a = node.get("attributes") or {}
        t = (a.get("type") or "").strip()
        r = rect_of(a.get("bounds"))
        text = (str(a.get("text") or "") + " " + str(a.get("description") or "")).strip()
        click = str(a.get("clickable")).lower() == "true"
        owner = str(a.get("bundleName") or "")
        if owner and bundle and owner != bundle:
            dropped_system += 1          # status bar / system UI hosting this page
        elif r and t not in DECOR and (ROLE_H or DEFAULT_MAP).get(t, ("unknown", True))[0] != "container":
            role, check_style = (ROLE_H or DEFAULT_MAP).get(t, ("unknown", True))
            if role != "unknown" or text or click:
                units.append({"side": "hmos", "cls": t, "role": role,
                              "check_style": check_style, "text": text, "rect": r,
                              "click": click,
                              "checked": str(a.get("checked")).lower() == "true",
                              "bg": a.get("backgroundColor") or ""})
        for c in (node.get("children") or []):
            walk(c)

    walk(data)
    if dropped_system:
        print(f"  hmos: dropped {dropped_system} node(s) owned by another bundle (system UI)")
    return units


def dedupe(units: list[dict]) -> list[dict]:
    """Drop nodes whose text duplicates an enclosing node: Compose emits both the
    FAB View and its label, which would consume two counterparts on the other side."""
    def area(u):
        return u["rect"][2] * u["rect"][3]

    def contains(outer, inner):
        ox, oy, ow, oh = outer["rect"]
        ix, iy, iw, ih = inner["rect"]
        return ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih

    keep = []
    for u in sorted(units, key=area, reverse=True):
        nt = norm_text(u["text"])
        if nt and any(norm_text(k["text"]) == nt and contains(k, u) for k in keep):
            continue
        keep.append(u)
    dropped = len(units) - len(keep)
    if dropped:
        units[:] = keep
    return units


# ── matching ────────────────────────────────────────────────────────────────
def centre(u: dict, size: tuple[int, int]) -> tuple[float, float]:
    x, y, w, h = u["rect"]
    W, H = size
    return ((x + w / 2) / W, (y + h / 2) / H)


def norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", s.lower()).strip()


def match(android: list[dict], hmos: list[dict], a_size, h_size, tol: float):
    """Two passes: same normalised text first (labels move between runs), then geometry."""
    pairs, unmatched_a = [], []
    used: set[int] = set()

    for au in android:
        nt = norm_text(au["text"])
        if not nt:
            continue
        for i, hu in enumerate(hmos):
            if i in used:
                continue
            if hu["role"] != au["role"] and au["role"] != "unknown":
                continue          # a bare Android View carries no role: match on text alone
            if norm_text(hu["text"]) == nt:
                used.add(i)
                ac, hc = centre(au, a_size), centre(hu, h_size)
                pairs.append((au, hu, math.hypot(ac[0] - hc[0], ac[1] - hc[1])))
                break

    for au in android:
        if au in [x[0] for x in pairs]:
            continue
        ac = centre(au, a_size)
        best, best_d = None, 1e9
        for i, hu in enumerate(hmos):
            if i in used:
                continue
            if hu["role"] != au["role"] and au["role"] != "unknown":
                continue
            hc = centre(hu, h_size)
            d = math.hypot(ac[0] - hc[0], ac[1] - hc[1])
            if d < best_d:
                best, best_d = i, d
        if best is not None and best_d <= tol:
            used.add(best)
            pairs.append((au, hmos[best], best_d))
        else:
            unmatched_a.append((au, best_d))
    unmatched_h = [hu for i, hu in enumerate(hmos) if i not in used]
    return pairs, unmatched_a, unmatched_h


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--android-xml", required=True)
    ap.add_argument("--android-png", required=True)
    ap.add_argument("--hmos-json", required=True)
    ap.add_argument("--hmos-png", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument("--map", default="tools/role_map.yaml",
                    help="role map yaml from tools/build_role_map.py ('' = built-in fallback)")
    ap.add_argument("--pos-tol", type=float, default=0.06, help="match radius, fraction of height")
    ap.add_argument("--move-tol", type=float, default=0.02, help="max |dy| before 'moved', fraction of height")
    ap.add_argument("--de-tol", type=float, default=12.0, help="CIE76 dE for check_style roles")
    ap.add_argument("--top-mask", type=float, default=0.055,
                    help="fraction of height treated as status bar and dropped on both sides")
    ap.add_argument("--bundle", default="com.simplemobiletools.gallery.pro",
                    help="hmos bundleName that owns the page; nodes from other bundles are dropped")
    a = ap.parse_args()

    load_map(a.map)
    apng = np.asarray(Image.open(a.android_png).convert("RGB"), dtype=np.float32)
    hpng = np.asarray(Image.open(a.hmos_png).convert("RGB"), dtype=np.float32)
    a_size = (apng.shape[1], apng.shape[0])
    h_size = (hpng.shape[1], hpng.shape[0])

    android = dedupe(load_android(Path(a.android_xml)))
    hmos = dedupe(load_hmos(Path(a.hmos_json), a.bundle))
    if a.top_mask:
        cutoff_a = a_size[1] * a.top_mask
        cutoff_h = h_size[1] * a.top_mask
        n0 = len(android), len(hmos)
        android = [u for u in android if u["rect"][1] + u["rect"][3] > cutoff_a]
        hmos = [u for u in hmos if u["rect"][1] + u["rect"][3] > cutoff_h]
        print(f"  top-mask {a.top_mask:.0%}: dropped android {n0[0]-len(android)}, hmos {n0[1]-len(hmos)} (status bar)")
    print(f"units: android={len(android)}  hmos={len(hmos)}  "
          f"({a_size[0]}x{a_size[1]} vs {h_size[0]}x{h_size[1]})")

    pairs, unmatched_a, unmatched_h = match(android, hmos, a_size, h_size, a.pos_tol)

    findings = []
    d1_skipped = 0

    # a page-wide vertical shift is ONE finding, not N: subtract the median first
    dy_all = [centre(hu, h_size)[1] - centre(au, a_size)[1] for au, hu, _ in pairs]
    dy0 = statistics.median(dy_all) if dy_all else 0.0
    if abs(dy0) > a.move_tol:
        findings.append({"role": "-", "text": f"page shifted vertically {dy0:+.1%}",
                         "verdict": "D3_page_offset", "dy": round(dy0, 4)})

    for au, hu, dist in pairs:
        dy = abs(centre(hu, h_size)[1] - centre(au, a_size)[1] - dy0)
        row = {"android": au["cls"], "hmos": hu["cls"], "role": au["role"],
               "text": (au["text"] or hu["text"])[:40], "dist": round(dist, 4)}
        if dy > a.move_tol:
            row.update(verdict="D3_moved", dy=round(dy, 4))
        elif au["checked"] != hu["checked"]:
            row.update(verdict="D3_state_mismatch",
                       a_checked=au["checked"], h_checked=hu["checked"])
        elif au["check_style"]:
            la = mean_lab(apng, au["rect"])
            lh = mean_lab(hpng, _scale_rect(hu["rect"], h_size, a_size))
            de = _de(la, lh)
            row.update(verdict="D2_style_drift" if de > a.de_tol else "pass", dE=round(de, 1))
        else:
            d1_skipped += 1
            row.update(verdict="D1_ok")
        findings.append(row)

    for au, dist in unmatched_a:
        via = ROLE_HVIA.get(au["role"], "component")
        if via != "component":
            findings.append({"android": au["cls"], "role": au["role"],
                             "text": au["text"][:40], "verdict": "manual_check",
                             "via": via, "nearest": round(dist, 4)})
            continue
        findings.append({"android": au["cls"], "role": au["role"],
                         "text": au["text"][:40], "verdict": "D3_missing",
                         "nearest": round(dist, 4)})
    for hu in unmatched_h:
        via = ROLE_AVIA.get(hu["role"], "component")
        if via != "component":
            findings.append({"hmos": hu["cls"], "role": hu["role"],
                             "text": hu["text"][:40], "verdict": "manual_check",
                             "via": "android " + via})
            continue
        findings.append({"hmos": hu["cls"], "role": hu["role"],
                         "text": hu["text"][:40], "verdict": "D3_extra"})

    order = {"D3_page_offset": 0, "D3_missing": 1, "D3_extra": 2, "D3_moved": 3,
             "D3_state_mismatch": 4, "manual_check": 8,
             "D2_style_drift": 5, "D1_ok": 6, "pass": 7}
    findings.sort(key=lambda r: order.get(r["verdict"], 9))

    print(f"\n{'verdict':18s} {'role':10s} {'android':16s} {'hmos':12s} detail")
    for r in findings:
        det = r.get("text", "")
        for k in ("dy", "dE", "nearest", "a_checked", "h_checked", "via"):
            if k in r:
                det += f"  {k}={r[k]}"
        print(f"{r['verdict']:18s} {r.get('role',''):10s} {r.get('android',''):16s} "
              f"{r.get('hmos',''):12s} {det[:52]}")

    counts: dict[str, int] = {}
    for r in findings:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(f"\nsummary: {counts}")
    print(f"D1: {d1_skipped} platform-owned control(s) skipped — appearance not compared")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"units": {"android": len(android), "hmos": len(hmos)},
             "counts": counts, "d1_skipped": d1_skipped, "findings": findings},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {a.json}")


def _scale_rect(r, src_size, dst_size):
    x, y, w, h = r
    sx = dst_size[0] / src_size[0]
    sy = dst_size[1] / src_size[1]
    return (x * sx, y * sy, w * sx, h * sy)


def _de(la, lb) -> float:
    if la is None or lb is None:
        return 0.0
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(la, lb)))


if __name__ == "__main__":
    main()
