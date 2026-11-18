#!/usr/bin/env python3
"""Patch an APK manifest so every <activity> is exported, rebuild and sign it.

Why: `adb shell am start -n pkg/.SomeActivity` is refused for activities declared
android:exported="false" ("SecurityException: Permission Denial: ... not exported
from uid ..."). The check happens before intent extras are ever looked at, so no
amount of `-e key value` can get past it. Rebuilding a test-only variant with
exported="true" makes every page reachable by a parameterised `am start`.

Usage:
  python tools/manifest_export_patch.py export/simple-gallery-6.28.1.apk \
      --out build/patched [--install emulator-5630] [--debuggable]

Output: <out>/<name>-exported-signed.apk  (install this one; uninstall the
original first -- the debug signature differs from the release key).

Test-only build: exporting every component widens the attack surface. Never ship
it.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

APKTOOL_VERSION = "2.11.1"
APKTOOL_URL = (
    "https://github.com/iBotPeaches/Apktool/releases/download/"
    f"v{APKTOOL_VERSION}/apktool_{APKTOOL_VERSION}.jar"
)


def sdk_build_tool(name: str) -> Path:
    """Newest build-tools binary named `name` (zipalign / apksigner)."""
    roots = [
        Path(os.environ.get("ANDROID_HOME", "")),
        Path(os.environ.get("ANDROID_SDK_ROOT", "")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk",
        Path.home() / "AppData/Local/Android/Sdk",
    ]
    for root in roots:
        bt = root / "build-tools"
        if not bt.is_dir():
            continue
        cands = sorted(
            (d for d in bt.iterdir() if d.is_dir()),
            key=lambda p: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", p.name)],
        )
        for d in reversed(cands):
            exe = d / name
            for suffix in ("", ".exe", ".bat"):
                if (exe.parent / (exe.name + suffix)).exists():
                    return exe.parent / (exe.name + suffix)
    raise SystemExit(f"could not find {name} in any build-tools dir")


def ensure_apktool(path: Path) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] downloading apktool {APKTOOL_VERSION} -> {path}")
    urllib.request.urlretrieve(APKTOOL_URL, path)
    return path


def patch_manifest(manifest: Path, debuggable: bool) -> tuple[int, int]:
    s = manifest.read_text(encoding="utf-8")
    before = len(re.findall(r'<activity\s[^>]*android:exported="false"', s))

    def fix(m: re.Match[str]) -> str:
        tag = m.group(0)
        if 'android:exported="false"' in tag:
            return tag.replace('android:exported="false"', 'android:exported="true"')
        if "android:exported=" not in tag:
            return tag.replace("<activity ", '<activity android:exported="true" ', 1)
        return tag

    s = re.sub(r"<activity\s[^>]*>", fix, s)
    if debuggable and 'android:debuggable="true"' not in s:
        s = s.replace("<application ", '<application android:debuggable="true" ', 1)
    manifest.write_text(s, encoding="utf-8")
    total = len(re.findall(r"<activity\s", s))
    return before, total


def run(cmd: list[str], **kw) -> None:
    print("[*]", " ".join(str(c) for c in cmd[:3]), "...")
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("apk", type=Path)
    ap.add_argument("--out", type=Path, default=Path("build/patched"))
    ap.add_argument("--apktool-jar", type=Path,
                    default=Path(os.environ.get("TMPDIR", "/tmp")) / "apktool.jar")
    ap.add_argument("--keystore", type=Path, default=Path.home() / ".android" / "debug.keystore")
    ap.add_argument("--ks-alias", default="androiddebugkey")
    ap.add_argument("--ks-pass", default="android")
    ap.add_argument("--install", metavar="SERIAL", help="adb serial to install to")
    ap.add_argument("--debuggable", action="store_true", help="also set android:debuggable")
    args = ap.parse_args()

    apk = args.apk.resolve()
    out = args.out.resolve()
    work = out / "work"
    out.mkdir(parents=True, exist_ok=True)
    if work.exists():
        shutil.rmtree(work)

    jar = ensure_apktool(args.apktool_jar)
    run(["java", "-jar", str(jar), "d", "-f", str(apk), "-o", str(work / "dec")])

    before, total = patch_manifest(work / "dec" / "AndroidManifest.xml", args.debuggable)
    print(f"[*] exported=false activities: {before} -> 0  (total activities: {total})")

    unsigned = out / "exported-unsigned.apk"
    run(["java", "-jar", str(jar), "b", str(work / "dec"), "-o", str(unsigned)])

    aligned = out / "exported-aligned.apk"
    run([str(sdk_build_tool("zipalign")), "-f", "4", str(unsigned), str(aligned)])

    signed = out / f"{apk.stem}-exported-signed.apk"
    run([
        str(sdk_build_tool("apksigner")), "sign",
        "--ks", str(args.keystore), "--ks-pass", f"pass:{args.ks_pass}",
        "--key-pass", f"pass:{args.ks_pass}", "--ks-key-alias", args.ks_alias,
        "--out", str(signed), str(aligned),
    ])
    print(f"[+] signed test APK: {signed}")

    if args.install:
        pkg = subprocess.run(
            [str(sdk_build_tool("aapt2")), "dump", "packagename", str(signed)],
            capture_output=True, text=True, check=True).stdout.strip()
        adb = ["adb", "-s", args.install]
        print(f"[*] reinstalling {pkg} on {args.install} (signature change => uninstall first)")
        subprocess.run([*adb, "uninstall", pkg], check=False)
        subprocess.run([*adb, "install", "-r", str(signed)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
