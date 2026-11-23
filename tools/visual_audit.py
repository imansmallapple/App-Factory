#!/usr/bin/env python3
"""visual_audit — one alignment, three judgements (D3 -> D1 -> D2), one verdict per element.

The three judgements must not each re-match the two screens: the control D3 pairs up must be
the same control D1 and D2 then look at, otherwise one element collects three verdicts and the
"one difference, one class" rule dies.

Pipeline
  1. parse both dumps into trees (parent links kept) and into alignable leaves
  2. estimate the page-wide shift, then align leaves optimally (visual_d3.align)
  3. derive blocks from containers and match them 1:N over the paired leaves
  4. classify each element once, in priority order:
       D3  page_offset > dataset_gap > missing > extra > moved > resized > role_changed
           > block_child_mismatch > reordered > unjudged > pass
       D1  platform-owned roles: appearance skipped, state checked, plus a within-page
           consistency check so a system control the port restyled is not silently accepted
       D2  app-authored roles: fill / text / radius / height against the Android original,
           thresholds from the measured capture noise floor
  5. emit one row per element plus a summary

Tolerances are measured, not chosen: appearance eps comes from two captures of the same
untouched screen (visual_d2.measure_eps). Box geometry is exact (it comes from the dump, not
from pixels), so the only geometric tolerance left is the page-shift one.

This file does not replace tools/visual_gate.py, which is the older rubric-based scorer with
fixed floors (block_match >= 0.85, text >= 0.95).
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visual_d1 as d1
import visual_d2 as d2
import visual_d3 as d3


# ── trees (parents kept, unlike the flat leaf loaders) ──────────────────────
def parse_android(xml_path: Path) -> list[dict]:
    root = ET.parse(xml_path).getroot()
    out: list[dict] = []

    def walk(node, parent):
        cls = (node.get("class") or "").split(".")[-1]
        text = ((node.get("text") or "") + " " + (node.get("content-desc") or "")).strip()
        rect = d1.rect_of(node.get("bounds"))
        idx = None
        if rect:
            role, check = (d1.ROLE_A or {}).get(cls, ("unknown", True))
            idx = len(out)
            out.append({"id": idx, "side": "android", "cls": cls, "role": role,
                        "check_style": check, "text": text, "rect": rect,
                        "click": node.get("clickable") == "true",
                        "checked": node.get("checked") == "true",
                        "enabled": node.get("enabled", "true") == "true",
                        "parent": parent, "children": []})
            if parent is not None:
                out[parent]["children"].append(idx)
        for c in [c for c in node if c.tag == "node"]:
            walk(c, idx if idx is not None else parent)

    walk(root, None)
    return out


def parse_hmos(json_path: Path, bundle: str) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out: list[dict] = []

    def walk(node, parent):
        a = node.get("attributes") or {}
        t = (a.get("type") or "").strip()
        rect = d1.rect_of(a.get("bounds"))
        text = (str(a.get("text") or "") + " " + str(a.get("description") or "")).strip()
        owner = str(a.get("bundleName") or "")
        idx = None
        if rect and not (owner and bundle and owner != bundle):
            role, check = (d1.ROLE_H or {}).get(t, ("unknown", True))
            idx = len(out)
            out.append({"id": idx, "side": "hmos", "cls": t, "role": role,
                        "check_style": check, "text": text, "rect": rect,
                        "click": str(a.get("clickable")).lower() == "true",
                        "checked": str(a.get("checked")).lower() == "true",
                        "enabled": str(a.get("enabled")).lower() != "false",
                        "parent": parent, "children": []})
            if parent is not None:
                out[parent]["children"].append(idx)
        for c in (node.get("children") or []):
            walk(c, idx if idx is not None else parent)

    walk(data, None)
    return out


STRUCTURAL = {"grid", "list", "pager", "swipe"}


def leaves(tree: list[dict]) -> list[dict]:
    out = []
    for n in tree:
        if n["role"] == "container" or n["role"] in STRUCTURAL:
            continue
        if n["role"] == "unknown" and not (n["text"] or n["click"]):
            continue
        out.append(n)
    return out


def blocks(tree: list[dict]) -> list[dict]:
    """A block is a container holding at least two alignable descendants (GUIPilot's widget
    container): it is what lets one Android card map to several ArkUI widgets."""
    leaf_ids = {n["id"] for n in leaves(tree)}
    by_id = {n["id"]: n for n in tree}

    def descendants(nid: int) -> list[int]:
        out, stack = [], list(by_id[nid]["children"])
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(by_id[c]["children"])
        return out

    out = []
    for n in tree:
        if n["id"] in leaf_ids:
            continue                       # a leaf is not a block
        inside = [c for c in descendants(n["id"]) if c in leaf_ids]
        if len(inside) >= 2:
            out.append({"id": n["id"], "rect": n["rect"], "leaves": inside,
                        "cls": n["cls"], "role": n["role"]})
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
    ap.add_argument("--move-tol", type=float, default=0.02)
    ap.add_argument("--size-tol", type=float, default=0.12)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    d1.load_map(a.map)
    apng = np.asarray(Image.open(a.android_png).convert("RGB"), dtype=np.float32)
    hpng = np.asarray(Image.open(a.hmos_png).convert("RGB"), dtype=np.float32)
    a_size = (apng.shape[1], apng.shape[0])
    h_size = (hpng.shape[1], hpng.shape[0])

    atree, htree = parse_android(Path(a.android_xml)), parse_hmos(Path(a.hmos_json), a.bundle)
    for tree, size in ((atree, a_size), (htree, h_size)):
        cut = size[1] * a.top_mask
        for n in tree:
            if n["rect"][1] + n["rect"][3] <= cut:
                n["role"] = "container"           # status bar: never an alignable unit

    au, hu = leaves(atree), leaves(htree)
    ab, hb = blocks(atree), blocks(htree)
    print(f"leaves: android={len(au)} hmos={len(hu)}   blocks: android={len(ab)} hmos={len(hb)}")

    exact = []
    for x in au:
        for y in hu:
            nx = d3.norm_text(x["text"])
            if nx and nx == d3.norm_text(y["text"]):
                exact.append(d1.centre(y, h_size)[1] - d1.centre(x, a_size)[1])
    dy = statistics.median(exact) if len(exact) >= 3 else 0.0

    raw_pairs, raw_ua, raw_uh = d3.align(au, hu, a_size, h_size, dy)
    pairs = [(au[i], hu[j], c) for i, j, c in raw_pairs]
    ua = [au[i] for i in raw_ua]
    uh = [hu[j] for j in raw_uh]
    print(f"aligned: {len(pairs)} pairs  unpaired android={len(ua)} hmos={len(uh)}  "
          f"page shift dy={dy:+.3f}")

    eps, src = dict(d2.DEFAULT_EPS), {c: "unmeasured" for c in d2.CHANNELS}
    if a.android_png2:
        p2 = np.asarray(Image.open(a.android_png2).convert("RGB"), dtype=np.float32)
        if d2.static_check(apng, p2, "android"):
            e = d2.measure_eps(au, apng, p2)
            for c in d2.CHANNELS:
                eps[c] = max(d2.FLOOR_EPS[c], e[c])
                src[c] = "measured(android)"
    if a.hmos_png2:
        p2 = np.asarray(Image.open(a.hmos_png2).convert("RGB"), dtype=np.float32)
        if d2.static_check(hpng, p2, "hmos"):
            e = d2.measure_eps(hu, hpng, p2)
            both = src["fill"].startswith("measured")
            for c in d2.CHANNELS:
                eps[c] = max(eps[c], e[c])
                src[c] = "measured(both)" if both else "measured(hmos)"
    print("appearance eps:", {c: round(eps[c], 4) for c in d2.CHANNELS}, "source:", src)

    sig_a = {x["id"]: d2.signature_full(apng, x) for x in au}
    sig_h = {y["id"]: d2.signature_full(hpng, y) for y in hu}

    rows: list[dict] = []
    if abs(dy) > a.move_tol:
        rows.append({"verdict": "page_offset", "dy": round(dy, 4)})

    for x, y, cost in pairs:
        dy_i = abs(d1.centre(y, h_size)[1] - dy - d1.centre(x, a_size)[1])
        dw = abs(x["rect"][2] / a_size[0] - y["rect"][2] / h_size[0]) / max(
            x["rect"][2] / a_size[0], 1e-6)
        dh = abs(x["rect"][3] / a_size[1] - y["rect"][3] / h_size[1]) / max(
            x["rect"][3] / a_size[1], 1e-6)
        row = {"android": x["cls"], "hmos": y["cls"], "role": x["role"],
               "text": (x["text"] or y["text"])[:32], "cost": round(cost, 3)}
        if x["role"] != y["role"] and "unknown" not in (x["role"], y["role"]):
            row.update(verdict="role_changed", hmos_role=y["role"])
        elif dy_i > a.move_tol:
            row.update(verdict="moved", dy=round(dy_i, 4))
        elif x["role"] not in d3.SIZE_EXEMPT and max(dw, dh) > a.size_tol:
            row.update(verdict="resized", dw=round(dw, 3), dh=round(dh, 3))
        else:
            row["verdict"] = "pass"

        if x["checked"] != y["checked"] or x["enabled"] != y["enabled"]:
            row["verdict"] = "state_mismatch"
            row["state"] = f"checked {x['checked']}/{y['checked']} enabled {x['enabled']}/{y['enabled']}"

        row["_pair"] = (x, y)
        if x["check_style"] is False:
            row["d1"] = "platform_owned"
            row["d2"] = "not_applicable"
        else:
            row["d1"] = "not_applicable"
            row["d2"] = "pending"
        rows.append(row)

    # ── D2: class level first, element level only for outliers ─────────────
    # Two devices never render text or antialiasing identically, so comparing every label
    # against its counterpart flags every label. What matters is whether a whole class
    # moved together (one finding about the theme) or one element left its siblings.
    by_role: dict[tuple[str, str], list[tuple[dict, dict, float]]] = {}
    for r in rows:
        if r.get("d2") != "pending":
            continue
        x, y = r["_pair"]
        for ch in d2.channels_for(x["role"]):
            if ch == "height":
                continue                    # handled below: font metrics, not style
            delta = d2.delta(ch, sig_a[x["id"]].get(ch), sig_h[y["id"]].get(ch))
            if delta is not None:
                by_role.setdefault((x["role"], ch), []).append((x, y, delta))

    flagged: set[tuple[int, int, str]] = set()
    for (role, ch), items in by_role.items():
        over = [d for _, _, d in items if d > eps[ch]]
        if len(items) >= 3 and len(over) >= max(3, int(0.8 * len(items))):
            rows.append({"verdict": "class_style_drift", "role": role, "channel": ch,
                         "n": len(items), "over_eps": len(over),
                         "median_delta": round(statistics.median([d for _, _, d in items]), 2),
                         "note": "the whole class moved: one finding about the theme"})
            continue
        series = [d for _, _, d in items]
        med = statistics.median(series)
        mad = statistics.median([abs(v - med) for v in series])
        for x, y, delta in items:
            z = delta / max(mad, d2.RES_FLOOR[ch])
            if delta > eps[ch] and z > d2.Z_K:
                flagged.add((x["id"], y["id"], ch))

    # text height first: Roboto and HarmonyOS Sans have different metrics, so a height
    # difference is something to verify against sp/fp, never a style defect
    heights: dict[str, list[float]] = {}
    for r in rows:
        if r.get("d2") != "pending":
            continue
        x, y = r["_pair"]
        dv = d2.delta("height", sig_a[x["id"]].get("height"), sig_h[y["id"]].get("height"))
        if dv is not None:
            heights.setdefault(x["role"], []).append(dv)
    for role, ds in heights.items():
        over = [v for v in ds if v > eps["height"]]
        if len(over) >= max(3, int(0.8 * len(ds))):
            rows.append({"verdict": "typography_check", "role": role, "channel": "height",
                         "n": len(ds), "over_eps": len(over),
                         "median_delta": round(statistics.median(ds), 4),
                         "note": "text height differs by platform font metrics; verify sp/fp"})

    for r in rows:
        if r.get("d2") != "pending":
            continue
        x, y = r.pop("_pair", (None, None))
        if x is None:
            continue
        bad = [f"{ch}={round(d2.delta(ch, sig_a[x['id']].get(ch), sig_h[y['id']].get(ch)) or 0, 2)}"
               for ch in d2.channels_for(x["role"])
               if (x["id"], y["id"], ch) in flagged]
        r["d2"] = ("drift(" + ", ".join(bad) + ")") if bad else "ok"
        if bad and r["verdict"] == "pass":
            r["verdict"] = "style_drift"

    # D1 consistency: the platform draws its own controls identically, so an odd one out
    # among system widgets means the port restyled it — what "D1 skips appearance" would miss
    for side, units, sigs in (("android", au, sig_a), ("hmos", hu, sig_h)):
        groups: dict[str, list[dict]] = {}
        for u in units:
            if u["check_style"] is False and u["role"] not in ("unknown", "container"):
                groups.setdefault(u["role"], []).append(u)
        for role, members in groups.items():
            if len(members) < d2.MIN_GROUP:
                continue
            for ch in ("fill", "radius"):
                vals = [(m, sigs[m["id"]][ch]) for m in members if sigs[m["id"]].get(ch) is not None]
                if len(vals) < d2.MIN_GROUP:
                    continue
                series = [v[0] if ch == "fill" else float(v) for _, v in vals]
                med = statistics.median(series)
                mad = statistics.median([abs(v - med) for v in series])
                for m, v in vals:
                    xv = v[0] if ch == "fill" else float(v)
                    z = abs(xv - med) / max(mad, d2.RES_FLOOR[ch])
                    if abs(xv - med) > eps[ch] and z > d2.Z_K:
                        rows.append({"verdict": "system_control_restyled", "side": side,
                                     "role": role, "channel": ch, "text": m["text"][:28],
                                     "value": round(xv, 2), "siblings_median": round(med, 2),
                                     "z": round(z, 2)})

    miss_role: dict[str, list[dict]] = {}
    for x in ua:
        miss_role.setdefault(x["role"], []).append(x)
    for role, items in miss_role.items():
        total = sum(1 for u in au if u["role"] == role)
        labels = [d3.norm_text(u["text"]) for u in items if d3.norm_text(u["text"])]
        distinct = len(set(labels)) / len(labels) if labels else 0.0
        repeat = (collections.Counter(labels).most_common(1)[0][1] / len(items)) if labels else 0.0
        if (len(items) >= max(3, 0.5 * total)
                and role in ("label", "image", "list_item", "unknown")
                and ((role != "unknown" and (distinct >= 0.8 or repeat >= 0.5))
                     or (role == "unknown" and repeat >= 0.5 and distinct <= 0.5))):
            rows.append({"verdict": "dataset_gap", "role": role, "missing": len(items),
                         "of_role": total, "distinct": round(distinct, 2),
                         "repeat": round(repeat, 2),
                         "sample": [u["text"][:20] for u in items[:4]]})
            continue
        if role == "unknown":
            rows.append({"verdict": "unjudged", "role": "unknown", "count": len(items),
                         "sample": [u["text"][:20] for u in items[:4]]})
            continue
        for u in items:
            rows.append({"verdict": "missing", "role": role, "android": u["cls"],
                         "text": u["text"][:28]})
    for y in uh:
        rows.append({"verdict": "extra", "role": y["role"], "hmos": y["cls"],
                     "text": y["text"][:28]})

    # 1:N blocks — one row per block pair whose child count differs
    pair_of = {x["id"]: y["id"] for x, y, _ in pairs}
    hb_by_leaf = {}
    for hblock in hb:
        for lid in hblock["leaves"]:
            hb_by_leaf[lid] = hblock
    seen_blocks: set[frozenset] = set()
    for b in ab:
        if len(b["leaves"]) >= 0.5 * len(au):
            continue                     # the page root is not a meaningful 1:N pair
        hit: collections.Counter = collections.Counter()
        for lid in b["leaves"]:
            if lid in pair_of and pair_of[lid] in hb_by_leaf:
                hit[hb_by_leaf[pair_of[lid]]["id"]] += 1
        if not hit:
            continue
        best, count = hit.most_common(1)[0]
        hblock = next(h for h in hb if h["id"] == best)
        if count < max(2, 0.5 * min(len(b["leaves"]), len(hblock["leaves"]))):
            continue
        sig = frozenset((lid, pair_of[lid]) for lid in b["leaves"] if lid in pair_of)
        if sig in seen_blocks:
            continue
        seen_blocks.add(sig)
        if len(b["leaves"]) != len(hblock["leaves"]):
            rows.append({"verdict": "block_child_mismatch",
                         "android_block": b["cls"], "hmos_block": hblock["cls"],
                         "android_children": len(b["leaves"]),
                         "hmos_children": len(hblock["leaves"]), "paired": count,
                         "note": "1:N block: one Android container became a different "
                                 "number of ArkUI children"})

    # reordered — block-level reading order (row band, x)
    if sum(1 for r in rows if r.get("verdict") == "pass") >= 4 and pairs:
        band = statistics.median([x["rect"][3] for x, _, _ in pairs]) or 40
        a_of = {x["id"]: x for x, _, _ in pairs}
        h_of = {y["id"]: y for _, y, _ in pairs}
        a_order = d3.reading_order([a_of[k] for k in a_of], a_size, band)
        h_order = d3.reading_order([h_of[k] for k in h_of], h_size, band)
        a_rank = {x["id"]: n for n, x in enumerate(a_order)}
        inv_pairs = {y["id"]: x["id"] for x, y, _ in pairs}
        seq = [a_rank[inv_pairs[y["id"]]] for y in h_order]
        inv = sum(1 for i in range(len(seq)) for j in range(i + 1, len(seq)) if seq[i] > seq[j])
        if inv >= max(2, 0.1 * len(seq)):
            rows.append({"verdict": "reordered", "inversions": inv, "of": len(seq),
                         "note": "reading order (row band, x) differs; one row per page"})

    gaps = {r["role"] for r in rows if r["verdict"] == "dataset_gap"}
    if gaps:                    # a different data set re-flows every content row
        gaps |= {"label", "image", "list_item", "unknown"}
    for r in rows:
        if r["verdict"] == "moved" and r.get("role") in gaps:
            r["verdict"] = "moved_by_data"

    order = ["page_offset", "dataset_gap", "missing", "extra", "moved", "resized",
             "role_changed", "class_style_drift", "style_drift", "state_mismatch",
             "system_control_restyled", "block_child_mismatch", "reordered",
             "typography_check", "moved_by_data", "unjudged", "pass"]
    rows.sort(key=lambda r: order.index(r["verdict"]) if r["verdict"] in order else len(order))

    for r in rows:                      # internal bookkeeping must not reach the report
        r.pop("_pair", None)
        r.pop("side", None) if r.get("verdict") != "system_control_restyled" else None

    counts = collections.Counter(r["verdict"] for r in rows)
    print(f"\n{'verdict':24s} {'role':10s} what / detail")
    for r in rows:
        what = r.get("text") or r.get("sample") or ""
        det = " ".join(f"{k}={r[k]}" for k in
                       ("dy", "dw", "dh", "d1", "d2", "state", "missing", "of_role",
                        "count", "android_children", "hmos_children", "paired",
                        "inversions", "channel", "z", "hmos_role")
                       if k in r and r[k] not in (None, "", "not_applicable"))
        print(f"{r['verdict']:24s} {str(r.get('role', '')):10s} {str(what)[:32]:32s} {det[:76]}")

    print(f"\nsummary: {dict(counts)}")
    print(f"tolerances: move={a.move_tol:.3f} size={a.size_tol:.3f} "
          f"eps={ {c: round(eps[c], 3) for c in d2.CHANNELS} }")
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"leaves": {"android": len(au), "hmos": len(hu)},
             "blocks": {"android": len(ab), "hmos": len(hb)}, "pairs": len(pairs),
             "dy_shift": dy, "eps": eps, "eps_source": src,
             "counts": dict(counts), "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"wrote {a.json}")


if __name__ == "__main__":
    main()
