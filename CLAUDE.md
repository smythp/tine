# Tine — Agent Guide

CLI tool that lets AI agents drive a GNOME Wayland desktop. Hybrid AT-SPI2 accessibility tree + vision approach.

This file is notes for agents (Claude Code, Codex, Aider, etc.) working inside this repo. For user-facing docs, see [README.md](README.md).

## What this is

Tine is the bridge between an AI coding agent and a physical Linux desktop. It reads the screen (screenshots + accessibility tree) and acts on it (keyboard/mouse injection). Think "Playwright for the desktop" but hybrid — structured accessibility data when available, vision fallback when not.

## Architecture

```
Agent session (local or remote)
  └→ tine CLI (runs on target machine)
       ├→ AT-SPI2 (read UI element tree: roles, names, bounding boxes, states)
       ├→ GNOME Shell extension (screenshots, window enumeration, focus)
       ├→ python-evdev / uinput (keyboard + mouse injection, kernel level)
       └→ ref cache (element ID → bounding box mapping)
```

**No portals, no consent dialogs.** Input injection goes through `/dev/uinput` at the kernel level via python-evdev. Mouse uses `EV_ABS`, keyboard/typing use `EV_KEY`. Screenshots go through the bundled GNOME Shell extension's D-Bus API. AT-SPI2 is just D-Bus queries.

## CLI surface

```bash
tine tree                      # AT-SPI2 tree with element refs
tine tree --app Firefox        # scoped to one app
tine screenshot                # full screen capture via extension
tine screenshot --annotate     # Set-of-Mark overlays
tine screenshot --grid         # labeled coordinate grid (A1, B3, ...)
tine screenshot --annotate --grid
tine click ref_3               # click at ref_3's bounding box center
tine click B3                  # click center of grid cell B3
tine click 450,320             # click at pixel coordinates
tine type "hello world"        # type text via evdev key events
tine key ctrl+c                # key combo
tine activate ref_3            # invoke AT-SPI2 action (bypasses coordinate pipeline)
tine windows                   # list windows (title, position, size, focused)
tine focus Firefox             # raise + focus window via extension D-Bus
tine describe                  # screenshot + tree → combined context for agent
```

## Key design decisions

- **CLI, not MCP.** Agents call it via shell. Simpler, more debuggable, composable. Works from any session.
- **Hybrid reads.** AT-SPI2 tree as primary, vision as fallback. Neither alone is good enough.
- **Ref system.** Short IDs (`ref_1`, `ref_2`, ...) assigned to AT-SPI elements. `click ref_3` resolves to bounding box center. Refs cached between calls.
- **Grid system.** When AT-SPI2 trees are sparse (shadow DOM, Chrome, most Electron apps), `screenshot --grid` overlays a labeled coordinate grid. Agents pick cells (B3, E5) and `click B3` resolves to cell center.
- **Lowest practical level.** python-evdev/uinput for input (no portal consent). GNOME extension for screenshots and focus (no portal permission prompts). AT-SPI2 via D-Bus (standard accessibility, no special permissions).
- **Target: GNOME Shell 49 Wayland on Linux.** Other compositors out of scope for v1.

## Dependencies

- Python 3.11+
- `PyGObject` (`gi.repository.Atspi` for the accessibility tree)
- `python-evdev` (input injection via uinput — `EV_ABS` mouse, `EV_KEY` keyboard)
- `Pillow` (screenshot annotation, grid overlay)
- GNOME Shell extension `ocr-screenshot@local` — bundled in `extension/`
- User must be in `input` group with uinput ACL (one-time udev setup, see README)

## Tests

```bash
pip install -e ".[dev]"
pytest
```

475 tests pass on master. Input and screenshot layers are mocked so most tests run without a display. AT-SPI2 walker tests use fixtures in `research/fixtures/`.

## Principles

- Simple over clever. A CLI that works beats an architecture that doesn't.
- Hybrid over pure. AT-SPI when available, vision when not, both when useful.
- Kernel level over compositor cooperation. No portals, no consent dialogs.
- Agent-first design. Output formats optimized for LLM consumption, not humans.
- Small surface. Every new command earns its place or doesn't ship.
