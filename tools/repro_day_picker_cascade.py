"""Test the day-picker cascade fix.

Reproduces the failure pattern from run-20260514-151726:
- Tube's Start Day picker doesn't have 'Midweek (T-W-Th)' as an option
- The failed pick left the dropdown menu open
- The open menu's items intercepted pointer events on ADD TIME PERIOD

Layer 1: pure-Python invariants on the new defaults.
Layer 2: live Playwright against a synthetic page that mimics the qchub
  multi-select dropdown — verify (a) candidate-trying works, (b) the
  dropdown is force-closed on exit even when no candidate matches.

Run: python tools/repro_day_picker_cascade.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright

from traffic_intake.qchub import (  # type: ignore
    _DEFAULT_DAYS,
    _select_days_in_picker,
)


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    # cp1252 console can't render unicode warning glyphs — strip them.
    safe = lambda s: s.encode("ascii", "replace").decode("ascii") if isinstance(s, str) else s
    print(f"  {status}  {safe(label)}  {safe(detail)}")
    return condition


# Synthetic HTML that mimics qchub's ACTUAL broken multiselect dropdown
# AND qchub's modal-closes-on-outside-click behavior (the gap that let
# the document.body.click() bug ship in order 176404):
# - Clicking the toggle adds .open to the wrapper (BS3 pattern), CSS shows menu
# - Clicking a menu item DOES NOT close the menu (multi-select widget)
# - Escape does NOT auto-close (focus stays on the clicked item)
# - Clicking <body> outside the modal CLOSES the modal (BS3 modal behavior).
#   The picker's button text reflects the last picked item; the modal's
#   visibility is reflected via the .fade.in classes (BS3 convention).
SYNTHETIC_HTML = """
<!doctype html>
<html><head><style>
  .dropdown-menu { display: none; }
  .dropdown.open > .dropdown-menu { display: block; }
  .modal { display: none; }
  .modal.fade.in { display: block; }
</style></head><body>
<div class="modal fade in" id="theModal">
  <div class="modal-header modal-title">Add Study Group</div>
  <div class="modal-body">
    Add Study Group panel content here.
    <ss-multiselect-dropdown>
      <div class="dropdown dropdown-inline btn-block">
        <button class="dropdown-toggle" type="button" onclick="
          this.parentElement.classList.toggle('open');
        ">Select day(s)<span class="caret"></span></button>
        <ul class="dropdown-menu">
          <li><a role="menuitem" href="javascript:;" onclick="
            this.closest('.dropdown').querySelector('button').textContent='Monday';
            /* No auto-close — multiselect pattern. THIS is the qchub bug. */
          ">Monday</a></li>
          <li><a role="menuitem" href="javascript:;" onclick="
            this.closest('.dropdown').querySelector('button').textContent='Tuesday';
          ">Tuesday</a></li>
          <li><a role="menuitem" href="javascript:;" onclick="
            this.closest('.dropdown').querySelector('button').textContent='Wednesday';
          ">Wednesday</a></li>
        </ul>
      </div>
    </ss-multiselect-dropdown>
  </div>
</div>
<script>
  // Mirror THREE BS3 default behaviors that qchub inherits:
  // 1. Outside-click closes the MODAL (regression guard for 176404 bug —
  //    document.body.click() in force-close was dismissing the modal).
  // 2. Escape key closes the MODAL (regression guard for 176406 bug —
  //    keyboard.press('Escape') in force-close was dismissing it).
  // 3. Click outside a .dropdown wrapper closes the DROPDOWN (this is the
  //    natural-close mechanism the new force-close exploits by clicking
  //    the modal-header — inside the modal but outside any dropdown).
  document.body.addEventListener('click', function(e) {
    var modal = document.getElementById('theModal');
    if (modal && !modal.contains(e.target)) {
      modal.classList.remove('in');
    }
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      var modal = document.getElementById('theModal');
      if (modal) modal.classList.remove('in');
    }
  });
  document.addEventListener('click', function(e) {
    document.querySelectorAll('.dropdown.open').forEach(function(d) {
      if (!d.contains(e.target)) {
        d.classList.remove('open');
      }
    });
  });
