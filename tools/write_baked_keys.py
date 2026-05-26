"""Generate `src/traffic_intake/_baked_keys.py` from the local keyring.

Called by `tools\\build_installer.bat` as step 1 of the installer build.
The generated module is gitignored — only exists for the duration of
this build, then deleted by the orchestrator after the .exe is produced.

Override either key via env var (e.g., for CI builds with a key that
isn't stored on the local machine).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import keyring

SERVICE = "traffic-intake"
KEYS = [
    # (env var override, keyring entry name, _baked_keys variable, friendly name for errors)
    ("ANTHROPIC_API_KEY",       "anthropic_api_key",        "ANTHROPIC",        "Anthropic"),
    ("GOOGLE_GEOCODING_API_KEY", "google_geocoding_api_key", "GOOGLE_GEOCODING", "Google Geocoding"),
    ("HERE_API_KEY",            "here_api_key",             "HERE",             "HERE"),
]

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "src" / "traffic_intake" / "_baked_keys.py"


def _resolve(env_var: str, keyring_name: str) -> str | None:
    val = os.environ.get(env_var)
    if val:
        return val.strip()
    return keyring.get_password(SERVICE, keyring_name)


def main() -> int:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for env_var, keyring_name, var_name, friendly in KEYS:
        val = _resolve(env_var, keyring_name)
        if val:
            resolved[var_name] = val
        else:
            missing.append(f"  - {friendly} (set {env_var} env var or save via keyring)")

    if missing:
        print("ERROR: missing API keys for installer build:", file=sys.stderr)
        for m in missing:
            print(m, file=sys.stderr)
        return 1

    body_lines = [
        '"""GENERATED — do not edit.',
        "",
        "Baked API keys for the shipped installer. Written by",
        "tools/write_baked_keys.py from the local keyring at build time.",
        "Gitignored — never commit. Deleted by build_installer.bat after",
        "the .exe is produced.",
        '"""',
        "",
    ]
    for var_name, val in resolved.items():
        # Use repr() so embedded quotes / specials are safely escaped.
        body_lines.append(f"{var_name} = {val!r}")
    body_lines.append("")  # trailing newline

    OUT.write_text("\n".join(body_lines), encoding="utf-8")

    # Confirm what landed (masked) so the operator can sanity-check.
    print(f"Wrote {OUT.relative_to(REPO)}:")
    for var_name, val in resolved.items():
        print(f"  {var_name}: {val[:12]}...{val[-4:]}  (len={len(val)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
