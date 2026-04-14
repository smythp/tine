# Tine GNOME Shell Extension

This directory contains the GNOME Shell extension tine uses to take screenshots, list windows, and focus windows without going through the Wayland Screencast portal (which would throw a consent dialog on every call and break headless agent flows).

## Why an extension at all?

The Wayland Screencast portal requires interactive consent per session. For an AI agent driving the desktop, that's a non-starter. Tine sidesteps the portal by exposing the GNOME Shell's internal screenshot and window APIs over a D-Bus interface. The extension runs inside gnome-shell, so it has direct access without needing portal permission.

## D-Bus API

Bus name: session bus
Object path: `/org/gnome/OCRScreenshot`
Interface: `org.gnome.OCRScreenshot`

Methods:

- `Full(filename: s) -> b` — full-screen capture, writes PNG to `filename`, returns success.
- `Area(filename: s) -> b` — interactive region picker, writes PNG, returns success.
- `ListWindows() -> s` — returns a JSON string with one object per normal window: `{title, wm_class, app_id, x, y, width, height, focused}`.
- `FocusWindow(query: s) -> b` — case-insensitive substring match on title or wm_class, calls `meta_window.activate()`, returns whether a match was found.
- `InvertColors() -> b` / `GetInvertState() -> b` — toggle a GLSL invert effect for low-vision workflows.

## Installation

```bash
cp -r extension/ocr-screenshot@local ~/.local/share/gnome-shell/extensions/
# Log out and back in (Wayland cannot hot-reload extensions)
gnome-extensions enable ocr-screenshot@local
```

Verify it's running:

```bash
gnome-extensions info ocr-screenshot@local
gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/OCRScreenshot \
    --method org.gnome.OCRScreenshot.ListWindows
```

## About the name

The extension is currently named `ocr-screenshot@local` for historical reasons — it started as a helper for an OCR pipeline before tine was extracted. Don't read into it. The extension does screenshots, window listing, and window focus. No OCR happens here; tine's OCR (when it lands in 0.2) runs in Python, not in the extension. A rename is planned for a future release.

## Tested on

GNOME Shell 49, Wayland. Should work on 47+ with minor adjustments to the `shell-version` field in `metadata.json`; other Shell versions untested.

## License

Apache License 2.0, same as the rest of tine.
