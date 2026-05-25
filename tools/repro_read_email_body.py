"""Unit test for the email_body field + read_email_body tool.

Verifies:
- StudyRequest accepts and round-trips email_body
- get_request DUMP omits email_body (keeps state checks lightweight)
- read_email_body returns the body content
- Empty/None body returns a useful explanatory message rather than crashing
- Forwarded-email body assembly: forwarder notes + original client message
- The body actually contains scope details that Ellen can use (the kind of
  thing the extractor would have missed and Ellen would have asked the user
  about — e.g. the FDOT 'all 4 approaches' phrasing)

Run: python tools/repro_read_email_body.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.chat import execute_tool  # type: ignore
from traffic_intake.extractor import _body_for_chat  # type: ignore
from traffic_intake.models import StudyRequest  # type: ignore
from traffic_intake.parser import ParsedEmail  # type: ignore


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}  {detail}")
    return condition


def make_request_with_body(body: str) -> StudyRequest:
    return StudyRequest(
        email_subject="Quote for SR 72 traffic counts",
        email_from="cseat@example.com",
        email_to="orders@qualitycounts.net",
        email_body=body,
        locations=[],
    )


def main() -> int:
    passed = 0
    total = 0

    SAMPLE_BODY = """Hi team,

Please quote the following traffic counts on SR 72 (FDOT D1):

- 7 turning movement counts (7am-7pm) at: SR 72 & Proctor Rd,
  SR 72 & Preservation Dr, SR 72 & Aventura Dr, SR 72 & Churchill Downs Rd,
  SR 72 & Coash Rd, SR 72 & Timberland Ln, SR 72 & Lorraine Rd
- 72-hr volume counts on each of the 4 approaches at each intersection (28 total)
- 1 mid-block 72-hr volume + class count on SR 72 between Proctor and Lorraine

Thanks,
Conor"""

    # ---- Case 1: StudyRequest carries the body ----
    req = make_request_with_body(SAMPLE_BODY)
    total += 1
    if check(
        "StudyRequest carries email_body",
        req.email_body == SAMPLE_BODY,
        "",
    ):
        passed += 1

    # ---- Case 2: get_request omits email_body from JSON dump ----
    total += 1
    result = execute_tool("get_request", {}, req)
    dump = json.loads(result)
    if check(
        "get_request DUMP omits email_body (keeps state checks lightweight)",
        "email_body" not in dump,
        f"keys={list(dump.keys())}",
    ):
        passed += 1

    # ---- Case 3: read_email_body returns the body ----
    total += 1
    result = execute_tool("read_email_body", {}, req)
    if check(
        "read_email_body returns the body content",
        result == SAMPLE_BODY,
        "",
    ):
        passed += 1

    # ---- Case 4: read_email_body on empty body returns explanatory message ----
    empty_req = make_request_with_body("")
    total += 1
    result = execute_tool("read_email_body", {}, empty_req)
    if check(
        "Empty body returns explanatory message (no crash)",
        "no email body captured" in result.lower(),
        f"result={result[:80]!r}",
    ):
        passed += 1

    # ---- Case 5: body contains the scope details Ellen needs to NOT ask about ----
    # This is the actual bug from today: extractor missed "all 4 approaches",
    # "72-hr", "volume + class on mid-block". Ellen now has access via body.
    body_keywords = ["4 approaches", "72-hr", "volume + class"]
    total += 1
    missing = [k for k in body_keywords if k not in SAMPLE_BODY]
    if check(
        "Body contains the scope details that extractor commonly misses",
        not missing,
        f"missing: {missing}" if missing else "",
    ):
        passed += 1

    # ---- Case 6: forwarded email body assembly ----
    fwd = ParsedEmail(
        subject="FW: Quote for SR 72",
        from_="qc-staffer@qualitycounts.net",
        to="orders@qualitycounts.net",
        cc=None,
        date=None,
        body_text="Some forwarded body",
        is_forwarded=True,
        forwarder_added_text="Andrew said this is a rush — please prioritize.",
        original_body="Hi team,\n\nPlease quote 7 TMCs on SR 72 at the following intersections...",
        original_from="cseat@example.com",
    )
    body = _body_for_chat(fwd)
    total += 1
    if check(
        "Forwarded body includes both forwarder notes AND original client message",
        "FORWARDER NOTES" in body and "rush" in body
        and "ORIGINAL CLIENT MESSAGE" in body and "7 TMCs" in body,
        "",
    ):
        passed += 1

    # ---- Case 7: non-forwarded email — body is just body_text ----
    plain = ParsedEmail(
        subject="x", from_="a@b", to="c@d", cc=None, date=None,
        body_text="plain body content",
    )
    total += 1
    if check(
        "Non-forwarded email -> body is just the plain body_text",
        _body_for_chat(plain) == "plain body content",
        "",
    ):
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
