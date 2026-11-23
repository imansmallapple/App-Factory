#!/usr/bin/env python3
"""audit_all — capture every page on both devices, then run visual_audit on each pair.

HarmonyOS has no "am start -n <page>" equivalent, so pages are reached by tapping a tab
declared in tools/pages.json. A page that cannot be reached is reported as `unreachable`
and kept out of the totals (but listed, so the coverage number is honest).

Per page: settle -> dump + screenshot (twice, for the noise floor) on each side -> audit.

Usage:
    python tools/audit_all.py [--config tools/pages.json] [--out tmp_a2h/audit_all]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def adb(serial: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run(["adb", "-s", serial, *args], timeout=timeout)


def hdc(exe: str, target: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run([exe, "-t", target, *args], timeout=timeout)


def android_capture(cfg: dict, page: dict, out: Path) -> bool:
    serial = cfg["android_serial"]
    tap = page["android"].get("tap")
    if tap:
        adb(serial, "shell", "input", "tap", str(tap[0]), str(tap[1]))
    time.sleep(cfg.get("settle_s", 3))
    r = adb(serial, "shell", "uiautomator", "dump", "/sdcard/_audit.xml")
    if "dumped" not in (r.stdout + r.stderr):
        return False
    # binary-safe: adb exec-out writes to the file handle, never through a text buffer
    with open(out / "android.xml", "wb") as fh:
        if subprocess.run(["adb", "-s", serial, "exec-out", "cat", "/sdcard/_audit.xml"],
                          stdout=fh, timeout=180).returncode != 0:
            return False
    for n in (1, 2):
        with open(out / f"android{n}.png", "wb") as fh:
            if subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"],
                              stdout=fh, timeout=180).returncode != 0:
                return False
    return True


def hmos_capture(cfg: dict, page: dict, out: Path) -> bool:
    exe, target = cfg["hmos_hdc"], cfg["hmos_target"]
    tap = page["hmos"].get("tap")
    if tap:
        hdc(exe, target, "shell", f"uitest uiInput click {tap[0]} {tap[1]}")
    time.sleep(cfg.get("settle_s", 3))
    hdc(exe, target, "shell", "uitest dumpLayout -p /data/local/tmp/_audit.json")
    hdc(exe, target, "shell", "snapshot_display -f /data/local/tmp/_audit1.jpeg")
    hdc(exe, target, "shell", "snapshot_display -f /data/local/tmp/_audit2.jpeg")
    for remote, local in (("/data/local/tmp/_audit.json", "hmos.json"),
                          ("/data/local/tmp/_audit1.jpeg", "hmos1.jpeg"),
                          ("/data/local/tmp/_audit2.jpeg", "hmos2.jpeg")):
        r = hdc(exe, target, "file", "recv", remote, str((out / local).resolve()), timeout=180)
        if "finish" not in (r.stdout + r.stderr):
            return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="tools/pages.json")
    ap.add_argument("--out", default="tmp_a2h/audit_all")
    ap.add_argument("--only", default="", help="comma separated page names")
    a = ap.parse_args()

    cfg = json.loads((ROOT / a.config).read_text(encoding="utf-8"))
    base = ROOT / a.out
    base.mkdir(parents=True, exist_ok=True)
    only = {s.strip() for s in a.only.split(",") if s.strip()}

    exe, target = cfg["hmos_hdc"], cfg["hmos_target"]
    hdc(exe, target, "shell", f"aa start -a {cfg['hmos_entry']} -b {cfg['hmos_bundle']}")
    time.sleep(2)
    adb(cfg["android_serial"], "shell", "am", "start",
        "-n", "com.google.samples.apps.sunflower/.GardenActivity")
    time.sleep(cfg.get("settle_s", 3))

    results = []
    for page in cfg["pages"]:
        name = page["name"]
        if only and name not in only:
            continue
        out = base / name
        out.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {name} ===")
        ok_a = android_capture(cfg, page, out)
        ok_h = hmos_capture(cfg, page, out)
        if not (ok_a and ok_h):
            print(f"  unreachable (android={ok_a} hmos={ok_h})")
            results.append({"page": name, "status": "unreachable",
                            "android": ok_a, "hmos": ok_h})
            continue

        cmd = [sys.executable, str(ROOT / "tools" / "visual_audit.py"),
               "--android-xml", str(out / "android.xml"),
               "--android-png", str(out / "android1.png"),
               "--android-png2", str(out / "android2.png"),
               "--hmos-json", str(out / "hmos.json"),
               "--hmos-png", str(out / "hmos1.jpeg"),
               "--hmos-png2", str(out / "hmos2.jpeg"),
               "--bundle", cfg["hmos_bundle"],
               "--json", str(out / "audit.json")]
        r = run(cmd, timeout=600)
        if r.returncode != 0:
            print(f"  audit failed: {r.stderr[-300:]}")
            results.append({"page": name, "status": "audit_failed"})
            continue
        data = json.loads((out / "audit.json").read_text(encoding="utf-8"))
        data.update({"page": name, "status": "ok"})
        results.append(data)
        print(f"  {data['counts']}")

    ok = [r for r in results if r.get("status") == "ok"]
    totals: dict[str, int] = {}
    for r in ok:
        for k, v in r["counts"].items():
            totals[k] = totals.get(k, 0) + v
    print(f"\n===== {len(ok)}/{len(results)} pages audited =====")
    print("totals:", totals)
    for r in results:
        if r.get("status") != "ok":
            print(f"  {r['page']}: {r['status']}")

    (base / "summary.json").write_text(json.dumps(
        {"pages": results, "totals": totals,
         "audited": len(ok), "requested": len(results)}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    lines = ["| page | pairs | verdicts |", "|---|---|---|"]
    for r in results:
        if r.get("status") != "ok":
            lines.append(f"| {r['page']} | — | {r['status']} |")
            continue
        v = ", ".join(f"{k} {n}" for k, n in sorted(r["counts"].items()))
        lines.append(f"| {r['page']} | {r['pairs']} | {v} |")
    (base / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {base/'summary.json'} and summary.md")


if __name__ == "__main__":
    main()
