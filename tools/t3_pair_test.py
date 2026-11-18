#!/usr/bin/env python3
"""Paired capture test: put both apps on their home screen, capture PNG + control
tree on each side, then score the pair through tools/visual_gate.py."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Users/chens/app-factory")
ECAT = ROOT / "eval" / "ecat" / "src"
PLUG = ECAT / "ecat" / "entropy" / "plugins" / "a2h_verify"
sys.path.insert(0, str(ECAT))

spec = importlib.util.spec_from_file_location("cua", PLUG / "check_ui_align.py")
cua = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cua)          # type: ignore[union-attr]

cfg = {
    "adb_exe": r"C:/Users/chens/AppData/Local/Android/Sdk/platform-tools/adb.exe",
    "android_serial": "emulator-5630",
    "hdc_exe": r"C:/hdc_bin/hdc.exe",
    "hmos_target": "3DK0224924000072",
}
env = {"SYSTEMROOT": r"C:\Windows",
       "PATH": r"C:\Windows\System32;C:\hdc_bin;C:\Users\chens\AppData\Local\Android\Sdk\platform-tools"}

out = ROOT / "export" / "t3_pair"
out.mkdir(parents=True, exist_ok=True)

adb, ser = cfg["adb_exe"], cfg["android_serial"]
hdc, tgt = cfg["hdc_exe"], cfg["hmos_target"]
subprocess.run([adb, "-s", ser, "shell", "am", "force-stop", "com.google.samples.apps.sunflower"],
               capture_output=True, timeout=30)
subprocess.run([adb, "-s", ser, "shell", "monkey", "-p", "com.google.samples.apps.sunflower",
                "-c", "android.intent.category.LAUNCHER", "1"], capture_output=True, timeout=30)
subprocess.run([hdc, "-t", tgt, "shell", "aa", "force-stop", "com.simplemobiletools.gallery.pro"],
               capture_output=True, timeout=30)
subprocess.run([hdc, "-t", tgt, "shell", "aa", "start", "-a", "EntryAbility",
                "-b", "com.simplemobiletools.gallery.pro"], capture_output=True, timeout=30)
# align pages: Android -> Plant list tab (Harmony already renders the list)
subprocess.run([adb, "-s", ser, "shell", "input", "tap", "810", "428"], capture_output=True, timeout=20)
time.sleep(4)

print("android shot:", cua._screencap_android(cfg, out / "Home_android.png", env))
print("harmony shot:", cua._screencap_hmos(cfg, out / "Home_harmony.png", env))
print("android dump:", cua._dump_android_ui(cfg, out / "Home_android.json", env))
print("harmony dump:", cua._dump_hmos_ui_to(cfg, out / "Home_harmony.json", env))

res = subprocess.run([sys.executable, str(ROOT / "tools" / "visual_gate.py"), "--dir", str(out),
                      "--out", str(out / "gate"), "--json", str(out / "gate.json")],
                     capture_output=True, text=True, timeout=240)
print("gate rc:", res.returncode)
if (out / "gate.json").exists():
    data = json.loads((out / "gate.json").read_text(encoding="utf-8"))
    for scr in data.get("screens") or []:
        print(" screen:", scr.get("screen"), "verdict:", scr.get("verdict"))
        print(" metrics:", scr.get("metrics"), "| missing:", scr.get("missing"))
else:
    print("stderr:", (res.stderr or "")[-400:])
