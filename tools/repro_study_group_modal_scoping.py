"""Verify Add Study Group locators are correctly scoped to the active modal.

Loads the saved page.html from the failed run-20260514-084417 (which has both
an active Add Study Group modal and a dormant Edit Study Group modal in the
same DOM), makes the right modal visible, and asserts each of the locators
in _drive_one_group / _add_time_period_until_row_appears / dismiss helpers
resolves to exactly one element inside the visible modal — NOT one of the
dormant lookalikes (which is the bug we're chasing).

Run: python tools/repro_study_group_modal_scoping.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright


FIXTURE = Path(
    r"C:\Users\jbris\AppData\Local\TrafficIntake\qchub-diagnostics"
    r"\run-20260514-084417\06-add-study-groups\page.html"
)


def main() -> int:
    if not FIXTURE.exists():
        print(f"FIXTURE MISSING: {FIXTURE}")
        return 1

    passed = 0
    failed = 0

    def check(label, condition, detail=""):
        nonlocal passed, failed
        status = "PASS" if condition else "FAIL"
        if condition:
            passed += 1
        else:
            failed += 1
        print(f"  {status}  {label}  {detail}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # Block external requests — the saved page references Google Maps
        # JS which can stall even `domcontentloaded` waits.
        page.route("**/*", lambda route: route.abort() if route.request.url.startswith(("http://", "https://")) else route.continue_())
        page.goto(FIXTURE.as_uri(), wait_until="domcontentloaded")

        # Make the Add Study Group modal visible. The dormant Edit modal stays
        # display:none — that's exactly the configuration we want to test
        # against (one Add modal `in`, several non-Add modals lurking).
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
                return {found: true};
            }"""
        )
        print(f"  inject result: {injected}\n")

        # Replicate the _active_modal helper inline here so this test stays
        # standalone (no chance of accidentally testing against an old impl).
        modal = page.locator("div.modal.fade.in").filter(
            has_text=re.compile(r"Add\s+Study\s+Group", re.IGNORECASE)
        ).first

        # ---- Test 1: modal scoping resolves to exactly the Add modal ----
        modal_text = modal.inner_text()[:60]
        check(
            "Active modal is the Add Study Group one",
            "Add Study Group" in modal_text,
            f"first 60 chars: {modal_text!r}",
        )

        # ---- Test 2: study-type radios are unambiguous inside the modal ----
        turn_count = modal.locator("input[name='optradio'][value='Turn']").count()
        check(
            "modal-scoped optradio[Turn] resolves to 1 element",
            turn_count == 1,
            f"got count={turn_count}",
        )

        # ---- Test 3: timePeriod radios in modal don't reach into Edit modal's timePeriod2 ----
        tp_standard = modal.locator("input[name='timePeriod'][value='standar']").count()
        check(
            "modal-scoped timePeriod[standar] resolves to 1 element (not Edit modal's timePeriod2)",
            tp_standard == 1,
            f"got count={tp_standard}",
        )
        tp2_inside_modal = modal.locator("input[name='timePeriod2']").count()
        check(
            "modal-scoped DOES NOT accidentally include timePeriod2 (Edit modal's name)",
            tp2_inside_modal == 0,
            f"got count={tp2_inside_modal}",
        )

        # ---- Test 4: ADD TIME PERIOD button resolves to 1 element inside modal ----
        atp = modal.get_by_role("button", name=re.compile(r"add\s*time\s*period", re.IGNORECASE))
        atp_count = atp.count()
        check(
            "modal-scoped ADD TIME PERIOD button resolves to 1 element (not 3 like page-level)",
            atp_count == 1,
            f"got count={atp_count}",
        )
        # Whole-page check shows the 3 buttons exist — proving scoping matters
        all_atp = page.get_by_role("button", name=re.compile(r"add\s*time\s*period", re.IGNORECASE)).count()
        check(
            "Whole-page ADD TIME PERIOD button count is 3 (proves scoping is meaningful)",
            all_atp == 3,
            f"got count={all_atp}",
        )

        # ---- Test 5: CREATE GROUP button is unambiguous inside modal ----
        cg = modal.get_by_role("button", name=re.compile(r"^\s*create\s*group\s*$", re.IGNORECASE))
        cg_count = cg.count()
        check(
            "modal-scoped CREATE GROUP button resolves to 1 element",
            cg_count == 1,
            f"got count={cg_count}",
        )

        # ---- Test 6: Cancel button is unambiguous inside modal ----
        cancel = modal.get_by_role("button", name=re.compile(r"^cancel$", re.IGNORECASE))
        cancel_count = cancel.count()
        check(
            "modal-scoped Cancel button resolves to 1 element",
            cancel_count == 1,
            f"got count={cancel_count}",
        )

        # ---- Test 7: Added Time Period rows locator (same as _add_time_period_until_row_appears) ----
        # qchub only renders <tbody> when rows exist, so we count <tr> directly.
        # The fixture has no rows committed — failure-state we detect.
        rows = modal.locator(
            "div.panel:has(h3.panel-title:has-text('Added Time Period')) table tr"
        )
        tr_count = rows.count()
        check(
            "Added Time Period table has 0 rows in this fixture (the failure-state we detect)",
            tr_count == 0,
            f"got tr count={tr_count}",
        )

        # ---- Test 8: AM Peak label resolves inside modal ----
        am_label = modal.locator("label").filter(
            has_text=re.compile(r"AM\s*Peak\s*\(", re.IGNORECASE)
        ).first
        check(
            "modal-scoped AM Peak label resolves",
            am_label.count() >= 1,
            f"got count={am_label.count()}",
        )

        # ---- Test 9: ss-multiselect-dropdown pickers inside modal (the day-picker source) ----
        # In the Standard mode panel we expect 2 visible pickers (AM, PM); the
        # fixture's modal might show them as visible since the radio is pristine.
        # Mainly we want to confirm scoping doesn't reach into the dormant modals.
        modal_pickers = modal.locator("ss-multiselect-dropdown button.dropdown-toggle").count()
        all_pickers = page.locator("ss-multiselect-dropdown button.dropdown-toggle").count()
        check(
            "modal-scoped picker count is LESS than whole-page picker count (proves scoping)",
            modal_pickers < all_pickers,
            f"modal={modal_pickers}  page-wide={all_pickers}",
        )

        # ---- Test 10: positive control — same locator finds 1 row in the
        # earlier successful run-000557 snapshot, where ADD TIME PERIOD
        # actually did commit a row.
        positive_fixture = Path(
            r"C:\Users\jbris\AppData\Local\TrafficIntake\qchub-diagnostics"
            r"\run-20260514-000557\06-add-study-groups\page.html"
        )
        if positive_fixture.exists():
            page2 = browser.new_page()
            page2.route("**/*", lambda route: route.abort() if route.request.url.startswith(("http://", "https://")) else route.continue_())
            page2.goto(positive_fixture.as_uri(), wait_until="domcontentloaded")
            page2.evaluate(
                """() => {
                    const heads = Array.from(document.querySelectorAll(
                        'div.modal-header.modal-title'
                    ));
                    const addModal = heads
                        .map(h => h.closest('div.modal.fade'))
                        .find(m => m && m.textContent.includes('Add Study Group'));
                    if (!addModal) return;
                    addModal.classList.add('in');
                    addModal.style.display = 'block';
                }"""
            )
            modal2 = page2.locator("div.modal.fade.in").filter(
                has_text=re.compile(r"Add\s+Study\s+Group", re.IGNORECASE)
            ).first
            rows2 = modal2.locator(
                "div.panel:has(h3.panel-title:has-text('Added Time Period')) table tr"
            )
            tr2 = rows2.count()
            check(
                "Positive control: earlier successful run shows >=1 committed row",
                tr2 >= 1,
                f"got tr count={tr2}",
            )
            page2.close()
        else:
            print(f"  SKIP positive control — fixture missing: {positive_fixture}")

        browser.close()

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
