#!/usr/bin/env python3
"""Authorize exactly one configured mailbox with Gmail read-only scope.

A separate token is stored privately; token.json used by Drive/Docs is untouched.
The browser performs the normal Google consent flow. No credentials are printed.
"""
import argparse
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.sunday_assistant.gmail_source import SCOPE, configuration, token_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--email', required=True)
    parser.add_argument('--client-secret', type=Path, required=True)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--verify-pending', action='store_true', help='Verify saved consent after enabling the Gmail API; no new consent')
    args = parser.parse_args()
    os.environ['SUNDAY_GMAIL_CONFIG'] = str(args.config.resolve())
    config = configuration()
    mailbox = args.email.lower()
    destination = token_path(config, mailbox)
    if destination.is_symlink():
        raise RuntimeError('Gmail 憑證不可連結到其他 Google token')
    pending = destination.with_suffix('.pending.json')
    if pending.is_symlink():
        raise RuntimeError('待核對 Gmail 憑證不可為連結')
    from backend.google_oauth_token import _write_back
    from google_auth_oauthlib.flow import InstalledAppFlow
    if args.verify_pending:
        from google.oauth2.credentials import Credentials
        credentials = Credentials.from_authorized_user_file(str(pending))
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), [SCOPE])
        credentials = flow.run_local_server(host='127.0.0.1', port=0, open_browser=not args.no_browser,
                                        timeout_seconds=600, login_hint=mailbox, prompt='consent',
                                        success_message='Gmail 唯讀連線已接收，請回到 Codex。',
                                        authorization_prompt_message='Authorize this mailbox in your browser: {url}')
    if not credentials.has_scopes([SCOPE]) or not credentials.refresh_token:
        raise RuntimeError('Google 未提供持續唯讀連線，未儲存憑證')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_back(pending, credentials.to_json())
    from google.auth.transport.requests import AuthorizedSession
    response = AuthorizedSession(credentials).get('https://gmail.googleapis.com/gmail/v1/users/me/profile', timeout=20)
    if not response.ok:
        raise RuntimeError('無法核對 Gmail 郵箱；請確認 Gmail API 已啟用，再用 --verify-pending 核對。尚未啟用收件。')
    if response.json()['emailAddress'].lower() != mailbox:
        raise RuntimeError('登入了不同 Gmail 帳號，未儲存憑證；請重新選擇指定郵箱')
    _write_back(destination, credentials.to_json())
    pending.unlink(missing_ok=True)
    print('Verified read-only Gmail connection:', mailbox)


if __name__ == '__main__':
    main()
