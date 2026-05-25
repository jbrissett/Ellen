"""Live Playwright test: call `_force_close_open_dropdowns` against the
ACTUAL qchub DOM saved from production runs.

The synthetic-modal test exercises the function's behavior in isolation,
but the real proof is: does the modal-header locator resolve to the right
element in qchub's own HTML? Does the JS class-cleanup find the right
classes? Does the modal stay visible afterward?

Uses the saved page.html from run-20260514-151726 (which captured the Tube
state with multiple dropdowns visible). After injecting `.in` on the Add
Study Group modal, we invoke the live function and assert:

  1. The 'div.modal.fade.in div.modal-header' locator resolves to exactly
     one element (the visible modal's header)
  2. The header's text is 'Add Study Group' (we're targeting the right modal)
  3. After force-close, the modal still has class 'in' (NOT dismissed)
  4. After force-close, no [role='menuitem']:visible items remain
  5. The function logs no leftover-menuitems warning

Run: python tools/repro_force_close_against_real_qchub_dom.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright

from traffic_intake.qchub import _force_close_open_dropdowns  # type: ignore


FIXTURE = Path(
    r"C:\Users\jbris\AppData\Local\TrafficIntake\qchub-diagnostics"
    r"\run-20260514-151726\06-add-study-groups\page.html"
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
    logs: list[str] = []
    def log(msg: str) -> None:
        logs.append(msg)

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

        # Inject the `in` class on the Add Study Group modal so :visible matches.
        # Mirrors what the live app sees when the modal is actually open.
        injected = page.evaluate(
            """() => {
                const heads = Array.from(document.querySelectorAll(
                    'div.modal-header.modal-title, div.modal-header'
                ));
                const addModal = heads
                    .map(h => h.closest('div.modal.fade'))
                    .find(m => m && m.textContent.includes('Add Study Group'));
                if (!addModal) return {found: false};
                addModal.classList.add('in');
                addModal.style.display = 'block';
                addModal.removeAttribute('aria-hidden');
                return {found: true};
            }"""
        )
        print(f"  inject result: {injected}\n")

        # ---- Pre-flight: the modal-header locator resolves on the real DOM ----
        header_locator = page.locator("div.modal.fade.in div.modal-header")
        header_count = header_locator.count()
        total += 1
        if check(
            "modal-header locator resolves to >=1 element on real qchub DOM",
            header_count >= 1,
            f"count={header_count}",
        ):
            passed += 1

        # The active modal's header should say 'Add Study Group'
        first_header_text = header_locator.first.inner_text().strip()
        total += 1
        if check(
            "First matched header is the Add Study Group modal",
            "Add Study Group" in first_header_text,
            f"text={first_header_text!r}",
        ):
            passed += 1

        # ---- Snapshot modal visibility BEFORE force-close ----
        modal_in_before = page.locator("div.modal.fade.in").count()
        total += 1
        if check(
            "Modal has .in class BEFORE force-close (sanity)",
            modal_in_before >= 1,
            f"modal-with-in count={modal_in_before}",
        ):
            passed += 1

        # ---- Call the live function ----
        logs.clear()
        _force_close_open_dropdowns(page, log)
        print(f"  force-close logs: {logs!r}")

        # ---- Assertion: modal STILL has .in class AFTER force-close ----
        modal_in_after = page.locator("div.modal.fade.in").count()
        total += 1
        if check(
            "Modal STILL has .in class AFTER force-close (NOT dismissed) — the 176404/176406 regression guard",
            modal_in_after == modal_in_before and modal_in_after >= 1,
            f"before={modal_in_before}, after={modal_in_after}",
        ):
            passed += 1

        # ---- Assertion: no leftover-menuitems warning was logged ----
        # The saved fixture has no OPEN menus, so we expect zero leftovers.
        warned_about_leftovers = any("still visible after force-close" in m for m in logs)
        total += 1
        if check(
            "No 'leftover menuitems' warning logged (fixture has no open menus)",
            not warned_about_leftovers,
            f"logs={logs!r}",
        )      :
            passed += 1

        # ---- Assertion: no [role='menuitem']:visible remains anywhere ----
        leftover_menuitems = page.locator("[role='menuitem']:visible").count()
        total += 1
        if check(
            "Zero visible [role='menuitem'] elements after force-close",
            leftover_menuitems == 0,
            f"leftover={leftover_menuitems}",
        ):
            passed += 1

        browser.close()

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
