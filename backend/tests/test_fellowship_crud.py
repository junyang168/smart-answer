from backend.api import service
from backend.api.models import FellowshipEmailContent, FellowshipEntry


def test_update_fellowship_accepts_iso_date(monkeypatch):
    entry = FellowshipEntry(date="06/05/2026", title="Updated title")
    received = {}

    def update_fellowship(date, payload):
        received["date"] = date
        received["payload"] = payload
        return payload

    monkeypatch.setattr(service.repository, "update_fellowship", update_fellowship)

    assert service.update_fellowship("2026-06-05", entry) == entry
    assert received == {"date": "06/05/2026", "payload": entry}


def test_delete_fellowship_accepts_iso_date(monkeypatch):
    received = {}

    def delete_fellowship(date):
        received["date"] = date

    monkeypatch.setattr(service.repository, "delete_fellowship", delete_fellowship)

    service.delete_fellowship("2026-06-05")

    assert received["date"] == "06/05/2026"


def test_get_fellowship_email_content_accepts_iso_date(monkeypatch):
    entry = FellowshipEntry(
        date="06/05/2026",
        emailSubject="Saved subject",
        emailBodyHtml="<p>Saved body</p>",
    )
    monkeypatch.setattr(service, "list_fellowships", lambda: [entry])

    content = service.get_fellowship_email_content("2026-06-05")

    assert content == FellowshipEmailContent(subject="Saved subject", html="<p>Saved body</p>")


def test_update_fellowship_email_content_accepts_iso_date(monkeypatch):
    payload = FellowshipEmailContent(subject="Subject", html="<p>Body</p>")
    received = {}

    def set_fellowship_email_content(date, subject, html):
        received.update(date=date, subject=subject, html=html)
        return FellowshipEntry(date=date, emailSubject=subject, emailBodyHtml=html)

    monkeypatch.setattr(
        service.repository,
        "set_fellowship_email_content",
        set_fellowship_email_content,
    )

    assert service.update_fellowship_email_content("2026-06-05", payload) == payload
    assert received == {
        "date": "06/05/2026",
        "subject": "Subject",
        "html": "<p>Body</p>",
    }
