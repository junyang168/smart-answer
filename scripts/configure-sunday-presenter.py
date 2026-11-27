#!/usr/bin/env python3
"""Configure a synced OneDrive root without storing personal URLs in source."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from backend.sunday_presenter import OneDrivePpts, config_path


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--sample-folder-url", required=True, help="Existing dated OneDrive folder URL")
    parser.add_argument("--output", type=Path, default=config_path())
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("The existing OneDrive Sunday sync root must be available")
    web = urlsplit(args.sample_folder_url)
    folder_id = dict(parse_qsl(web.query)).get("id", "")
    if web.scheme != "https" or web.netloc != "onedrive.live.com" or web.path != "/my" or not re.search(r"/\d{1,2}-\d{1,2}-\d{4}$", folder_id):
        parser.error("Use an actual dated onedrive.live.com/my?id=... folder URL")
    root_id = folder_id.rsplit("/", 1)[0]
    url = urlunsplit(("https", "onedrive.live.com", "/my", urlencode({"id": root_id}), ""))
    data = {"syncRoot": str(args.root.resolve()), "webRootUrl": url}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(args.output)
    print(f"Saved OneDrive PPT configuration: {args.output}")


if __name__ == "__main__":
    main()
