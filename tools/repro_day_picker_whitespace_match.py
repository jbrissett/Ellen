"""Validate the whitespace-tolerant day-picker matching logic.

Reproduces the qchub regression diagnosed in
run-20260514-223411: qchub renders 'Midweek  (T-W-Th)' (two spaces),
our code was sending 'Midweek (T-W-Th)' (one space). The new matcher
collapses internal whitespace before comparing.
"""
import re


def whitespace_pattern(s: str) -> re.Pattern[str]:
    parts = [re.escape(tok) for tok in s.strip().split()]
    return re.compile(r"^\s*" + r"\s+".join(parts) + r"\s*$", re.IGNORECASE)


def collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def main() -> None:
    candidates = [
        "Midweek (T-W-Th)",
        "Tuesday",
        "Saturday",
    ]
    rendered = [
        "Midweek  (T-W-Th)",   # qchub's actual rendering — double space
        " Midweek (T-W-Th) ",  # leading/trailing
        "MIDWEEK (T-W-Th)",    # case variant
        "Tuesday",
        "  Saturday  ",
        "midweek\t(t-w-th)",   # tab between
        "Friday",              # negative: shouldn't match Midweek
    ]
    print("=== regex-based match ===")
    for cand in candidates:
        pat = whitespace_pattern(cand)
        for r in rendered:
            ok = bool(pat.match(r))
            print(f"  {cand!r:30s} vs {r!r:30s} -> {ok}")
    print()
    print("=== collapse-ws fallback match ===")
    for cand in candidates:
        target = collapse_ws(cand)
        for r in rendered:
            ok = collapse_ws(r) == target
            print(f"  {cand!r:30s} vs {r!r:30s} -> {ok}")
    # Spot-checks
    pat = whitespace_pattern("Midweek (T-W-Th)")
    assert pat.match("Midweek  (T-W-Th)"), "double-space should match"
    assert pat.match("Midweek (T-W-Th)"), "single-space should match"
    assert pat.match("midweek (t-w-th)"), "case-insensitive"
    assert not pat.match("Midweek extra (T-W-Th)"), "extra tokens shouldn't match"
    assert collapse_ws("Midweek  (T-W-Th)") == "midweek (t-w-th)"
    print("\nAll spot-checks PASSED")


if __name__ == "__main__":
    main()
