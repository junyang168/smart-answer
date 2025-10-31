from __future__ import annotations

import argparse

from dotenv import load_dotenv

from backend.api.sunday_service_email import send_sunday_service_email


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Sunday service info and PPT to workers.")
    parser.add_argument("--date", help="Service date in YYYY-MM-DD format (defaults to upcoming service).")
    parser.add_argument("--dry-run", action="store_true", help="Print email details without sending.")
    args = parser.parse_args()

    load_dotenv()

    result = send_sunday_service_email(args.date, dry_run=args.dry_run)
    if args.dry_run:
        print("DRY RUN - Sunday service email would be sent with the following details:")
    else:
        print("SUCCESS - Sunday service email sent.")
    print("  Date:", result.date)
    print("  Recipients:", ", ".join(result.recipients))
    print("  PPT Filename:", result.ppt_filename)
    print("  Subject:", result.subject)


if __name__ == "__main__":
    main()
