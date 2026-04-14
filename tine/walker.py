"""AT-SPI2 subprocess walker: run walker module and validate output.

Shared by cli.py and describe.py to avoid duplicating OOM guards and
JSON validation logic.
"""

import json
import subprocess
import sys


def walk_atspi(app=None):
    """Run AT-SPI2 walker in a subprocess to avoid D-Bus cache staleness.

    Returns the raw tree dict (JSON parsed from subprocess stdout).
    Raises RuntimeError on failure.
    """
    cmd = [sys.executable, '-m', 'tine.atspi_walker']
    if app is not None:
        cmd.append(app)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError('AT-SPI2 walker timed out after 30s')
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if 'No module named' in stderr and 'gi' in stderr:
            raise RuntimeError(
                "AT-SPI2 walker failed: PyGObject (python-gi) is not installed. "
                "Install it via your distro package manager, not pip: "
                "'sudo apt install python3-gi gir1.2-atspi-2.0' (Debian/Ubuntu), "
                "'sudo pacman -S python-gobject at-spi2-core' (Arch), or "
                "'sudo dnf install python3-gobject at-spi2-core' (Fedora)."
            )
        raise RuntimeError(f'AT-SPI2 walker failed: {stderr}')
    _ATSPI_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
    if len(result.stdout) > _ATSPI_MAX_BYTES:
        raise RuntimeError(
            f'AT-SPI2 walker output exceeds 50 MB ({len(result.stdout)} bytes); '
            'aborting to prevent OOM'
        )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'AT-SPI2 walker returned invalid JSON: {e}') from e
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f'AT-SPI2 walker returned unexpected type: {type(parsed).__name__}'
        )
    return parsed
