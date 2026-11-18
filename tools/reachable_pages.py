#!/usr/bin/env python3
"""reachable_pages — Table A: every activity registered in the APK manifest,
probed with an explicit `am start` and a real landing assertion.

Why this exists: `am start` returning 0 does NOT mean the page was reached.
MediaActivity in simple-gallery starts, renders, then finishes itself; the
launcher is on top again and logcat shows no exception at all. So the assertion
is the one the acceptance layer needs:

    3 s after launch, `topResumedActivity` must still be the target component.

Verdict vocabulary (failures are typed, never lumped together):

    reached             target still on top after settle
    reached_trampoline  target finished by design and handed off to its successor
                        (SplashActivity -> MainActivity)
    crashed_abi         UnsatisfiedLinkError / missing .so -> environment limit
                        (simple-gallery ships no x86_64 panorenderer, so on an
                        x86_64 emulator the panorama pages can never render),
                        NOT an app defect -> must not enter the FN denominator
    crashed_app         FATAL EXCEPTION whose ComponentInfo is this activity
    blocked_dialog      a system/permission dialog is on top (permissions are
                        pre-granted first, so this should be rare)
    redirect            still in the stack but something else is on top
    finished            gone from the stack: it called finish() on itself
    rejected            am start refused (not exported / not found / SecurityException)

Usage:
    python tools/reachable_pages.py --apk build/patched/simple-gallery-6.28.1-exported-signed.apk \
        --serial emulator-5630 --params tools/table-A-params.json \
        --labels reports/table-A-gallery-patched.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

AAPT_CANDIDATES = [
    r"C:/Users/chens/AppData/Local/Android/Sdk/build-tools/36.0.0/aapt.exe",
    "aapt",
]
DIALOG_HINTS = ("permissioncontroller", "GrantPermissions", "com.android.systemui")


def sh(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "<timeout>"


def aapt_bin() -> str:
    for c in AAPT_CANDIDATES:
        if c == "aapt" or Path(c).is_file():
            return c
    sys.exit("aapt not found")


def enumerate_activities(apk: Path) -> tuple[str, list[dict]]:
    """Real activities from AndroidManifest; activity-alias entries are theme
    variants of SplashActivity and are collapsed into the parent."""
    rc, out = sh([aapt_bin(), "dump", "xmltree", str(apk), "AndroidManifest.xml"], timeout=120)
    if rc != 0:
        sys.exit(f"aapt failed: {out[:400]}")
    pkg = ""
    m = re.search(r'package="([^"]+)"', out)
    if m:
        pkg = m.group(1)
    acts: list[dict] = []
    cur: dict | None = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("E: activity-alias") or s.startswith("E: activity"):
            cur = {"kind": "alias" if "alias" in s else "activity"}
            acts.append(cur)
            continue
        if s.startswith("E: ") and cur is not None:
            cur = None
            continue
        if cur is None:
            continue
        m = re.search(r'A: android:name\(0x[0-9a-f]+\)="([^"]+)"', s)
        if m and "name" not in cur:
            cur["name"] = m.group(1)
        m = re.search(r'A: android:exported\(0x[0-9a-f]+\)=\(type 0x12\)(0x[0-9a-f]+)', s)
        if m:
            cur["exported"] = m.group(1).lower() != "0x0"
    alias_names = [a["name"] for a in acts if a.get("kind") == "alias" and a.get("name")]
    real = [a for a in acts if a.get("kind") == "activity" and a.get("name")
            and not any(a["name"].startswith(n + ".") for n in alias_names)]
    for a in real:
        if a["name"].startswith("."):
            a["name"] = pkg + a["name"]
        a.setdefault("exported", None)
    return pkg, real


def grant_permissions(serial: str, pkg: str) -> list[str]:
    """Pre-grant every declared-but-ungranted runtime permission. Skip this and the
    first probe lands on GrantPermissionsActivity, making the page look unreachable
    for a reason that has nothing to do with the app (a false negative)."""
    rc, out = sh(["adb", "-s", serial, "shell", "dumpsys", "package", pkg], 60)
    perms = sorted(set(re.findall(r"^\s+(android\.permission\.\w+): granted=false", out, re.M)))
    for p in perms:
        sh(["adb", "-s", serial, "shell", "pm", "grant", pkg, p], 30)
    return perms


def dump_top(serial: str) -> tuple[str, str]:
    """(component actually on screen, raw dump) — raw is reused for stack checks.

    `dumpsys window` focus is the primary signal: it is cheap (~1 s) and it is
    literally "what the user sees". `dumpsys activity top` was tried first and had
    to be dropped — after a hard emulator kill it reports a stale task
    (`TASK null id=2`, launcher) while the app is in fact on screen, which turned
    every row into a false "not reached".
    """
    rc, wout = sh(["adb", "-s", serial, "shell", "dumpsys", "window"], timeout=25)
    m = re.search(r"mCurrentFocus=Window\{\S+ u\d+ ([\w.]+)/([\w.$]+)", wout)
    if m:
        return f"{m.group(1)}/{m.group(2)}", wout
    rc, out = sh(["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"], timeout=30)
    for line in out.splitlines():
        if "topResumedActivity" in line or "mResumedActivity" in line:
            m = re.search(r"(?:topResumedActivity|mResumedActivity)=ActivityRecord\{\S+ u\d+ ([\w./]+)", line)
            if m:
                return m.group(1), out
    return "", out


def in_stack(serial: str, needle: str) -> bool:
    """Is the component still anywhere in the activity stack? Only called on
    non-reached rows, so the expensive full dump never runs on the happy path.
    Distinguishes 'finished itself' (gone) from 'covered by something else'."""
    rc, out = sh(["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"], timeout=30)
    return needle in out


def crash_info(serial: str, pkg: str) -> dict | None:
    """Last FATAL EXCEPTION block in the crash buffer, attributed to its component."""
    rc, out = sh(["adb", "-s", serial, "shell", "logcat", "-d", "-b", "crash", "-t", "120"], 40)
    if "FATAL EXCEPTION" not in out:
        return None
    tail = "FATAL EXCEPTION" + out.split("FATAL EXCEPTION")[-1]
    if pkg not in tail:
        return None
    m = re.search(r"ComponentInfo\{[\w.]+/([\w.$]+)\}", tail)
    lines = [l.split("AndroidRuntime: ", 1)[-1] for l in tail.splitlines() if "AndroidRuntime" in l]
    exc = next((l.strip() for l in lines if re.match(r"^[\w.]*(Exception|Error)", l.strip())), "")
    caused = [l for l in lines if l.startswith("Caused by:")]
    return {"component": m.group(1) if m else "", "exception": exc, "caused_by": caused[:2],
            "abi": any("UnsatisfiedLinkError" in l or 'library "' in l for l in lines)}


def build_params(params: dict, simple: str) -> tuple[list[str], list[str]]:
    """(am start args, notes) for one page — the 'parameterizable entry' half."""
    spec = params.get(simple) or {}
    args: list[str] = []
    for k, v in (spec.get("extras") or {}).items():
        args += ["--es", k, str(v)]
    for k, v in (spec.get("int_extras") or {}).items():
        args += ["--ei", k, str(v)]
    for k, v in (spec.get("bool_extras") or {}).items():
        args += ["--ez", k, "true" if v else "false"]
    for x in spec.get("raw_args") or []:
        args.append(str(x))
    notes = [spec["pre_state"]] if spec.get("pre_state") else []
    return args, notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", required=True)
    ap.add_argument("--serial", default="emulator-5630")
    ap.add_argument("--labels", default="reports/table-A.json")
    ap.add_argument("--params", default="")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--trampolines", default="SplashActivity")
    ap.add_argument("--only", default="", help="comma-separated activity simple names")
    a = ap.parse_args()

    apk = Path(a.apk)
    params = json.loads(Path(a.params).read_text(encoding="utf-8")) if a.params else {}
    trampolines = {x.strip() for x in a.trampolines.split(",") if x.strip()}
    pkg, acts = enumerate_activities(apk)
    if a.only:
        keep = {x.strip() for x in a.only.split(",")}
        acts = [x for x in acts if x["name"].rsplit(".", 1)[-1] in keep]
    print(f"[A] {apk.name}: package={pkg} real_activities={len(acts)}")

    sh(["adb", "-s", a.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], 20)
    sh(["adb", "-s", a.serial, "shell", "wm", "dismiss-keyguard"], 20)
    pre_granted = grant_permissions(a.serial, pkg)
    print(f"[A] pre-granted {len(pre_granted)}: {', '.join(p.split('.')[-1] for p in pre_granted)}")

    rows = []
    for i, act in enumerate(acts, 1):
        name, simple = act["name"], act["name"].rsplit(".", 1)[-1]
        extra_args, notes = build_params(params, simple)
        sh(["adb", "-s", a.serial, "shell", "logcat", "-c"], 30)
        sh(["adb", "-s", a.serial, "shell", "am", "force-stop", pkg], 45)
        time.sleep(0.3)
        t0 = time.time()
        rc, out = sh(["adb", "-s", a.serial, "shell", "am", "start", "-W", "-n",
                      f"{pkg}/{name}", *extra_args], 60)
        rejected = "Permission Denial" in out or "not exported" in out or "Error type" in out
        time.sleep(max(0.0, a.settle - (time.time() - t0)))
        top, raw = dump_top(a.serial)
        tries = 0
        while not top and tries < 3:            # transient dumpsys slowness
            tries += 1
            time.sleep(1.0)
            top, raw = dump_top(a.serial)

        if not rejected and any(h in top for h in DIALOG_HINTS):
            trc, tout = sh(["adb", "-s", a.serial, "shell", "logcat", "-d", "-t", "60"], 30)
            for p in set(re.findall(r"(android\.permission\.\w+)", tout)):
                sh(["adb", "-s", a.serial, "shell", "pm", "grant", pkg, p], 30)
            sh(["adb", "-s", a.serial, "shell", "input", "keyevent", "KEYCODE_BACK"], 20)
            time.sleep(1.0)
            sh(["adb", "-s", a.serial, "shell", "am", "start", "-n", f"{pkg}/{name}", *extra_args], 45)
            time.sleep(a.settle)
            top, raw = dump_top(a.serial)

        crash = crash_info(a.serial, pkg)
        # logcat was cleared immediately before this launch, so any FATAL for this
        # package in this window belongs to this row -- some crashes carry no
        # ComponentInfo line (Kotlin NPE inside a helper), and dropping those to
        # "finished" would hide a real defect.
        mine = crash if crash else None

        if rejected:
            verdict, reason = "not_reached", "rejected"
        elif not top:
            verdict, reason = "unknown", "no_focus"
        elif simple in top:
            verdict, reason = "reached", "landed"
        elif mine:
            verdict, reason = "not_reached", "crashed_abi" if mine["abi"] else "crashed_app"
        elif any(h in top for h in DIALOG_HINTS):
            verdict, reason = "not_reached", "blocked_dialog"
        elif simple in trampolines:
            verdict, reason = "reached_trampoline", f"handoff_to={top.rsplit('/', 1)[-1] or '?'}"
        elif in_stack(a.serial, simple):
            verdict, reason = "not_reached", "redirect"
        else:
            verdict, reason = "not_reached", "finished"

        rows.append({
            "activity": name, "simple": simple, "exported": act.get("exported"),
            "verdict": verdict, "reason": reason, "top_after": top,
            "launch_ms": int((time.time() - t0) * 1000),
            "am_status": (re.search(r"Status: (\w+)", out).group(1)
                          if re.search(r"Status: (\w+)", out) else ""),
            "params": extra_args or None, "notes": notes or None, "crash": mine,
        })
        print(f"[{i:02d}/{len(acts)}] {simple:32s} {verdict:18s} {reason:14s} "
              f"top={top.rsplit('/', 1)[-1] or '-'}")

    reached = [r for r in rows if r["verdict"].startswith("reached")]
    counts: dict[str, int] = {}
    for r in rows:
        if not r["verdict"].startswith("reached"):
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
    summary = {"total": len(rows), "reached": len(reached),
               "reached_direct": sum(1 for r in reached if r["verdict"] == "reached"),
               "reached_trampoline": sum(1 for r in reached if r["verdict"] == "reached_trampoline"),
               "not_reached": len(rows) - len(reached), "by_reason": counts,
               "pre_granted_permissions": pre_granted}

    out_path = Path(a.labels)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"table": "A", "apk": str(apk), "package": pkg,
                                    "serial": a.serial,
                                    "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "settle_s": a.settle, "summary": summary, "rows": rows},
                                   ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# Table A — 注册页面直跳可达清单（能力上限，不是覆盖率）", "",
          f"- apk `{apk.name}` · package `{pkg}` · device `{a.serial}`",
          f"- 判据：`am start` 后 {a.settle:g}s，`topResumedActivity` 仍是目标组件才算到达",
          f"- 到达 **{len(reached)}/{len(rows)}**（直跳 {summary['reached_direct']} + 跳板 "
          f"{summary['reached_trampoline']}），未到达 {summary['not_reached']}：{counts}",
          "", "| # | activity | exported | verdict | reason | 3s 后栈顶 | 参数 |",
          "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        p = " ".join(r["params"]) if r["params"] else "—"
        md.append(f"| {i} | `{r['simple']}` | {r['exported']} | {r['verdict']} | {r['reason']} | "
                  f"`{r['top_after'].rsplit('/', 1)[-1] or '-'}` | {p} |")
    out_path.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n[A] reached {len(reached)}/{len(rows)} -> {out_path} (+ .md)")


if __name__ == "__main__":
    main()
