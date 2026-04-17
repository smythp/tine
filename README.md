# tine

**Drive a GNOME Wayland desktop from AI agents. CLI-first, no portals, no consent dialogs.**

Tine is a command-line bridge between an AI coding agent (Claude Code, Codex, etc.) and a running Linux desktop. It reads the screen, walks the accessibility tree, and injects keyboard and mouse events at the kernel level — no Wayland portal dialogs, no per-action consent prompts, no X11 fallback hacks.

```
$ tine describe
[screenshot + AT-SPI2 tree summary]

$ tine click ref_17           # click by accessibility-tree ref
$ tine click B3               # click by labeled grid cell
$ tine type "hello, world"    # kernel-level key injection
$ tine key ctrl+t             # modifier combos
$ tine focus Firefox          # raise + focus a window
```

> **Status:** alpha. Tested on GNOME 49 Wayland / Arch Linux. API may change before 1.0.

---

## Why

Anthropic's computer-use feature works on Windows and macOS. If you use Linux — and especially if you use Wayland, which is more locked-down than X11 and breaks most of the existing Linux automation stack — you're mostly out of luck. Tine is an attempt at a usable Wayland alternative.

It reads the screen three ways:

- **AT-SPI2 accessibility tree.** When an app exposes its widgets to the Linux a11y stack (GTK apps, Qt apps, most native GNOME stuff), tine walks the tree and gives the agent structured data — roles, names, bounding boxes, actions. The agent can say `click ref_17` and tine clicks the center of the button with that ref.
- **Labeled coordinate grid.** When AT-SPI2 is sparse or missing (Chrome, Electron, most web content, games), tine overlays a labeled grid on the screenshot and the agent says `click B3`. Not fancy, but it works.
- **OCR text refs.** Run OCR on the screenshot (RapidOCR, local, CPU) and get refs like `ref_t3` tied to detected text regions. The agent can click by on-screen text — `tine click ref_t3` clicks the center of the OCR region with that ref. Optional, lazy-loaded, install with `pip install tine-cli[ocr]`.

And it injects input at the kernel level through `/dev/uinput`, so no Wayland portal ever prompts for consent and no headless agent loop gets interrupted by a dialog.

---

## Quickstart

Tine targets **GNOME Shell on Wayland, Linux only**. Other environments are out of scope for v1.

### 1. System prerequisites

Tine uses PyGObject (`gi.repository.Atspi`) to read the accessibility tree. PyGObject is best installed via your distro's package manager — installing it from pip requires building `pycairo` and `PyGObject` from source and pulling in `libcairo2-dev`, `libgirepository-dev`, `pkg-config`, `cmake`, and a C compiler. Don't do that. Install these instead:

```bash
# Arch / Manjaro
sudo pacman -S python python-gobject at-spi2-core python-pip python-virtualenv

# Fedora
sudo dnf install python3 python3-gobject at-spi2-core python3-pip python3-virtualenv

# Debian / Ubuntu
sudo apt install python3 python3-gi gir1.2-atspi-2.0 at-spi2-core python3-pip python3-venv
```

### 2. Install tine

