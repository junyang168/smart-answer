#!/usr/bin/env python3
"""Configure personal Gmail intake without changing shared Drive/Docs credentials."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.sunday_assistant.gmail_source import validate_configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--mailbox', action='append', required=True)
    parser.add_argument('--reviewer', action='append', required=True)
    parser.add_argument('--token-directory', type=Path, required=True)
    parser.add_argument('--disabled', action='store_true')
    args = parser.parse_args()
    config = args.config.expanduser().resolve()
    token_root = args.token_directory.expanduser().resolve()
    # config normally lives under DATA_BASE_DIR/config; secrets must not.
    data = config.parent.parent
    if token_root == data or data in token_root.parents:
        parser.error('Token directory must be outside the website data directory')
    value = {'enabled': not args.disabled, 'saturday_followup': True, 'mailboxes': sorted(set(address.lower() for address in args.mailbox)),
             'reviewers': sorted(set(address.lower() for address in args.reviewer)), 'token_directory': str(token_root)}
    validate_configuration(value)
    if config.exists() and json.loads(config.read_text()) != value:
        parser.error('Existing configuration differs; review it before changing mailbox access')
    token_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        with config.open('x') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        config.chmod(0o600)
    print('Gmail intake configured:', ', '.join(value['mailboxes']))
    print('Each mailbox still requires its own read-only Google consent.')


if __name__ == '__main__':
    main()
