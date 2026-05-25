"""Unit + fixture test for the Tube Counts time-period form.

Two layers:
1. Pure function: _tube_duration_and_unit maps TimeWindow -> (Duration, Unit)
2. Live Playwright: load run-20260514-144407/06-add-study-groups page.html,
   inject the Tube modal visible, call _fill_tube_time_period, verify
   Duration field and unit <select> read back the expected values.

Run: python tools/repro_tube_time_period.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright

from traffic_intake.models import TimeWindow  # type: ignore
from traffic_intake.qchub import _fill_tube_time_period, _tube_duration_and_unit  # type: ignore


FIXTURE = Path(
    r"C:\Users\jbris\AppData\Local\TrafficIntake\qchub-diagnostics"
    r"\run-20260514-144407\06-add-study-groups\page.html"
)


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}  {detail}")
    return condition


def main() -> int:
    passed = 0
    total = 0

    # ----- Layer 1: duration/unit mapping -----
    print("Layer 1 — _tube_duration_and_unit:")
    cases = [
        # (window, expected_duration, expected_unit, description)
        (TimeWindow(label="72-hr", start="00:00", end="23:59", total_hours=72), 3, "Day", "72h -> 3 Days"),
        (TimeWindow(label="24-hr", start="00:00", end="23:59", total_hours=24), 1, "Day", "24h -> 1 Day"),
        (TimeWindow(label="1 week", start="00:00", end="23:59", total_hours=168), 7, "Day", "168h -> 7 Days"),
        (TimeWindow(label="48h", start="00:00", end="23:59", total_hours=48), 2, "Day", "48h -> 2 Days"),
        (TimeWindow(label="36-hr", start="00:00", end="23:59", total_hours=36), 36, "Hour", "36h -> 36 Hours (not /24)"),
        (TimeWindow(label="8h", start="00:00", end="23:59", total_hours=8), 8, "Hour", "8h -> 8 Hours"),
        # No total_hours: compute from start-end
        (TimeWindow(label="7am-7pm", start="07:00", end="19:00"), 12, "Hour", "07:00-19:00 -> 12 Hours"),
        (TimeWindow(label="AM peak", start="07:00", end="09:00"), 2, "Hour", "07:00-09:00 -> 2 Hours"),
    ]
    for w, exp_d, exp_u, desc in cases:
        total += 1
        d, u = _tube_duration_and_unit(w)
        if check(desc, d == exp_d and u == exp_u, f"got ({d}, {u!r})"):
            passed += 1

    # ----- Layer 2: form fill against saved fixture -----
    print("\nLayer 2 — _fill_tube_time_period against saved page.html:")
    if not FIXTURE.exists():
        print(f"  SKIP — fixture missing: {FIXTURE}")
        print(f"\nResult: {passed}/{total} passed (layer-2 skipped)")
        return 0 if passed == total else 1

    logs: list[str] = []
    def log(msg: str) -> None:
        logs.append(msg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # `commit` returns as soon as the navigation is committed (response
        # headers received) — doesn't wait for any subresource. Plus block
        # external fetches so the saved Google Maps script can't hang us.
        page.route("**/*", lambda route: route.abort() if route.request.url.startswith(("http://", "https://")) else route.continue_())
        page.goto(FIXTURE.as_uri(), wait_until="commit", timeout=60_000)
        # Wait until <body> exists so we know the parser has the DOM ready.
        # Don't wait for `domcontentloaded` — blocked external scripts make
        # that event never fire.
        page.wait_for_selector("body", timeout=15_000)
        page.wait_for_timeout(500)

        # Inject `in` class + display:block on the Add Study Group modal.
        page.evaluate(
            """() => {
                const heads = Array.from(document.querySelectorAll('div.modal-header.modal-title'));
                const addModal = heads.map(h => h.closest('div.modal.fade')).find(m => m && m.textContent.includes('Add Study Group'));
                if (!addModal) return;
                addModal.classList.add('in');
                addModal.style.display = 'block';
                addModal.removeAttribute('aria-hidden');
            }"""
        )

        modal = page.locator("div.modal.fade.in").filter(
            has_text=re.compile(r"Add\s+Study\s+Group", re.IGNORECASE)
        ).first

        # Fill 3 Days (the 72-hour case)
        _fill_tube_time_period(modal, page, duration=3, unit="Day", days=["Midweek (T-W-Th)"], log=log)

        # Read back the Duration input and the unit select
        panel = modal.locator(
            "div.panel:has(h3.panel-title:has-text('Add Time Period'))"
        ).first
        actual_duration = panel.locator("input[type='number']").first.input_value()
        actual_unit_select = panel.locator("select:has(option[value='Hour'])").first
        actual_unit = actual_unit_select.input_value()

        total += 1
        if check(
            "Fixture: Duration field reads back '3'",
            actual_duration == "3",
            f"got {actual_duration!r}",
        ):
            passed += 1

        total += 1
        if check(
            "Fixture: unit <select> reads back 'Day'",
            actual_unit == "Day",
            f"got {actual_unit!r}",
        ):
            passed += 1

        # Verify the panel actually has the expected structural elements:
        # an ss-multiselect-dropdown for Start Day, plus an ADD TIME PERIOD button.
        total += 1
        n_pickers = panel.locator("ss-multiselect-dropdown button.dropdown-toggle").count()
        if check(
            "Fixture: tube panel has at least 1 Start Day picker",
            n_pickers >= 1,
            f"got {n_pickers}",
        ):
            passed += 1

        browser.close()

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
