#!/usr/bin/env python3
"""visual_d3 — structural comparison via widget alignment (GUIPilot, arXiv 2506.07385).

The paper abstracts each screen into widgets described by position, width, height and
type, defines a partial order plus replace/insert/delete costs, and turns screen matching
into an optimal widget-alignment problem. This file implements that idea:

  cost(a, h) = W_ROLE * [role differs]
             + W_POS  * distance(centres) / screen diagonal
             + W_SIZE * (|dw|/max(w) + |dh|/max(h)) / 2
             + W_TEXT * (1 - text similarity)
  leaving a widget unpaired costs DELETE_COST on each side

and the assignment is solved optimally (Hungarian), not greedily: a wrong pair costs
more than two insert/delete operations, so the solver backtracks instead of letting an
early mistake cascade.

Verdicts, one per element, in this priority (never double counted):
  missing > extra > moved > resized > reordered

  dataset_gap is not a structural defect: when most of a role is missing and the missing
  labels are all different, the port migrated a subset of the content, which is one fact
  about the data, not N facts about widgets.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import math
import re
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visual_d1 as d1

# ── costs ───────────────────────────────────────────────────────────────────
W_ROLE, W_POS, W_SIZE, W_TEXT = 0.55, 1.00, 0.35, 0.45
DELETE_COST = 0.42          # cost of leaving one widget unpaired
PAIR_LIMIT = 0.85           # a pair above this is really "not the same widget"
GREEDY_LIMIT = 320          # above this many widgets, Hungarian gets too slow
# a label's box is its text: comparing widths would flag every different string
SIZE_EXEMPT = {"label", "clock"}


def norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", s.lower()).strip()


def similarity(a: str, b: str) -> float:
    na, nb = norm_text(a), norm_text(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def rect_centre(u, size):
    x, y, w, h = u["rect"]
    W, H = size
    return ((x + w / 2) / W, (y + h / 2) / H)


def pair_cost(au, hu, a_size, h_size, dy_shift: float) -> float:
    ac = rect_centre(au, a_size)
    x, y, w, h = hu["rect"]
    hc = ((x + w / 2) / h_size[0], (y + h / 2 + dy_shift * h_size[1]) / h_size[1])
    dist = math.hypot(ac[0] - hc[0], ac[1] - hc[1])
    aw, ah = au["rect"][2] / a_size[0], au["rect"][3] / a_size[1]
    hw, hh = hu["rect"][2] / h_size[0], hu["rect"][3] / h_size[1]
    size_diff = (abs(aw - hw) / max(aw, hw, 1e-6) + abs(ah - hh) / max(ah, hh, 1e-6)) / 2
    sim = similarity(au["text"], hu["text"])
    role_diff = 0.0 if au["role"] == hu["role"] else 1.0
    return (W_ROLE * role_diff + W_POS * dist + W_SIZE * size_diff
            + W_TEXT * (1.0 - sim))


# ── optimal assignment (Hungarian, O(n^3)) ──────────────────────────────────
def hungarian(cost: np.ndarray) -> list[int]:
    """Minimisation on a square matrix; returns column index per row."""
    n = cost.shape[0]
    INF = 1e18
    u = np.zeros(n + 1)
    v = np.zeros(n + 1)
    p = np.zeros(n + 1, dtype=int)
    way = np.zeros(n + 1, dtype=int)
    c = np.zeros((n + 1, n + 1))
    c[1:, 1:] = cost
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(n + 1, INF)
        used = np.zeros(n + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = c[i0, j] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    ans = [-1] * n
    for j in range(1, n + 1):
        if p[j]:
            ans[p[j] - 1] = j - 1
    return ans


def text_anchors(au, hu) -> list[tuple[int, int]]:
    """Widgets whose normalised text appears exactly once on each side are certainly the
    same widget. Anchoring them first stops the optimiser from trading a certain match for
    a cheap-looking positional one when the two data sets have different lengths."""
    ca = collections.Counter(norm_text(u["text"]) for u in au if norm_text(u["text"]))
    ch = collections.Counter(norm_text(u["text"]) for u in hu if norm_text(u["text"]))
    by_text = {}
    for j, u in enumerate(hu):
        t = norm_text(u["text"])
        if t and ch[t] == 1:
            by_text[t] = j
    out = []
    for i, u in enumerate(au):
        t = norm_text(u["text"])
        if t and ca[t] == 1 and t in by_text:
            out.append((i, by_text[t]))
    return out


def align(au, hu, a_size, h_size, dy_shift: float):
    """Optimal widget alignment; returns (pairs, unmatched_android, unmatched_hmos)."""
    n, m = len(au), len(hu)
    if n == 0 or m == 0:
        return [], list(range(n)), list(range(m))

    anchors = text_anchors(au, hu)
    if anchors:
        ai = {i for i, _ in anchors}
        hj = {j for _, j in anchors}
        rest_a = [i for i in range(n) if i not in ai]
        rest_h = [j for j in range(m) if j not in hj]
        sub_pairs, sub_ua, sub_uh = align([au[i] for i in rest_a], [hu[j] for j in rest_h],
                                          a_size, h_size, dy_shift)
        pairs = [(i, j, pair_cost(au[i], hu[j], a_size, h_size, dy_shift))
                 for i, j in anchors]
        pairs += [(rest_a[i], rest_h[j], c) for i, j, c in sub_pairs]
        return pairs, [rest_a[i] for i in sub_ua], [rest_h[j] for j in sub_uh]

    if n + m > GREEDY_LIMIT:
        # too many widgets for the O(n^3) solver in pure python: rank pairs by cost
        cand = sorted(((pair_cost(au[i], hu[j], a_size, h_size, dy_shift), i, j)
                       for i in range(n) for j in range(m)))
        ui, uj, pairs = set(), set(), []
        for c, i, j in cand:
            if c > PAIR_LIMIT or i in ui or j in uj:
                continue
            ui.add(i)
            uj.add(j)
            pairs.append((i, j, c))
        return (pairs, [i for i in range(n) if i not in ui], [j for j in range(m) if j not in uj])

    size = n + m
    big = np.full((size, size), 1e6)
    big[:n, :m] = np.array([[pair_cost(au[i], hu[j], a_size, h_size, dy_shift)
                             for j in range(m)] for i in range(n)])
    for i in range(n):                     # android widget left unpaired
        big[i, m + i] = DELETE_COST
    for j in range(m):                     # hmos widget left unpaired
        big[n + j, j] = DELETE_COST
    big[n:, m:] = 0.0
    assign = hungarian(big)

    pairs, ua, uh = [], [], []
    used_h = set()
    for i in range(n):
        j = assign[i]
        if j is None or j < 0 or j >= m:
            ua.append(i)
            continue
        c = big[i, j]
        if c > PAIR_LIMIT or j in used_h:
            ua.append(i)
            continue
        used_h.add(j)
        pairs.append((i, j, float(c)))
    uh = [j for j in range(m) if j not in used_h]
    return pairs, ua, uh


# ── classification helpers ──────────────────────────────────────────────────
def reading_order(entries, size, band: float):
    """Grid-aware linearisation: row band first, then x. Sorting by y alone turns a
    two-column grid into a random order and invents 'reordered' findings."""
    out = sorted(entries, key=lambda e: (round(e["rect"][1] / max(band, 1e-6)),
                                         e["rect"][0]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--android-xml", required=True)
    ap.add_argument("--android-png", required=True)
    ap.add_argument("--hmos-json", required=True)
    ap.add_argument("--hmos-png", required=True)
    ap.add_argument("--map", default="tools/role_map.yaml")
    ap.add_argument("--bundle", default="com.simplemobiletools.gallery.pro")
    ap.add_argument("--top-mask", type=float, default=0.055)
    ap.add_argument("--move-tol", type=float, default=0.02, help="vertical shift, fraction of height")
    ap.add_argument("--size-tol", type=float, default=0.12, help="relative width/height change")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    from PIL import Image

    d1.load_map(a.map)
    apng = np.asarray(Image.open(a.android_png).convert("RGB"))
    hpng = np.asarray(Image.open(a.hmos_png).convert("RGB"))
    a_size = (apng.shape[1], apng.shape[0])
    h_size = (hpng.shape[1], hpng.shape[0])

    au = d1.dedupe(d1.load_android(Path(a.android_xml)))
    hu = d1.dedupe(d1.load_hmos(Path(a.hmos_json), a.bundle))
    for units, size in ((au, a_size), (hu, h_size)):
        cut = size[1] * a.top_mask
        units[:] = [u for u in units if u["rect"][1] + u["rect"][3] > cut]
    print(f"widgets: android={len(au)}  hmos={len(hu)}")

    # page-wide shift estimated from exact-text pairs, so a system-bar inset is not
    # reported as N moved widgets
    dy_shift = 0.0
    exact = []
    for x in au:
        for y in hu:
            if x["role"] == y["role"] and norm_text(x["text"]) and norm_text(x["text"]) == norm_text(y["text"]):
                exact.append(rect_centre(y, h_size)[1] - rect_centre(x, a_size)[1])
    if len(exact) >= 3:
        dy_shift = statistics.median(exact)

    pairs, ua, uh = align(au, hu, a_size, h_size, dy_shift)
    print(f"aligned pairs: {len(pairs)}   unpaired android={len(ua)}  hmos={len(uh)}"
          f"   page shift dy={dy_shift:+.3f}")

    findings = []
    if abs(dy_shift) > a.move_tol:
        findings.append({"verdict": "page_offset", "dy": round(dy_shift, 4),
                         "note": "whole page shifted; subtracted before classification"})

    per_element = []
    for i, j, cost in pairs:
        x, y = au[i], hu[j]
        dy = abs(rect_centre(y, h_size)[1] - dy_shift - rect_centre(x, a_size)[1])
        dw = abs(x["rect"][2] / a_size[0] - y["rect"][2] / h_size[0]) / max(
            x["rect"][2] / a_size[0], 1e-6)
        dh = abs(x["rect"][3] / a_size[1] - y["rect"][3] / h_size[1]) / max(
            x["rect"][3] / a_size[1], 1e-6)
        row = {"android": x["cls"], "hmos": y["cls"], "role": x["role"],
               "text": (x["text"] or y["text"])[:32], "cost": round(cost, 3)}
        if x["role"] != y["role"] and "unknown" not in (x["role"], y["role"]):
            # the same content is a different kind of widget on the other side
            # (a FAB label becomes a Button): that is information, not a size defect
            row.update(verdict="role_changed", hmos_role=y["role"])
        elif dy > a.move_tol:
            row.update(verdict="moved", dy=round(dy, 4))
        elif x["role"] not in SIZE_EXEMPT and max(dw, dh) > a.size_tol:
            row.update(verdict="resized", dw=round(dw, 3), dh=round(dh, 3))
        else:
            row.update(verdict="pass")
        per_element.append(row)
        findings.append(row)

    # ---- missing / extra, with the dataset_gap collapse --------------------
    missing_by_role: dict[str, list[dict]] = {}
    for i in ua:
        missing_by_role.setdefault(au[i]["role"], []).append(au[i])
    for role, items in missing_by_role.items():
        total = sum(1 for u in au if u["role"] == role)
        labels = [norm_text(u["text"]) for u in items if norm_text(u["text"])]
        distinct = len(set(labels)) / len(labels) if labels else 0.0
        repeat = 0.0
        if labels:
            top = collections.Counter(labels).most_common(1)[0][1]
            repeat = top / len(items)
        # content migrated partially shows up either as many different labels missing,
        # or as one repeated template (12 "Picture of plant" rows) — both are about data
        if (len(items) >= max(3, 0.5 * total)
                and role in ("label", "image", "list_item", "unknown")
                and ((role != "unknown" and (distinct >= 0.8 or repeat >= 0.5))
                     or (role == "unknown" and repeat >= 0.5 and distinct <= 0.5))):
            findings.append({"verdict": "dataset_gap", "role": role,
                             "missing": len(items), "of_role": total,
                             "distinct": round(distinct, 2), "repeat": round(repeat, 2),
                             "sample": [u["text"][:24] for u in items[:5]],
                             "note": "most of this role is absent: content was migrated "
                                     "partially, this is not N missing widgets"})
            continue
        if role == "unknown":
            # a Compose View with no class tells us nothing: calling each one "missing"
            # would be 19 rows of noise about nodes we cannot classify
            findings.append({"verdict": "unjudged", "role": "unknown",
                             "count": len(items), "of_role": total,
                             "sample": [u["text"][:20] for u in items[:4]],
                             "note": "unmatched nodes with no resolvable role (Compose View)"})
            continue
        for u in items:
            findings.append({"verdict": "missing", "role": role,
                             "android": u["cls"], "text": u["text"][:32]})
    for j in uh:
        findings.append({"verdict": "extra", "role": hu[j]["role"],
                         "hmos": hu[j]["cls"], "text": hu[j]["text"][:32]})

    # ---- reordered: block level, reading order of the successfully paired ----
    ok = [r for r in per_element if r["verdict"] == "pass"]
    if len(ok) >= 4:
        band = statistics.median([au[i]["rect"][3] for i, _, _ in pairs]) if pairs else 40
        idx_ok = [k for k, r in enumerate(per_element) if r["verdict"] == "pass"]
        a_order = reading_order([au[pairs[k][0]] for k in idx_ok], a_size, band)
        h_order = reading_order([hu[pairs[k][1]] for k in idx_ok], h_size, band)
        # compare the two orders through the shared pair identity
        origin = {}
        for k in idx_ok:
            origin[id(au[pairs[k][0]])] = k
            origin[id(hu[pairs[k][1]])] = k
        seq_a = [origin[id(u)] for u in a_order]
        seq_h = [origin[id(u)] for u in h_order]
        h_rank = {k: n for n, k in enumerate(seq_a)}
        h_seq_rank = [h_rank.get(k, 10 ** 6) for k in seq_h]
        inversions = sum(1 for p in range(len(h_seq_rank))
                         for q in range(p + 1, len(h_seq_rank))
                         if h_seq_rank[p] > h_seq_rank[q])
        if inversions >= max(2, 0.1 * len(h_seq_rank)):
            findings.append({"verdict": "reordered", "inversions": inversions,
                             "of": len(h_seq_rank),
                             "note": "reading order (row band, x) differs; reported once"})

    # If a role's content was only partially migrated, its items sit at different rows
    # on the two sides: that is the data difference showing through, not a layout fault.
    gap_roles = {f["role"] for f in findings if f["verdict"] == "dataset_gap"}
    for f in findings:
        if f["verdict"] == "moved" and f.get("role") in gap_roles:
            f["verdict"] = "moved_by_data"
            f["note"] = "position follows the data set, see dataset_gap"

    order = {"page_offset": 0, "dataset_gap": 1, "missing": 2, "extra": 3,
             "moved": 4, "resized": 5, "role_changed": 6, "reordered": 7,
             "moved_by_data": 8, "unjudged": 9, "pass": 10}
    findings.sort(key=lambda r: order.get(r["verdict"], 9))

    print(f"\n{'verdict':13s} {'role':10s} {'what':34s} detail")
    for f in findings:
        what = f.get("text") or f.get("sample") or ""
        det = " ".join(f"{k}={f[k]}" for k in ("dy", "dw", "dh", "cost", "missing",
                                               "of_role", "distinct", "repeat", "inversions",
                                               "count", "note", "hmos_role")
                       if k in f)
        print(f"{f['verdict']:13s} {str(f.get('role','')):10s} {str(what)[:34]:34s} {det[:64]}")

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
    print(f"\nsummary: {counts}")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"widgets": {"android": len(au), "hmos": len(hu)},
             "aligned": len(pairs), "dy_shift": dy_shift,
             "counts": counts, "findings": findings}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"wrote {a.json}")


if __name__ == "__main__":
    main()
