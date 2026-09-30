from __future__ import annotations

import datetime as dt
import json
import stat

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from backend import google_oauth_token as tok


def _token_file(tmp_path, *, expired=True, refresh_token="r"):
    expiry = dt.datetime.utcnow() + dt.timedelta(hours=-1 if expired else 1)
    data = {
        "token": "old-access",
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "c",
        "client_secret": "s",
        "scopes": ["https://www.googleapis.com/auth/drive"],
        "expiry": expiry.isoformat() + "Z",
    }
    path = tmp_path / "config" / "token.json"
    path.parent.mkdir()
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def google(monkeypatch):
    """Google's token endpoint: grants a new access token, or refuses with invalid_grant."""

    state = {"refuse": False, "calls": 0}

    def refresh(self, request):
        state["calls"] += 1
        if state["refuse"]:
            raise RefreshError("invalid_grant: Bad Request")
        self.token = "new-access"
        self.expiry = dt.datetime.utcnow() + dt.timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    return state


def test_expired_token_is_refreshed_and_written_back(tmp_path, google):
    path = _token_file(tmp_path)
    creds = tok.load_credentials(path)
    assert creds.token == "new-access"
    assert json.loads(path.read_text())["token"] == "new-access"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert google["calls"] == 1


def test_valid_token_is_used_without_calling_google(tmp_path, google):
    path = _token_file(tmp_path, expired=False)
    assert tok.load_credentials(path).token == "old-access"
    assert google["calls"] == 0


def test_check_always_asks_google(tmp_path, google):
    path = _token_file(tmp_path, expired=False)
    assert tok.check(path).startswith("Google OAuth token ok")
    assert google["calls"] == 1


def test_revoked_token_raises_with_the_way_out(tmp_path, google):
    google["refuse"] = True
    path = _token_file(tmp_path)
    with pytest.raises(tok.OAuthTokenError, match="invalid_grant.*generate_user_token.py"):
        tok.load_credentials(path)
    assert json.loads(path.read_text())["token"] == "old-access"  # left as it was


def test_missing_token_and_missing_refresh_token(tmp_path, google):
    with pytest.raises(tok.OAuthTokenError, match="not found"):
        tok.load_credentials(tmp_path / "nowhere.json")
    with pytest.raises(tok.OAuthTokenError, match="no refresh token"):
        tok.load_credentials(_token_file(tmp_path, refresh_token=None))


def test_write_back_goes_through_a_symlink_to_the_config_file(tmp_path, google):
    target = _token_file(tmp_path)
    link = tmp_path / "release-token.json"
    link.symlink_to(target)
    tok.load_credentials(link)
    assert link.is_symlink()
    assert json.loads(target.read_text())["token"] == "new-access"


def test_path_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_FILE", str(tmp_path / "t.json"))
    assert tok.token_path() == tmp_path / "t.json" and tok.is_configured()
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_FILE")
    assert tok.token_path() == tok.REPO_ROOT / "token.json" and not tok.is_configured()


def test_check_command_exit_codes(tmp_path, google, capsys):
    path = _token_file(tmp_path)
    assert tok.main(["check", str(path)]) == 0
    google["refuse"] = True
    assert tok.main(["check", str(path)]) == 1
    assert "cannot be refreshed" in capsys.readouterr().err
    assert tok.main([]) == 2
