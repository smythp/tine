# research/probes

Instrumented HTML pages and inspection scripts for debugging tine's coordinate
pipeline and input delivery. Reach for these when tine *seems* to work but
clicks or types don't land the way you expect.

## Why probes exist

tine has three rulers that can all be wrong at once:

1. **AT-SPI2 bboxes** from the walker. Firefox historically reported these in
   a fake frame (finding-20260418-3ff1, fixed in c05fda3); Wayland compositors
   don't tell clients their true screen position so AT-SPI toolkits have to
   fake it in different ways.
2. **OCR bboxes** from RapidOCR on screenshots. Pixel-accurate in theory;
   occasionally drifts in practice (brief-20260418-dmu7).
3. **`tine target` crosshair** overlay. Draws at the coord you ask for, but
   that's visual sanity only — not proof the click will land there
   (notion-20260418-olb9). Before the cursor-motion fix (commit 18db120),
   clicks didn't actually land where the cursor appeared.

Cross-checking one ruler against another won't catch a bug that affects both.
The probes give you a fourth, independent ground truth: actual screen pixels.

## Files

| file | purpose |
| --- | --- |
| `click-probe.html` | Instrumented page for click-delivery debugging. Writes every pointerdown/click event's `clientX/Y`, `isTrusted`, `target`, and a monotonic counter into `document.title` so you can read the DOM's view of a click via `tine windows`. Source of the playbook in `skein folio playbook-20260418-sw7y`. |
| `bbox-probe.html` | Page for AT-SPI bbox / coord-math debugging. 8x8 solid-color markers at known CSS positions (`0,0`, `100,100`, ..., `50,600`) and six named interactive targets whose labels encode their own CSS coords (e.g. `tgtA 20,20`). JS dumps each target's `getBoundingClientRect` into `document.title`. |
| `find_markers.py` | Scans a screenshot PNG for the bbox-probe markers (PIL). Tolerates Firefox dark-mode color inversion. Anchors on the CSS(0,0) marker and checks every other marker lies exactly where linearity predicts — the canonical linearity check. |
| `atspi_bbox_probe.py` | Walks AT-SPI for a target app and dumps each `tgt*` (or `push button` with a name) with its `SCREEN`, `WINDOW`, and `PARENT` coord-type extents side by side. Also dumps the ancestor chain from one leaf up to the desktop so you can see the frame node and its reported origin. |
| `coord_verify_cost.py` | Times the full `describe(ocr=True)` pipeline and reports wall-clock totals plus the `verify_atspi_coords` fraction, named-ref count, OCR detection count, and whether verification produced any `'false'` tag. Feeds the decision in brief-20260418-33wp — measurement (finding-20260419-4m11) showed verify is <0.05% of total describe time, so option 1 (always-on) was kept. |

## The ground-truth workflow

Premise: no existing ruler is trustworthy until you prove it against pixels.

```
bbox-probe.html loaded in Firefox
        │
        ▼
tine screenshot --out /tmp/bbx.png     # real Wayland pixels
        │
        ▼
find_markers.py /tmp/bbx.png           # origin + linearity check
        │ (emits: CSS(0,0) -> screen(X0, Y0), scale 1:1 OK)
        ▼
atspi_bbox_probe.py firefox            # AT-SPI's view
        │
        ▼
compare: real_screen == window_offset + (atspi_xy - frame_xy)
```

All three rulers must agree modulo the known window/frame transforms. If any
two disagree, the one that matches `find_markers.py` is truth.

### Running on archgnome

The probes are local files; push them to the target over SSH, then point tine
at them. From the dev machine:

```sh
# Sync probe page and scripts
cat research/probes/bbox-probe.html    | ssh archgnome 'cat > /tmp/bbox-probe.html'
cat research/probes/find_markers.py    | ssh archgnome 'cat > /tmp/find_markers.py'
cat research/probes/atspi_bbox_probe.py | ssh archgnome 'cat > /tmp/atspi_bbox_probe.py'

# Load in Firefox
ssh archgnome 'source /tmp/tine-env.sh && tine focus Firefox \
  && tine key ctrl+l && tine type file:///tmp/bbox-probe.html && tine key Return'

# Capture + inspect
ssh archgnome 'source /tmp/tine-env.sh && tine screenshot --out /tmp/bbx.png \
  && /home/patrick/projects/tine/venv/bin/python3 /tmp/find_markers.py /tmp/bbx.png'

# AT-SPI side
ssh archgnome '/home/patrick/projects/tine/venv/bin/python3 /tmp/atspi_bbox_probe.py firefox'
```

See `CLAUDE.local.md` for the full `tine-env.sh` + archgnome setup.

### Reading the output

`find_markers.py` expects seven markers. On a normal light page you'll see:

```
c1_magenta   css=(   0,   0) rgb=(255, 0, 255)  screen=(320, 253)  normal
c2_violet    css=( 100, 100) rgb=(127, 0, 255)  screen=(420, 353)  normal
...
Content origin: CSS (0, 0) -> screen (320, 253)
...
c2_violet    expected=(420,353)  actual=(420,353)  OK
...
CSS -> screen mapping is linear with unit scale. Ground truth trusted.
```

Under Firefox's forced-colors dark override you'll see every marker labeled
`inverted` instead — that's cosmetic, positions are still truth.

Non-zero `DRIFT` on any marker means the CSS-to-screen mapping is not linear
or not unit-scale. Stop and investigate before building on the origin value.

## When to reach for which probe

- **"tine click lands in empty space"** → bbox-probe + find_markers +
  atspi_bbox_probe. Compare AT-SPI `tgtA` extents to the real screen position
  the linearity check gives you. Mirrors how finding-20260418-3ff1 was cracked.
- **"click fires but DOM doesn't respond"** → click-probe. Fire clicks at
  distinct coordinates and read `document.title` via `tine windows`. If all
  clicks report the same `clientX/Y`, cursor isn't moving — see
  playbook-20260418-sw7y.
- **"typing feels wrong"** → click-probe has an input target; focus it, type,
  and inspect the title for event counts.

## Extending

Each probe follows the same pattern: encode state in `document.title` so a
second machine can read it over SSH via `tine windows`. If you add a new
probe, keep that pattern — otherwise you'll need screenshots + OCR to read
state, and that defeats the ground-truth guarantee.
