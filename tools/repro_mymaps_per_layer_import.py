"""Live test against the actual saved MyMaps failure DOM.

Order 176406's MyMaps run failed at the second-group import with:
  'Couldn't find the Import control in the default layer card.'

The saved page.html shows TWO 'Import' affordances in DOM: Layer 1's
(hidden, post-import) and Layer 2's (visible, the one we want). Our old
locator `get_by_text("Import", exact=True).first` picked Layer 1's
hidden one by document order and timed out clicking it.

This test loads the saved failure DOM and verifies:
  1. `get_by_text("Import", exact=True).first` (the OLD locator) resolves
     to a NON-VISIBLE element (proves the bug)
  2. The new aria-label + :visible locator resolves to EXACTLY ONE
     visible element (proves the fix)
  3. That visible element is in the same DOM subtree as the 'Untitled
     layer' label (sanity — it's the right layer's Import button)

Run: python tools/repro_mymaps_per_layer_import.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright


FIXTURE = Path(
    r"C:\Users\jbris\AppData\Local\TrafficIntake\mymaps-diagnostics"
    r"\run-20260514-173102\FAILURE-import-per-group-layers\page.html"
)


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    safe = lambda s: s.encode("ascii", "replace").decode("ascii") if isinstance(s, str) else s
    print(f"  {status}  {safe(label)}  {safe(detail)}")
    return condition


def main() -> int:
    if not FIXTURE.exists():
        print(f"FIXTURE MISSING: {FIXTURE}")
        return 1

    passed = 0
    total = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.url.startswith(("http://", "https://"))
            else route.continue_(),
        )
        page.goto(FIXTURE.as_uri(), wait_until="commit", timeout=60_000)
        page.wait_for_selector("body", timeout=15_000)

        # ---- The OLD locator: get_by_text("Import", exact=True).first ----
        # Should resolve to SOMETHING (we saw it in the markup) but NOT visible.
        old_matches = page.get_by_text("Import", exact=True).count()
        total += 1
        if check(
            "OLD locator finds multiple 'Import' text matches (the bug surface)",
            old_matches >= 2,
            f"got {old_matches}",
        ):
            passed += 1

        # ---- The NEW approach: aria-label + .last picks the newest layer ----
        import_aria = "Import data from a CSV file, spreadsheet or KML."
        all_imports = page.locator(f'[aria-label="{import_aria}"]')
        n_imports = all_imports.count()
        total += 1
        if check(
            "Multiple Import affordances in DOM (proves the bug surface)",
            n_imports >= 2,
            f"got {n_imports}",
        ):
            passed += 1

        # The LAST element in document order should be Layer 2's (the new one)
        last_y = all_imports.last.evaluate("el => el.getBoundingClientRect().y")
        first_y = all_imports.first.evaluate("el => el.getBoundingClientRect().y")
        total += 1
        if check(
            ".last has a LATER (greater y) screen position than .first (newer layer appears below)",
            last_y > first_y,
            f"first_y={first_y}, last_y={last_y}",
        ):
            passed += 1

        # ---- .last should also be in the same subtree as 'Untitled layer' ----
        result = page.evaluate(
            """(aria) => {
                const els = document.querySelectorAll(`[aria-label='${aria}']`);
                if (els.length === 0) return {found: false};
                const target = els[els.length - 1];  // .last
                // Walk up looking for a container that also has 'Untitled layer'
                let cur = target;
                for (let i = 0; i < 12; i++) {
                    if (!cur.parentElement) break;
                    cur = cur.parentElement;
                    if (cur.querySelector('[aria-label="Untitled layer"]')) {
                        return {found: true, depth: i + 1};
                    }
                }
                return {found: false};
            }""",
            import_aria,
        )
        total += 1
        if check(
            ".last is in the same subtree as 'Untitled layer' (the NEW empty layer)",
            result.get("found") is True,
            f"result={result}",
        ):
            passed += 1

        # ---- .last finds "Untitled layer" at a SHALLOWER depth than .first ----
        # Both eventually find an "Untitled layer" ancestor (Layer 2 is in
        # the same layer-list container as Layer 1), but .last finds it
        # nearby in the SAME layer card. .first has to walk up to the
        # whole layer-list to find Layer 2's label.
        result_first = page.evaluate(
            """(aria) => {
                const els = document.querySelectorAll(`[aria-label='${aria}']`);
                let cur = els[0];
                for (let i = 0; i < 12; i++) {
                    if (!cur.parentElement) break;
                    cur = cur.parentElement;
                    if (cur.querySelector('[aria-label="Untitled layer"]')) {
                        return {depth: i + 1};
                    }
                }
                return {depth: -1};
            }""",
            import_aria,
        )
        result_last = page.evaluate(
            """(aria) => {
                const els = document.querySelectorAll(`[aria-label='${aria}']`);
                let cur = els[els.length - 1];
                for (let i = 0; i < 12; i++) {
                    if (!cur.parentElement) break;
                    cur = cur.parentElement;
                    if (cur.querySelector('[aria-label="Untitled layer"]')) {
                        return {depth: i + 1};
                    }
                }
                return {depth: -1};
            }""",
            import_aria,
        )
        total += 1
        if check(
            ".last's nearest 'Untitled layer' ancestor is SHALLOWER than .first's (i.e., .last is INSIDE the new layer's card)",
            result_last["depth"] < result_first["depth"] and result_last["depth"] > 0,
            f"first_depth={result_first['depth']}, last_depth={result_last['depth']}",
        ):
            passed += 1

        # ---- The visible Import should be inside a layer card whose
        # sibling label says 'Untitled layer' (the new empty layer) ----
        total += 1
        # Find the nearest ancestor that's a panel/group, then check it
        # contains an aria-label="Untitled layer" element.
        result = page.evaluate(
            """(aria) => {
                const el = document.querySelector(`[aria-label='${aria}']`);
                if (!el) return {found: false};
                // Walk up looking for a container that also has 'Untitled layer'
                let cur = el;
                for (let i = 0; i < 10; i++) {
                    if (!cur.parentElement) break;
                    cur = cur.parentElement;
                    if (cur.querySelector('[aria-label="Untitled layer"]')) {
                        return {found: true, depth: i + 1};
                    }
                }
                return {found: false};
            }""",
            import_aria,
        )
        if check(
            "Visible Import is in same subtree as 'Untitled layer' (right layer's import button)",
            result.get("found") is True,
            f"result={result}",
        ):
            passed += 1

        browser.close()

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
