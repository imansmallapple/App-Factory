#!/usr/bin/env python3
"""Smoke-test the ui_align control-tree dumps that feed the visual gate (T3).

Calls the two new helpers against the live devices, then scores the pair with
tools/visual_gate.py to prove the structural metrics are no longer missing.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:/Users/chens/app-factory")
ECAT = ROOT / "eval" / "ecat" / "src"
PLUG = ECAT / "ecat" / "entropy" / "plugins" / "a2h_verify"
sys.path.insert(0, str(ECAT))

spec = importlib.util.spec_from_file_location("cua", PLUG / "check_ui_align.py")
cua = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cua)          # type: ignore[union-attr]

spec2 = importlib.util.spec_from_file_location("vg", ROOT / "tools" / "visual_gate.py")
vg = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(vg)          # type: ignore[union-attr]

cfg = {
    "adb_exe": r"C:/Users/chens/AppData/Local/Android/Sdk/platform-tools/adb.exe",
    "android_serial": "emulator-5630",
    "hdc_exe": r"C:/hdc_bin/hdc.exe",
    "hmos_target": "3DK0224924000072",
}
env = {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\System32;C:\hdc_bin;C:\Users\chens\AppData\Local\Android\Sdk\platform-tools"}

out = ROOT / "export" / "t3_dump_test"
out.mkdir(parents=True, exist_ok=True)

a_json = out / "GardenActivity_android.json"
h_json = out / "GardenActivity_harmony.json"

print("android dump:", cua._dump_android_ui(cfg, a_json, env), a_json.stat().st_size if a_json.exists() else 0)
print("harmony dump:", cua._dump_hmos_ui_to(cfg, h_json, env), h_json.stat().st_size if h_json.exists() else 0)

for p in (a_json, h_json):
    if p.exists():
        blocks = vg.parse_dump(p)
        print(f"{p.name}: {len(blocks)} text blocks, sample={[b['text'][:18] for b in blocks[:4]]}")

# score with the real screenshots from the live run when present
shots = ROOT / "eval" / "sunflower-migration" / ".ecat" / "runs_20261113_1618" / "0" / "ui_align" / "screenshots"
if shots.is_dir():
    for name in ("GardenActivity_android.png", "GardenActivity_harmony.png"):
        src = shots / name
        if src.exists():
            shutil.copyfile(src, out / name)
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "visual_gate.py"), "--dir", str(out),
         "--out", str(out / "gate"), "--json", str(out / "gate.json")],
        capture_output=True, text=True, timeout=180,
    )
    print("gate rc:", res.returncode)
    if (out / "gate.json").exists():
        data = json.loads((out / "gate.json").read_text(encoding="utf-8"))
        for s in data.get("screens") or []:
            print(" screen:", s.get("screen"), "verdict:", s.get("verdict"))
            print(" metrics:", s.get("metrics"))
            print(" missing:", s.get("missing"))
    else:
        print("stderr:", (res.stderr or "")[-400:])
else:
    print("no live run screenshots at", shots)
