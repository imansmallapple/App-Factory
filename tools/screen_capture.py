#!/usr/bin/env python3
"""Capture every screen and state of an app, with its control tree.

This is the missing first half of the visual gate: `ui_align` captures one screen
per round; a gate needs the same screens captured on *both* ends, each paired
with a dumpLayout/uiautomator tree so structural metrics have something to read.

A capture spec is a list of screens; each screen is a short script:

    {"name": "list",    "steps": [{"launch": true}]}
    {"name": "detail",  "steps": [{"launch": true}, {"tap": "Apple"}]}
    {"name": "garden",  "steps": [{"launch": true}, {"tap": "My Garden"}, {"shot": "garden_empty"}]}

Steps: launch, tap <label>, back, shot <name>, wait <seconds>, input {at, text}.

Usage:
    python tools/screen_capture.py --platform harmony --device <serial> \
        --bundle <pkg> --spec shots/sunflower.json --out shots/
    python tools/screen_capture.py --platform android --device emulator-5630 \
        --bundle com.google.samples.apps.sunflower --spec shots/sunflower.json --out shots/
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ADB = "C:/Users/chens/AppData/Local/Android/Sdk/platform-tools/adb.exe"
HDC = "hdc"
BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
REMOTE_PNG = "/data/local/tmp/_cap.png"
REMOTE_DUMP = "/data/local/tmp/_cap.json"
REMOTE_XML = "/data/local/tmp/_cap.xml"


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class Harmony:
    platform = "harmony"

    def __init__(self, device: str, bundle: str):
        self.device, self.bundle = device, bundle
        self.base = [HDC, "-t", device]

    def sh(self, *args: str) -> str:
        return run(self.base + ["shell", " ".join(args)]).stdout

    def launch(self) -> None:
        self.sh("aa", "force-stop", self.bundle)
        time.sleep(0.5)
        self.sh("aa", "start", "-b", self.bundle, "-a", "EntryAbility")
        time.sleep(2.0)

    def back(self) -> None:
        self.sh("uitest", "uiInput", "keyEvent", "Back")
        time.sleep(1.0)

    def tap(self, x: int, y: int) -> None:
        self.sh("uitest", "uiInput", "click", str(x), str(y))
        time.sleep(1.2)

    def dump_text(self) -> str:
        self.sh("uitest", "dumpLayout", "-p", REMOTE_DUMP)
        with tempfile.TemporaryDirectory() as td:
            run(self.base + ["file", "recv", REMOTE_DUMP, str(Path(td) / "d.json")])
            p = Path(td) / "d.json"
            return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    def shot(self, out_png: Path) -> bool:
        self.sh("uitest", "screenCap", "-p", REMOTE_PNG)
        with tempfile.TemporaryDirectory() as td:
            run(self.base + ["file", "recv", REMOTE_PNG, str(Path(td) / "s.png")])
            src = Path(td) / "s.png"
            if src.exists() and src.stat().st_size > 1000:
                out_png.write_bytes(src.read_bytes())
                return True
        return False


class Android:
    platform = "android"

    def __init__(self, device: str, bundle: str):
        self.device, self.bundle = device, bundle
        self.base = [ADB, "-s", device] if device else [ADB]

    def sh(self, *args: str) -> str:
        return run(self.base + ["shell", " ".join(args)]).stdout

    def launch(self) -> None:
        self.sh("am", "force-stop", self.bundle)
        time.sleep(0.4)
        self.sh("monkey", "-p", self.bundle, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(2.0)

    def back(self) -> None:
        self.sh("input", "keyevent", "4")
        time.sleep(1.0)

    def tap(self, x: int, y: int) -> None:
        self.sh("input", "tap", str(x), str(y))
        time.sleep(1.2)

    def dump_text(self) -> str:
        self.sh("uiautomator", "dump", REMOTE_XML)
        return self.sh("cat", REMOTE_XML)

    def shot(self, out_png: Path) -> bool:
        proc = subprocess.run(self.base + ["exec-out", "screencap", "-p"],
                              capture_output=True, timeout=60)
        if proc.stdout and len(proc.stdout) > 1000:
            out_png.write_bytes(proc.stdout)
            return True
        return False


def labels_and_centres(dump: str) -> list[tuple[str, int, int]]:
    """(label, cx, cy) for every node that has text and a box.

    Handles both ends: HarmonyOS dumpLayout (JSON, attributes.text + bounds
    string) and Android uiautomator dump (XML attribute list).
    """
    text = dump.strip()
    out: list[tuple[str, int, int]] = []
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return out

        def walk(node):
            if isinstance(node, list):
                for v in node:
                    walk(v)
                return
            if not isinstance(node, dict):
                return
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
            if isinstance(attrs, dict):
                m = BOUNDS.search(str(attrs.get("bounds") or ""))
                label = str(attrs.get("text") or "").strip()
                if m and label:
                    x1, y1, x2, y2 = (int(v) for v in m.groups())
                    out.append((label, (x1 + x2) // 2, (y1 + y2) // 2))
            for k in ("children", "childNodes", "nodes"):
                walk(node.get(k))

        walk(data)
        return out

    for m in re.finditer(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', text, re.S):
        label = m.group(1).strip()
        if label:
            x1, y1, x2, y2 = (int(m.group(i)) for i in range(2, 6))
            out.append((label, (x1 + x2) // 2, (y1 + y2) // 2))
    return out


def find_label(dump: str, want: str) -> tuple[int, int] | None:
    want_l = want.casefold()
    for label, cx, cy in labels_and_centres(dump):
        if want_l in label.casefold():
            return cx, cy
    return None


def capture(driver, spec: list[dict], out_dir: Path, prefix: str) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for screen in spec:
        name = screen["name"]
        shots: list[str] = []
        for step in screen.get("steps", []):
            if "launch" in step:
                driver.launch()
            elif "back" in step:
                driver.back()
            elif "wait" in step:
                time.sleep(float(step["wait"]))
            elif "tap" in step:
                pos = find_label(driver.dump_text(), step["tap"])
                if pos is None:
                    results.append({"screen": name, "status": "error",
                                    "message": f"label not found: {step['tap']}"})
                    shots = []
                    break
                driver.tap(*pos)
            elif "shot" in step:
                shot_name = str(step["shot"])
                png = out_dir / f"{shot_name}_{prefix}.png"
                ok = driver.shot(png)
                dump = driver.dump_text()
                (out_dir / f"{shot_name}_{prefix}.json").write_text(dump, encoding="utf-8")
                shots.append(str(png))
                if not ok:
                    results.append({"screen": name, "status": "error",
                                    "message": f"screenshot failed: {shot_name}"})
                    shots = []
                    break
        if shots:
            results.append({"screen": name, "status": "ok", "shots": shots,
                            "labels": len(labels_and_centres(driver.dump_text()))})
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture screens + control trees on HarmonyOS or Android")
    ap.add_argument("--platform", choices=["harmony", "android"], required=True)
    ap.add_argument("--device", default="")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    screens = spec if isinstance(spec, list) else (spec.get("screens") or [])
    driver = (Harmony if args.platform == "harmony" else Android)(args.device, args.bundle)
    results = capture(driver, screens, Path(args.out), args.platform)
    print(json.dumps({"platform": args.platform, "results": results}, ensure_ascii=False, indent=1))
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
