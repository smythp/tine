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

> **Status:** alpha. Tested on GNOME 49 Wayland / Arch Linux. 475 tests passing. API may change before 1.0.

---

## Why this exists

Existing computer-use tools on Linux are stuck in one of three corners:

- **Pure vision (pyautogui, Anthropic computer use)** — slow, coordinate-fragile, no structure. Every click is a screenshot and a guess.
- **Pure accessibility (AT-SPI2 scripts)** — structured but sparse. Chrome, Electron, anything with a shadow DOM is a black box.
- **Portal-based automation (RemoteDesktop/ScreenCast portals)** — throws a consent dialog on every session, breaking headless agent flows.

Tine is the hybrid. AT-SPI2 when the app exposes it, labeled-grid vision when it doesn't, kernel-level `uinput` for input so portals never enter the picture.

---

## Quickstart

Tine targets **GNOME Shell on Wayland, Linux only**. Other environments are out of scope for v1.

### 1. System prerequisites

```bash
# Arch / Manjaro
sudo pacman -S python python-gobject gobject-introspection at-spi2-core

# Fedora
sudo dnf install python3 python3-gobject gobject-introspection at-spi2-core

# Debian / Ubuntu
sudo apt install python3 python3-gi gir1.2-atspi-2.0 at-spi2-core
```

### 2. Install tine

```bash
pip install tine-cli
```

The PyPI package is `tine-cli`; the installed command is `tine`.

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

(Yes, the extension is named `ocr-screenshot` for historical reasons — see [extension/README.md](extension/README.md). It does screenshots, window listing, and window focus. No OCR happens inside the extension. A rename is planned.)

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
| `tine click ref_3`   | Click the center of a cached ref's bounding box.                  |
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

## The killer example: log into Reddit from a Claude Code session

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

## Known limitations (v0.1)

- **GNOME Shell / Wayland only.** Other compositors (KDE, Hyprland, Sway) should work for the input side, but the screenshot/focus path depends on the bundled GNOME Shell extension. PRs welcome.
- **Linux only.** No macOS or Windows plans.
- **Requires uinput access.** One-time udev setup. No way around this without portals.
- **AT-SPI2 is sparse in some apps.** Chrome, some Electron apps, most games. Use `tine screenshot --grid` as the fallback.
- **OCR text refs not yet shipped.** Coming in 0.2 — tracked in a separate brief.

---

## Contributing

Tests:

```bash
pip install -e ".[dev]"
pytest
```

Most tests run without a display — the input and screenshot layers are mocked. The AT-SPI2 walker tests use fixtures from `research/fixtures/`.

Issues and PRs welcome. Keep changes focused; tine is deliberately small.

---

## License

Apache License 2.0. See [LICENSE](LICENSE).
