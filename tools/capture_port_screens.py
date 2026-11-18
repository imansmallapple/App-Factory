#!/usr/bin/env python3
"""Capture named screens of one app on an OpenHarmony device for the visual gate.

Writes `<Screen>_harmony.png` + `<Screen>_harmony.json` into --out, which is exactly
what `tools/visual_gate.py --dir <same dir>` pairs with `<Screen>_android.png`.

A screen is "whatever is on the display after tapping one label", so nothing about the
app lives here — the bundle and the labels come from the caller:

    python tools/capture_port_screens.py --bundle com.example.app --ability EntryAbility \
        --screens "home:" "list:Open list" "detail:First item" --out reports/gate/live
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def sh(target: str, *args: str) -> str:
    proc = subprocess.run(["hdc", "-t", target, "shell", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return (proc.stdout or "") + (proc.stderr or "")


def first_target() -> str:
    out = subprocess.run(["hdc", "list", "targets"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith("["):
            return line
    raise SystemExit("no HarmonyOS device — `hdc list targets` is empty")


def recv(target: str, remote: str, out_dir: Path, name: str) -> Path:
    dest = out_dir / name
    dest.unlink(missing_ok=True)
    # hdc mangles a native absolute path: run in the target dir with a bare name
    subprocess.run(["hdc", "-t", target, "file", "recv", remote, name], cwd=str(out_dir),
                   capture_output=True, text=True)
    return dest


def walk(node: object, want: str = "", texts: list[str] | None = None) -> str:
    """Bounds of the node whose text equals `want`; collects texts when `texts` is given."""
    if isinstance(node, list):
        for item in node:
            hit = walk(item, want, texts)
            if hit:
                return hit
        return ""
    if not isinstance(node, dict):
        return ""
    attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
    attrs = attrs or {}
    text = str(attrs.get("text") or "").strip()
    if texts is not None and text:
        texts.append(text)
    if want and text == want:
        return str(attrs.get("bounds") or "")
    for key in ("children", "childNodes", "nodes"):
        hit = walk(node.get(key), want, texts)
        if hit:
            return hit
    return ""


def dump(target: str, out_dir: Path, screen: str) -> dict:
    sh(target, "uitest", "dumpLayout", "-p", "/data/local/tmp/_cap.json")
    path = recv(target, "/data/local/tmp/_cap.json", out_dir, f"{screen}_harmony.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def tap(target: str, out_dir: Path, label: str) -> bool:
    tree = dump(target, out_dir, "_tap")
    bounds = walk(tree, label)
    if not bounds:
        return False
    x1, y1, x2, y2 = [int(n) for n in re.findall(r"-?\d+", bounds)[:4]]
    sh(target, "uitest", "uiInput", "click", str((x1 + x2) // 2), str((y1 + y2) // 2))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture named port screens for the visual gate")
    ap.add_argument("--target", default="")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--ability", default="EntryAbility")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--reset", action="store_true", help="clear app data before the first screen")
    ap.add_argument("--screens", nargs="+", required=True, metavar="NAME[:LABEL]",
                    help="screen name, optionally followed by the label to tap first "
                         "(a bare NAME captures the launch screen)")
    args = ap.parse_args(argv)

    target = args.target or first_target()
    args.out.mkdir(parents=True, exist_ok=True)

    sh(target, "power-shell", "wakeup")
    sh(target, "aa", "force-stop", args.bundle)
    if args.reset:
        sh(target, "bm", "clean", "-n", args.bundle, "-d")
    sh(target, "aa", "start", "-a", args.ability, "-b", args.bundle)
    time.sleep(args.settle)

    for spec in args.screens:
        name, _, label = spec.partition(":")
        if label:
            if not tap(target, args.out, label):
                print(f"[capture] {name}: label {label!r} not found — screen skipped", file=sys.stderr)
                continue
            time.sleep(args.settle)
        tree = dump(target, args.out, name)
        texts: list[str] = []
        walk(tree, "", texts)
        sh(target, "snapshot_display", "-f", "/data/local/tmp/_cap.jpeg")
        shot = recv(target, "/data/local/tmp/_cap.jpeg", args.out, f"{name}_harmony.png")
        print(f"[capture] {name}: {len(texts)} texts, {shot.stat().st_size if shot.exists() else 0} bytes")

    (args.out / "_tap_harmony.json").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