</script>
</body></html>
"""


def main() -> int:
    passed = 0
    total = 0

    # ----- Layer 1: _DEFAULT_DAYS unchanged (Midweek (T-W-Th) confirmed
    # by user as a real option in both TMC and Tube pickers) ------
    print("Layer 1 — default day constants:")
    total += 1
    if check(
        "Default day is 'Midweek (T-W-Th)' — confirmed valid in both TMC and Tube pickers",
        _DEFAULT_DAYS == ["Midweek (T-W-Th)"],
        f"got {_DEFAULT_DAYS}",
    ):
        passed += 1

    # ----- Layer 1b: panel-scoped vs modal-scoped picker resolution
    # against the actual failure-state fixture ------
    print("\nLayer 1b — modal vs panel scoping against saved Tube-state fixture:")
    tube_fixture = Path(
        r"C:\Users\jbris\AppData\Local\TrafficIntake\qchub-diagnostics"
        r"\run-20260514-151726\06-add-study-groups\page.html"
    )
    if tube_fixture.exists():
        with sync_playwright() as p_fx:
            browser_fx = p_fx.chromium.launch(headless=True)
            page_fx = browser_fx.new_page()
            page_fx.route("**/*", lambda route: route.abort() if route.request.url.startswith(("http://", "https://")) else route.continue_())
            page_fx.goto(tube_fixture.as_uri(), wait_until="commit", timeout=60_000)
            page_fx.wait_for_selector("body", timeout=15_000)
            # Inject the `in` class onto the Add Study Group modal so :visible matches.
            page_fx.evaluate(
                """() => {
                    const heads = Array.from(document.querySelectorAll('div.modal-header.modal-title'));
                    const addModal = heads.map(h => h.closest('div.modal.fade')).find(m => m && m.textContent.includes('Add Study Group'));
                    if (!addModal) return;
                    addModal.classList.add('in');
                    addModal.style.display = 'block';
                }"""
            )
            modal_fx = page_fx.locator("div.modal.fade.in").first
            panel_fx = modal_fx.locator(
                "div.panel:has(h3.panel-title:has-text('Add Time Period'))"
            ).first

            modal_picker_count = modal_fx.locator(
                "ss-multiselect-dropdown:visible button.dropdown-toggle"
            ).count()
            panel_picker_count = panel_fx.locator(
                "ss-multiselect-dropdown:visible button.dropdown-toggle"
            ).count()

            total += 1
            if check(
                "Modal-scoped picker count > panel-scoped (Tube subtype dropdown is in modal but NOT in Add Time Period panel)",
                modal_picker_count > panel_picker_count,
                f"modal={modal_picker_count}, panel={panel_picker_count}",
            ):
                passed += 1

            # The Tube subtype's button text is "Volume" — that's the picker
            # picker_index=0 was hitting under the modal scope.
            modal_first_text = modal_fx.locator(
                "ss-multiselect-dropdown:visible button.dropdown-toggle"
            ).first.text_content() or ""
            total += 1
            if check(
                "Modal-scoped picker[0] = Tube subtype dropdown ('Volume') — proves the bug",
                "Volume" in modal_first_text,
                f"text={modal_first_text!r}",
            ):
                passed += 1

            # Panel-scoped picker[0] = Start Day picker (text 'Select day(s)')
            panel_first_text = panel_fx.locator(
                "ss-multiselect-dropdown:visible button.dropdown-toggle"
            ).first.text_content() or ""
            total += 1
            if check(
                "Panel-scoped picker[0] = Start Day picker ('Select day(s)') — proves the fix",
                "Select day(s)" in panel_first_text,
                f"text={panel_first_text!r}",
            ):
                passed += 1

            browser_fx.close()
    else:
        print(f"  SKIP — Tube-state fixture missing: {tube_fixture}")

    # ----- Layer 2: against the synthetic dropdown ------
    print("\nLayer 2 — live dropdown interaction:")
    logs: list[str] = []
    def log(msg: str) -> None:
        logs.append(msg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(SYNTHETIC_HTML)
        modal = page.locator("div.modal.fade.in")

        # Case A: candidate-trying — passing ['Midweek (T-W-Th)', 'Tuesday']
        # should walk past Midweek (no match) and land on Tuesday.
        logs.clear()
        _select_days_in_picker(modal, page, picker_index=0, days=["Midweek (T-W-Th)", "Tuesday"], log=log)
        toggle_text = modal.locator("button.dropdown-toggle").first.text_content() or ""
        total += 1
        if check(
            "Candidate-trying lands on 'Tuesday' after Midweek doesn't match",
            "Tuesday" in toggle_text,
            f"toggle text: {toggle_text!r}, logs: {logs!r}",
        ):
            passed += 1

        # After pick, the menu should be closed. Check via Playwright's
        # :visible so class-based open states (.dropdown.open) are caught,
        # not just inline display:block.
        menu_visible = page.locator("ul.dropdown-menu:visible").count()
        menuitems_visible = page.locator("[role='menuitem']:visible").count()
        total += 1
        if check(
            "Dropdown menu closed after pick (menu hidden AND menuitems not visible)",
            menu_visible == 0 and menuitems_visible == 0,
            f"menu_visible={menu_visible}, menuitems_visible={menuitems_visible}",
        ):
            passed += 1

        # CRITICAL: the modal must STAY OPEN after force-close.
        # Order 176404 (2026-05-14) failed because our force-close was doing
        # document.body.click() which dismissed the modal as a side effect.
        # The synthetic now wires that outside-click->modal-close behavior so
        # the test catches a regression of that exact bug.
        modal_visible = page.locator("div.modal.fade.in").count()
        total += 1
        if check(
            "Modal stays open after force-close (regression guard for order 176404 bug)",
            modal_visible == 1,
            f"modal_visible={modal_visible} (expected 1 — force-close must not dismiss the modal)",
        ):
            passed += 1

        # Case B: NO candidate matches — should still close the menu so it
        # doesn't intercept later clicks. This is the cascade-fix proof.
        # Reset the page first.
        page.set_content(SYNTHETIC_HTML)
        modal = page.locator("div.modal.fade.in")
        logs.clear()
        _select_days_in_picker(modal, page, picker_index=0, days=["Definitely Not A Day"], log=log)

        menu_visible = page.locator("ul.dropdown-menu:visible").count()
        menuitems_visible = page.locator("[role='menuitem']:visible").count()
        total += 1
        if check(
            "Dropdown force-closed even when no candidate matches (cascade fix)",
            menu_visible == 0 and menuitems_visible == 0,
            f"menu_visible={menu_visible}, menuitems_visible={menuitems_visible}",
        ):
            passed += 1

        # Modal must still be open after the failure path too.
        modal_visible = page.locator("div.modal.fade.in").count()
        total += 1
        if check(
            "Modal stays open after failure-path force-close",
            modal_visible == 1,
            f"modal_visible={modal_visible}",
        ):
            passed += 1

        # And the failure log should include what the menu DID show, so the
        # next real run gives us actionable diagnostic info.
        total += 1
        joined = "\n".join(logs)
        if check(
            "Failure log enumerates visible menu options (diagnostic surface)",
            "Visible options" in joined and "Monday" in joined,
            f"log excerpt: {joined[-200:]!r}",
        ):
            passed += 1

        browser.close()

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
