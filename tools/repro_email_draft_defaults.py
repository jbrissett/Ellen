"""Validate the deterministic parts of email_draft (no Outlook COM):
- First-name extraction from various contact-name formats
- Subject derivation (Re: prefix)
- Default body matches the sample template (Lakeland N Socrum Loop Road,
  2026-05-01): greeting + 'Thanks for sending this over!' + PDF/MAP/schedule
  + courtesy + day sign-off
- Day sign-off picks 'great weekend' on Friday, 'great day' otherwise
- PDF version resolution prefers _v3 over _v2 over original

Doesn't invoke Outlook COM — that's left for live testing.
"""
import datetime as _dt
import tempfile
from pathlib import Path

from traffic_intake.email_draft import (
    _build_cc, _day_signoff, _default_body_html, _default_subject,
    _email_address_only, _first_name, _resolve_latest_pdf, _split_addresses,
)
from traffic_intake.models import StudyRequest


def make_request(**overrides) -> StudyRequest:
    base = dict(
        email_subject="Lakeland N Socrum Loop Road - Data Collection",
        email_from="Gibbons, Natalie <Natalie.Gibbons@kimley-horn.com>",
        email_to="QCFLA <QCFLA@qualitycounts.net>",
        email_cc="Howell, Christina <Christina.Howell@kimley-horn.com>",
        client_contact_name="Natalie Gibbons",
        client_contact_email="Natalie.Gibbons@kimley-horn.com",
        locations=[],
    )
    base.update(overrides)
    return StudyRequest(**base)


def test_first_name() -> None:
    assert _first_name("Mingde Lin") == "Mingde"
    assert _first_name("Lin, Mingde") == "Mingde"
    assert _first_name("  Lin,  Mingde  ") == "Mingde"
    assert _first_name("Lin, Mingde Q.") == "Mingde"
    assert _first_name(None) is None
    assert _first_name("") is None
    assert _first_name("Cher") == "Cher"


def test_default_subject() -> None:
    r = make_request()
    assert _default_subject(r) == "Re: Lakeland N Socrum Loop Road - Data Collection", _default_subject(r)
    r2 = make_request(email_subject="Re: ongoing thread")
    assert _default_subject(r2) == "Re: ongoing thread", _default_subject(r2)


def test_day_signoff() -> None:
    friday = _dt.datetime(2026, 5, 1)  # known Friday
    assert friday.weekday() == 4
    assert _day_signoff(friday) == "Have a great weekend!"
    tuesday = _dt.datetime(2026, 5, 5)
    assert tuesday.weekday() == 1
    assert _day_signoff(tuesday) == "Have a great day!"


def test_default_body_full() -> None:
    """The happy-path body has greeting, thanks line, PDF+MAP sentence,
    schedule, MAP link, courtesy, and sign-off — all the elements from
    the Lakeland N Socrum Loop Road sample.
    """
    r = make_request()
    body = _default_body_html(
        r, map_url="https://example.com/map",
        deployment_schedule="next week", has_pdf=True,
    )
    # Greeting uses first name
    assert "Hi Natalie," in body, body
    # Sample's opener
    assert "Thanks for sending this over!" in body, body
    # PDF + map referenced together
    assert "attached the estimate and the project map" in body.lower(), body
    # Schedule
    assert "next week" in body, body
    # MAP link
    assert "MAP:" in body, body
    assert "https://example.com/map" in body, body
    # Courtesy
    assert "let me know if you have any questions" in body.lower(), body
    # Sign-off (one of the two)
    assert "Have a great" in body, body


def test_default_body_no_map() -> None:
    """When no map link, body says estimate-only and won't render a MAP: line."""
    r = make_request()
    body = _default_body_html(r, map_url=None, deployment_schedule=None, has_pdf=True)
    assert "MAP:" not in body, body
    assert "attached the estimate for your review" in body.lower(), body


def test_default_body_no_pdf_no_map() -> None:
    r = make_request()
    body = _default_body_html(r, map_url=None, deployment_schedule=None, has_pdf=False)
    assert "MAP:" not in body, body
    assert "I have the request in hand" in body, body


def test_default_body_map_only() -> None:
    r = make_request()
    body = _default_body_html(
        r, map_url="https://example.com/map",
        deployment_schedule="the week of June 3rd", has_pdf=False,
    )
    assert "MAP:" in body, body
    assert "follow up with the priced estimate" in body, body
    assert "the week of June 3rd" in body, body


def test_email_address_only() -> None:
    assert _email_address_only(
        "Gibbons, Natalie <Natalie.Gibbons@kimley-horn.com>"
    ) == "Natalie.Gibbons@kimley-horn.com"
    assert _email_address_only("plain@example.com") == "plain@example.com"
    assert _email_address_only(None) is None
    assert _email_address_only("") is None


def test_split_addresses() -> None:
    s = ('QCFLA <QCFLA@qualitycounts.net>, Jean-Paul Brissett <jbrissett@qualitycounts.net>; '
         '"Howell, Christina" <Christina.Howell@kimley-horn.com>')
    parts = _split_addresses(s)
    assert len(parts) == 3, parts


def test_build_cc_dedupes_and_skips_self() -> None:
    cc = _build_cc(
        original_to="QCFLA <QCFLA@qualitycounts.net>",
        original_cc="Jean-Paul Brissett <jbrissett@qualitycounts.net>",
        our_address="asuarez@qualitycounts.net",
        override="Christina.Howell@kimley-horn.com",
    )
    # Override comes first
    assert cc[0].lower() == "christina.howell@kimley-horn.com"
    addrs = [(_email_address_only(a) or a).lower() for a in cc]
    assert "asuarez@qualitycounts.net" not in addrs, addrs
    assert any("qcfla" in a for a in addrs), addrs
    assert any("jbrissett" in a for a in addrs), addrs


def test_resolve_latest_pdf_picks_highest_version() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        v1 = tmpdir / "Estimate_176425.pdf"
        v2 = tmpdir / "Estimate_176425_v2.pdf"
        v3 = tmpdir / "Estimate_176425_v3.pdf"
        for p in (v1, v2, v3):
            p.write_bytes(b"%PDF-stub")
        artifacts = {"estimate_pdf_path": str(v1)}
        got = _resolve_latest_pdf(make_request(), artifacts)
        assert got == v3, got


def main() -> None:
    test_first_name()
    test_default_subject()
    test_day_signoff()
    test_default_body_full()
    test_default_body_no_map()
    test_default_body_no_pdf_no_map()
    test_default_body_map_only()
    test_email_address_only()
    test_split_addresses()
    test_build_cc_dedupes_and_skips_self()
    test_resolve_latest_pdf_picks_highest_version()
    print("All email_draft default-logic tests PASSED")


if __name__ == "__main__":
    main()
