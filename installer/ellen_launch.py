"""PyInstaller entry point for the Ellen .exe.

PyInstaller's bootloader runs the entry script as `__main__`, with no
parent package — so a `from ..first_launch import ...` style relative
import inside `traffic_intake/ui/__main__.py` fails with
"attempted relative import with no known parent package."

Fix: import + invoke `main()` from this top-level launcher. Python
imports `traffic_intake.ui.__main__` as a fully-qualified module here
(parent packages established), so every relative import inside the
package resolves correctly.

Don't move this back into the package — that re-creates the same
issue.

Observed 2026-05-26 when the first .exe built from the spec crashed
on launch with the relative-import error.
"""
from __future__ import annotations

import sys


def main() -> int:
    # Import lazily so PyInstaller's static analysis still picks up
    # everything via the spec's Analysis(...) hidden imports + the
    # spec-level `pathex` that puts `src/` on sys.path. Lazy import
    # also lets the splash claim AppUserModelID + show before any
    # heavy module pulls happen.
    from traffic_intake.ui.__main__ import main as _real_main
    return int(_real_main() or 0)


if __name__ == "__main__":
    sys.exit(main())
