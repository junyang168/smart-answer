"""The owner's Google OAuth token (Drive, Docs), wherever the process runs from.

Production runs from an immutable release directory, and the token lives in
the configuration directory outside it. Before releases existed the code ran
from one fixed directory with `token.json` beside it, so looking in the
working directory worked; since then it found nothing, fell back to the
service account without a word, and the token went unrefreshed from
2026-07-28 until Google revoked it (OPS-27).

So the path is explicit: `GOOGLE_OAUTH_TOKEN_FILE`, set on the backend's
LaunchAgent by scripts/deploy.sh. A development checkout without it uses
`token.json` at the repository root.

    python -m backend.google_oauth_token check [path]

refreshes the token and writes it back; deploy.sh runs it before switching
releases, so a dead token stops the deploy instead of breaking exports later.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tempfile

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAUTH_HINT = "sign in again: in the config directory run the repo's generate_user_token.py"


class OAuthTokenError(RuntimeError):
    pass


def token_path() -> Path:
    configured = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip()
    return Path(configured) if configured else REPO_ROOT / "token.json"


def is_configured() -> bool:
    """True when production named a token file: then it must work, no silent fallback."""

    return bool(os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip())


def _write_back(path: Path, text: str) -> None:
    # Through a symlink to the config directory, keeping the file private.
    target = path.resolve()
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".token-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_credentials(path: Path | None = None, *, force_refresh: bool = False):
    """Credentials from the token file, refreshed and saved back when expired
    (or always, with `force_refresh`: the only proof Google still accepts it).
    Scopes are the ones the owner granted, as stored in the file; asking for
    others makes Google refuse the refresh.

    Raises OAuthTokenError when the file is missing, unreadable, or can no
    longer be refreshed (Google answers `invalid_grant` once it is revoked).
    """

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = path or token_path()
    if not path.exists():
        raise OAuthTokenError(f"Google OAuth token not found: {path}; {REAUTH_HINT}")
    try:
        credentials = Credentials.from_authorized_user_file(str(path))
    except Exception as exc:
        raise OAuthTokenError(f"Google OAuth token unreadable: {path}: {exc}") from exc
    if force_refresh or not credentials.valid:
        if not credentials.refresh_token:
            raise OAuthTokenError(f"Google OAuth token has no refresh token: {path}; {REAUTH_HINT}")
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise OAuthTokenError(f"Google OAuth token cannot be refreshed ({exc}): {path}; {REAUTH_HINT}") from exc
        _write_back(path, credentials.to_json())
        logger.info("refreshed Google OAuth token %s", path)
    return credentials


def check(path: Path | None = None) -> str:
    """Force a refresh, proving Google still accepts the refresh token."""

    path = path or token_path()
    credentials = load_credentials(path=path, force_refresh=True)
    return f"Google OAuth token ok: {path} (expires {credentials.expiry:%Y-%m-%d %H:%M} UTC)"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "check" or len(args) > 2:
        print("usage: python -m backend.google_oauth_token check [token.json]", file=sys.stderr)
        return 2
    try:
        print(check(Path(args[1]) if len(args) == 2 else None))
    except OAuthTokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
