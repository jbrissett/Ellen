"""Regression tests for qchub auto-create data shapes and pre-create
dupe-check fuzzy match.

Pure-Python layer of the auto-create flow — no qchub browser, no network.
The browser-driving piece (`_create_company_via_admin` /
`_create_client_user_via_admin`) is tested manually against the live
admin pages because Playwright + Angular + qchub timing isn't worth
mocking; the data layer beneath it gets these unit tests.

Pin downs:
  - `CompanyInfo` / `UserInfo` default the phone to the documented
    sentinel ('000-000-0000'). Per user direction 2026-05-18:
    we don't track client phones in our database, so qchub's
    required phone field gets a placeholder + chat warning rather
    than blocking the create.

  - `find_company_near_matches` ranks duplicates by token overlap
    using the same `_tokenize_company` normalization as the existing
    User-form fuzzy matcher — corporate noise tokens (Inc/LLC/etc.)
    don't drive false positives. Subset matches score 1.0,
    partials use Jaccard similarity.

  - `normalize_state_to_full_name` handles 2-letter abbrevs from
    email signatures + full names from chat corrections, mapping
    both to qchub's State-dropdown labels.
"""
from __future__ import annotations

from traffic_intake.qchub import (
    CompanyInfo,
    CompanyNearMatch,
    UserInfo,
    _PHONE_SENTINEL,
    find_company_near_matches,
    normalize_state_to_full_name,
)


# ----- data-shape defaults -----

def test_company_info_phone_default_is_sentinel():
    """Phone is required on the qchub form but we don't store client
    phones. Sentinel must be set so the form doesn't reject."""
    c = CompanyInfo(
        name="Acme", email="x@acme.com",
        address_1="1 A St", city="Y", state="FL", zip_code="33333",
    )
    assert c.phone == _PHONE_SENTINEL == "000-000-0000"


def test_company_info_phone_override():
    c = CompanyInfo(
        name="Acme", email="x@acme.com",
        address_1="1 A St", city="Y", state="FL", zip_code="33333",
        phone="555-1234",
    )
    assert c.phone == "555-1234"


def test_company_info_parent_defaults_none():
    """Most companies are top-level. Parent is for branches only —
    leave None unless the caller explicitly wants a branch."""
    c = CompanyInfo(
        name="Acme", email="x@acme.com",
        address_1="1 A St", city="Y", state="FL", zip_code="33333",
    )
    assert c.parent_company_match is None


def test_user_info_phone_default():
    u = UserInfo(
        email="j@x.com", first_name="J", last_name="X",
        company_match_text="X Co",
    )
    assert u.phone == _PHONE_SENTINEL


# ----- find_company_near_matches -----

def test_exact_subset_match_scores_1_0():
    """Candidate is a subset of an option (or vice versa) → score 1.0.
    The user MUST see this case before we create another '3J Consulting'."""
    matches = find_company_near_matches("3J Consulting", [
        "3J Consulting - Beaverton, OR",
    ])
    assert len(matches) == 1
    assert matches[0].score == 1.0
    assert matches[0].option_text == "3J Consulting - Beaverton, OR"


def test_no_token_overlap_returns_empty():
    matches = find_company_near_matches("PSI Engineering", [
        "3J Consulting - Beaverton, OR",
        "Acme Co - LA, CA",
    ])
    assert matches == []


def test_corporate_suffix_alone_does_not_match():
    """Two firms sharing only 'Inc' (or other corporate-noise tokens)
    must NOT be flagged as a near-match — those tokens are filtered
    out by `_tokenize_company`."""
    matches = find_company_near_matches("Smith Inc", [
        "Jones Inc - Anywhere, US",
        "Doe Engineering, LLC - X, Y",
    ])
    assert matches == []


def test_partial_token_overlap_returns_jaccard_score():
    """Partial overlap → Jaccard similarity between 0 and 1."""
    matches = find_company_near_matches("Kimley Horn", [
        "Kimley-Horn and Associates - Sarasota, FL",
    ])
    assert len(matches) == 1
    assert 0 < matches[0].score <= 1.0
    assert "kimley" in matches[0].overlap_tokens
    assert "horn" in matches[0].overlap_tokens


def test_top_matches_sorted_by_score_descending():
    """When multiple options overlap, the strongest match comes first."""
    opts = [
        "Smith Engineering - Denver, CO",          # subset → score 1.0
        "Smith Consulting Group - Dallas, TX",      # partial → < 1.0
        "Acme Corp - LA, CA",                       # no overlap → not returned
    ]
    matches = find_company_near_matches("Smith Engineering", opts)
    assert len(matches) == 2  # Acme excluded
    assert matches[0].option_text == "Smith Engineering - Denver, CO"
    assert matches[0].score == 1.0
    assert matches[1].score < matches[0].score


def test_max_matches_caps_results():
    """A common-token firm like 'Smith' could match dozens — cap so the
    user gets a short, scannable list."""
    opts = [f"Smith Group {i} Engineering - X, Y" for i in range(20)]
    matches = find_company_near_matches("Smith Engineering", opts, max_matches=3)
    assert len(matches) == 3


def test_empty_candidate_returns_empty():
    """Edge case: candidate has no significant tokens (only noise like 'Inc')."""
    assert find_company_near_matches("Inc", ["Smith Inc - X, Y"]) == []
    assert find_company_near_matches("", ["Smith Inc - X, Y"]) == []


def test_overlap_tokens_excludes_noise():
    """The overlap_tokens reported back to the user shouldn't include
    corporate noise — those tokens were never in the comparison set."""
    matches = find_company_near_matches("Smith Engineering Inc", [
        "Smith Engineering LLC - X, Y",
    ])
    assert "inc" not in matches[0].overlap_tokens
    assert "llc" not in matches[0].overlap_tokens
    assert "smith" in matches[0].overlap_tokens
    assert "engineering" in matches[0].overlap_tokens


# ----- normalize_state_to_full_name -----

def test_state_abbrev_uppercase():
    assert normalize_state_to_full_name("FL") == "Florida"
    assert normalize_state_to_full_name("UT") == "Utah"
    assert normalize_state_to_full_name("CA") == "California"


def test_state_abbrev_lowercase():
    """Email signatures sometimes have lowercase state codes."""
    assert normalize_state_to_full_name("fl") == "Florida"
    assert normalize_state_to_full_name("tx") == "Texas"


def test_state_full_name_passes_through():
    assert normalize_state_to_full_name("Florida") == "Florida"


def test_state_full_name_case_normalized():
    """Full names with weird casing get normalized to the dropdown's canonical form."""
    assert normalize_state_to_full_name("florida") == "Florida"
    assert normalize_state_to_full_name("FLORIDA") == "Florida"


def test_state_dc_handled():
    """District of Columbia is on the dropdown; both DC and the full
    spelling should resolve."""
    assert normalize_state_to_full_name("DC") == "District of Columbia"
    assert normalize_state_to_full_name("District of Columbia") == "District of Columbia"


def test_state_unknown_returns_unchanged():
    """Unrecognized state input falls through so the caller can decide
    whether to ask the user or fail loudly. Returning a fake mapping
    would be worse — qchub would silently accept the wrong state."""
    assert normalize_state_to_full_name("Atlantis") == "Atlantis"
    assert normalize_state_to_full_name("XX") == "XX"


def test_state_empty_and_none():
    assert normalize_state_to_full_name("") == ""
    assert normalize_state_to_full_name(None) == ""
    assert normalize_state_to_full_name("   ") == ""
