# visual_audit — one alignment, three judgements

`tools/visual_audit.py` runs D3 → D1 → D2 off a **single** alignment, and emits one verdict per
element. That was the missing piece: `visual_d1.py` and `visual_d2.py` each matched the screens
again on their own subsets, so the same control could collect up to three unrelated verdicts.

```
1. parse both dumps into trees (parents kept) and alignable leaves
2. page-wide shift from exact-text pairs, then optimal leaf alignment   (visual_d3.align)
3. blocks = containers with >=2 alignable descendants, matched 1:N over the paired leaves
4. classify once per element, in priority order
5. one row per element + summary JSON
```

## Verdict priority

```
page_offset > dataset_gap > missing > extra > moved > resized > role_changed
> class_style_drift > style_drift > state_mismatch > system_control_restyled
> block_child_mismatch > reordered > typography_check > moved_by_data > unjudged > pass
```

| Verdict | Class | Meaning |
|---|---|---|
| `page_offset` | D3 | whole page shifted; reported once and subtracted |
| `dataset_gap` | D3 | most of a role absent with one repeated label or all-distinct labels: content migrated partially, one fact about data |
| `missing` / `extra` | D3 | a widget with a real role is absent / added |
| `moved` / `resized` | D3 | paired but shifted / relative size change over tolerance |
| `role_changed` | D3 | same content, different widget kind (FAB label → Button) |
| `block_child_mismatch` | D3 | one container became a different number of ArkUI children (1:N) |
| `reordered` | D3 | reading order `(row band, x)` differs; one row per page |
| `moved_by_data` | D3 | position follows a data set that is itself different |
| `unjudged` | D3 | unmatched nodes with no resolvable role (Compose `View`), counted only |
| `state_mismatch` | D1 | `checked` / `enabled` differ |
| `system_control_restyled` | D1 | a platform-owned role where one instance breaks its siblings — the case plain "D1 skips appearance" would miss |
| `class_style_drift` | D2 | the whole class moved together: one finding about the theme |
| `style_drift` | D2 | one element left its siblings (robust z > 3.5 over the class) |
| `typography_check` | D2 | text height differs: font metrics, verify sp/fp — not a defect |

Tolerances: appearance `eps` is measured from two captures of the same untouched screen
(`visual_d2.measure_eps`); box geometry is exact because it comes from the dump, so the only
geometric tolerance is `--move-tol` (2% of height) and `--size-tol` (12%).

## Measured on sunflower, both devices

| Page | leaves | pairs | summary |
|---|---|---|---|
| garden | 10 / 7 | 5 | `page_offset 1, missing 1, extra 2, moved 2, class_style_drift 1, typography_check 1, unjudged 1, pass 3` |
| plant list | 37 / 19 | 16 | `page_offset 1, dataset_gap 1, missing 2, extra 3, role_changed 1, class_style_drift 2, typography_check 1, moved_by_data 12, pass 3` |

What the numbers say about this port: one label class recoloured on the list page (ΔE 46 — the
port puts plant names on a green band, Android puts them under the image), one unknown-class fill
difference, the garden page is missing the "Add plant" affordance, and the plant list only carries
6 of Android's 10+ plants.

## Running every page

`tools/audit_all.py` reads `tools/pages.json`, drives both devices page by page (a tap per page,
because HarmonyOS has no `am start -n` equivalent), captures dump + two screenshots per side, runs
the audit and writes `summary.json` / `summary.md`. Unreachable pages stay in the list as
`unreachable` so the coverage number is honest.

```
$ python tools/audit_all.py
garden      {page_offset 1, missing 1, extra 2, moved 2, class_style_drift 1,
             block_child_mismatch 1, typography_check 1, unjudged 1, pass 3}
plant_list  {page_offset 1, dataset_gap 1, missing 2, extra 3, role_changed 1,
             class_style_drift 2, block_child_mismatch 1, typography_check 1,
             moved_by_data 12, pass 3}
2/2 pages audited
```

1:N now fires on both pages: an Android plant card carries 4 alignable children, the port's card
carries 2 — one `block_child_mismatch` row instead of four unrelated diffs on the leaves.

## Pass / fail gate

`visual_audit.py` ends with a verdict, a 0..1 score and an exit code (0 pass, 1 fail), so it can
be used as a gate and not just a report generator.

| Blocking (the port is wrong in a way a user hits) | Warning (explainable, human glance) | Info |
|---|---|---|
| `missing` of an interactive or chrome role (button, input, toggle, tabs, appbar, menu, dialog…) | `missing` of a content role (label/image/list_item) — a data-set question | `typography_check` |
| `state_mismatch` (checked/enabled differ) | `extra`, `moved`, `resized`, `page_offset` | `unjudged` |
| `system_control_restyled` (one platform widget breaks its siblings) | `dataset_gap`, `reordered` below 20% | `moved_by_data` |
| `block_child_mismatch` (1:N came out with a different child count) | `style_drift` below visibility (ΔE ≤ 20, radius ≤ 0.25) | |
| `style_drift` / `class_style_drift` beyond visibility (ΔE > 20) | `role_changed` when only one side is interactive | |
| `role_changed` between two interactive roles | | |

Two rules that stop the gate from crying wolf:

* a `missing` box that **contains** an already-paired one is the same affordance exported twice
  (the Android FAB exports both a Button and its label) → warning, not blocking
* text anchors (normalised text unique on both sides) are paired before the optimiser runs, so a
  shorter data set cannot steal a certain match

Score: `0` when nothing blocks, else `min(1, 0.5 + 0.1 × (blocking − 1))`.

## ECAT integration

`eval/ecat/src/ecat/entropy/plugins/a2h_verify/deterministic_gate.py` runs the audit on the
screenshots and dumps `check_ui_align` already captured (no second navigation), aggregates the
verdict, and never raises: a broken gate reports `unavailable` so the migration loop keeps going.

`UiAlignPlugin._evaluate` gains:

* `details["deterministic_gate"]` — per-page verdict, score, blocking rows
* `config["ui_align_deterministic"]` (default on) — run the gate at all
* `config["ui_align_deterministic_score"]` (default off) — feed the gate score into the gradient
  instead of the old constant `hard_score = 0.0`
* `extract_work_items` — every blocking row becomes a `WorkItem` (`severity="high"`), so the next
  iteration is told exactly which page and which widget failed

Smoke test on the real captures (`tmp_a2h/test_bridge.py`):

```
gate: fail score: 0.6 judged: 2/2 failing: ['garden', 'plant_list']
  garden      ok fail score=0.5 blocking=1
  plant_list  ok fail score=0.6 blocking=2
work items:
  - ui_gate[garden]: block_child_mismatch — block_child_mismatch
  - ui_gate[plant_list]: class_style_drift label — style change beyond visibility
  - ui_gate[plant_list]: block_child_mismatch — block_child_mismatch
```

## Honest gaps

1. `reordered` has never fired on real data — only on synthetic inversions.
2. Page navigation is a hand-written tap coordinate per page (`tools/pages.json`); there is no
   automatic route discovery on HarmonyOS yet.
3. `move_tol` / `size_tol` are still fixed; only appearance eps is measured. Geometry noise is zero
   for a static screen, but a layout that legitimately reflows (different font metrics) is not
   modelled.
4. `dataset_gap` thresholds (50% of a role, 80% distinct) are heuristics from two pages.
5. The old rubric-based scorer `tools/visual_gate.py` (fixed floors `block_match>=0.85`,
   `text>=0.95`) is untouched and still exists; this file supersedes its purpose but does not
   replace it yet.