Create a venv that inherits system site-packages (so the distro's `python3-gi` is importable) and install `tine-cli` into it:

```bash
python3 -m venv --system-site-packages ~/.local/share/tine-venv
~/.local/share/tine-venv/bin/pip install tine-cli
mkdir -p ~/.local/bin
ln -sf ~/.local/share/tine-venv/bin/tine ~/.local/bin/tine
# ensure ~/.local/bin is on your PATH
```

**Optional: OCR text refs.** If you want `tine click ref_tN` (click by on-screen text), install the `[ocr]` extra. It pulls in RapidOCR and ONNX Runtime (~200 MB), plus a couple of system libs OpenCV needs:

```bash
# Debian/Ubuntu: OpenCV needs libGL and glib at runtime
sudo apt install libgl1 libglib2.0-0

~/.local/share/tine-venv/bin/pip install "tine-cli[ocr]"
```

OCR is lazy-imported, so base tine works fine without it and only `tine screenshot --ocr` / `tine describe --ocr` / `tine click ref_tN` need the extra.

The PyPI package is `tine-cli` (the name `tine` was already taken); the installed command is `tine`.

**Why the `--system-site-packages` venv:** modern Debian, Ubuntu, and Fedora set [PEP 668](https://peps.python.org/pep-0668/) "externally-managed-environment" on the system Python, so `pip install tine-cli` fails there. A venv is the correct answer, and `--system-site-packages` makes it inherit the system `python3-gi` so tine doesn't have to rebuild PyGObject from source.

**Arch / Manjaro users** can also do `pip install --user tine-cli` directly if they prefer — Arch doesn't enforce PEP 668.

### 3. Give your user uinput access (one-time)

Tine injects input via `/dev/uinput`. Add yourself to the `input` group and an ACL so you don't need root:

```bash
sudo usermod -aG input $USER
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
# log out and back in for group changes to take effect
```

### 4. Install the GNOME Shell extension

The `extension/` directory in this repo contains a small GNOME Shell extension that exposes screenshots and window enumeration over D-Bus. Tine calls into it instead of going through the Screencast portal.

```bash
cp -r extension/ocr-screenshot@local ~/.local/share/gnome-shell/extensions/
# log out / back in (Wayland can't hot-reload extensions), then:
gnome-extensions enable ocr-screenshot@local
```

### 5. Try it

```bash
tine describe                 # full desktop context (screenshot + a11y tree)
tine windows                  # list open windows
tine screenshot --grid        # overlay a labeled grid (A1..Z26)
tine click B4                 # click center of grid cell B4
tine focus Firefox
tine type "https://news.ycombinator.com"
tine key Return
```

If all five commands run clean, you're done — hand the CLI to an agent and let it drive.

---

## The commands

| Command              | What it does                                                      |
|----------------------|-------------------------------------------------------------------|
| `tine tree`          | Walk the AT-SPI2 accessibility tree. Assigns short refs (`ref_1`, `ref_2`, ...) and caches them. |
| `tine tree --app Firefox` | Scope the walk to a single app.                              |
| `tine screenshot`    | Full-screen capture via the GNOME Shell extension.                |
| `tine screenshot --annotate` | Overlay Set-of-Mark boxes on known a11y elements.         |
| `tine screenshot --grid`     | Overlay a labeled coordinate grid for sparse-tree apps.   |
| `tine screenshot --ocr`      | Run OCR, add `ref_tN` entries for detected text regions.  |
| `tine describe --ocr`        | `describe` plus OCR text refs (needs `[ocr]` extra).      |
| `tine click ref_3`   | Click the center of a cached a11y ref's bounding box.             |
| `tine click ref_t3`  | Click the center of an OCR text ref.                              |
| `tine click B3`      | Click the center of grid cell B3.                                 |
| `tine click 450,320` | Click raw pixel coordinates.                                      |
| `tine target ref_3`  | 3x3 sub-grid crosshair for refinement when `click` misses.        |
| `tine activate ref_3`| Invoke the AT-SPI2 action directly — no mouse, no coordinates.    |
| `tine type "text"`   | Type via `EV_KEY` events.                                         |
| `tine key ctrl+c`    | Press a key combination.                                          |
| `tine windows`       | Enumerate windows: title, position, size, focus state.            |
| `tine focus Firefox` | Raise and focus a window by title match.                          |
| `tine inputd start`  | Start the persistent input daemon (8x faster per command).        |
| `tine describe`      | Screenshot + tree in one call — the standard "what's on screen?". |

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│  Claude Code / Codex / other agent session             │
└────────────────────────┬───────────────────────────────┘
                         │  shell commands
                         ▼
┌────────────────────────────────────────────────────────┐
│  tine CLI                                              │
│  ┌───────────────┐  ┌────────────────┐  ┌──────────┐   │
│  │ ref cache     │  │ grid resolver  │  │ inputd   │   │
│  │ (ref_N→bbox)  │  │ (B3→pixels)    │  │ (8x fast)│   │
│  └───────┬───────┘  └────────┬───────┘  └─────┬────┘   │
└──────────┼───────────────────┼────────────────┼────────┘
           │                   │                │
           ▼                   ▼                ▼
    ┌──────────┐       ┌──────────────┐   ┌─────────┐
    │ AT-SPI2  │       │ GNOME Shell  │   │ /dev/   │
    │ D-Bus    │       │ extension    │   │ uinput  │
    │ (read)   │       │ (screenshot, │   │ (kernel │
    │          │       │  windows)    │   │  input) │
    └──────────┘       └──────────────┘   └─────────┘
```

- **AT-SPI2** gives structured UI data: roles, names, bounding boxes, states, actions. Read-only, standard accessibility API, no special permissions.
- **GNOME Shell extension** exposes a D-Bus interface for screenshots and window management without hitting the Screencast portal.
- **python-evdev / uinput** injects mouse (`EV_ABS`) and keyboard (`EV_KEY`) events at the kernel level. No compositor cooperation, no consent prompts.

---

## Example: log into Reddit from a Claude Code session

```bash
tine focus Firefox
tine key ctrl+l
tine type "https://old.reddit.com/login"
tine key Return

tine describe                 # agent reads the login page
# → agent sees ref_12 "username field", ref_13 "password field", ref_14 "log in button"

tine click ref_12
tine type "my_username"
tine click ref_13
tine type "$REDDIT_PASSWORD"
tine activate ref_14          # bypass the click — invoke the a11y action directly
```

Each step is a single shell command. The agent reads `describe`, decides, runs one command, checks `describe` again. No portals, no consent dialogs, no coordinate-by-screenshot guesswork.

---

## How tine compares

| Tool                   | Input method            | Wayland? | Structured reads | Portal dialogs  |
|------------------------|-------------------------|----------|------------------|-----------------|
| **tine**               | uinput (kernel)         | ✅       | AT-SPI2 + grid   | ❌ none          |
| xdotool                | X11                     | ❌       | X11 props        | n/a             |
| ydotool                | uinput (kernel)         | ✅       | ❌               | ❌               |
| pyautogui              | X11 / mouse events      | partial  | ❌               | n/a             |
| Playwright-desktop     | browser only            | n/a      | browser DOM      | n/a             |
| Anthropic computer use | screenshot + coordinates| ✅       | vision only      | portal consent  |

Tine is the only one that combines structured reads with portal-free input on Wayland.

---

## Click sanity checks

Silent misses — the click physically lands but nothing interactive was there — are the worst failure mode when an agent drives a CLI, because the agent has no immediate signal that anything went wrong. Tine runs two cross-checks to catch them.

**Coord verification at describe time.** When `tine describe --ocr` runs, every named AT-SPI2 ref is cross-referenced against OCR detections with the same text. Each ref gets a `coord_verified` tag:

| Value     | Meaning                                                                                       |
|-----------|-----------------------------------------------------------------------------------------------|
| `true`    | An OCR detection of the same text sits within ~20 px of the AT-SPI2 bbox center. Safe to click. |
| `false`   | OCR found the text at a different pixel position. The AT-SPI2 bbox is likely wrong. If a `disputed_by` ref is shown, use that `ref_tN` instead — or fall back to `tine activate`. |
| `unknown` | No OCR coverage or ambiguous match (duplicate text with mismatched counts). Treat as unverified. |

When one or more refs come back `false` / `unknown`, `tine describe --ocr` prints a **Coord Verification** section listing them so the agent can pick a safer click target. This check is free — it runs inside the describe pipeline you were going to call anyway.

**Post-click tree hash.** After `tine click ref_N`, tine walks the AT-SPI2 subtree for the focused app again and compares a structural hash (depth + roles + names + states, no bboxes) against the pre-click state. If the hash is unchanged, it emits a stderr warning:

```
warning: click may not have landed — AT-SPI2 tree unchanged after click (app='Firefox')
```

This isn't free: two AT-SPI2 walks plus a 300 ms settle delay. Measured on archgnome, that adds roughly **0.9 s per click on simple apps (Calculator) and ~2.1 s on complex apps (Firefox with a loaded page)**. It's opt-out, not opt-in — silent misses justify the cost for most agent use — but if you're running rapid click sequences (form fill, benchmarks) you can disable with `TINE_NO_VERIFY_CLICK=1`.

Limitations:

- **Best-effort signal, not a guarantee.** Pages with live-region content (tickers, spinners, autoplay) can mutate the tree on their own timer. If the tree changes within the 300 ms settle window, the warning won't fire even when the click was a no-op. Treat the warning as a hint.
- **Idempotent clicks trip it too.** Clicking an already-focused button, or a button whose action is a no-op in the current state, looks identical to a silent miss from the AT-SPI2 tree's perspective.
- **ref_N only.** Grid-cell, raw-coordinate, and `ref_tN` clicks don't run the check (the latter because OCR refs are their own source of truth).

---

## Known limitations (v0.1)

- **GNOME Shell / Wayland only.** Other compositors (KDE, Hyprland, Sway) should work for the input side, but the screenshot/focus path depends on the bundled GNOME Shell extension. PRs welcome.
- **Linux only.** No macOS or Windows plans.
- **Requires uinput access.** One-time udev setup. No way around this without portals.
- **AT-SPI2 is sparse in some apps.** Chrome, some Electron apps, most games. Use `tine screenshot --grid` as the fallback.

---

## Contributing

Tests:

```bash
pip install -e ".[dev]"
pytest
```

Most tests run without a display — the input and screenshot layers are mocked. The AT-SPI2 walker tests use fixtures from `research/fixtures/`.

Issues and PRs welcome.

---

## License

Apache License 2.0. See [LICENSE](LICENSE).
