# D1 implementation + sunflower field test

Date: 2026-11-23 — Devices: Android `emulator-5630` (1080×2400, Android 17) /
HarmonyOS `62T0225C25025145` (1280×2832)

## 1. Role map

`tools/build_role_map.py` → `tools/role_map.yaml` + `docs/role-map.md`

Coverage is enforced, not eyeballed: every component of both inventories must land in a role, or
be listed explicitly in `no_counterpart` / `non_visual`, otherwise the script exits 1.

| Source | Count | Note |
|---|---|---|
| ArkUI basic components | 51 | openharmony/docs master `ts-basic-components-*` |
| ArkUI container components | 45 | `ts-container-*` |
| ArkUI media | 1 | `ts-media-components-video` |
| ArkUI advanced | 35 | `ohos-arkui-advanced-*` (Chip, SegmentButton, Dialog, SubHeader…) |
| Atomic-service components | 7 | `ohos-atomicservice-*` |
| **HarmonyOS total** | **135** | 125 mapped, 9 with no counterpart, 2 non-visual |
| android.widget | 98 | android-36 `android.jar` |
| Material Components | 27 | `com.google.android.material` |
| Compose M3 | 57 | the material3 jar sunflower compiles against |
| Other androidx | 11 | RecyclerView, ViewPager2, LazyVerticalGrid… |
| **Android total** | **190** | 152 mapped, 5 with no counterpart, 33 non-visual |

45 roles. Roles with `check_style=false` (D1: existence / position / state only) are toggle,
checkbox, checkbox_group, radio, slider, progress, rating, input, search, picker_date,
picker_time, picker_text, stepper, tabs, appbar, navbar, menu, dialog, toast, refresh, badge,
divider, scrollbar, swipe, segmented, filter.

## 2. Tool

`tools/visual_d1.py`

```
D3  existence / position / state        always checked
D1  roles with check_style=false        appearance skipped; state (checked/enabled/text) compared
D2  roles with check_style=true         appearance compared (CIE76 dE)
```

Three rules that only surfaced because of real data:

1. **Containers never align** — every `role=container` node is dropped; blocks are derived from
   leaves instead.
2. **Nested duplicate text is collapsed** — Compose exports the FAB and its label as two nodes;
   without dedupe one counterpart on the HarmonyOS side gets consumed twice.
3. **A page-wide shift is one finding** — the median dy across pairs is subtracted first and
   reported as a single `D3_page_offset`. Without this, one 2.6% shift produced 20 "moved" rows.

## 3. sunflower field test

### 3.1 Live capture (garden screen)

Alignable units after folding and dedupe: Android 13 / HarmonyOS 7 (raw 23 / 68).

| Verdict | Count |
|---|---|
| pass | 4 |
| D3_page_offset | 1 (whole page 2.6% lower) |
| D3_missing | 3 |
| D3_moved | 1 |
| D2_style_drift | 2 |
| **D1** | **0** |

D1 = 0 is not a defect in the tool: this port contains **no platform-owned controls at all** —
only Button / Text / Image — so there is nothing to skip. That is the state of this app.

### 3.2 D1 branch verification (`tools/fixtures/d1_demo/`)

Two captures with identical geometry, where the system controls are deliberately coloured
differently and so is the app's own button:

```
D2_style_drift     button     Button    Button    Go      dE=49.2
D1_ok              toggle     Switch    Toggle
D1_ok              checkbox   CheckBox  Checkbox
pass               label      TextView  Text      Hello   dE=0.0

D1: 2 platform-owned control(s) skipped — appearance not compared
```

Switch (green) vs Toggle (blue) differ a lot, but the role is `check_style=false`, so no defect is
raised — that is D1 working.

Flipping the Toggle's `checked` to false:

```
D3_state_mismatch  toggle     Switch    Toggle    a_checked=True  h_checked=False
```

State is not skipped, it is still reported.

## 4. Audit of the first version

The first generated map carried 13 errors; all are fixed:

- three doc pages that are not components (`ScrollableCommon`, `WithEnv`, `WithTheme`)
- ArkUI `AlphabetIndexer` filed under `dialog`; moved to `container`
- the `scrollbar` role held Android scrolling-physics classes (`Scroller`, `OverScroller`,
  `EdgeEffect`); cleared, now `android_via: attribute`
- adapter classes filed inside `list` / `expandable`; moved to non-visual
- ArkUI `Filter` (a filter sheet) collided by name with `android.widget.Filter`; split per platform
- `Gauge` was in `rating`; moved to `progress`
- `Chronometer` was in `picker_time`; moved to `clock`
- `ZoomButton` / `ZoomControls` were in `stepper`; Android has no stepper widget at all
- `NavDestination` was in `appbar`; moved to `container`
- compose `Label` (internal material3) removed from `label`
- `WebView`, `SurfaceView`, `TextureView`, `GLSurfaceView`, `Dialog`, `DialogFragment`,
  `MaterialDatePicker`, `DatePickerDialog`, `TimePickerDialog` were missing entirely
- the "no counterpart" list held 15 names that were in fact mapped

The build script now fails instead of writing a wrong table when: a role holds a non-visual class,
a one-sided role does not declare how the empty side implements it, an entry is duplicated, the
"no counterpart" list overlaps the mapped set or names something absent from the inventories, or a
role has no title.

## 5. Still open

1. Tolerances are fixed (`pos-tol 6%`, `move-tol 2%`, `ΔE 12`). No relative calibration yet
   (repeat-capture noise + per-role MAD).
2. No 1:N / many-to-many pairing; matching is text-first, then nearest centre 1:1.
3. The "group consistency + platform conformance" check for D1 is not implemented — a role marked
   `check_style=false` skips appearance unconditionally.
4. Grid ordering by `(row band, x)` is not implemented, so some `moved` rows on the list screen are
   scroll-state artefacts.
5. Toggle's `checked` field has only been verified on a synthetic dump, not on a real screen that
   has a switch.
