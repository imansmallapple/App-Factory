# App Factory

Tooling for an automated **Android → OpenHarmony** porting pipeline, plus a deterministic
**visual gate** that decides whether a ported screen is acceptable.

The migration itself is driven by [ECAT](https://github.com/imansmallapple/ECAT) (branch
`feature/visual-gates`), a separate repository that consumes the gate described here. This repo
holds the pieces that are independent of any single run: the visual comparison tools, the
Android ↔ ArkUI role map, page-enumeration probes and the analysis utilities for an ECAT run.

## Status

| Piece | State |
|---|---|
| Role map (135 ArkUI components, 190 Android classes, 45 roles) | done, self-checked |
| D1 — platform-convention differences | done, field-tested |
| D2 — app-authored style drift | done, field-tested |
| D3 — structural comparison (optimal widget alignment) | done, `reordered` only verified on synthetic data |
| Pass/fail gate + score + exit code | done |
| ECAT integration (`deterministic_gate.py`) | written and smoke-tested; **not yet exercised by a full ECAT run** |
| Whole-app loop (`audit_all.py`) | works on 2 pages of the sample port; page list is hand-written |

Everything below was measured on a real pair of devices: an Android 17 emulator (1080×2400) and a
HarmonyOS phone (1280×2832) running the same app.

## The visual gate

Three judgements, one alignment, one verdict per element. Nothing here calls a model: an earlier
VLM-based scorer is kept as `tools/visual_gate.py` (legacy, fixed floors) but is superseded.

```
D3  structure    does it exist, is it in the right place, the right size, the right order
D1  convention   the platform owns this control's look  -> appearance is not compared
D2  style        the app owns this control's look       -> appearance is compared
```

### Run it on one page

```bash
uv run --with pillow --with numpy --with pyyaml python tools/visual_audit.py \
  --android-xml  page_android.xml  --android-png  page_android.png \
  --android-png2 page_android2.png                       # 2nd capture of the same screen = noise floor
  --hmos-json    page_harmony.json --hmos-png    page_harmony.jpeg \
  --hmos-png2    page_harmony2.jpeg \
  --json out.json
```

Exit code `0` = pass, `1` = fail. The JSON carries `gate.blocking`, `gate.warnings`,
`gate.informational` and a `0..1` score.

### Run it on every page

```bash
python tools/audit_all.py --config tools/pages.json --out tmp/audit_all
```

`tools/pages.json` lists each page with the tap that reaches it on each device (HarmonyOS has no
`am start -n` equivalent, so navigation is a tap). Pages that cannot be reached are reported as
`unreachable` and stay out of the totals.

### What blocks and what only warns

| Blocking | Warning | Informational |
|---|---|---|
| an interactive or chrome control is missing (button, input, toggle, tabs, appbar, menu, dialog…) | content is missing (label / image / list item) — a data-set question | text-height difference (font metrics) |
| `checked` / `enabled` differ | extra element, moved, resized, whole-page offset | unmatched nodes with no resolvable role |
| a platform-owned control breaks its siblings (restyled) | data set differs, order change below 20% | position follows a different data set |
| 1:N block came out with a different child count | style drift below visibility (ΔE ≤ 20, radius ≤ 0.25) | |
| style drift beyond visibility (ΔE > 20) | same content, different widget kind (one side non-interactive) | |
| same content, different *interactive* widget | | |

Score: `0` when nothing blocks, otherwise `min(1, 0.5 + 0.1 × (blocking − 1))`.

Thresholds are measured, not chosen: appearance `eps` is the p95 of the per-element difference
between **two captures of the same untouched screen**; the outlier test is a MAD-based robust
z-score (`z > 3.5`), never a fixed colour delta.

### Measured on the sample port

| Page | Verdict | Why |
|---|---|---|
| garden | FAIL 0.5 | the plant card carries 4 alignable children on Android and 2 on the port |
| plant list | FAIL 0.6 | plant names sit on a green band instead of under the image (ΔE 46); card 4 vs 2 children |

Warnings on the same pages: whole-page offset ~2%, search field added by the port, 6 of Android's
10+ plants present, text height off by 0.5% of screen height (Roboto vs HarmonyOS Sans).

## Role map

`tools/build_role_map.py` generates `tools/role_map.yaml` and `docs/role-map.md` from authoritative
inventories — the ArkUI component reference in `openharmony/docs` and `android.jar` / the Material 3
jar — and **fails** rather than emitting a wrong table when a component is unmapped, filed in two
roles, or listed as “no counterpart” while also being mapped.

```bash
uv run --with pyyaml python tools/build_role_map.py   # exit 0 = table consistent
```

Many-to-many by design: 5 Android switch classes map to one ArkUI `Toggle`; a card maps to nothing
because ArkUI has no `Card` component (the port draws one with `Column` + radius + shadow, recorded
as `hmos_via: handrolled`). Roles can also be realised by an attribute or an API rather than a
component (`bindTips`, `ListItem.swipeAction`, `promptAction.showToast`) — those are never reported
as “missing”, because such a thing never appears as a node in a control-tree dump.

## Repo layout

```
tools/visual_audit.py      one alignment -> D3 + D1 + D2, one verdict per element (the gate)
tools/visual_d1.py         D1/D3 baseline (kept: reference implementation of the role lookup)
tools/visual_d2.py         style channels, measured noise floor, class-level drift
tools/visual_d3.py         cost function + Hungarian alignment, reading order
tools/build_role_map.py    role map generator with self-checks
tools/role_map.yaml        the generated map (45 roles)
tools/audit_all.py         whole-app runner
tools/pages.json           page list + tap coordinates
tools/visual_gate.py       legacy rubric scorer (fixed floors) — superseded
tools/visual_diff.py       perceptual-diff pre-filter used by the legacy scorer
tools/fixtures/            D1 and D2 regression fixtures (synthetic, no device needed)
tools/reachable_pages.py   Table A: every activity registered in an APK manifest, can it be reached
tools/manifest_export_patch.py  patch an APK so every activity is exported (enumeration only)
tools/ui_crawl.py          minimal click-only crawler (page fingerprints + actions)
tools/ui_runner.py         execute UI flows on a HarmonyOS device, report JSON
tools/ecat_*.py            ECAT run inspectors: live timeline, task graph, token usage, PR per iter
docs/                      role map, audit design, gate rules
```

## Requirements

* Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (`pillow`, `numpy`, `pyyaml` are pulled per run)
* `adb` for the Android side, `hdc` (from DevEco Studio) for the HarmonyOS side
* Both devices connected and the same app installed on each

## Known limitations

1. `reordered` has never fired on real data — only on synthetic inversions.
2. Page navigation is a hand-written tap coordinate per page; there is no route discovery on
   HarmonyOS.
3. Geometric tolerances (2% move, 12% size) are fixed; only the appearance thresholds are measured.
4. `dataset_gap` thresholds (50% of a role missing, 80% distinct labels) are heuristics taken from
   two pages and need more apps before they can be trusted.
5. The ECAT bridge is smoke-tested against captured pages, not yet inside a full migration run.

## References

* GUIPilot — widget alignment with replace/insert/delete costs (arXiv 2506.07385): basis for D3.
* ScreenProof — “one element breaks the style its page sets”, hard negatives (arXiv 2611.10234):
  basis for D2, and the reason no model judge is used as a gate.
* NiCro — cross-device element descriptors (arXiv 2305.14611): not used yet; needed once matching
  has to survive very different screen densities.
