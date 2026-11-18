#!/usr/bin/env python3
"""ui_runner — execute UI flows on a HarmonyOS device and report JSON.

Runs as a separate process so a Hypium hiccup cannot take down ECAT's
discriminator. If `hypium` is importable it is used as the driver; otherwise the
runner falls back to the raw `hdc shell uitest ...` commands ECAT already relies
on. Both paths produce the same ScreenState, so flows stay portable.

    python tools/ui_runner.py --probe                     # inspect driver + tree
    python tools/ui_runner.py --flows .ecat/ui_flows.json --out flows.json
    python tools/ui_runner.py --flows ... --changed SettingsActivity.ets,Config.ets

Flow schema (per flow):

    {"id": "settings_open",
     "screens": ["SettingsActivity.ets"],
     "steps": [
        {"launch": true},
        {"tap": "设置"},
        {"expect_page_changed": true},
        {"expect_visible": ["外观", "排序", "存储"]},
        {"back": true},
        {"expect_page_changed": true}
     ]}

Step verbs: launch, tap, input{at,text}, expect_visible, expect_absent,
expect_page_changed, back, wait{seconds}.

Output JSON: {"runner", "device", "bundle", "flows": [{id,status,step,step_name,
expected,actual,shot,dump,message}], "summary": {...}}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── device plumbing ───────────────────────────────────────────────────────────
HDC_CANDIDATES = [
    os.environ.get("HDC_EXE"),
    r"C:\hdc_bin\hdc.EXE",
    r"C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe",
    shutil.which("hdc"),
]
REMOTE_SHOT = "/data/local/tmp/_ui_runner_shot.jpeg"
REMOTE_DUMP = "/data/local/tmp/_ui_runner_dump.json"


def resolve_hdc() -> str:
    for c in HDC_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise SystemExit("hdc not found — set HDC_EXE")


def _list_targets(hdc: str) -> list[str]:
    try:
        out = subprocess.run([hdc, "list", "targets"], capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return []
    return [t.strip() for t in out.splitlines() if t.strip() and "Empty" not in t]


def resolve_device(hdc: str, wanted: str | None) -> str:
    if wanted:
        # still verify: a stale hdc server reports the serial as connected while
        # every shell call fails, which used to look like a broken app
        if wanted in _list_targets(hdc):
            return wanted
    else:
        targets = _list_targets(hdc)
        if targets:
            return targets[0]
    # server may be stale after a replug — restart it once and re-check
    for cmd in (["kill"], ["start"]):
        try:
            subprocess.run([hdc, *cmd], capture_output=True, timeout=30)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    targets = _list_targets(hdc)
    if wanted and wanted in targets:
        return wanted
    if targets:
        return targets[0]
    raise SystemExit("no HarmonyOS device — `hdc list targets` is empty (tried restarting the hdc server)")


def sh(hdc: str, target: str, args: list[str], timeout: float = 20.0) -> str:
    try:
        r = subprocess.run([hdc, "-t", target, "shell", *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return ""


# ── screen state ──────────────────────────────────────────────────────────────
@dataclass
class Node:
    label: str
    x: int
    y: int


@dataclass
class ScreenState:
    labels: list[str] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    raw: dict | None = None

    def has(self, needle: str) -> bool:
        n = needle.strip().lower()
        return any(n in lab.lower() for lab in self.labels)

    def sig(self) -> str:
        """Coarse page fingerprint: sorted label multiset, digits masked.

        Masking numbers matters: a counter/timer that ticks after a tap would
        otherwise look like a navigation and make `expect_page_changed` pass on
        a dead button.
        """
        norm = [re.sub(r"\d+", "#", lab.lower()) for lab in self.labels if len(lab) <= 24]
        return "|".join(sorted(norm))


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# Component ids and system chrome pollute the label set; flows should see only
# what a user could read on screen.
_SYS_PREFIX = ("StatusBar", "NavBar", "SCBDesktop", "SwiperPage", "FormItem",
               "PluginRoot", "TimeView", "GridSwiper", "session", "__")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_JUNK = re.compile(r"^[\s\d,:.%/-]*$")


def is_ui_label(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 40 or _JUNK.match(t):
        return False
    if t.startswith(_SYS_PREFIX):
        return False
    if "__" in t:
        return False
    # snake_case / camelCase identifiers are ids, not user-facing text
    if "_" in t and not _CJK.search(t) and " " not in t:
        return False
    return True


def _center(b: str) -> tuple[int, int] | None:
    m = _BOUNDS.search(b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


LOCK_MARKERS = ("ScreenLockRootComponent", "Double-tap to activate", "Enter your password",
                "Draw pattern", "Slide to unlock")


def is_locked(state: "ScreenState") -> bool:
    return any(m in lab for lab in state.labels for m in LOCK_MARKERS)


def clickables(text: str) -> list[dict]:
    """Clickable nodes from a uitest dump: what a flow can actually target."""
    out: list[dict] = []
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
        if isinstance(attrs, dict) and str(attrs.get("clickable")).lower() == "true":
            xy = _center(str(attrs.get("bounds") or ""))
            if xy:
                out.append({
                    "text": attrs.get("text") or "",
                    "description": attrs.get("description") or "",
                    "id": attrs.get("id") or "",
                    "key": attrs.get("key") or "",
                    "type": attrs.get("type") or "",
                    "x": xy[0], "y": xy[1],
                })
        for k in ("children", "childNodes", "nodes"):
            walk(node.get(k))

    walk(data)
    return out


def parse_uitest_dump(text: str) -> ScreenState:
    """Parse `uitest dumpLayout` JSON (OpenHarmony shape)."""
    st = ScreenState()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return st
    st.raw = data if isinstance(data, dict) else {"root": data}

    def walk(node):
        if isinstance(node, list):
            for v in node:
                walk(v)
            return
        if not isinstance(node, dict):
            return
        attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
        if isinstance(attrs, dict):
            label = ""
            for k in ("text", "hint", "description", "accessibilityText", "id", "key"):
                v = attrs.get(k)
                if isinstance(v, str) and v.strip():
                    label = v.strip()
                    break
            xy = _center(str(attrs.get("bounds") or ""))
            if label and xy and is_ui_label(label):
                st.nodes.append(Node(label, xy[0], xy[1]))
                st.labels.append(label)
        for k in ("children", "childNodes", "nodes"):
            walk(node.get(k))

    walk(st.raw)
    return st


# ── driver: hypium if available, else uitest ──────────────────────────────────
class Driver:
    kind = "uitest"

    def __init__(self, hdc: str, target: str, bundle: str, scratch: Path):
        self.hdc, self.target, self.bundle, self.scratch = hdc, target, bundle, scratch
        self.hy = None

    # --- lifecycle -----------------------------------------------------------
    def wake_and_unlock(self) -> None:
        """Best effort: a locked screen makes every flow look like a missing UI."""
        if not is_locked(self.state()):
            return
        sh(self.hdc, self.target, ["uitest", "uiInput", "keyEvent", "WakeUp"])
        time.sleep(0.6)
        sh(self.hdc, self.target, ["uitest", "uiInput", "swipe", "640", "2200", "640", "800", "600"])
        time.sleep(0.8)

    def launch(self) -> bool:
        self.wake_and_unlock()
        sh(self.hdc, self.target, ["aa", "force-stop", self.bundle])
        time.sleep(0.4)
        sh(self.hdc, self.target, ["aa", "start", "-b", self.bundle, "-a", "EntryAbility"])
        return self.wait_stable()

    def back(self) -> None:
        sh(self.hdc, self.target, ["uitest", "uiInput", "keyEvent", "Back"])
        time.sleep(0.4)

    # --- perception ----------------------------------------------------------
    def state(self) -> ScreenState:
        sh(self.hdc, self.target, ["uitest", "dumpLayout", "-p", REMOTE_DUMP])
        local = self.scratch / "_dump.json"
        local.unlink(missing_ok=True)
        subprocess.run([self.hdc, "-t", self.target, "file", "recv", REMOTE_DUMP, str(local)],
                       capture_output=True, text=True, timeout=30)
        if not local.exists():
            return ScreenState()
        return parse_uitest_dump(local.read_text(encoding="utf-8", errors="ignore"))

    def wait_stable(self, tries: int = 8, gap: float = 0.5) -> bool:
        prev = None
        for _ in range(tries):
            time.sleep(gap)
            cur = self.state()
            if cur.labels and prev is not None and cur.sig() == prev.sig():
                return True
            prev = cur
        return bool(prev and prev.labels)

    # --- action --------------------------------------------------------------
    def tap(self, label: str) -> bool:
        st = self.state()
        hit = next((n for n in st.nodes if label.strip().lower() in n.label.lower()), None)
        if hit is None:
            return False
        sh(self.hdc, self.target, ["uitest", "uiInput", "click", str(hit.x), str(hit.y)])
        time.sleep(0.5)
        return True

    def _clickables_now(self) -> list[dict]:
        sh(self.hdc, self.target, ["uitest", "dumpLayout", "-p", REMOTE_DUMP])
        local = self.scratch / "_clicks.json"
        local.unlink(missing_ok=True)
        subprocess.run([self.hdc, "-t", self.target, "file", "recv", REMOTE_DUMP, str(local)],
                       capture_output=True, text=True, timeout=30)
        if not local.exists():
            return []
        return clickables(local.read_text(encoding="utf-8", errors="ignore"))

    def tap_type(self, type_name: str, index: int = 0) -> bool:
        hits = [c for c in self._clickables_now() if c["type"] == type_name]
        if len(hits) <= index:
            return False
        c = hits[index]
        sh(self.hdc, self.target, ["uitest", "uiInput", "click", str(c["x"]), str(c["y"])])
        time.sleep(0.6)
        return True

    def tap_xy(self, x: int, y: int) -> bool:
        sh(self.hdc, self.target, ["uitest", "uiInput", "click", str(int(x)), str(int(y))])
        time.sleep(0.6)
        return True

    def input_text(self, label: str, text: str) -> bool:
        st = self.state()
        hit = next((n for n in st.nodes if label.strip().lower() in n.label.lower()), None)
        if hit is None:
            return False
        sh(self.hdc, self.target, ["uitest", "uiInput", "click", str(hit.x), str(hit.y)])
        time.sleep(0.4)
        sh(self.hdc, self.target, ["uitest", "uiInput", "inputText", str(hit.x), str(hit.y), text])
        time.sleep(0.5)
        return True

    # --- evidence ------------------------------------------------------------
    def screenshot(self, path: Path) -> str | None:
        sh(self.hdc, self.target, ["uitest", "screenCap", "-p", REMOTE_SHOT])
        path.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([self.hdc, "-t", self.target, "file", "recv", REMOTE_SHOT, str(path)],
                           capture_output=True, text=True, timeout=30)
        return str(path) if path.exists() else None


class HypiumDriver(Driver):
    """Hypium for locating/waiting/acting; uitest dump for state (one parser).

    The dump-based state keeps both drivers byte-identical in how they normalise
    a screen, so a flow behaves the same with or without hypium installed.
    """

    kind = "hypium"

    def __init__(self, hdc, target, bundle, scratch, hy):
        super().__init__(hdc, target, bundle, scratch)
        self.hy = hy
        self.BY, self.MP = hy.BY, hy.MatchPattern
        try:
            self.drv = hy.UiDriver.connect(device_sn=target)
        except TypeError:
            self.drv = hy.UiDriver.connect()
        try:
            self.drv.set_implicit_wait_time(2)
        except Exception:  # noqa: BLE001
            pass

    # --- selectors -----------------------------------------------------------
    def _selector(self, label: str):
        """Contains-match first; fall back to id/description for icon buttons."""
        return self.BY.text(label, self.MP.CONTAINS)

    def _find(self, label: str, timeout: float = 0):
        sel = self._selector(label)
        if timeout:
            try:
                return self.drv.wait_for_component(sel, timeout=timeout)
            except Exception:  # noqa: BLE001
                return None
        for probe in (sel,
                      self.BY.description(label, self.MP.CONTAINS),
                      self.BY.id(label, self.MP.CONTAINS)):
            try:
                comps = self.drv.find_all_components(probe)
            except Exception:  # noqa: BLE001
                comps = None
            comp = comps[0] if isinstance(comps, list) and comps else comps
            if comp is not None:
                return comp
        return None

    # --- lifecycle -----------------------------------------------------------
    def wake_and_unlock(self) -> None:
        for name in ("wake_up_display", "unlock"):
            fn = getattr(self.drv, name, None)
            if callable(fn):
                try:
                    fn()
                    time.sleep(0.5)
                except Exception:  # noqa: BLE001
                    pass
        super().wake_and_unlock()

    def launch(self) -> bool:
        try:
            self.drv.stop_app(self.bundle)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.drv.start_app(self.bundle, wait_time=1.5)
        except Exception:  # noqa: BLE001
            return super().launch()
        try:
            self.drv.wait_for_idle(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return self.wait_stable()

    def back(self) -> None:
        try:
            self.drv.press_back()
        except Exception:  # noqa: BLE001
            super().back()
        time.sleep(0.4)

    # --- action --------------------------------------------------------------
    def tap(self, label: str) -> bool:
        comp = self._find(label, timeout=5)          # built-in wait: the main gain
        if comp is None:
            return False
        try:
            self.drv.click(comp)
        except Exception:  # noqa: BLE001
            return super().tap(label)
        time.sleep(0.5)
        return True

    def tap_type(self, type_name: str, index: int = 0) -> bool:
        """Click by component type, using the same clickable-filtered dump as the
        uitest path. Hypium's BY.type() also matches layout containers that are
        not clickable, so coordinates from the dump are the reliable source."""
        return super().tap_type(type_name, index)

    def input_text(self, label: str, text: str) -> bool:
        comp = self._find(label, timeout=5)
        if comp is None:
            # hypium cannot target a placeholder/hint; the dump knows the field box
            return super().input_text(label, text)
        try:
            self.drv.clear_text(comp)
            self.drv.input_text(comp, text)
        except Exception:  # noqa: BLE001
            return super().input_text(label, text)
        time.sleep(0.5)
        return True

    def close(self):
        for name in ("close", "quit"):
            fn = getattr(self.drv, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
                return


def make_driver(hdc: str, target: str, bundle: str, scratch: Path, prefer_hypium: bool) -> Driver:
    if prefer_hypium:
        try:
            import hypium as hy  # noqa: PLC0415

            return HypiumDriver(hdc, target, bundle, scratch, hy)
        except Exception as exc:  # noqa: BLE001
            print(f"[ui_runner] hypium unavailable ({exc}); falling back to uitest", file=sys.stderr)
    return Driver(hdc, target, bundle, scratch)


# ── flow execution ────────────────────────────────────────────────────────────
def _action_str(step: dict) -> str:
    """Short description of what a step does — becomes an edge label downstream."""
    if "tap" in step:
        return f"tap({step['tap']})"
    if "tap_type" in step:
        spec = step["tap_type"]
        return f"tap_type({spec.get('type')}#{spec.get('index', 0)})"
    if "tap_xy" in step:
        xy = step["tap_xy"]
        return f"tap_xy({xy[0]},{xy[1]})"
    if "input" in step:
        return f"input({(step['input'] or {}).get('at', '')})"
    if "back" in step:
        return "back"
    if "launch" in step:
        return "launch"
    if "reset" in step:
        return "reset"
    for key in ("wait", "expect_visible", "expect_absent", "expect_page_changed"):
        if key in step:
            return key
    return next((k for k in step if k != "note"), "?")


class PreflightError(RuntimeError):
    """Something is wrong with the device or the installed app — do not run flows."""


def app_version_from_dump(text: str) -> str:
    """versionName out of `bm dump -n <bundle>` (JSON with a versionName field)."""
    import re as _re

    hit = _re.search(r'"versionName"\s*:\s*"([^"]+)"', text)
    return hit.group(1) if hit else ""


def preflight(d: "Driver", bundle: str, expect_version: str | None = None) -> dict:
    """Fail in seconds instead of burning five minutes: locked screen, missing app, wrong build."""
    st = d.state()
    if not st.labels:
        raise PreflightError("device 没吐出任何 UI（未连接、黑屏或 hdc 掉线）")
    if is_locked(st):
        d.wake_and_unlock()  # screen-off is recoverable; a PIN is not
        st = d.state()
        if is_locked(st):
            raise PreflightError("设备处于锁屏（PIN/密码锁屏无法自动解锁）——请先手动解锁再跑")
    dump = sh(d.hdc, d.target, ["bm", "dump", "-n", bundle])
    if not dump.strip() or bundle not in dump:
        raise PreflightError(f"应用未安装：{bundle}（bm dump 里找不到这个包）")
    version = app_version_from_dump(dump)
    if expect_version and version and version != expect_version:
        raise PreflightError(
            f"装的是 {bundle} v{version}，期望 v{expect_version} —— 先装对包再跑"
            "（bundle 名相同的两个 app 靠肉眼分不出来）"
        )
    return {"bundle": bundle, "version": version}


def order_flows(flows: list[dict]) -> list[dict]:
    """Topological order over `needs`. Unknown reference or a cycle fails before any device time."""
    by_id = {str(f.get("id")): f for f in flows}
    if len(by_id) != len(flows):
        raise ValueError("duplicate flow ids in spec")
    for flow in flows:
        for dep in flow.get("needs") or []:
            if str(dep) not in by_id:
                raise ValueError(
                    f"flow `{flow.get('id')}` needs `{dep}`, which is not in the spec"
                )

    # stable topological order: among flows whose dependencies are already scheduled, take the
    # one that appears first in the file. Predictable output matters more than speed here — a
    # user reading the log expects the same order they wrote, minus the ones that had to wait.
    ordered: list[dict] = []
    scheduled: set[str] = set()
    remaining = list(flows)
    while remaining:
        progressed = False
        for flow in list(remaining):
            fid = str(flow.get("id"))
            if all(str(dep) in scheduled for dep in flow.get("needs") or []):
                ordered.append(flow)
                scheduled.add(fid)
                remaining.remove(flow)
                progressed = True
                break  # restart from the file order, so order stays stable
        if not progressed:
            stuck = ", ".join(str(f.get("id")) for f in remaining)
            raise ValueError(f"dependency cycle: {stuck}")
    return ordered


def dependency_blockers(flow: dict, statuses: dict[str, str]) -> list[str]:
    """Dependencies that did not pass — running this flow would only produce a fake failure."""
    return [str(dep) for dep in (flow.get("needs") or []) if statuses.get(str(dep)) != "pass"]


def run_flow(d: Driver, flow: dict, shots: Path, dumpdir: Path) -> dict:
    fid = flow.get("id") or "flow"
    steps = flow.get("steps") or []
    prev = d.state()
    # Step trace: every action, plus the screen it is expected to land on. A step may carry
    # `"screen": "<name>"` to mark "after this action we are on that screen" — that is what
    # turns the flow into action-level edges instead of a bare screen list.
    trace: list[dict] = []

    def fail(idx: int, name: str, expected: list, actual: list, msg: str, *_: object) -> dict:
        out = _fail(fid, idx, name, expected, actual, msg, d, shots, dumpdir)
        out["trace"] = trace
        return out

    for idx, step in enumerate(steps):
        name = next((k for k in step if k != "note"), "?")
        entry: dict = {"step": idx, "action": _action_str(step)}
        trace.append(entry)
        try:
            if "reset" in step:
                # clear app data so flows do not depend on state left by an earlier
                # run. `bm clean` needs -d (data) and a stopped app on HarmonyOS.
                sh(d.hdc, d.target, ["aa", "force-stop", d.bundle])
                sh(d.hdc, d.target, ["bm", "clean", "-n", d.bundle, "-d"])
                time.sleep(1.0)
                continue
            if "launch" in step:
                ok = d.launch()
                prev = d.state()
                if not prev.labels or is_locked(prev):
                    return {"id": fid, "status": "error", "step": idx, "step_name": name,
                            "expected": [], "actual": [],
                            "message": ("device is locked" if is_locked(prev)
                                        else "device did not render any UI (unplugged or app failed to start)")}

            elif "tap" in step:
                # the screen may still be settling (cold start, tab transition): retry
                deadline, ok = time.time() + 4.0, False
                while not ok and time.time() < deadline:
                    ok = d.tap(str(step["tap"]))
                    if not ok:
                        time.sleep(0.6)
                if not ok:
                    return fail(idx, name, [step["tap"]], [], f"element not found: {step['tap']}", d, shots, dumpdir)

            elif "input" in step:
                spec = step["input"]
                ok = d.input_text(str(spec.get("at", "")), str(spec.get("text", "")))
                if not ok:
                    return fail(idx, name, [spec.get("at", "")], [], "input target not found", d, shots, dumpdir)

            elif "tap_type" in step:
                spec = step["tap_type"]
                if not d.tap_type(str(spec.get("type", "")), int(spec.get("index", 0))):
                    return fail(idx, name, [f'{spec.get("type")}[{spec.get("index", 0)}]'], [],
                                 "typed element not found", d, shots, dumpdir)

            elif "tap_xy" in step:
                xy = step["tap_xy"]
                d.tap_xy(int(xy[0]), int(xy[1]))

            elif "back" in step:
                d.back()

            elif "wait" in step:
                time.sleep(float(step["wait"].get("seconds", 1)))

            elif "expect_visible" in step:
                # a cold start renders a splash before the real page: poll instead of
                # failing on the first frame (this is what made the same flow flaky)
                want = [str(x) for x in step["expect_visible"]]
                deadline = time.time() + float(step.get("within", 8.0))
                missing = want
                while True:
                    st = d.state()
                    missing = [w for w in want if not st.has(w)]
                    if not missing or time.time() >= deadline:
                        break
                    time.sleep(0.6)
                if missing:
                    return fail(idx, name, want, st.labels, f"missing {missing}", d, shots, dumpdir)

            elif "expect_absent" in step:
                want = [str(x) for x in step["expect_absent"]]
                deadline = time.time() + float(step.get("within", 8.0))
                still = want
                while True:
                    st = d.state()
                    still = [w for w in want if st.has(w)]
                    if not still or time.time() >= deadline:
                        break
                    time.sleep(0.6)
                if still:
                    return fail(idx, name, [f"absent:{w}" for w in want], st.labels,
                                 f"still visible {still}", d, shots, dumpdir)

            elif "expect_page_changed" in step:
                # navigation is async: poll for up to 6s before declaring it dead
                baseline, st, deadline = prev.sig(), prev, time.time() + 6.0
                while time.time() < deadline:
                    st = d.state()
                    if st.sig() != baseline and st.labels:
                        break
                    time.sleep(0.5)
                if st.sig() == baseline:
                    return fail(idx, name, ["page changed"], st.labels[:12], "page did not change", d, shots, dumpdir)
                prev = st

            if step.get("screen"):
                # captured *after* the action ran: this is the screen the step lands on
                entry["screen"] = str(step["screen"])
                entry["sig"] = d.state().sig()[:32]
            if step.get("state"):
                # a view *inside* that screen (tab / filter / panel) the step proves
                entry["state"] = str(step["state"])

        except Exception as exc:  # noqa: BLE001
            return fail(idx, name, [], [], f"exception: {exc}", d, shots, dumpdir)

    return {"id": fid, "status": "pass", "step": None, "step_name": None,
            "expected": [], "actual": [], "message": "", "trace": trace}


def _fail(fid, idx, name, expected, actual, msg, d: Driver, shots: Path, dumpdir: Path) -> dict:
    shot = d.screenshot(shots / f"{fid}_step{idx}.jpeg")
    st = d.state()
    dumpfile = dumpdir / f"{fid}_step{idx}.json"
    try:
        dumpfile.parent.mkdir(parents=True, exist_ok=True)
        dumpfile.write_text(json.dumps(st.raw or {}, ensure_ascii=False)[:200_000], encoding="utf-8")
    except OSError:
        dumpfile = None
    return {"id": fid, "status": "fail", "step": idx, "step_name": name,
            "expected": expected, "actual": actual, "message": msg,
            "shot": shot, "dump": str(dumpfile) if dumpfile else None}


# ── probe ─────────────────────────────────────────────────────────────────────
def probe(d: Driver, launch: bool = True) -> dict:
    info = {"runner": d.kind, "device": d.target, "bundle": d.bundle}
    if launch:
        d.launch()
    else:
        info["launch"] = "skipped"
    st = d.state()
    info["label_count"] = len(st.labels)
    info["labels"] = sorted(set(st.labels))
    info["labels_sample"] = st.labels[:25]
    try:
        sh(d.hdc, d.target, ["uitest", "dumpLayout", "-p", REMOTE_DUMP])
        local = d.scratch / "_probe_dump.json"
        subprocess.run([d.hdc, "-t", d.target, "file", "recv", REMOTE_DUMP, str(local)],
                       capture_output=True, text=True, timeout=30)
        if local.exists():
            clicks = clickables(local.read_text(encoding="utf-8", errors="ignore"))
            info["clickable_count"] = len(clicks)
            info["clickables"] = clicks[:40]
    except Exception as exc:  # noqa: BLE001
        info["clickable_error"] = str(exc)
    if d.hy is not None:
        info["driver_methods"] = [m for m in dir(d.drv) if not m.startswith("_")][:80]
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="Run HarmonyOS UI flows, emit JSON")
    ap.add_argument("--flows", help="flows json path")
    ap.add_argument("--out", help="result json path")
    ap.add_argument("--changed", default="", help="comma separated changed files (flow filter)")
    ap.add_argument("--bundle", default=None,
                    help="bundle name of the app under test (required: no default, testing the wrong app is silent)")
    ap.add_argument("--device", default=None)
    ap.add_argument("--shots", default=None, help="screenshot dir")
    ap.add_argument("--scratch", default=None, help="scratch dir for dumps")
    ap.add_argument("--no-hypium", action="store_true", help="force the uitest fallback")
    ap.add_argument("--probe", action="store_true", help="connect, launch, print driver + labels")
    ap.add_argument("--no-launch", action="store_true", help="with --probe: inspect the current screen only")
    ap.add_argument("--timeout", type=float, default=300.0, help="global timeout seconds")
    ap.add_argument("--expect-version", default=None,
                    help="fail if the installed build is not this version (catches the wrong HAP)")
    ap.add_argument("--hap", default=None,
                    help="HAP/APK file that was installed: its sha256 is recorded as the build identity "
                         "(versionName is identical across rebuilds, so it cannot tell two builds apart)")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="do not check lock state / installed build before running")
    args = ap.parse_args()

    hdc = resolve_hdc()
    target = resolve_device(hdc, args.device)
    scratch = Path(args.scratch or (Path(os.environ.get("TMPDIR", ".")) / "ui_runner"))
    scratch.mkdir(parents=True, exist_ok=True)
    d = make_driver(hdc, target, args.bundle, scratch, prefer_hypium=not args.no_hypium)

    if not args.bundle:
        print("error: --bundle is required (there is no default on purpose)", file=sys.stderr)
        sys.exit(2)

    app_info: dict = {"bundle": args.bundle, "version": "", "sha256": ""}
    if args.hap:
        hap_path = Path(args.hap)
        if not hap_path.exists():
            print(f"error: --hap not found: {hap_path}", file=sys.stderr)
            sys.exit(2)
        import hashlib

        digest = hashlib.sha256()
        with hap_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        app_info["sha256"] = digest.hexdigest()
    if not args.skip_preflight:
        try:
            checked = preflight(d, args.bundle, args.expect_version)
            app_info.update(checked)
            if args.hap:
                app_info["sha256"] = app_info.get("sha256") or ""
            print(
                f"preflight: {app_info['bundle']} v{app_info['version'] or '?'}"
                f"{' sha ' + app_info['sha256'][:12] if app_info.get('sha256') else ''} · 屏幕已解锁",
                file=sys.stderr,
            )
        except PreflightError as exc:
            print(f"preflight failed: {exc}", file=sys.stderr)
            sys.exit(2)

    try:
        if args.probe:
            payload = probe(d, launch=not args.no_launch)
            payload["app"] = app_info
            text = json.dumps(payload, ensure_ascii=False, indent=1)
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(text, encoding="utf-8")
            # hypium logs to stdout; delimit our own payload so callers can parse it
            print("===UI_RUNNER_JSON===")
            print(text)
            return

        if not args.flows:
            ap.error("--flows is required unless --probe")
        spec = json.loads(Path(args.flows).read_text(encoding="utf-8"))
        changed = {c.strip() for c in args.changed.split(",") if c.strip()}
        shots = Path(args.shots) if args.shots else scratch / "shots"
        dumpdir = scratch / "dumps"

        try:
            ordered = order_flows(spec.get("flows") or [])
        except ValueError as exc:
            print(f"spec error: {exc}", file=sys.stderr)
            sys.exit(2)

        results, statuses, started = [], {}, time.time()
        for flow in ordered:
            fid = str(flow.get("id"))
            screens = set(flow.get("screens") or [])
            if changed and screens and not (screens & changed) and not flow.get("force"):
                results.append({"id": fid, "status": "skip", "step_name": "filter",
                                "message": f"untouched screens {sorted(screens)}"})
                statuses[fid] = "skip"
                continue
            blocked = dependency_blockers(flow, statuses)
            if blocked:
                # a failed seed must not turn every dependent flow into a fake failure
                results.append({"id": fid, "status": "skip", "step": None, "step_name": "dependency",
                                "expected": [], "actual": [],
                                "screens": flow.get("screens") or [],
                                "message": f"blocked_by: {', '.join(blocked)}"})
                statuses[fid] = "skip"
                continue
            if time.time() - started > args.timeout:
                results.append({"id": fid, "status": "skip", "step_name": "timeout",
                                "message": "global timeout"})
                statuses[fid] = "skip"
                continue
            res = run_flow(d, flow, shots, dumpdir)
            res["screens"] = flow.get("screens") or []   # so work items can target the file
            results.append(res)
            statuses[fid] = str(res.get("status"))

        summary = {
            "total": len(results),
            "executed": sum(1 for r in results if r["status"] in ("pass", "fail")),
            "passed": sum(1 for r in results if r["status"] == "pass"),
            "failed": sum(1 for r in results if r["status"] == "fail"),
            "errored": sum(1 for r in results if r["status"] == "error"),
            "skipped": sum(1 for r in results if r["status"] == "skip"),
            "blocked": sum(1 for r in results if r.get("step_name") == "dependency"),
        }
        payload = {"runner": d.kind, "device": target, "bundle": args.bundle,
                   "app": app_info, "flows": results, "summary": summary}
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print("===UI_RUNNER_JSON===")
        print(text)
        sys.exit(0 if summary["failed"] == 0 else 1)
    finally:
        if isinstance(d, HypiumDriver):
            d.close()


if __name__ == "__main__":
    main()
