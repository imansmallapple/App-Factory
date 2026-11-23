# D2 — app-authored style drift

`tools/visual_d2.py` implements "this control is mine, and it no longer looks like it should".
Two judgements, because uniformity alone lets an entire class drift unnoticed.

## 1. What is judged

| Level | Question | Needs Android? | Verdict |
|---|---|---|---|
| intra-page | does one element break the style its page sets? | no | `isolated_style_fault` |
| cross-platform | did the whole class move away from the Android original? | yes | `class_style_drift` (one finding per role+channel) |
| typography | text height moved, but font metrics differ by platform | yes | `typography_check` — verify sp/fp, not a defect by itself |

Only roles with `check_style=true` are looked at; platform-owned controls are D1 and are skipped
before any measurement.

## 2. Channels

| Channel | How it is measured |
|---|---|
| `fill` | dominant colour inside the element (mode of quantised RGB, not the mean — text poisons a mean) |
| `text` | mean of the darkest 25% of pixels; skipped when the crop has no contrast (`std(lum) <= 15`) |
| `radius` | corner arc: how far down the top row the fill only reaches, normalised by the shorter side |
| `height` | element height / screen height (font-size proxy) |

Colours are compared in CIE Lab (CIE76 ΔE), geometry in normalised units, so the two sides can be
1080×2400 and 1280×2832.

Not every channel applies to every role (`CHANNEL_MASK`): a label has no background of its own, an
image has no text colour. `label` → `text,height`; `image` → `radius,height`; `divider` → `fill`;
everything else → all four.

## 3. Thresholds are measured, never invented

```
eps   p95 of |delta| between two captures of the same untouched screen, per channel
z     |x - median| / max(MAD, RES_FLOOR)
flag  when  |delta| > eps  AND  z > 3.5

RES_FLOOR   measurement resolution (1 dE, 0.005 of the short side, 0.001 of screen height)
MIN_GROUP   >= 5 instances of a role on a page, otherwise intra-page is skipped
```

`eps` answers "could this just be capture noise", `z` answers "is this element unlike its siblings".
They are deliberately separate: a flat group has `MAD = 0`, and dividing by zero would flag
everything.

A noise floor is only accepted when the two captures really are the same screen: if
`mean|diff| > 5` the tool prints `screen was not static ... eps NOT measured` and falls back to
defaults, recording `eps_source` in the JSON. (First attempt on the phone produced
`fill eps = 97` because the screen had dimmed between the two shots — the guard exists because of
that.)

## 4. Collapsing a whole class into one finding

ScreenProof's hard negative ("change every sibling alike") is implemented in `level3`: per role and
channel, count elements whose |delta| > eps; if at least 80% of them moved, and at least 80% agree
in direction, emit **one** `class_style_drift` instead of N element findings.

Without this, a port that recolours every button produces hundreds of identical rows.

## 5. Results

Fixtures (`tools/fixtures/d2_demo/`, six buttons per page):

| Fixture | What is in it | Output |
|---|---|---|
| A | one button rounded, five square | `isolated_style_fault` ×1 (radius 0.3 vs median 0, z=60) |
| B | whole class recoloured, uniformly | `class_style_drift` ×1 (fill, n=6, agreement 1.0) |
| C | identical pair | no findings |

sunflower garden on both devices, eps measured from a second capture of the untouched screen:

```
noise floor (eps): fill 0.5  text 0.5  radius 0.004  height 0.001   source: measured(both)
cross_platform label height  n=4 over_eps=3  median_delta=-0.005  -> typography_check
summary: {'typography_check': 1}
```

No false `class_style_drift`: the port's buttons, labels and images measured inside eps. The only
row is the 0.5%-of-height text difference, i.e. Roboto vs HarmonyOS Sans — reported for a human to
verify, not counted as a defect.

## 6. Still open

1. `border` and `shadow` channels are not measured yet (the shadow estimator needs a
   background-region sample, which is unreliable on dark themes).
2. Level 2 (page-to-page drift inside one app) needs multi-page input; today each run is one page.
3. Matching is still text-first + nearest centre 1:1; a 200-element screen needs descriptors and
   1:N block matching before D2 numbers can be trusted at that scale.
4. No brand-token comparison: when a design token file exists, comparing against it beats any
   statistical test.
5. `radius` uses only the top-left corner and assumes one uniform radius (no per-corner values).
