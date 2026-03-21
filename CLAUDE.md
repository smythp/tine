# Tine

CLI tool that lets AI agents drive a GNOME Wayland desktop. Hybrid AT-SPI2 accessibility tree + vision approach.

## What This Is

Tine is the bridge between a Claude Code session and a physical Linux desktop. It reads the screen (screenshots + accessibility tree), and acts on it (keyboard/mouse injection). Think "Playwright for the desktop" but hybrid — structured accessibility data when available, vision fallback when not.

Part of the spiritengine ecosystem (siblings: skein, spindle, shuttle, mill, horizon).

## Architecture

```
CC session (local or remote)
  └→ tine CLI (runs on target machine)
       ├→ AT-SPI2 (read UI element tree: roles, names, bounding boxes, states)
       ├→ GNOME Shell extension (screenshots, window enumeration)
       ├→ ydotool / uinput (keyboard + mouse injection, kernel level)
       └→ ref cache (element ID → bounding box mapping)
```

**No portals, no consent dialogs.** Input injection goes through /dev/uinput at the kernel level. Screenshots go through our GNOME Shell extension D-Bus API. AT-SPI2 is just D-Bus queries.

## CLI Interface

```bash
tine tree                      # AT-SPI2 tree with element refs
tine tree --app Firefox        # scoped to one app
tine screenshot                # full screen capture via extension
tine screenshot --annotate     # screenshot with Set-of-Mark overlays
tine click ref_3               # click at ref_3's bounding box center
tine click 450,320             # click at pixel coordinates
tine type "hello world"        # type text
tine key ctrl+c                # key combo
tine windows                   # list windows (title, position, size, focused)
tine focus Firefox             # raise + focus window
tine describe                  # screenshot + tree → combined context for agent
```

## Key Design Decisions

- **CLI, not MCP.** CC calls it via Bash. Simpler, more debuggable, composable. Works from spins, shards, any session.
- **Hybrid approach.** AT-SPI2 tree as primary, vision as fallback. Neither alone is good enough.
- **Ref system.** Short IDs (ref_1, ref_2) assigned to AT-SPI elements. `click ref_3` resolves to bounding box center. Refs cached between calls.
- **Lowest practical level.** ydotool/uinput for input (no portal consent dialogs). GNOME extension for screenshots (no portal permission prompts). AT-SPI2 via D-Bus (standard accessibility, no special permissions).
- **Target: GNOME 49 Wayland on Arch Linux** (archgnome machine at 192.168.1.7).

## Dependencies

- Python 3.11+
- pyatspi2 / python-atspi (AT-SPI2 bindings)
- ydotool (input injection) — or direct uinput writes
- GNOME Shell extension (ocr-screenshot@local, already deployed on archgnome)
- User must be in `input` group with uinput ACL

## Development

This project follows the RSP (Research → Spec → Prototype) methodology. See SKEIN briefs for current phase and work items.

## Principles

- Simple over clever. A CLI that works beats an architecture that doesn't.
- Hybrid over pure. AT-SPI when available, vision when not, both when useful.
- Kernel level over compositor cooperation. No portals, no consent dialogs.
- Agent-first design. Output formats optimized for LLM consumption, not humans.
