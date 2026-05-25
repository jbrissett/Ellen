"""Quick CLI to run the parser + extractor on a file. Used for testing."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extractor import extract
from .parser import parse_email_file, prepare_for_extraction


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse a traffic study email and extract its details.")
    ap.add_argument("path", type=Path, help="Path to a .eml or .msg file.")
    ap.add_argument("--raw", action="store_true", help="Print the cleaned email body instead of extracting.")
    ap.add_argument("--show-attachments", action="store_true", help="List attachments found in the email and exit.")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"File not found: {args.path}", file=sys.stderr)
        return 1

    parsed = parse_email_file(args.path)
    prepare_for_extraction(parsed)

    if args.show_attachments:
        print(f"Subject: {parsed.subject}")
        print(f"Attachments: {len(parsed.attachments)}")
        for a in parsed.attachments:
            inline = " [inline]" if a.is_inline else ""
            print(f"  - {a.filename} ({a.content_type}, {len(a.data):,} bytes, category={a.category}){inline}")
        print()
        print(f"KMZ/KML: {len(parsed.kmz_attachments())}")
        print(f"Reference images (>=30 KB): {len(parsed.image_attachments())}")
        return 0

    if args.raw:
        print(f"Subject: {parsed.subject}")
        print(f"From:    {parsed.from_}")
        print(f"To:      {parsed.to}")
        print(f"Date:    {parsed.date}")
        print(f"Forwarded: {parsed.is_forwarded}")
        if parsed.is_forwarded:
            print(f"  Original From: {parsed.original_from}")
            print(f"  Original Date: {parsed.original_date}")
            print(f"  Original Subject: {parsed.original_subject}")
            print("---FORWARDER NOTES---")
            print(parsed.forwarder_added_text or "(none)")
            print("---ORIGINAL BODY---")
            print(parsed.original_body)
        else:
            print("---BODY---")
            print(parsed.body_text)
        return 0

    request = extract(parsed)
    print(json.dumps(request.model_dump(mode="json"), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
