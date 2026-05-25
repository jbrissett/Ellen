"""Live reproducer for the Specific Time panel fill.

Loads the saved page.html from the most recent diagnostic run as a static
fixture, makes the Add Study Group modal visible (Angular stripped the `in`
class + `display:none` when the snapshot was saved), then calls the helpers
in qchub.py and asserts the four fields read back the expected values.

Static fixture limits: this proves the LOCATORS are correct and that the
fields accept .fill() values. It cannot prove Angular's event wiring (the
fixture has no Angular runtime). The live qchub app is the source of truth
for that.

Run: python tools/repro_specific_time_fill.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright

from traffic_intake.qchub import _fill_specific_time, _fill_start_time_input  # type: ignore


FIXTURE = Path(
    r"C:\Users\jbris\AppData\Local\TrafficIntake\qchub-diagnostics"
    r"\run-20260514-000557\06-add-study-groups\page.html"
)


def main() -> int:
    if not FIXTURE.exists():
        print(f"FIXTURE MISSING: {FIXTURE}")
        return 1

    logs: list[str] = []
    def log(msg: str) -> None:
        logs.append(msg)
        print(f"  log: {msg}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FIXTURE.as_uri())

        # Make the Add Study Group modal visible. The saved snapshot has
        # `display:none` on it (closed at capture time). We need `modal fade in`
        # + a non-`none` display for `:visible` to match.
        injected = page.evaluate(
            """() => {
                const heads = Array.from(document.querySelectorAll(
                    'div.modal-header.modal-title'
                ));
                const addModal = heads
                    .map(h => h.closest('div.modal.fade'))
                    .find(m => m && m.textContent.includes('Add Study Group'));
                if (!addModal) return {found: false};
                addModal.classList.add('in');
                addModal.style.display = 'block';
                addModal.removeAttribute('aria-hidden');
                return {found: true, html_id: addModal.getAttribute('class')};
            }"""
        )
        print(f"  inject result: {injected}")

        # Sanity: the modal locator we use in production must now match.
        modal = page.locator("div.modal.fade.in")
        modal_text = modal.first.inner_text()[:80]
        assert "Add Study Group" in modal_text, f"Wrong modal scoped: {modal_text!r}"
        print(f"  modal.first.text (first 80 chars): {modal_text!r}")

        # ---- Test 1: _fill_start_time_input directly ----
        print("\n--- Test 1: _fill_start_time_input('06:00') ---")
        ok = _fill_start_time_input(modal, "06:00", log)
        actual_start = modal.locator("input[id^='search-to-date-']").first.input_value()
        print(f"  helper returned: {ok}")
        print(f"  Start Time input now reads: {actual_start!r}")
        assert ok, "Helper returned False"
        assert actual_start == "06:00", f"Expected '06:00', got {actual_start!r}"

        # Reset
        modal.locator("input[id^='search-to-date-']").first.fill("")
        modal.locator("input#duration_hours").first.fill("")

        # ---- Test 2: full _fill_specific_time call ----
        # Skip the day picker — that needs Angular to render the multi-select,
        # which the static fixture can't do. We assert only the two text fields
        # we own; the day-picker is exercised by the existing _select_days code
        # in production runs.
        print("\n--- Test 2: _fill_specific_time(start='06:00', hours=13) ---")
        # Patch out _select_days_in_picker for this test — fixture has no
        # multi-select dropdown widget mounted.
        import traffic_intake.qchub as qchub_mod
        orig = qchub_mod._select_days_in_picker
        qchub_mod._select_days_in_picker = lambda *a, **kw: log("(day-picker stubbed)")
        try:
            _fill_specific_time(page, start_time="06:00", hours=13, days=["Midweek (T-W-Th)"], log=log)
        finally:
            qchub_mod._select_days_in_picker = orig

        actual_start = modal.locator("input[id^='search-to-date-']").first.input_value()
        actual_hours = modal.locator("input#duration_hours").first.input_value()
        print(f"  Start Time: {actual_start!r}")
        print(f"  Hours:      {actual_hours!r}")
        assert actual_start == "06:00", f"Start Time wrong: {actual_start!r}"
        assert actual_hours == "13", f"Hours wrong: {actual_hours!r}"

        # ---- Test 3: locator must NOT match the dormant Edit Study Group modal ----
        # The Add Study Group modal we made visible has search-to-date-1; the
        # Edit modal in the same page has search-to-date-2 (different DOM
        # subtree). Verify our scoped locator hits only -1.
        print("\n--- Test 3: scoping to div.modal.fade.in excludes dormant Edit modal ---")
        scoped_ids = modal.locator("input[id^='search-to-date-']").evaluate_all(
            "els => els.map(e => e.id)"
        )
        print(f"  ids inside .modal.fade.in: {scoped_ids}")
        assert scoped_ids == ["search-to-date-1"], f"Unexpected: {scoped_ids}"

        # And confirm the document overall has both (proving the scoping matters):
        all_ids = page.locator("input[id^='search-to-date-']").evaluate_all(
            "els => els.map(e => e.id)"
        )
        print(f"  ids anywhere on page:      {all_ids}")
        assert "search-to-date-1" in all_ids and "search-to-date-2" in all_ids, (
            f"Expected both -1 and -2 in fixture, got {all_ids}"
        )

        browser.close()

    print("\nALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
