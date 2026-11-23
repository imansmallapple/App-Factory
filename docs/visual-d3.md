# D3 — structural comparison (widget alignment)

`tools/visual_d3.py` answers "did the widget structure survive the port": what exists, where it
sits, how big it is, in what order. No appearance is looked at — that is D1/D2.

Basis: GUIPilot (arXiv 2506.07385) abstracts a screen into widgets (position, width, height, type),
defines a partial order with replace / insert / delete costs, and turns screen matching into an
optimal widget-alignment problem. This file implements that idea.

## 1. Cost of pairing two widgets

```
cost(a, h) = 0.55 * [role differs]
           + 1.00 * distance(centres) / screen diagonal
           + 0.35 * (|dw|/max(w) + |dh|/max(h)) / 2
           + 0.45 * (1 - text similarity)          # SequenceMatcher on normalised text

leaving a widget unpaired costs 0.42 per side
a pair is only accepted when cost <= 0.85 (anything above is "not the same widget")
```

Because a wrong pair costs more than two insert/delete operations, the solver backtracks instead of
letting an early mistake cascade — that is exactly what the greedy nearest-neighbour matcher in
`visual_d1.py` could not do.

Assignment is solved with the Hungarian algorithm (O(n³), validated against brute force on 200
random matrices in `tools/test_hungarian.py`). Above 320 widgets the run falls back to cost-ranked
greedy and says so.

The page-wide vertical shift is estimated first from exact-text pairs (median dy) and subtracted, so
a system-bar inset is one `page_offset` line instead of N `moved` lines.

## 2. Verdicts, one per element, in priority order

| Verdict | Meaning |
|---|---|
| `page_offset` | the whole page moved; reported once, then subtracted |
| `dataset_gap` | most of a role is absent with the same repeated label or all distinct labels — the port migrated part of the content, one fact about data, not N missing widgets |
| `missing` | a widget with a real role is absent on the HarmonyOS side |
| `extra` | the port has a widget Android does not |
| `moved` | paired, vertical shift beyond `--move-tol` (2% of height) |
| `resized` | paired, relative width/height change beyond `--size-tol` (12%); labels exempt, a label's box is its text |
| `role_changed` | same content became a different kind of widget (FAB label → Button) — information, not a defect |
| `reordered` | reading order `(row band, x)` of the paired widgets differs; one finding per page |
| `moved_by_data` | position follows a data set that is itself different; see `dataset_gap` |
| `unjudged` | unmatched nodes with no resolvable role (Compose `View`), counted instead of spamming `missing` |

`row band = round(y / median widget height)`: sorting a two-column grid by `y` alone invents
reorderings, so the order is `(band, x)`.

## 3. Measured results (sunflower, both devices)

| Page | widgets | pairs | summary |
|---|---|---|---|
| garden | 10 / 7 | 5 | `page_offset 1, missing 1, extra 2, moved 1, resized 1, unjudged 1, pass 3` |
| plant list | 37 / 19 | 16 | `page_offset 1, dataset_gap 1, missing 2, extra 3, moved 6, role_changed 1, moved_by_data 6, pass 3` |

Before this change the plant list produced **21 `missing`** rows, 19 of which were Compose `View`
nodes that could not be classified and 12 of which were the same repeated "Picture of plant"
template. They now collapse into one `dataset_gap` plus one `unjudged` count.

## 4. Differences from the paper

| GUIPilot | Here |
|---|---|
| optimal alignment with typed costs | done (Hungarian + cost function) |
| partial order of widgets | only reading order `(band, x)`; no full partial order |
| blocks (container of widgets) | not implemented — leaves only, containers dropped |
| 1:N matching (one Android card = container + image + two labels) | not implemented, 1:1 only |
| thresholds from a design mock | fixed tolerances; relative calibration still missing |

## 5. Still open

1. 1:N block matching and container→block derivation, needed once screens exceed ~50 widgets.
2. Relative tolerances: measure `move_tol` / `size_tol` from repeat captures like D2 does with `eps`.
3. `reordered` has only been exercised on synthetic inversions, not on a real page.
4. `dataset_gap` thresholds (50% of a role, 80% distinct labels) are heuristics picked from two
   pages; they need a third and fourth app before they can be trusted.
