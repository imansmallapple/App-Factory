#!/usr/bin/env python3
"""Minimal click-only UI crawler: builds nodes (page fingerprints) + edges (actions).

Answers the "B layer" question without any framework: adb + uiautomator dump only.
Nodes are page states (activity + hash of visible text/ids), edges are the taps that
move between them. Same package only; anything that jumps to another app is recorded
as an <external: pkg/activity> node and backed out of.

  python tools/ui_crawl.py --serial emulator-5630 --package com.x.y --max-steps 30 \
      --out build/ui-graph.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

TMP = Path("/sdcard/hermes_ui.xml")


def adb(serial: str, *args: str, timeout: int = 60) -> str:
    p = subprocess.run(["adb", "-s", serial, *args], capture_output=True, text=True, timeout=timeout)
    return p.stdout


def top_activity(serial: str) -> str:
    out = adb(serial, "shell", "dumpsys activity activities")
    for line in out.splitlines():
        if "topResumedActivity" in line:
            m = re.search(r"u0 ([^ ]+?) (t\d+)?\}?$", line.strip())
            if m:
                return m.group(1)
            m = re.search(r"u0 ([^ }]+)", line)
            if m:
                return m.group(1)
    return "?"


def dump_tree(serial: str) -> ET.Element:
    for _ in range(3):
        adb(serial, "shell", "uiautomator", "dump", "--compressed", str(TMP))
        xml = adb(serial, "exec-out", "cat", str(TMP))
        if xml.strip().startswith("<"):
            try:
                return ET.fromstring(xml)
            except ET.ParseError:
                pass
        time.sleep(0.5)
    raise RuntimeError("uiautomator dump failed")


def center(node: ET.Element) -> tuple[int, int]:
    x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", node.get("bounds", "[0,0][0,0]")))
    return (x1 + x2) // 2, (y1 + y2) // 2


def area(node: ET.Element) -> int:
    x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", node.get("bounds", "[0,0][0,0]")))
    return max(0, x2 - x1) * max(0, y2 - y1)


def label(node: ET.Element) -> str:
    for attr in ("text", "content-desc", "resource-id"):
        v = (node.get(attr) or "").strip()
        if v:
            return v.replace("\n", " ")[:60]
    return (node.get("class") or "node").rsplit(".", 1)[-1]


def fingerprint(root: ET.Element, activity: str) -> tuple[str, str]:
    parts = []
    for n in root.iter("node"):
        t = (n.get("text") or "").strip()
        d = (n.get("content-desc") or "").strip()
        r = (n.get("resource-id") or "").strip()
        if t or d or r:
            parts.append(f"{r}|{t}|{d}")
    h = hashlib.sha1("\n".join(sorted(parts)).encode()).hexdigest()[:10]
    name = activity.rsplit(".", 1)[-1]
    visible = [p.split("|")[1] for p in parts if p.split("|")[1]][:2]
    return h, f"{name}[{h}]" + (f" “{'/'.join(visible)}”" if visible else "")


def crawl(serial: str, package: str, out: Path, max_steps: int, delay: float) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    tried: set[tuple[str, str]] = set()

    def observe() -> tuple[str, str, ET.Element]:
        act = top_activity(serial)
        root = dump_tree(serial)
        h, name = fingerprint(root, act)
        node_id = f"{act}#{h}"
        nodes.setdefault(node_id, {"id": node_id, "activity": act, "label": name, "visits": 0})
        nodes[node_id]["visits"] += 1
        return node_id, act, root

    cur, act, root = observe()
    steps = 0
    while steps < max_steps:
        candidates = []
        screen = area(ET.Element("node", bounds=root.get("bounds") or "[0,0][1080,2400]"))
        for n in root.iter("node"):
            if n.get("clickable") != "true":
                continue
            if n.get("package") != package:      # ignore system bars / other apps
                continue
            a = area(n)
            if a == 0 or a > screen * 0.55:      # full-screen containers are useless
                continue
            lbl = label(n)
            key = (cur, lbl)
            if key in tried:
                continue
            candidates.append((lbl, n))
        todo = [(l, n) for l, n in candidates if (cur, l) not in tried]
        if not todo:
            adb(serial, "shell", "input", "keyevent", "4")
            time.sleep(delay)
            steps += 1
            nxt, act, root = observe()
            if nxt == cur:                       # back did nothing -> dead end
                break
            cur = nxt
            continue
        lbl, node = todo[0]
        tried.add((cur, lbl))
        x, y = center(node)
        adb(serial, "shell", "input", "tap", str(x), str(y))
        time.sleep(delay)
        nxt, nact, nroot = observe()
        if not nact.startswith(package):
            nodes.setdefault(nxt, {"id": nxt, "activity": nact, "label": f"external {nact}", "visits": 0})
        edges.append({"from": cur, "action": f"tap({lbl})", "to": nxt,
                      "external": not nact.startswith(package)})
        cur = nxt
        steps += 1
        if not nact.startswith(package):         # bounced into another app: come back
            adb(serial, "shell", "input", "keyevent", "4")
            time.sleep(delay)
            cur, act, root = observe()

    graph = {"package": package, "steps": steps, "nodes": list(nodes.values()), "edges": edges}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return graph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", type=Path, default=Path("build/ui-graph.json"))
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--delay", type=float, default=1.2)
    a = ap.parse_args()
    g = crawl(a.serial, a.package, a.out, a.max_steps, a.delay)
    print(f"nodes={len(g['nodes'])} edges={len(g['edges'])} steps={g['steps']} -> {a.out}")
    for n in g["nodes"]:
        print(f"  NODE {n['label']}  ({n['visits']} visits)")
    for e in g["edges"]:
        print(f"  EDGE {e['from'].rsplit('#',1)[0].rsplit('.',1)[-1]} -{e['action']}-> {e['to'].rsplit('#',1)[0].rsplit('.',1)[-1]}"
              + ("  [external]" if e["external"] else ""))


if __name__ == "__main__":
    main()
