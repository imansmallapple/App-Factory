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

## Honest gaps

1. **1:N block detection does not fire on these two pages**: the only containers with ≥2 leaves are
   page-level grids, which are filtered as roots. It needs a card-structured app to exercise.
2. `reordered` has never fired on real data — only on synthetic inversions.
3. `move_tol` / `size_tol` are still fixed; only appearance eps is measured. Geometry noise is zero
   for a static screen, but a layout that legitimately reflows (different font metrics) is not
   modelled.
4. `dataset_gap` thresholds (50% of a role, 80% distinct) are heuristics from two pages.
5. The old rubric-based scorer `tools/visual_gate.py` (fixed floors `block_match>=0.85`,
   `text>=0.95`) is untouched and still exists; this file supersedes its purpose but does not
   replace it yet.
